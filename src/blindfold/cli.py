"""blindfold serve | activate | schema | hook"""
import argparse
import json
import re
import sys
import urllib.request

from . import session as sessions
from .config import home, load

TABLE_EXT = (".csv", ".tsv", ".xlsx", ".xls", ".xlsm", ".parquet")
READ_CMD = re.compile(r"(?i)\b(?:cat|head|tail|type|more|less|get-content|gc|import-csv)\b[^|;&\n]*"
                      r"\.(?:csv|tsv|xlsx|xls|xlsm|parquet)\b")
NET_CMD = re.compile(r"(?i)(?:^|[\s;|&(])(?:curl|wget|invoke-webrequest|iwr|invoke-restmethod|irm|scp|ssh|sftp|ftp|nc)"
                     r"(?:\.exe)?(?=\s|$)|\bgit\s+push\b")
TABLE_MSG = ("blindfold: raw table files are not opened directly in a protected session. Run "
             "`blindfold schema \"{path}\"` to see the columns, types and a made-up sample row, then write code "
             "that prints totals, counts or group summaries.")
NET_MSG = "blindfold: network commands are blocked while blindfold is on."


def parse_activate(words: list[str]) -> tuple[str, str]:
    sid = words[-1] if words else ""
    first = words[0] if len(words) > 1 else ""
    if sid.lower() == "strict" or sid == "${CLAUDE_SESSION_ID}":
        sid = ""   # the session ID was not filled in; never activate a session by that name
    return ("strict" if first.lower() == "strict" else "normal"), sid


def open_internet() -> bool:
    try:
        urllib.request.urlopen("https://example.com", timeout=3)
        return True
    except Exception:
        return False


def hook_decision(data: dict, root=None) -> str | None:
    if not sessions.state_exists(data.get("session_id"), root):
        return None
    tool, ti = data.get("tool_name", ""), data.get("tool_input") or {}
    if tool == "Read":
        path = str(ti.get("file_path", ""))
        return TABLE_MSG.format(path=path) if path.lower().endswith(TABLE_EXT) else None
    if tool in ("Bash", "PowerShell"):
        cmd = str(ti.get("command", ""))
        m = READ_CMD.search(cmd)
        if m:
            return TABLE_MSG.format(path="<file>")
        if NET_CMD.search(cmd):
            return NET_MSG
    return None


def _mask(v) -> str:
    s = str(v)
    s = re.sub(r"\d", "9", s)
    return "".join("X" if c.isupper() else "x" if c.isalpha() else c for c in s)


def schema_text(path: str) -> str:
    import pandas as pd
    low = path.lower()
    if low.endswith((".xlsx", ".xls", ".xlsm")):
        df = pd.read_excel(path)
    elif low.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, sep="\t" if low.endswith(".tsv") else ",")
    lines = [f"rows: {len(df)}", "columns (name | type | made-up sample in the same format):"]
    for col in df.columns:
        sample = next((v for v in df[col] if pd.notna(v)), "")
        lines.append(f"  {col} | {df[col].dtype} | {_mask(sample)}")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="blindfold")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    a = sub.add_parser("activate")
    a.add_argument("words", nargs="*")
    s = sub.add_parser("schema")
    s.add_argument("file")
    sub.add_parser("hook")
    args = p.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn
        from .proxy import make_app
        cfg = load()
        sessions.purge(home(), cfg.retention_days)
        uvicorn.run(make_app(cfg), host="127.0.0.1", port=cfg.port, log_level="warning")
        return 0
    if args.cmd == "activate":
        depth, sid = parse_activate(args.words)
        sess = sessions.activate(sid, depth)
        if sess is None:
            print("blindfold: could not read this session's ID, so protection is NOT on.")
            return 1
        print(f"blindfold: on for this session ({sess.depth} depth).")
        if open_internet():
            print("blindfold: this computer can reach the open internet, so code run in this session "
                  "could send restored values out.")
        return 0
    if args.cmd == "schema":
        print(schema_text(args.file))
        return 0
    # The hook is guidance: any failure lets the tool run (exit 0, no output) rather than
    # breaking Claude Code. stdin is read as UTF-8 bytes, never in the console code page.
    try:
        buf = getattr(sys.stdin, "buffer", None)   # absent only on an in-memory text stream
        data = json.loads(buf.read().decode("utf-8") if buf is not None else sys.stdin.read())
        reason = hook_decision(data) if isinstance(data, dict) else None
        if reason:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                     "permissionDecision": "deny",
                                                     "permissionDecisionReason": reason}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
