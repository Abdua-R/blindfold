from blindfold import session as S
from blindfold.config import Config
from blindfold.scrub import (PH_RE, StreamRestorer, restore_json, restore_text, scrub_text,
                             withhold_tables)

CFG = Config(use_ner=False)


def sess(tmp_path, depth="normal"):
    return S.activate(f"t{depth}", depth, tmp_path)


def test_scrub_and_restore_roundtrip(tmp_path):
    s = sess(tmp_path)
    text = "Mijoz: Tursunov Bobur, PINFL 31505883017120. Tursunovga xat yozing."
    out = scrub_text(s, text, CFG)
    assert "Tursunov" not in out and "31505883017120" not in out
    assert "[Person_1]" in out and "[PINFL_1]" in out and "[Person_1]ga" in out
    assert restore_text(s, out) == "Mijoz: Tursunov Bobur, PINFL 31505883017120. Tursunov Boburga xat yozing."


def test_propagation_catches_later_bare_mentions_in_other_script(tmp_path):
    s = sess(tmp_path)
    scrub_text(s, "ФИО: Каримов Азиз", CFG)
    out = scrub_text(s, "Karimovning hisobi", CFG)
    assert "Karimov" not in out and "[Person_1]ning" in out


def test_existing_placeholders_untouched(tmp_path):
    s = sess(tmp_path)
    assert scrub_text(s, "see [Person_7] and [1]", CFG) == "see [Person_7] and [1]"


def test_strict_masks_every_number_and_date(tmp_path):
    s = sess(tmp_path, "strict")
    out = scrub_text(s, "2025-03-31 holatiga NPL 4.2%, 1 250 000 so'm, line 17", CFG)
    assert not any(c.isdigit() for c in PH_RE.sub("", out))
    assert "[Date_1]" in out and "[Num_1]" in out
    assert restore_text(s, out) == "2025-03-31 holatiga NPL 4.2%, 1 250 000 so'm, line 17"


def test_withhold_tables_only_above_fifty_identifying_rows():
    # Decision 10 revised (user, 2026-10-12, option B): small client tables go out with placeholders;
    # only tables with more than 50 identifying rows are withheld whole.
    client_rows = "   name        sum\n0  [Person_1]  120\n1  [Person_2]  90\n2  [Person_3]  70"
    assert withhold_tables(client_rows) == client_rows
    fifty = "   name        sum\n" + "\n".join(f"{i}  [Person_{i + 1}]  {i}00" for i in range(50))
    assert withhold_tables(fifty) == fifty
    big = "   name        sum\n" + "\n".join(f"{i}  [Person_{i + 1}]  {i}00" for i in range(51))
    assert withhold_tables(big) == "[blindfold: 51 rows x 3 columns withheld, row-level client data]"
    summary = "region     total\nToshkent   1200\nSamarqand  900\nBuxoro     300"
    assert withhold_tables(summary) == summary
    prose = "Dear [Person_1], thank you.\nWe met [Person_2], yes.\nRegards, team."
    assert withhold_tables(prose) == prose


def test_restore_json_nested(tmp_path):
    s = sess(tmp_path)
    scrub_text(s, "Mijoz: Tursunov Bobur", CFG)
    assert restore_json(s, {"a": ["[Person_1]", 3], "b": {"c": "x [Person_1]"}}) == {
        "a": ["Tursunov Bobur", 3], "b": {"c": "x Tursunov Bobur"}}


def test_stream_restorer_every_split(tmp_path):
    s = sess(tmp_path)
    scrub_text(s, "Mijoz: Tursunov Bobur, PINFL 31505883017120", CFG)
    text = "Hi [Person_1] [1] [PINFL_1] end ["
    want = restore_text(s, text)
    for i in range(len(text) + 1):
        for j in range(i, len(text) + 1):
            r = StreamRestorer(s)
            got = r.feed(text[:i]) + r.feed(text[i:j]) + r.feed(text[j:]) + r.flush()
            assert got == want, (i, j)


def test_c2_known_address_masked_again_without_detector(tmp_path):
    s = sess(tmp_path)
    scrub_text(s, "Manzil: Chilonzor tumani, 5-kvartal, 14-uy", CFG)
    out = scrub_text(s, "Ertaga hujjatlar Chilonzor tumani, 5-kvartal, 14-uy manziliga yuboriladi.", CFG)
    assert "Chilonzor" not in out and "[Address_1]" in out


def test_c2_short_first_name_masked_again_after_reload(tmp_path):
    s = sess(tmp_path)
    assert scrub_text(s, "Mijoz: Ali", CFG) == "Mijoz: [Person_1]"
    s.save_locked()
    S._cache.clear()
    s2 = S.get("tnormal", tmp_path)
    out = scrub_text(s2, "Ali keldi, Aliga pul berildi.", CFG)
    assert out == "[Person_1] keldi, [Person_1]ga pul berildi."


def test_c2_map_without_forms_key_still_masks_value(tmp_path):
    import json
    s = sess(tmp_path)
    s.save_locked()
    (s.dir / "map.json").write_text(json.dumps([{"type": "Address", "n": 1, "key": ["chilonzor"],
                                                  "value": "Chilonzor tumani, 5-kvartal"}]), encoding="utf-8")
    S._cache.clear()
    s2 = S.get("tnormal", tmp_path)
    assert scrub_text(s2, "bu Chilonzor tumani, 5-kvartal edi", CFG) == "bu [Address_1] edi"


def test_fix19_lowercase_common_word_not_masked_as_known_person(tmp_path):
    s = sess(tmp_path)
    scrub_text(s, "Mijoz: Umid Karimov", CFG)
    scrub_text(s, "Umid keldi.", CFG)
    assert scrub_text(s, "Biz katta umid bilan kutamiz.", CFG) == "Biz katta umid bilan kutamiz."
    assert scrub_text(s, "Umid va UMID keldi.", CFG) == "[Person_1] va [Person_1] keldi."
