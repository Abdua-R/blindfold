import base64
import copy
import json

import pytest

from blindfold import session as S
from blindfold.config import Config
from blindfold.protocol import IMG_NOTE, Refuse, WITHHELD_NOTE, find_activation, scrub_request
from blindfold.scrub import restore_text

CFG = Config(use_ner=False)
NAME = "Tursunov Bobur Alisherovich"


def tiny_pdf(text: str) -> bytes:
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, x)
    return out


def cmd(args=""):
    a = f"\n<command-args>{args}</command-args>" if args else ""
    return f"<command-message>blindfold</command-message>\n<command-name>/blindfold</command-name>{a}"


def body(*messages, system=None):
    b = {"model": "claude", "messages": list(messages)}
    if system is not None:
        b["system"] = system
    return b


def test_find_activation_variants():
    assert find_activation([{"role": "user", "content": cmd()}]) == ("normal", 0)
    assert find_activation([{"role": "user", "content": "hi"},
                            {"role": "user", "content": [{"type": "text", "text": cmd("strict")}]}]) == ("strict", 1)
    assert find_activation([{"role": "user", "content": "blindfold-activation depth=strict"}]) == ("strict", 0)
    assert find_activation([{"role": "user", "content": "/blindfold strict please"}]) == ("strict", 0)
    assert find_activation([{"role": "user", "content": "nothing here"}]) is None


def test_command_args_are_scrubbed(tmp_path):
    s = S.activate("p1", "normal", tmp_path)
    b, _ = scrub_request(body({"role": "user", "content": cmd(f"{NAME} PINFL 31505883017120")}), s, CFG)
    txt = json.dumps(b, ensure_ascii=False)
    assert "Tursunov" not in txt and "31505883017120" not in txt


def test_system_scrubbed_with_cache_control_kept(tmp_path):
    s = S.activate("p2", "normal", tmp_path)
    sysb = [{"type": "text", "text": "x-anthropic-billing-header: cc_version=2"},
            {"type": "text", "text": r"memory at C:\Users\rtursunov\.claude\projects\x",
             "cache_control": {"type": "ephemeral"}}]
    b, _ = scrub_request(body({"role": "user", "content": cmd()}, system=sysb), s, CFG)
    assert b["system"][0] == sysb[0]
    assert "rtursunov" not in b["system"][1]["text"]
    assert b["system"][1]["cache_control"] == {"type": "ephemeral"}


def test_tool_result_table_image_pdf(tmp_path):
    s = S.activate("p3", "normal", tmp_path)
    table = "   F.I.Sh.        Summa\n0  Karimov Aziz    1200\n1  Rashidova Dilnoza  900\n2  Yusupov Akmal   300"
    pdf = base64.b64encode(tiny_pdf(f"Mijoz: {NAME}")).decode()
    msg = {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": table},
        {"type": "tool_result", "tool_use_id": "toolu_2", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf}}]}]}
    b, _ = scrub_request(body({"role": "user", "content": cmd()}, msg), s, CFG)
    c = b["messages"][1]["content"]
    assert "Karimov" not in c[0]["content"] and "[Person_" in c[0]["content"]   # small table: placeholders
    assert c[1]["content"][0] == {"type": "text", "text": IMG_NOTE}
    doc = c[1]["content"][1]
    assert doc["source"]["type"] == "text" and "Tursunov" not in doc["source"]["data"]
    assert "[Person_" in doc["source"]["data"]


def test_url_document_refused(tmp_path):
    s = S.activate("p4", "normal", tmp_path)
    msg = {"role": "user", "content": [{"type": "document", "source": {"type": "url", "url": "https://x"}}]}
    with pytest.raises(Refuse):
        scrub_request(body({"role": "user", "content": cmd()}, msg), s, CFG)


def test_history_identical_after_reload(tmp_path):
    s = S.activate("p5", "normal", tmp_path)
    m0 = {"role": "user", "content": cmd(f"{NAME} haqida yozing")}
    b1, _ = scrub_request(body(m0), s, CFG)
    s.save_locked()
    S._cache.clear()
    s2 = S.get("p5", tmp_path)
    reply_restored = f"{NAME} haqida xat tayyor."
    s2.record_text(reply_restored, "[Person_1] haqida xat tayyor.")
    m1 = {"role": "assistant", "content": [{"type": "text", "text": reply_restored}]}
    m2 = {"role": "user", "content": "rahmat"}
    b2, _ = scrub_request(body(m0, m1, m2), s2, CFG)
    assert b2["messages"][0] == b1["messages"][0]
    assert b2["messages"][1]["content"][0]["text"] == "[Person_1] haqida xat tayyor."


def test_tool_use_history_inverted_by_id(tmp_path):
    s = S.activate("p6", "normal", tmp_path)
    s.record_tool("toolu_9", {"command": "grep '[Person_1]' a.txt"})
    s.merge_pending()
    m1 = {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_9", "name": "Bash",
                                            "input": {"command": f"grep '{NAME}' a.txt"}}]}
    b, _ = scrub_request(body({"role": "user", "content": cmd()}, m1), s, CFG)
    assert b["messages"][1]["content"][0]["input"] == {"command": "grep '[Person_1]' a.txt"}


def test_messages_before_mid_session_activation_untouched(tmp_path):
    s = S.activate("p7", "normal", tmp_path)
    early = {"role": "user", "content": f"{NAME} (sent before blindfold)"}
    reply = {"role": "assistant", "content": "ok"}
    raw_ok = {S.block_hash(early), S.block_hash(reply)}
    b, _ = scrub_request(body(early, reply, {"role": "user", "content": cmd(NAME)}), s, CFG, raw_ok)
    assert b["messages"][0] == early
    assert "Tursunov" not in json.dumps(b["messages"][2], ensure_ascii=False)


def test_subagent_scrubbed_from_zero(tmp_path):
    s = S.activate("p8", "normal", tmp_path)
    s.start_index = 2
    b, _ = scrub_request(body({"role": "user", "content": f"Find files about {NAME}"}), s, CFG)
    assert "Tursunov" not in json.dumps(b, ensure_ascii=False)


def test_thinking_and_unknown_assistant_blocks_verbatim(tmp_path):
    s = S.activate("p9", "normal", tmp_path)
    th = {"type": "thinking", "thinking": NAME, "signature": "sig"}
    ws = {"type": "web_search_tool_result", "tool_use_id": "x", "content": [{"encrypted_content": "e"}]}
    b, _ = scrub_request(body({"role": "user", "content": cmd()}, {"role": "assistant", "content": [th, ws]}), s, CFG)
    assert b["messages"][1]["content"] == [th, ws]


def test_restore_after_scrub_gives_back_values(tmp_path):
    s = S.activate("p10", "normal", tmp_path)
    b, _ = scrub_request(body({"role": "user", "content": cmd(f"{NAME}")}), s, CFG)
    assert NAME in restore_text(s, json.dumps(b, ensure_ascii=False))


def test_tool_use_fallback_name_url_input_scrubbed(tmp_path):
    """Fix round 1: leaves() used to skip "name"/"url" keys at every depth (SKIP_KEYS applied
    unconditionally), so a fallback tool_use whose own input schema happens to use those key
    names leaked the value verbatim. A fresh tool_use id (not in sess.inv_tool) takes the
    fallback branch, which must now walk its input with no skip set at all."""
    s = S.activate("p11", "normal", tmp_path)
    m1 = {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_new", "name": "WebSearch",
                                            "input": {"name": NAME, "url": f"https://x/?q={NAME}"}}]}
    b, _ = scrub_request(body({"role": "user", "content": cmd()}, m1), s, CFG)
    txt = json.dumps(b["messages"][1]["content"][0]["input"], ensure_ascii=False)
    assert "Tursunov" not in txt


def test_unknown_block_nested_name_scrubbed(tmp_path):
    """An unknown-type content block falls through inner() to leaves(); its own id must stay
    byte for byte (SKIP_KEYS at the block's top level) but a "name" key nested one level down
    (inside the block's own "input" field) must be scrubbed, since nested dicts get no skip."""
    s = S.activate("p12", "normal", tmp_path)
    msg = {"role": "user", "content": [{"type": "mcp_tool_call", "id": "x1", "input": {"name": NAME}}]}
    b, _ = scrub_request(body({"role": "user", "content": cmd()}, msg), s, CFG)
    assert "Tursunov" not in json.dumps(b["messages"][1], ensure_ascii=False)
    assert b["messages"][1]["content"][0]["id"] == "x1"


def test_document_content_source_image_withheld(tmp_path):
    """A document with a structured "content" source (used for citations) can itself hold an
    image block; leaves() used to walk this generically and forward the image's base64 bytes
    verbatim (both "data" and "type" were historically skip-listed so the image block was never
    recognised). Each content item must now go through inner(), which withholds the image."""
    s = S.activate("p13", "normal", tmp_path)
    msg = {"role": "user", "content": [{"type": "document", "source": {"type": "content", "content": [
        {"type": "text", "text": "cover page"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "SECRETBYTES"}}]}}]}
    b, _ = scrub_request(body({"role": "user", "content": cmd()}, msg), s, CFG)
    txt = json.dumps(b["messages"][1], ensure_ascii=False)
    assert "SECRETBYTES" not in txt
    assert IMG_NOTE in txt


def test_nested_document_url_source_refused(tmp_path):
    """A document block does not have to sit at the top of a content list: it can be buried
    inside an unknown block's own fields (e.g. a non-standard tool's input echoing a document
    reference). leaves() must recognise it there too and run it through document(), which
    refuses a url source rather than forwarding an unchecked external URL."""
    s = S.activate("p14", "normal", tmp_path)
    msg = {"role": "user", "content": [{"type": "mcp_tool_call", "id": "x2",
                                        "input": {"doc": {"type": "document",
                                                           "source": {"type": "url", "url": "https://x/doc.pdf"}}}}]}
    with pytest.raises(Refuse):
        scrub_request(body({"role": "user", "content": cmd()}, msg), s, CFG)


def test_strict_depth_unknown_block_withheld(tmp_path):
    """Regression guard: the leaves()/SKIP_KEYS rework must not disturb the existing strict-depth
    behavior of withholding any unrecognised block type outright."""
    s = S.activate("p15", "strict", tmp_path)
    msg = {"role": "user", "content": [{"type": "mcp_tool_call", "id": "x3", "input": {"name": NAME}}]}
    b, _ = scrub_request(body({"role": "user", "content": cmd()}, msg), s, CFG)
    assert b["messages"][1]["content"][0] == {"type": "text", "text": WITHHELD_NOTE}


def test_memo_distinguishes_tables_flag(tmp_path):
    """The same raw text block, scrubbed as assistant text (tables=False, never withheld as a
    table) and then as user text (tables=True, withheld when it looks like a table), used to
    share one memo cache slot keyed only on the bare block; whichever ran first poisoned the
    other. The memo key must include the tables flag so the user block is still withheld even
    though the assistant turn scrubbed the identical text first."""
    s = S.activate("p16", "normal", tmp_path)
    names = ["Karimov Aziz", "Rashidova Dilnoza", "Yusupov Akmal", "Tursunov Bobur", "Sobirova Nodira",
             "Ergashev Jasur", "Qodirov Otabek", "Mirzayeva Lola"]
    table_text = "   F.I.Sh.        Summa\n" + "\n".join(
        f"{i}  {names[i % 8]} {chr(65 + i // 8)}.    {1000 + i}" for i in range(51))
    m_assistant = {"role": "assistant", "content": [{"type": "text", "text": table_text}]}
    m_user = {"role": "user", "content": [{"type": "text", "text": table_text}]}
    b, _ = scrub_request(body({"role": "user", "content": cmd()}, m_assistant, m_user), s, CFG)
    assert not b["messages"][1]["content"][0]["text"].startswith("[blindfold:")
    assert b["messages"][2]["content"][0]["text"].startswith("[blindfold: 51 rows x")


def test_memo_deepcopy_prevents_cache_corruption(tmp_path):
    """Fix round 2: memo() used to return a shallow copy of the cached hit, so mutating a
    nested field of one scrub_request()'s output corrupted the cached entry for every later
    call on the identical block. Scrub the same tool_result twice, tamper with a nested field
    of the first result, and confirm the second (fresh) scrub is unaffected."""
    s = S.activate("p17", "normal", tmp_path)
    msg = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                         "content": [{"type": "text", "text": f"Mijoz: {NAME}"}]}]}
    b1, _ = scrub_request(body({"role": "user", "content": cmd()}, msg), s, CFG)
    block1 = b1["messages"][1]["content"][0]
    expected = copy.deepcopy(block1)
    block1["content"][0]["text"] = "TAMPERED"

    b2, _ = scrub_request(body({"role": "user", "content": cmd()}, msg), s, CFG)
    block2 = b2["messages"][1]["content"][0]
    assert block2 == expected
    assert block2["content"][0]["text"] != "TAMPERED"


def test_document_content_source_bare_string_scrubbed(tmp_path):
    """Fix round 2: document()'s content-source branch forwarded a bare string item
    unscrubbed (only dict items were routed through inner()); a structured document's content
    list can itself hold plain text strings, not just typed blocks."""
    s = S.activate("p18", "normal", tmp_path)
    msg = {"role": "user", "content": [{"type": "document",
                                        "source": {"type": "content", "content": [f"Mijoz: {NAME}"]}}]}
    b, _ = scrub_request(body({"role": "user", "content": cmd()}, msg), s, CFG)
    assert "Tursunov" not in json.dumps(b["messages"][1], ensure_ascii=False)
