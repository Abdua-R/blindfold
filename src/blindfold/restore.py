"""Put real values back into Claude's reply while it streams, and record exact inversions."""
import json

from .scrub import StreamRestorer, restore_json, restore_text


def is_network_tool(name: str) -> bool:
    return name in ("WebFetch", "WebSearch") or name.startswith("mcp__")


def sse_event(name: str, data: dict) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def _take(notice) -> str | None:
    """A notice is a string or a zero-argument callable that claims it (returns None if there is
    nothing left to show); it is resolved only at the moment it is inserted into a reply."""
    return notice() if callable(notice) else notice


class SSERestorer:
    def __init__(self, sess, notice=None):
        self.sess, self.notice, self.buf, self.blocks = sess, notice, b"", {}

    def feed(self, chunk: bytes) -> bytes:
        self.buf += chunk.replace(b"\r\n", b"\n")
        out = []
        while b"\n\n" in self.buf:
            raw, self.buf = self.buf.split(b"\n\n", 1)
            out.append(self._event(raw + b"\n\n"))
        return b"".join(out)

    def close(self) -> bytes:
        rest, self.buf = self.buf, b""
        return self._event(rest) if rest.strip() else rest

    def _event(self, raw: bytes) -> bytes:
        lines = raw.split(b"\n")
        data = [l[5:].lstrip() for l in lines if l.startswith(b"data:")]
        if not data:
            return raw
        try:
            d = json.loads(b"\n".join(data))
        except ValueError:
            return raw
        t = d.get("type")
        if t == "content_block_start":
            return self._start(d, raw)
        if t == "content_block_delta":
            return self._delta(d, raw)
        if t == "content_block_stop":
            return self._stop(d, raw)
        return raw

    def _text_delta(self, i: int, text: str) -> bytes:
        return sse_event("content_block_delta", {"type": "content_block_delta", "index": i,
                                                 "delta": {"type": "text_delta", "text": text}})

    def _start(self, d, raw):
        i, cb = d["index"], d.get("content_block", {})
        if cb.get("type") == "text":
            blk = {"kind": "text", "r": StreamRestorer(self.sess), "orig": "", "rest": ""}
            self.blocks[i] = blk
            pre = raw
            if cb.get("text"):
                blk["orig"] = cb["text"]
                blk["rest"] = restore_text(self.sess, cb["text"])
                pre = sse_event("content_block_start", {**d, "content_block": {**cb, "text": blk["rest"]}})
            if self.notice:
                text, self.notice = _take(self.notice), None
                if text:
                    blk["rest"] += text
                    pre += self._text_delta(i, text)
            return pre
        if cb.get("type") == "tool_use":
            self.blocks[i] = {"kind": "tool", "id": cb.get("id"), "name": cb.get("name", ""), "json": ""}
        return raw

    def _delta(self, d, raw):
        blk, dt = self.blocks.get(d.get("index")), d.get("delta", {})
        if blk and blk["kind"] == "text" and dt.get("type") == "text_delta":
            blk["orig"] += dt.get("text", "")
            out = blk["r"].feed(dt.get("text", ""))
            blk["rest"] += out
            return sse_event("content_block_delta", {**d, "delta": {**dt, "text": out}}) if out else b""
        if blk and blk["kind"] == "tool" and dt.get("type") == "input_json_delta":
            blk["json"] += dt.get("partial_json", "")
            return b""
        return raw

    def _stop(self, d, raw):
        i = d.get("index")
        blk = self.blocks.pop(i, None)
        if not blk:
            return raw
        if blk["kind"] == "text":
            tail = blk["r"].flush()
            blk["rest"] += tail
            self.sess.record_text(blk["rest"], blk["orig"])
            return (self._text_delta(i, tail) if tail else b"") + raw
        try:
            inp = json.loads(blk["json"]) if blk["json"].strip() else {}
        except ValueError:
            pre = sse_event("content_block_delta", {"type": "content_block_delta", "index": i,
                                                     "delta": {"type": "input_json_delta",
                                                               "partial_json": blk["json"]}})
            return pre + raw
        new = inp if is_network_tool(blk["name"]) else restore_json(self.sess, inp)
        self.sess.record_tool(blk["id"], inp)
        pre = sse_event("content_block_delta", {"type": "content_block_delta", "index": i,
                                                "delta": {"type": "input_json_delta",
                                                          "partial_json": json.dumps(new, ensure_ascii=False)}})
        return pre + raw


def restore_message(sess, msg: dict, notice) -> dict:
    out = []
    for b in msg.get("content") or []:
        if b.get("type") == "text":
            rest = restore_text(sess, b.get("text", ""))
            if notice:
                rest, notice = (_take(notice) or "") + rest, None
            sess.record_text(rest, b.get("text", ""))
            out.append({**b, "text": rest})
        elif b.get("type") == "tool_use":
            sess.record_tool(b.get("id"), b.get("input", {}))
            new = b.get("input", {}) if is_network_tool(b.get("name", "")) else restore_json(sess, b.get("input", {}))
            out.append({**b, "input": new})
        else:
            out.append(b)
    return {**msg, "content": out}
