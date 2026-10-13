import threading

from blindfold import session as S


def test_bad_session_id_is_rejected(tmp_path):
    for sid in ["../x", "a\\b", "", "x" * 200, None]:
        assert S.activate(sid, "normal", tmp_path) is None
        assert S.get(sid, tmp_path) is None
    assert not (tmp_path / "x").exists()


def test_placeholders_stable_and_merged(tmp_path):
    s = S.activate("s1", "normal", tmp_path)
    assert s.placeholder("Person", "Tursunov Bobur Alisherovich") == "[Person_1]"
    assert s.placeholder("Person", "Tursunov") == "[Person_1]"
    assert s.placeholder("Person", "Каримову") == "[Person_2]"
    assert s.placeholder("Person", "Karimovga") == "[Person_2]"
    assert s.placeholder("PINFL", "31505883017120") == "[PINFL_1]"
    assert s.value_of("[Person_2]") == "Каримову"   # restored exactly as first written (decision 11 revised)
    assert s.value_of("[Person_9]") is None


def test_parallel_placeholders_consistent(tmp_path):
    s = S.activate("s2", "normal", tmp_path)
    names = [f"Name{i}ov" for i in range(40)]
    out = {}

    def work(k):
        out[k] = [s.placeholder("Person", n) for n in names]

    ts = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert out[0] == out[1] == out[2] == out[3]
    assert len(set(out[0])) == 40


def test_persistence_roundtrip(tmp_path):
    s = S.activate("s3", "strict", tmp_path)
    s.placeholder("Person", "Tursunov Bobur")
    s.memo["h"] = {"type": "text", "text": "[Person_1]"}
    s.record_text("Hello Tursunov Bobur", "Hello [Person_1]")
    s.record_tool("toolu_1", {"command": "echo [Person_1]"})
    s.start_index = 3
    s.save_locked()
    S._cache.clear()
    t = S.get("s3", tmp_path)
    assert t.depth == "strict" and t.start_index == 3
    assert t.placeholder("Person", "Tursunov Bobur") == "[Person_1]"
    assert t.memo["h"]["text"] == "[Person_1]"
    assert t.inv_text[S.block_hash("Hello Tursunov Bobur")] == "Hello [Person_1]"
    assert t.inv_tool["toolu_1"] == {"command": "echo [Person_1]"}


def test_activate_keeps_existing_depth(tmp_path):
    S.activate("s4", "strict", tmp_path)
    assert S.activate("s4", "normal", tmp_path).depth == "strict"


def test_purge_removes_old_sessions(tmp_path):
    import os
    import time
    S.activate("old", "normal", tmp_path).save_locked()
    d = tmp_path / "sessions" / "old"
    past = time.time() - 10 * 86400
    os.utime(d, (past, past))
    assert S.purge(tmp_path, 7) == 1 and not d.exists()


def test_longer_name_never_merges_into_shorter_entry(tmp_path):
    s = S.activate("merge1", "normal", tmp_path)
    assert s.placeholder("Person", "Nodira") == "[Person_1]"
    full = s.placeholder("Person", "Sobirova Nodira")
    assert full == "[Person_2]" and s.value_of(full) == "Sobirova Nodira"
    assert s.placeholder("Person", "Nodira") == "[Person_1]"
    assert s.placeholder("Person", "Sobirova") == "[Person_2]"


def test_word_order_is_kept_on_restore(tmp_path):
    s = S.activate("order1", "normal", tmp_path)
    a = s.placeholder("Person", "Malika Rashidova")
    b = s.placeholder("Person", "Rashidova Malika")
    assert s.value_of(a) == "Malika Rashidova" and s.value_of(b) == "Rashidova Malika"
    assert s.placeholder("Person", "Rashidovaga") in (a, b)


def test_name_restored_exactly_as_first_written(tmp_path):
    s = S.activate("verbatim1", "normal", tmp_path)
    ph = s.placeholder("Person", "Петрова Сергей")
    assert s.value_of(ph) == "Петрова Сергей"
    ph2 = s.placeholder("Person", "Петрова Анна")
    assert s.value_of(ph2) == "Петрова Анна"
