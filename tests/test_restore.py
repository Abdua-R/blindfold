import json

from blindfold import session as S
from blindfold.config import Config
from blindfold.restore import SSERestorer, restore_message, sse_event
from blindfold.scrub import scrub_text

CFG = Config(use_ner=False)


def setup(tmp_path):
    s = S.activate("r1", "normal", tmp_path)
    scrub_text(s, "Mijoz: Tursunov Bobur, PINFL 31505883017120", CFG)
    return s


def events(raw: bytes):
    out = []
    for chunk in raw.decode().split("\n\n"):
        data = [l[5:].strip() for l in chunk.split("\n") if l.startswith("data:")]
        if data:
            out.append(json.loads(data[0]))
    return out


def stream(*evs):
    return b"".join(sse_event(e["type"], e) for e in evs)


def start(i, block):
    return {"type": "content_block_start", "index": i, "content_block": block}


def delta(i, d):
    return {"type": "content_block_delta", "index": i, "delta": d}


def stop(i):
    return {"type": "content_block_stop", "index": i}


def run(r, raw, cut):
    return b"".join(r.feed(raw[k:k + cut]) for k in range(0, len(raw), cut)) + r.close()


def test_text_restored_across_any_byte_split(tmp_path):
    s = setup(tmp_path)
    raw = stream({"type": "message_start", "message": {"id": "m"}}, start(0, {"type": "text", "text": ""}),
                 delta(0, {"type": "text_delta", "text": "Salom [Pers"}),
                 delta(0, {"type": "text_delta", "text": "on_1], PINFL [PINFL_1]."}),
                 stop(0), {"type": "message_stop"})
    for cut in (1, 7, 64, len(raw)):
        r = SSERestorer(s)
        text = "".join(e["delta"]["text"] for e in events(run(r, raw, cut)) if e["type"] == "content_block_delta")
        assert text == "Salom Tursunov Bobur, PINFL 31505883017120.", cut
        s.merge_pending()
        assert s.inv_text[S.block_hash(text)] == "Salom [Person_1], PINFL [PINFL_1]."


def test_tool_input_restored_and_recorded(tmp_path):
    s = setup(tmp_path)
    raw = stream(start(1, {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}),
                 delta(1, {"type": "input_json_delta", "partial_json": '{"command": "grep \'[Per'}),
                 delta(1, {"type": "input_json_delta", "partial_json": 'son_1]\' a.txt"}'}),
                 stop(1))
    out = events(SSERestorer(s).feed(raw))
    d = [e for e in out if e["type"] == "content_block_delta"]
    assert len(d) == 1
    assert json.loads(d[0]["delta"]["partial_json"]) == {"command": "grep 'Tursunov Bobur' a.txt"}
    s.merge_pending()
    assert s.inv_tool["toolu_1"] == {"command": "grep '[Person_1]' a.txt"}


def test_network_tool_keeps_placeholders(tmp_path):
    s = setup(tmp_path)
    raw = stream(start(0, {"type": "tool_use", "id": "toolu_2", "name": "WebSearch", "input": {}}),
                 delta(0, {"type": "input_json_delta", "partial_json": '{"query": "[Person_1] loan"}'}), stop(0))
    d = [e for e in events(SSERestorer(s).feed(raw)) if e["type"] == "content_block_delta"]
    assert json.loads(d[0]["delta"]["partial_json"]) == {"query": "[Person_1] loan"}


def test_thinking_relayed_verbatim(tmp_path):
    s = setup(tmp_path)
    raw = stream(start(0, {"type": "thinking", "thinking": ""}),
                 delta(0, {"type": "thinking_delta", "thinking": "about [Person_1]"}),
                 delta(0, {"type": "signature_delta", "signature": "abc"}), stop(0))
    assert SSERestorer(s).feed(raw) == raw


def test_notice_prefixed_once_and_inversion_excludes_it(tmp_path):
    s = setup(tmp_path)
    raw = stream(start(0, {"type": "text", "text": ""}), delta(0, {"type": "text_delta", "text": "Hi [Person_1]"}), stop(0))
    r = SSERestorer(s, notice="NOTE\n\n")
    text = "".join(e["delta"]["text"] for e in events(r.feed(raw)) if e["type"] == "content_block_delta")
    assert text == "NOTE\n\nHi Tursunov Bobur"
    s.merge_pending()
    assert s.inv_text[S.block_hash(text)] == "Hi [Person_1]"


def test_restore_plain_message(tmp_path):
    s = setup(tmp_path)
    msg = {"content": [{"type": "text", "text": "Hi [Person_1]"},
                       {"type": "tool_use", "id": "toolu_3", "name": "Read", "input": {"file_path": "[Person_1].txt"}}]}
    out = restore_message(s, msg, None)
    assert out["content"][0]["text"] == "Hi Tursunov Bobur"
    assert out["content"][1]["input"] == {"file_path": "Tursunov Bobur.txt"}
    s.merge_pending()
    assert s.inv_text[S.block_hash("Hi Tursunov Bobur")] == "Hi [Person_1]"
    assert s.inv_tool["toolu_3"] == {"file_path": "[Person_1].txt"}


def test_restore_plain_message_network_tool_keeps_placeholders(tmp_path):
    s = setup(tmp_path)
    msg = {"content": [{"type": "tool_use", "id": "toolu_5", "name": "WebSearch",
                        "input": {"query": "[Person_1] loan"}}]}
    out = restore_message(s, msg, None)
    assert out["content"][0]["input"] == {"query": "[Person_1] loan"}


def test_truncated_tool_json_emitted_unchanged(tmp_path):
    s = setup(tmp_path)
    raw = stream(start(0, {"type": "tool_use", "id": "toolu_4", "name": "Write", "input": {}}),
                 delta(0, {"type": "input_json_delta", "partial_json": '{"file_path": "a.txt", "content": "abc'}),
                 stop(0))
    out = events(SSERestorer(s).feed(raw))
    d = [e for e in out if e["type"] == "content_block_delta"]
    assert len(d) == 1
    assert d[0]["delta"]["partial_json"] == '{"file_path": "a.txt", "content": "abc'
    s.merge_pending()
    assert "toolu_4" not in s.inv_tool
