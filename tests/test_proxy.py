import asyncio
import json

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from blindfold.config import Config
from blindfold.proxy import make_app
from blindfold.restore import sse_event

NAME = "Tursunov Bobur Alisherovich"
CMD = "<command-message>blindfold</command-message>\n<command-name>/blindfold</command-name>"


def fake_upstream(seen: list, reply: str):
    async def handler(request: Request):
        raw = await request.body()
        seen.append((dict(request.headers), raw, request.url.path))
        if request.url.path.endswith("count_tokens"):
            return JSONResponse({"input_tokens": 10})
        evs = [sse_event("message_start", {"type": "message_start", "message": {"id": "m"}}),
               sse_event("content_block_start", {"type": "content_block_start", "index": 0,
                                                 "content_block": {"type": "text", "text": ""}})]
        for part in (reply[:5], reply[5:13], reply[13:]):
            evs.append(sse_event("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                         "delta": {"type": "text_delta", "text": part}}))
        evs += [sse_event("content_block_stop", {"type": "content_block_stop", "index": 0}),
                sse_event("message_stop", {"type": "message_stop"})]
        return StreamingResponse(iter(evs), media_type="text/event-stream")
    return Starlette(routes=[Route("/v1/messages", handler, methods=["POST"]),
                             Route("/v1/messages/count_tokens", handler, methods=["POST"])])


def call(app, path, payload, sid="sessA", raw=None):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            headers = {"authorization": "Bearer oauth-token", "anthropic-beta": "oauth-2025-04-20",
                       "anthropic-version": "2023-06-01", "x-claude-code-session-id": sid,
                       "content-type": "application/json"}
            return await c.post(path, content=raw if raw is not None else json.dumps(payload), headers=headers)
    return asyncio.run(go())


def text_of(resp):
    out = ""
    for chunk in resp.text.split("\n\n"):
        for line in chunk.split("\n"):
            if line.startswith("data:"):
                d = json.loads(line[5:])
                if d.get("type") == "content_block_delta":
                    out += d["delta"].get("text", "")
    return out


def app_with(tmp_path, seen, reply="Salom [Person_1]!"):
    up = httpx.ASGITransport(app=fake_upstream(seen, reply))
    return make_app(Config(use_ner=False, upstream="http://upstream"), transport=up, root=tmp_path)


def test_unprotected_session_passes_bytes_unchanged(tmp_path):
    seen = []
    raw = json.dumps({"stream": True, "messages": [{"role": "user", "content": f"{NAME}"}]}).encode()
    r = call(app_with(tmp_path, seen, "Salom!"), "/v1/messages", None, raw=raw)
    assert r.status_code == 200 and seen[0][1] == raw
    assert seen[0][0]["authorization"] == "Bearer oauth-token"
    assert seen[0][0]["anthropic-beta"] == "oauth-2025-04-20"


def test_protected_session_scrubs_out_and_restores_back(tmp_path):
    seen = []
    body = {"stream": True, "messages": [{"role": "user", "content": f"{CMD}\n<command-args>{NAME}</command-args>"}]}
    r = call(app_with(tmp_path, seen), "/v1/messages", body)
    sent = seen[0][1].decode()
    assert "Tursunov" not in sent and "[Person_1]" in sent
    assert text_of(r) == "Salom Tursunov Bobur Alisherovich!"
    assert seen[0][0]["authorization"] == "Bearer oauth-token"


def test_second_turn_history_byte_identical(tmp_path):
    seen = []
    app = app_with(tmp_path, seen)
    m0 = {"role": "user", "content": f"{CMD}\n<command-args>{NAME}</command-args>"}
    r = call(app, "/v1/messages", {"stream": True, "messages": [m0]})
    restored = text_of(r)
    m1 = {"role": "assistant", "content": [{"type": "text", "text": restored}]}
    call(app, "/v1/messages", {"stream": True, "messages": [m0, m1, {"role": "user", "content": "ok"}]})
    first, second = json.loads(seen[0][1]), json.loads(seen[1][1])
    assert second["messages"][0] == first["messages"][0]
    assert second["messages"][1]["content"][0]["text"] == "Salom [Person_1]!"


def test_count_tokens_scrubbed_and_unknown_route_404(tmp_path):
    seen = []
    app = app_with(tmp_path, seen)
    call(app, "/v1/messages", {"stream": True, "messages": [{"role": "user", "content": CMD}]})
    r = call(app, "/v1/messages/count_tokens", {"messages": [{"role": "user", "content": f"{CMD} {NAME}"}]})
    assert r.json() == {"input_tokens": 10} and "Tursunov" not in seen[-1][1].decode()
    r = call(app, "/v1/files", {})
    assert r.status_code == 404 and r.json()["error"]["type"] == "not_found_error"


def test_refusal_is_400_no_retry_and_nothing_sent(tmp_path):
    seen = []
    body = {"stream": True, "messages": [{"role": "user", "content": CMD},
                                         {"role": "user", "content": [{"type": "document",
                                                                       "source": {"type": "url", "url": "https://x"}}]}]}
    r = call(app_with(tmp_path, seen), "/v1/messages", body)
    assert r.status_code == 400 and r.headers["x-should-retry"] == "false"
    assert r.json()["type"] == "error" and seen == []


def test_capture_mode_writes_outbound_bodies(tmp_path, monkeypatch):
    cap = tmp_path / "cap"
    monkeypatch.setenv("BLINDFOLD_CAPTURE_DIR", str(cap))
    seen = []
    call(app_with(tmp_path, seen), "/v1/messages", {"stream": True, "messages": [
        {"role": "user", "content": f"{CMD}\n<command-args>{NAME}</command-args>"}]})
    lines = (cap / "outbound.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and "Tursunov" not in lines[0]


def call_no_sid(app, path, payload):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            headers = {"authorization": "Bearer oauth-token", "anthropic-beta": "oauth-2025-04-20",
                       "anthropic-version": "2023-06-01", "content-type": "application/json"}
            return await c.post(path, content=json.dumps(payload), headers=headers)
    return asyncio.run(go())


def test_activated_session_list_body_refused_and_upstream_not_called(tmp_path):
    seen = []
    app = app_with(tmp_path, seen)
    call(app, "/v1/messages", {"stream": True, "messages": [{"role": "user", "content": CMD}]})
    seen.clear()
    raw = json.dumps([NAME]).encode()
    r = call(app, "/v1/messages", None, raw=raw)
    assert r.status_code == 400 and seen == []
    assert r.json()["error"]["type"] == "invalid_request_error"


def test_activated_session_malformed_messages_refused(tmp_path):
    seen = []
    app = app_with(tmp_path, seen)
    call(app, "/v1/messages", {"stream": True, "messages": [{"role": "user", "content": CMD}]})
    seen.clear()
    r = call(app, "/v1/messages", {"messages": ["x"]})
    assert r.status_code == 400 and seen == []


def test_upstream_unreachable_returns_400_no_retry(tmp_path):
    def handler(request):
        raise httpx.ConnectError("refused")
    app = make_app(Config(use_ner=False, upstream="http://upstream"),
                  transport=httpx.MockTransport(handler), root=tmp_path)
    r = call(app, "/v1/messages", {"stream": True, "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 400 and r.headers["x-should-retry"] == "false"
    assert r.json()["error"]["type"] == "api_error"


def test_marker_without_session_id_refused_and_upstream_not_called(tmp_path):
    seen = []
    app = app_with(tmp_path, seen)
    r = call_no_sid(app, "/v1/messages", {"messages": [{"role": "user", "content": CMD}]})
    assert r.status_code == 400 and seen == []
    assert "identified" in r.json()["error"]["message"]


def test_non_streaming_json_response_restored(tmp_path):
    seen = []

    async def handler(request: Request):
        raw = await request.body()
        seen.append((dict(request.headers), raw, request.url.path))
        return JSONResponse({"id": "m", "content": [{"type": "text", "text": "Salom [Person_1]!"}]})

    up = httpx.ASGITransport(app=Starlette(routes=[Route("/v1/messages", handler, methods=["POST"])]))
    app = make_app(Config(use_ner=False, upstream="http://upstream"), transport=up, root=tmp_path)
    body = {"messages": [{"role": "user", "content": f"{CMD}\n<command-args>{NAME}</command-args>"}]}
    r = call(app, "/v1/messages", body)
    assert r.status_code == 200
    assert r.json()["content"][0]["text"] == f"Salom {NAME}!"
    assert "Tursunov" not in seen[0][1].decode()


def test_generic_scrub_exception_refused(tmp_path, monkeypatch):
    import blindfold.proxy as proxy_mod
    monkeypatch.setattr(proxy_mod, "scrub_request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    seen = []
    app = app_with(tmp_path, seen)
    r = call(app, "/v1/messages", {"stream": True, "messages": [{"role": "user", "content": CMD}]})
    assert r.status_code == 400 and seen == []
    assert r.json()["error"]["message"] == proxy_mod.FAILED


def test_session_state_persisted_after_stream(tmp_path):
    seen = []
    app = app_with(tmp_path, seen)
    m0 = {"role": "user", "content": f"{CMD}\n<command-args>{NAME}</command-args>"}
    call(app, "/v1/messages", {"stream": True, "messages": [m0]})
    from blindfold.session import Session
    fresh = Session("sessA", tmp_path)
    assert "Salom [Person_1]!" in fresh.inv_text.values()


PINFL = "31505883017120"


def test_c3_context_before_activation_in_first_request_scrubbed(tmp_path):
    seen = []
    ctx = {"role": "user", "content": f"Mijoz: {NAME}, PINFL {PINFL}"}
    call(app_with(tmp_path, seen), "/v1/messages",
         {"stream": True, "messages": [ctx, {"role": "user", "content": CMD}]})
    sent = seen[0][1].decode()
    assert "Tursunov" not in sent and PINFL not in sent


def test_c3_mid_session_activation_keeps_raw_relayed_turns_identical(tmp_path):
    seen = []
    app = app_with(tmp_path, seen, "ok")
    m0 = {"role": "user", "content": f"{NAME} haqida yozing"}
    call(app, "/v1/messages", {"stream": True, "messages": [m0]}, sid="sessM")
    m1 = {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}
    m2 = {"role": "user", "content": f"{CMD}\nMijoz: {NAME}"}
    call(app, "/v1/messages", {"stream": True, "messages": [m0, m1, m2]}, sid="sessM")
    m3 = {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}
    call(app, "/v1/messages", {"stream": True, "messages": [m0, m1, m2, m3, {"role": "user", "content": "x"}]},
         sid="sessM")
    for k in (1, 2):
        out = json.loads(seen[k][1])
        assert out["messages"][0] == m0
        assert "Tursunov" not in json.dumps(out["messages"][2], ensure_ascii=False)


def test_c3_prefix_never_relayed_raw_is_scrubbed_after_restart(tmp_path):
    seen = []
    m0 = {"role": "user", "content": f"{NAME} haqida yozing"}
    call(app_with(tmp_path, seen, "ok"), "/v1/messages", {"stream": True, "messages": [m0]}, sid="sessR")
    m1 = {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}
    m2 = {"role": "user", "content": CMD}
    call(app_with(tmp_path, seen, "ok"), "/v1/messages", {"stream": True, "messages": [m0, m1, m2]}, sid="sessR")
    assert "Tursunov" not in seen[1][1].decode()


def _gliner_stub(monkeypatch):
    import sys
    import types
    from blindfold import ner
    loads = []

    class GLiNER2:
        @staticmethod
        def from_pretrained(name):
            loads.append(name)
            raise OSError("offline")
    mod = types.ModuleType("gliner2")
    mod.GLiNER2 = GLiNER2
    monkeypatch.setitem(sys.modules, "gliner2", mod)
    monkeypatch.setattr(ner, "_model", None)
    monkeypatch.setattr(ner, "_failed", False)
    return loads


def test_c4_ner_load_failure_degrades_with_notice(tmp_path, monkeypatch):
    loads = _gliner_stub(monkeypatch)
    seen = []
    up = httpx.ASGITransport(app=fake_upstream(seen, "Salom [Person_1]!"))
    app = make_app(Config(use_ner=True, upstream="http://upstream"), transport=up, root=tmp_path)
    body = {"stream": True, "messages": [{"role": "user", "content": f"{CMD}\n<command-args>{NAME}</command-args>"}]}
    r = call(app, "/v1/messages", body)
    assert r.status_code == 200 and len(seen) == 1
    assert "Tursunov" not in seen[0][1].decode()
    assert "without the name model" in text_of(r)
    r2 = call(app, "/v1/messages", {**body, "messages": body["messages"] + [{"role": "user", "content": "yana"}]})
    assert r2.status_code == 200 and len(loads) == 1


def test_i2_notice_not_consumed_by_count_tokens(tmp_path, monkeypatch):
    from blindfold import detect as D
    monkeypatch.setattr(D, "_llm", lambda text, cfg: None)
    seen = []
    up = httpx.ASGITransport(app=fake_upstream(seen, "Salom [Person_1]!"))
    app = make_app(Config(use_ner=False, upstream="http://upstream", llm_base_url="http://llm.i2/v1"),
                   transport=up, root=tmp_path)
    msgs = [{"role": "user", "content": f"{CMD}\n<command-args>{NAME}</command-args>"}]
    call(app, "/v1/messages/count_tokens", {"messages": msgs})
    r = call(app, "/v1/messages", {"stream": True, "messages": msgs})
    assert "without your private model" in text_of(r)
    r = call(app, "/v1/messages", {"stream": True, "messages": msgs + [{"role": "user", "content": "yana"}]})
    assert "without your private model" not in text_of(r)
