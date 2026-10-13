"""Walk an Anthropic Messages request and scrub every text-bearing part, deterministically."""
import base64
import copy
import io
import re

from pypdf import PdfReader

from .scrub import scrub_text
from .session import block_hash


class Refuse(Exception):
    """Raised when a request cannot be checked locally; the proxy answers 400 and sends nothing."""


IMG_NOTE = "[blindfold: image withheld]"
DOC_NOTE = "[blindfold: scanned document withheld]"
WITHHELD_NOTE = "[blindfold: content withheld]"
# Structural keys relayed byte for byte, but only at a block's own top level; a nested dict
# (one level inside a block's own fields, e.g. a tool_use input) gets no skip set at all, so a
# "name"/"url"/"data" key buried in there is scrubbed like any other string.
SKIP_KEYS = {"type", "id", "tool_use_id", "signature", "cache_control", "media_type",
             "encrypted_content", "encrypted_index"}
# Block types recognised wherever they occur, top level or nested, and routed through inner()
# instead of a generic walk, so an image/document/text block hiding inside an unknown block or
# a document's structured "content" source is withheld or scrubbed rather than forwarded raw.
_ROUTED_TYPES = ("text", "image", "document", "container_upload")
_ACT = re.compile(r"<command-name>/blindfold</command-name>|blindfold-activation depth=|^\s*/blindfold\b", re.M)
_STRICT = re.compile(r"<command-args>\s*strict\b|blindfold-activation depth=strict|^\s*/blindfold\s+strict\b",
                     re.M | re.I)


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def find_activation(messages) -> tuple[str, int] | None:
    for i, m in enumerate(messages or []):
        if m.get("role") != "user":
            continue
        txt = _text_of(m.get("content"))
        if _ACT.search(txt):
            return ("strict" if _STRICT.search(txt) else "normal", i)
    return None


def _pdf_text(b64: str) -> str:
    reader = PdfReader(io.BytesIO(base64.b64decode(b64)))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


class _Walker:
    def __init__(self, sess, cfg):
        self.sess, self.cfg, self.notices = sess, cfg, set()

    def scrub(self, text: str, tables: bool = True) -> str:
        return scrub_text(self.sess, text, self.cfg, tables=tables, notices=self.notices)

    def memo(self, block: dict, fn, tables: bool = True) -> dict:
        bare = {k: v for k, v in block.items() if k != "cache_control"}
        # The tables flag is part of the key: the same block dict is scrubbed one way for
        # assistant/system text (tables=False) and another for user/tool-result text
        # (tables=True withholds a detected table), and the two must never share a cache slot.
        key = block_hash({"block": bare, "tables": tables})
        hit = self.sess.memo.get(key)
        if hit is None:
            hit = fn(bare)
            self.sess.memo[key] = hit
        out = copy.deepcopy(hit)
        if "cache_control" in block:
            out["cache_control"] = block["cache_control"]
        return out

    def memo_str(self, s: str, tables: bool = True) -> str:
        return self.memo({"type": "_str", "text": s},
                         lambda b: {**b, "text": self.scrub(b["text"], tables)}, tables=tables)["text"]

    def leaves(self, obj, top: bool = True):
        if isinstance(obj, str):
            return self.scrub(obj, tables=False)
        if isinstance(obj, list):
            return [self.leaves(x, top=False) for x in obj]
        if isinstance(obj, dict):
            if obj.get("type") in _ROUTED_TYPES:
                return self.inner(obj)
            skip = SKIP_KEYS if top else set()
            return {k: (v if k in skip else self.leaves(v, top=False)) for k, v in obj.items()}
        return obj

    def document(self, b: dict) -> dict:
        src = b.get("source") or {}
        st = src.get("type")
        if st == "base64" and src.get("media_type") == "application/pdf":
            text = _pdf_text(src.get("data", ""))
            if not text.strip():
                return {"type": "text", "text": DOC_NOTE}
            out = {k: v for k, v in b.items() if k not in ("source", "title", "context")}
            out["source"] = {"type": "text", "media_type": "text/plain", "data": self.scrub(text)}
            for k in ("title", "context"):
                if isinstance(b.get(k), str):
                    out[k] = self.scrub(b[k], tables=False)
            return out
        if st == "text":
            return {**b, "source": {**src, "data": self.scrub(src.get("data", ""))}}
        if st == "content":
            items = src.get("content") or []
            new_items = [self._content_item(x) for x in items]
            return {**b, "source": {**src, "content": new_items}}
        raise Refuse("blindfold: a document in this request could not be checked locally, so nothing was sent.")

    def _content_item(self, x):
        """One item of a document's structured "content" source: a block dict goes through
        inner() (so a nested image/document is withheld or refused rather than forwarded raw);
        a bare string is itself a piece of document text and must be scrubbed like any other;
        anything else (already the wrong shape for this source type) passes through untouched."""
        if isinstance(x, dict):
            return self.inner(x)
        if isinstance(x, str):
            return self.scrub(x, tables=False)
        return x

    def inner(self, b: dict) -> dict:
        t = b.get("type")
        if t == "text":
            return {**b, "text": self.scrub(b.get("text", ""))}
        if t == "image":
            return {"type": "text", "text": IMG_NOTE}
        if t == "document":
            return self.document(b)
        if t == "container_upload":
            raise Refuse("blindfold: uploaded files cannot be checked locally, so nothing was sent.")
        if self.sess.depth == "strict":
            return {"type": "text", "text": WITHHELD_NOTE}
        return self.leaves(b)

    def tool_result(self, b: dict) -> dict:
        c = b.get("content")
        if isinstance(c, str):
            return {**b, "content": self.scrub(c)}
        if isinstance(c, list):
            return {**b, "content": [self.inner(x) for x in c]}
        return b

    def user_block(self, b: dict) -> dict:
        if b.get("type") == "image":
            out = {"type": "text", "text": IMG_NOTE}
            if "cache_control" in b:
                out["cache_control"] = b["cache_control"]
            return out
        if b.get("type") == "tool_result":
            return self.memo(b, self.tool_result, tables=True)
        return self.memo(b, self.inner, tables=True)

    def assistant_block(self, b: dict) -> dict:
        t = b.get("type")
        if t == "text":
            orig = self.sess.inv_text.get(block_hash(b.get("text", "")))
            if orig is not None:
                return {**b, "text": orig}
            return self.memo(b, lambda x: {**x, "text": self.scrub(x.get("text", ""), tables=False)}, tables=False)
        if t == "tool_use":
            orig = self.sess.inv_tool.get(b.get("id"))
            if orig is not None:
                return {**b, "input": orig}
            return self.memo(b, lambda x: {**x, "input": self.leaves(x.get("input", {}), top=False)})
        return b

    def message(self, m: dict) -> dict:
        c = m.get("content")
        if m.get("role") == "assistant":
            if isinstance(c, str):
                orig = self.sess.inv_text.get(block_hash(c))
                return {**m, "content": orig if orig is not None else self.memo_str(c, tables=False)}
            return {**m, "content": [self.assistant_block(b) for b in c or []]}
        if isinstance(c, str):
            return {**m, "content": self.memo_str(c)}
        return {**m, "content": [self.user_block(b) for b in c or []]}

    def system(self, sysv):
        if isinstance(sysv, str):
            return self.memo_str(sysv, tables=False)
        return [self.memo(b, lambda x: {**x, "text": self.scrub(x.get("text", ""), tables=False)}, tables=False)
                if b.get("type") == "text" else b for b in sysv]


def scrub_request(body: dict, sess, cfg, raw_ok=frozenset()) -> tuple[dict, set]:
    """raw_ok: hashes of messages this proxy process actually relayed raw for this session ID
    while it was unprotected. Messages before the activation are left as they were only as far
    as every one of them is in raw_ok; anything else (and everything after a restart) is scrubbed."""
    sess.merge_pending()
    w = _Walker(sess, cfg)
    out = dict(body)
    if "system" in out:
        out["system"] = w.system(out["system"])
    msgs = out.get("messages") or []
    act = find_activation(msgs)
    start = 0
    while act and start < act[1] and block_hash(msgs[start]) in raw_ok:
        start += 1
    out["messages"] = [m if i < start else w.message(m) for i, m in enumerate(msgs)]
    return out, w.notices
