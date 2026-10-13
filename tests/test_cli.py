import json

import pandas as pd

from blindfold import cli
from blindfold import session as S


def test_parse_activate():
    assert cli.parse_activate(["strict", "abc"]) == ("strict", "abc")
    assert cli.parse_activate(["$0", "abc"]) == ("normal", "abc")
    assert cli.parse_activate(["Which", "abc"]) == ("normal", "abc")
    assert cli.parse_activate(["abc"]) == ("normal", "abc")


def test_activate_writes_state_and_prints(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BLINDFOLD_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "open_internet", lambda: True)
    assert cli.main(["activate", "strict", "sid1"]) == 0
    out = capsys.readouterr().out
    assert "strict" in out and "open internet" in out
    assert S.state_exists("sid1", tmp_path)


def test_hook_denies_tables_and_network_only_when_active(tmp_path):
    S.activate("h1", "normal", tmp_path)
    read = {"session_id": "h1", "tool_name": "Read", "tool_input": {"file_path": "C:/d/loans.xlsx"}}
    assert "blindfold schema" in cli.hook_decision(read, tmp_path)
    assert cli.hook_decision({**read, "session_id": "other"}, tmp_path) is None
    bash = {"session_id": "h1", "tool_name": "Bash", "tool_input": {"command": "head -5 loans.csv"}}
    assert "blindfold schema" in cli.hook_decision(bash, tmp_path)
    net = {"session_id": "h1", "tool_name": "PowerShell", "tool_input": {"command": "Invoke-WebRequest https://x"}}
    assert "network" in cli.hook_decision(net, tmp_path)
    ok = {"session_id": "h1", "tool_name": "Bash", "tool_input": {"command": "python analysis.py"}}
    assert cli.hook_decision(ok, tmp_path) is None
    assert cli.hook_decision({"session_id": "../x", "tool_name": "Read", "tool_input": {}}, tmp_path) is None


def test_hook_main_prints_deny_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BLINDFOLD_HOME", str(tmp_path))
    S.activate("h2", "normal", tmp_path)
    data = {"session_id": "h2", "tool_name": "Read", "tool_input": {"file_path": "a.csv"}}
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(data)))
    assert cli.main(["hook"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_schema_masks_values(tmp_path):
    p = tmp_path / "loans.csv"
    pd.DataFrame({"client": ["Tursunov Bobur", "Karimov Aziz"], "days_overdue": [120, 5],
                  "pinfl": ["31505883017120", "42207914020054"]}).to_csv(p, index=False)
    out = cli.schema_text(str(p))
    assert "rows: 2" in out and "client" in out and "days_overdue" in out
    assert "Tursunov" not in out and "31505883017120" not in out and "120" not in out


def test_m1_activate_refuses_missing_or_bad_session_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BLINDFOLD_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "open_internet", lambda: False)
    for words in ([], [""], ["strict"], ["strict", "strict"], ["${CLAUDE_SESSION_ID}"],
                  ["strict", "${CLAUDE_SESSION_ID}"]):
        assert cli.main(["activate", *words]) == 1, words
        assert "NOT on" in capsys.readouterr().out
    assert not (tmp_path / "sessions" / "strict").exists()


def _stdin_bytes(monkeypatch, data: bytes):
    import io
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(data), encoding="cp1252"))


def test_m2_hook_reads_utf8_bytes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BLINDFOLD_HOME", str(tmp_path))
    S.activate("h3", "normal", tmp_path)
    path = "C:/Users/\u0420\u0443\u0441\u0442\u0430\u043c/loans.csv"
    data = {"session_id": "h3", "tool_name": "Read", "tool_input": {"file_path": path}}
    _stdin_bytes(monkeypatch, json.dumps(data, ensure_ascii=False).encode("utf-8"))
    assert cli.main(["hook"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert path in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_m2_hook_never_raises(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BLINDFOLD_HOME", str(tmp_path))
    for raw in (b"", b"not json", b"\xff\xfe", b"[1, 2]"):
        _stdin_bytes(monkeypatch, raw)
        assert cli.main(["hook"]) == 0
        assert capsys.readouterr().out == ""
