"""The loopback relay between Claude Code and Anthropic."""
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from . import session as sessions
from .config import Config, home
from .protocol import _ACT, Refuse, find_activation, scrub_request
from .session import block_hash, valid_sid
from .restore import SSERestorer, restore_message

log = logging.getLogger("blindfold")

DROP_REQ = {"host", "content-length", "accept-encoding", "connection", "transfer-encoding"}
DROP_RESP = {"content-length", "content-encoding", "transfer-encoding", "connection", "content-type"}
NOTICES = {"llm_unavailable": "blindfold: names in this session were checked without your private model, "
                              "which did not answer in time.\n\n",
           "ner_unavailable": "blindfold: names in this session were checked without the name model, "
                              "which could not load.\n\n"}
FAILED = "blindfold: request withheld because the scrubber failed; nothing was sent."
NO_SESSION = ("blindfold: this session could not be identified, so protection could not start; "
             "nothing was sent.")
UPSTREAM_DOWN = "blindfold: could not reach the model service; nothing was changed. Try again."


def _error(status: int, kind: str, message: str) -> JSONResponse:
    return JSONResponse({"type": "error", "error": {"type": kind, "message": message}},
                        status_code=status, headers={"x-should-retry": "false"})


def _capture(body: dict) -> None:
    d = os.environ.get("BLINDFOLD_CAPTURE_DIR")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        with open(Path(d) / "outbound.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(body, ensure_ascii=False) + "\n")


def _notice_text(keys) -> str:
    """Every notice key gets a line; an unknown key is shown by name rather than dropped."""
    return "".join(NOTICES.get(k, f"blindfold: {k}.\n\n") for k in keys)


def _raw_marker(raw: bytes) -> bool:
    """Best-effort activation-marker check on bytes that did not parse as a JSON object,
    so a marker hiding in a malformed or non-dict body still forces the refuse-don't-leak path."""
    try:
        text = raw.decode("utf-8", errors="ignore")
    except Exception:
        return False
    return bool(_ACT.search(text))


def make_app(cfg: Config, transport: httpx.AsyncBaseTransport | None = None, root=None) -> Starlette:
    root = Path(root or home())
    client = httpx.AsyncClient(base_url=cfg.upstream, transport=transport,
                               timeout=httpx.Timeout(600.0, connect=30.0))
    # Session ID -> hashes of messages relayed raw while that session was unprotected. In memory
    # only, so after a restart nothing counts as already sent and everything is scrubbed.
    # ponytail: grows with every unprotected turn until restart; prune per session if memory matters.
    raw_sent: dict[str, set[str]] = {}

    async def relay(path, content, headers, sess, notice):
        resp = await client.send(client.build_request("POST", path, content=content, headers=headers), stream=True)
        rh = {k: v for k, v in resp.headers.items() if k.lower() not in DROP_RESP}
        ctype = resp.headers.get("content-type", "")
        restore = sess is not None and resp.status_code == 200 and path.split("?")[0] == "/v1/messages"
        if "text/event-stream" in ctype:
            r = SSERestorer(sess, notice) if restore else None

            async def gen():
                try:
                    async for chunk in resp.aiter_raw():
                        out = r.feed(chunk) if r else chunk
                        if out:
                            yield out
                    if r:
                        tail = r.close()
                        if tail:
                            yield tail
                finally:
                    await resp.aclose()
                    if r:
                        await asyncio.to_thread(sess.save_locked)

            return StreamingResponse(gen(), status_code=resp.status_code, headers=rh, media_type=ctype)
        data = await resp.aread()
        await resp.aclose()
        if restore and "json" in ctype:
            data = json.dumps(restore_message(sess, json.loads(data), notice), ensure_ascii=False).encode()
            await asyncio.to_thread(sess.save_locked)
        return Response(data, status_code=resp.status_code, headers=rh, media_type=ctype or None)

    async def messages(request: Request):
        raw = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_REQ}
        headers["accept-encoding"] = "identity"
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        sid = request.headers.get("x-claude-code-session-id")

        # Everything that decides whether this body is protected, and everything that touches
        # disk or walks the body to scrub it, stays inside this block: any exception here (a
        # corrupt state file, a malformed messages list, a scrubber bug) must fail closed with
        # a 400, never bubble up into a Starlette 500.
        try:
            cur_sess = sessions.get(sid, root)
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                body = None
            act = find_activation(body.get("messages") or []) if isinstance(body, dict) else None
            marker = act is not None or (not isinstance(body, dict) and _raw_marker(raw))
            protected = cur_sess is not None or marker

            if not protected:
                sess, notice, content = None, None, raw
            elif not isinstance(body, dict):
                return _error(400, "invalid_request_error", FAILED)
            else:
                sess = cur_sess
                if sess is None:
                    sess = sessions.activate(sid, act[0], root)
                    if sess is None:
                        return _error(400, "invalid_request_error", NO_SESSION)

                raw_ok = frozenset(raw_sent.get(sid, ()))

                def scrub():
                    with sess.lock:
                        new_body, notices = scrub_request(body, sess, cfg, raw_ok)
                        fresh = sorted(n for n in notices if n not in sess.notices)
                        sess.pending_notices.update(fresh)
                        sess.save()
                        sess.audit({"path": request.url.path, "entities": len(sess.entities), "notices": fresh})
                        return new_body

                def claim():
                    # Called only when a restored /v1/messages reply actually carries the text.
                    with sess.lock:
                        keys = sorted(sess.pending_notices)
                        sess.notices.update(keys)
                        sess.pending_notices.clear()
                        return _notice_text(keys) or None

                new_body = await asyncio.to_thread(scrub)
                _capture(new_body)
                notice = claim if sess.pending_notices else None
                content = json.dumps(new_body, ensure_ascii=False).encode("utf-8")
        except Refuse as e:
            return _error(400, "invalid_request_error", str(e))
        except Exception as e:
            log.warning("scrub failed: %s", type(e).__name__)   # type only: messages may quote client text
            return _error(400, "invalid_request_error", FAILED)

        # Reaching the model is outside blindfold's control; a connection failure, a timeout,
        # or a restore bug on the way back must still answer 400, never a 5xx.
        try:
            resp = await relay(path, content, headers, sess, notice)
        except Exception as e:
            log.warning("upstream or restore failed: %s", type(e).__name__)
            return _error(400, "api_error", UPSTREAM_DOWN)
        if sess is None and valid_sid(sid) and isinstance(body, dict) and isinstance(body.get("messages"), list):
            raw_sent.setdefault(sid, set()).update(block_hash(m) for m in body["messages"])
        return resp

    async def not_found(request: Request):
        return _error(404, "not_found_error", "blindfold: this endpoint is not available.")

    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield
        await client.aclose()

    return Starlette(routes=[Route("/v1/messages", messages, methods=["POST"]),
                             Route("/v1/messages/count_tokens", messages, methods=["POST"]),
                             Route("/{path:path}", not_found,
                                  methods=["GET", "POST", "PUT", "DELETE", "HEAD", "PATCH", "OPTIONS"])],
                     lifespan=lifespan)
