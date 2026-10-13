import json

import httpx

from blindfold import detect as D
from blindfold import ner
from blindfold.config import Config
from blindfold.detect import _mask
from blindfold.llm import llm_find
from blindfold.types import Span


def test_resolve_longest_wins_and_no_overlap():
    a = Span(0, 7, "Person", "Karimov")
    b = Span(0, 12, "Person", "Karimov Aziz")
    c = Span(20, 34, "PINFL", "31505883017120")
    assert D.resolve([(2, a), (3, b), (0, c)]) == [b, c]


def test_locate_finds_all_occurrences():
    assert D.locate("Aziz va Aziz", [("Aziz", "Person")]) == [
        Span(0, 4, "Person", "Aziz"), Span(8, 12, "Person", "Aziz")]


def test_public_names_kept_in_normal_hidden_in_strict(monkeypatch):
    monkeypatch.setattr(ner, "find", lambda text: [Span(0, 10, "Org", "Kapitalbank")])
    cfg = Config(use_ner=True)
    assert D.detect("Kapitalbank", cfg, "normal") == []
    assert [s.value for s in D.detect("Kapitalbank", cfg, "strict")] == ["Kapitalbank"]


def _transport(reply=None, fail=False):
    def handler(request):
        if fail:
            return httpx.Response(503)
        body = json.loads(request.content)
        assert body["messages"][-1]["content"].endswith("Dilnoza aytdi.")
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
    return httpx.MockTransport(handler)


def test_llm_find_parses_json_and_drops_bad_types():
    cfg = Config(llm_base_url="http://llm.test/v1")
    reply = 'Here: {"items": [{"text": "Dilnoza", "type": "Person"}, {"text": "x", "type": "Bad"}]}'
    assert llm_find("Dilnoza aytdi.", cfg, transport=_transport(reply)) == [("Dilnoza", "Person")]


def test_llm_unavailable_sets_notice(monkeypatch):
    cfg = Config(llm_base_url="http://llm.test/v1", use_ner=False)
    monkeypatch.setattr(D, "_llm", lambda text, cfg: llm_find(text, cfg, transport=_transport(fail=True)))
    notices = set()
    D.detect("Dilnoza aytdi.", cfg, "normal", notices)
    assert notices == {"llm_unavailable"}


def test_mask_merges_overlapping_spans_sharing_a_start():
    assert _mask("Tursunov Bobur keldi", [Span(0, 8, "Person", "Tursunov"),
                                           Span(0, 14, "Person", "Tursunov Bobur")]) == "[ID] keldi"


def test_mask_merges_partial_overlap():
    assert _mask("abcdefgh", [Span(0, 4, "X", "abcd"), Span(2, 6, "X", "cdef")]) == "[ID]gh"


def test_detect_union_of_layers():
    cfg = Config(use_ner=False)
    got = {(s.type, s.value) for s in D.detect("Mijoz: Tursunov Bobur\nPINFL 31505883017120", cfg)}
    assert ("Person", "Tursunov Bobur") in got and ("PINFL", "31505883017120") in got


C1_PROBES = ["\u0410\u0434\u0440\u0435\u0441: \u0433. \u0421\u0430\u043c\u0430\u0440\u043a\u0430\u043d\u0434, "
             "\u0443\u043b. \u0420\u0435\u0433\u0438\u0441\u0442\u0430\u043d 12, \u043a\u0432 5",
             "Ijarachi \u00abSamarqand Agro Invest\u00bb MChJ",
             "Shartnoma \u00abToshkent Savdo\u00bb MChJ bilan",
             "Manzil: Toshkent sh., Chilonzor t., 5-uy"]


def test_c1_org_and_address_containing_public_name_still_masked():
    cfg = Config(use_ner=False)
    for text in C1_PROBES:
        assert D.detect(text, cfg, "normal"), text


def test_c1_bare_public_names_stay_public_in_normal_depth(monkeypatch):
    cfg = Config(use_ner=True)
    for v in ("Kapitalbank", "Toshkent", "AKB \u00abKapitalbank\u00bb"):
        monkeypatch.setattr(ner, "find", lambda text, v=v: [Span(0, len(v), "Org", v)])
        assert D.detect(v, cfg, "normal") == [], v


def test_i3_llm_cooldown_after_failure(monkeypatch):
    cfg = Config(llm_base_url="http://llm.cool/v1", use_ner=False)
    calls, clock = [], [1000.0]
    monkeypatch.setattr(D, "_llm", lambda text, cfg: calls.append(text))
    monkeypatch.setattr(D, "_now", lambda: clock[0], raising=False)
    for _ in range(3):
        notices = set()
        D.detect("Dilnoza aytdi.", cfg, "normal", notices)
        assert notices == {"llm_unavailable"}
    assert len(calls) == 1
    clock[0] += 301
    D.detect("Dilnoza aytdi.", cfg, "normal", set())
    assert len(calls) == 2


def _counting(n, reply='{"items": [{"text": "Dilnoza", "type": "Person"}]}', fail=False):
    def handler(request):
        n.append(json.loads(request.content)["model"])
        return httpx.Response(500) if fail else httpx.Response(
            200, json={"choices": [{"message": {"content": reply}}]})
    return httpx.MockTransport(handler)


def test_v11_llm_cache_one_request_per_text_and_model(tmp_path):
    from blindfold import llm
    n, cfg = [], Config(llm_base_url="http://llm.cache/v1")
    t = _counting(n)
    assert llm_find("Dilnoza aytdi.", cfg, transport=t) == [("Dilnoza", "Person")]
    assert llm_find("Dilnoza aytdi.", cfg, transport=t) == [("Dilnoza", "Person")]
    assert len(n) == 1
    llm_find("Dilnoza aytdi.", Config(llm_base_url="http://llm.cache/v1", llm_model="other"), transport=t)
    assert len(n) == 2
    llm_find("Dilnoza aytdi.", Config(llm_base_url="http://llm.other/v1"), transport=t)
    assert len(n) == 3
    assert (tmp_path / "bfhome" / "llm_cache.json").exists()
    llm._mem.clear()   # a fresh process loads the cache from disk
    assert llm_find("Dilnoza aytdi.", cfg, transport=t) == [("Dilnoza", "Person")]
    assert len(n) == 3


def test_v11_llm_failures_never_cached():
    n, cfg = [], Config(llm_base_url="http://llm.fail/v1")
    assert llm_find("Dilnoza aytdi.", cfg, transport=_counting(n, fail=True)) is None
    assert llm_find("Dilnoza aytdi.", cfg, transport=_counting(n, fail=True)) is None
    assert len(n) == 2


def test_v11_llm_cache_entries_expire_with_retention(monkeypatch):
    from blindfold import llm
    n, cfg = [], Config(llm_base_url="http://llm.old/v1", retention_days=7)
    clock = [1_000_000.0]
    monkeypatch.setattr(llm, "_clock", lambda: clock[0], raising=False)
    llm_find("Dilnoza aytdi.", cfg, transport=_counting(n))
    clock[0] += 8 * 86400
    llm_find("Dilnoza aytdi.", cfg, transport=_counting(n))
    assert len(n) == 2


def _flaky(responses, calls):
    def handler(request):
        calls.append(1)
        status, body = responses[min(len(calls), len(responses)) - 1]
        return httpx.Response(status, text=body) if status != 200 else httpx.Response(200, json=body)
    return httpx.MockTransport(handler)


OK = {"choices": [{"message": {"content": '{"items": [{"text": "Malika", "type": "Person"}]}'}}]}


def test_llm_retries_short_per_minute_429(tmp_path, monkeypatch):
    from blindfold import llm
    monkeypatch.setenv("BLINDFOLD_HOME", str(tmp_path))
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    calls = []
    t = _flaky([(429, "Quota exceeded per minute. Please retry in 476.68ms."), (200, OK)], calls)
    cfg = Config(llm_base_url="http://llm.test/v1", llm_timeout_s=30)
    assert llm.llm_find("Malika keldi.", cfg, transport=t) == [("Malika", "Person")]
    assert len(calls) == 2


def test_llm_does_not_retry_daily_cap(tmp_path, monkeypatch):
    from blindfold import llm
    monkeypatch.setenv("BLINDFOLD_HOME", str(tmp_path))
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    calls = []
    t = _flaky([(429, "GenerateRequestsPerDayPerProjectPerModel-FreeTier. Please retry in 50s."), (200, OK)], calls)
    assert llm.llm_find("Malika keldi.", Config(llm_base_url="http://llm.test/v1"), transport=t) is None
    assert len(calls) == 1


def test_llm_retry_stops_at_time_budget(tmp_path, monkeypatch):
    from blindfold import llm
    monkeypatch.setenv("BLINDFOLD_HOME", str(tmp_path))
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    calls = []
    t = _flaky([(503, "high demand, retry in 20s")] * 10, calls)
    cfg = Config(llm_base_url="http://llm.test/v1", llm_timeout_s=30)
    assert llm.llm_find("Malika keldi.", cfg, transport=t) is None
    assert len(calls) <= 2
