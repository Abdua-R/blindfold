"""Live format harness (not run by pytest). Assumes blindfold serve runs with BLINDFOLD_CAPTURE_DIR set and
Claude Code points at it. For each fixture file from make_formats: ask Claude through /blindfold, then check
(a) no seeded value of that file left in the capture lines written during the run, (b) every seeded value
came back restored in the reply, (c) whether the reply talks about the masking.

Usage: python eval/run_formats.py <capture dir> <fixture dir> [results.json]
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from offline_leak import leaked

PROMPT = "Read {f} and tell me who the client is and list every identifier you find."
# Headless `claude -p "/blindfold ..."` runs the skill as a local command with no model turn, so the harness
# reproduces what the skill does: activate the session ID, then send the skill's own text before the task.
SKILL = (Path(__file__).resolve().parents[1] / "src" / "blindfold" / "skill" / "SKILL.md").read_text(encoding="utf-8")
SKILL_BODY = SKILL.split("---", 2)[2].replace("!`blindfold activate $0 ${CLAUDE_SESSION_ID}`", "").replace("$0", "{depth}")
MENTIONS = re.compile(r"placeholder|redact|blindfold", re.I)


def ask_claude(prompt: str, cwd: Path, depth: str = "normal") -> str:
    # One retry after a minute when the model service throttles: that is upstream load, not a blindfold result.
    out = _ask_once(prompt, cwd, depth)
    if re.match(r"\W*API Error", out):
        time.sleep(60)
        out = _ask_once(prompt, cwd, depth)
    return out


def _ask_once(prompt: str, cwd: Path, depth: str) -> str:
    sid = str(uuid.uuid4())
    subprocess.run([shutil.which("blindfold") or "blindfold", "activate", depth, sid], capture_output=True)
    full = SKILL_BODY.replace("{depth}", depth) + "\n\n" + prompt
    try:
        # The prompt goes on stdin: Windows runs claude through a .cmd shim, and cmd.exe cuts arguments at newlines.
        r = subprocess.run([shutil.which("claude") or "claude", "-p", "--session-id", sid, "--allowedTools", "Bash,Read,Glob,Grep"], input=full, cwd=cwd,
                           capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600)
        return r.stdout
    except subprocess.TimeoutExpired:
        return ""


def _lines(capture: Path) -> list[str]:
    p = capture / "outbound.jsonl"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def run(capture: Path, fixtures: Path, out: Path) -> list[dict]:
    seeds = json.loads((fixtures / "seeds.json").read_text(encoding="utf-8"))
    # Claude works in a scratch copy without seeds.json, so it cannot read the expected values.
    work = Path(tempfile.mkdtemp(prefix="bf_formats_"))
    for f in fixtures.iterdir():
        if f.is_file() and f.name != "seeds.json":
            shutil.copy2(f, work)
    res = []
    for doc in seeds:
        before = len(_lines(capture))
        reply = ask_claude(PROMPT.format(f=doc["file"]), work)
        sent = "\n".join(_lines(capture)[before:])
        vals = [i["value"] for i in doc["items"]]
        res.append({"file": doc["file"], "capture_lines": sent.count("\n") + bool(sent),
                    "leaked": [v for v in vals if leaked(v, sent)],
                    "missing": [v for v in vals if v not in reply],
                    "mentions_masking": bool(MENTIONS.search(reply)), "reply": reply})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{'file':<14}{'values':>7}{'leaked':>8}{'missing':>9}{'mentions':>10}{'lines':>7}")
    for r, d in zip(res, seeds):
        print(f"{r['file']:<14}{len(d['items']):>7}{len(r['leaked']):>8}{len(r['missing']):>9}"
              f"{str(r['mentions_masking']):>10}{r['capture_lines']:>7}")
    return res


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) < 2:
        sys.exit(__doc__)
    run(Path(a[0]), Path(a[1]), Path(a[2] if len(a) > 2 else "eval/out/formats/results.json"))
