import json
import sys
from pathlib import Path

from blindfold.config import Config
from blindfold.ids import luhn_ok, only_digits, pinfl_ok

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import capture_scan  # noqa: E402
import make_synthetic  # noqa: E402
import offline_leak  # noqa: E402


def test_synthetic_values_are_valid_and_seeded(tmp_path):
    docs = make_synthetic.build(tmp_path, n_docs=6)
    seeds = json.loads((tmp_path / "seeds.json").read_text(encoding="utf-8"))
    assert len(docs) == 6 and len(seeds) == 6
    for doc in seeds:
        text = (tmp_path / doc["file"]).read_text(encoding="utf-8")
        for item in doc["items"]:
            assert item["value"] in text
            if item["type"] == "PINFL":
                assert pinfl_ok(only_digits(item["value"]))
            if item["type"] == "Card":
                assert luhn_ok(only_digits(item["value"]))


def test_offline_leak_scan_reports_structured_types_fully_caught(tmp_path):
    make_synthetic.build(tmp_path, n_docs=6)
    res = offline_leak.scan(tmp_path, Config(use_ner=False))
    for t in ("PINFL", "Card", "Account", "Phone"):
        assert res["recall_by_type"][t] == 1.0, (t, res)
    assert 0.0 <= res["doc_leak_share"] <= 1.0


def test_capture_scan_finds_a_leak(tmp_path):
    make_synthetic.build(tmp_path, n_docs=2)
    seeds = json.loads((tmp_path / "seeds.json").read_text(encoding="utf-8"))
    leaked = seeds[0]["items"][0]["value"]
    cap = tmp_path / "outbound.jsonl"
    cap.write_text(json.dumps({"messages": [{"role": "user", "content": f"x {leaked} y"}]}) + "\n", encoding="utf-8")
    res = capture_scan.scan(cap, tmp_path / "seeds.json")
    assert res["doc_leak_share"] > 0


def test_variants_cover_scripts_and_suffixes():
    v = offline_leak.variants("Karimov Aziz")
    assert "Karimov" in v and "каримов" in {x.lower() for x in v}


ROOT = Path(__file__).resolve().parents[1]
STUB = r'''
$stdin = @($input) -join "`n"
$p = if ($stdin) { $stdin } else { $args[-1] }
if ($p -like "*Reply with exactly one word: A, B or TIE.*") {
    if ($p -like "*TASKMARK_compare*") { "I think answer A is better because it is longer." }
    elseif ($p -like "*TASKMARK_translate*") { "A" }
    else { "TIE." }
} else { "ANSWER<$p> seeds_visible=" + (Test-Path seeds.json) }
'''


def test_v11_tasks_cover_ten_kinds_on_format_fixtures():
    import make_formats  # noqa: F401
    tasks = json.loads((ROOT / "eval/tasks.json").read_text(encoding="utf-8"))
    assert len(tasks) == 10 and len({t["id"] for t in tasks}) == 10
    files = {"clients.xlsx", "clients.csv", "note.docx", "deck.pptx", "letter.pdf", "notes.md", "page.html"}
    for t in tasks:
        assert t["doc"] in files and "{doc}" in t["prompt"] and '"' not in t["prompt"], t
    assert sum(bool(t.get("strict")) for t in tasks) >= 1


def test_v11_quality_runner_uses_skill_and_one_word_judge(tmp_path):
    import shutil
    import subprocess
    if not shutil.which("powershell"):
        import pytest
        pytest.skip("Windows PowerShell not available")
    import os
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "claude.ps1").write_text(STUB, encoding="utf-8")
    (stub / "blindfold.ps1").write_text(
        'Add-Content -Path (Join-Path $PSScriptRoot "activations.txt") -Value ($args -join " ")', encoding="utf-8")
    tasks = [{"id": i, "doc": "notes.md", "prompt": f"TASKMARK_{i} read {{doc}}", **({"strict": True} if i == "sum" else {})}
             for i in ("letter", "sum", "compare", "translate")]
    (tmp_path / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "notes.md").write_text("x", encoding="utf-8")
    (fx / "seeds.json").write_text("[]", encoding="utf-8")
    env = {**os.environ, "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}"}
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(ROOT / "eval/run_quality.ps1"), "-Docs", str(fx), "-Out", str(tmp_path / "out"),
                        "-Tasks", str(tmp_path / "tasks.json")],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "At least as good: 2 of 3 valid (1 invalid)" in r.stdout
    v = {x["id"]: x for x in json.loads((tmp_path / "out/verdicts.json").read_text(encoding="utf-8-sig"))}
    assert {k: x["verdict"] for k, x in v.items()} == {"letter": "YES", "sum": "YES", "compare": "INVALID",
                                                       "translate": "NO"}
    prot = Path(v["sum"]["blindfold"]).read_text(encoding="utf-8-sig")
    assert "blindfold-activation depth=strict" in prot and "TASKMARK_sum read notes.md" in prot
    assert "seeds_visible=False" in prot
    letter = Path(v["letter"]["blindfold"]).read_text(encoding="utf-8-sig")
    assert "blindfold-activation depth=normal" in letter and "TASKMARK_letter" in letter
    acts = (stub / "activations.txt").read_text(encoding="utf-8-sig").split()
    assert acts[0] == "activate" and "strict" in acts and "normal" in acts
    assert "ANSWER<TASKMARK_letter" in Path(v["letter"]["plain"]).read_text(encoding="utf-8-sig")


def test_v11_format_harness_checks_capture_restoration_and_mentions(tmp_path, monkeypatch):
    import make_formats
    import run_formats
    fx, cap = tmp_path / "fx", tmp_path / "cap"
    seeds = {d["file"]: d["items"] for d in make_formats.build(fx)}
    cap.mkdir()
    (cap / "outbound.jsonl").write_text(json.dumps({"old": seeds["page.html"][0]["value"]}) + "\n", encoding="utf-8")
    calls = []

    def fake(prompt, cwd):
        calls.append((prompt, cwd))
        f = prompt.split()[1]
        vals = [i["value"] for i in seeds[f]]
        sent = vals[1] if f == "notes.md" else "[Person_1]"     # notes.md leaks its first phone
        with (cap / "outbound.jsonl").open("a", encoding="utf-8") as h:
            h.write(json.dumps({"messages": [{"content": sent}]}, ensure_ascii=False) + "\n")
        if f == "deck.pptx":
            return "The values are redacted placeholders."
        return " ".join(vals[:-1] if f == "letter.pdf" else vals)

    monkeypatch.setattr(run_formats, "ask_claude", fake)
    res = run_formats.run(cap, fx, tmp_path / "results.json")
    assert calls[0][0] == "Read clients.xlsx and tell me who the client is and list every identifier you find."
    assert calls[0][1] != fx and not (calls[0][1] / "seeds.json").exists()   # scratch copy without answers
    assert (calls[0][1] / "clients.xlsx").exists()
    by = {r["file"]: r for r in res}
    assert set(by) == {"clients.xlsx", "clients.csv", "note.docx", "deck.pptx", "letter.pdf", "notes.md", "page.html"}
    assert by["notes.md"]["leaked"] == [seeds["notes.md"][1]["value"]]
    assert by["page.html"]["leaked"] == []          # the line written before the run is not counted
    assert by["letter.pdf"]["missing"] == [seeds["letter.pdf"][-1]["value"]]
    assert by["clients.csv"]["missing"] == [] and by["clients.csv"]["mentions_masking"] is False
    assert by["deck.pptx"]["mentions_masking"] is True
    assert json.loads((tmp_path / "results.json").read_text(encoding="utf-8")) == res
