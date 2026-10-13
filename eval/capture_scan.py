"""Leak test with Claude: scan the proxy's capture file (BLINDFOLD_CAPTURE_DIR) for seeded values."""
import json
import sys
from pathlib import Path

from offline_leak import leaked, summarize


def scan(capture: Path, seeds: Path) -> dict:
    sent = capture.read_text(encoding="utf-8")
    sent_text = "\n".join(json.dumps(json.loads(l), ensure_ascii=False) for l in sent.splitlines() if l.strip())
    docs = json.loads(seeds.read_text(encoding="utf-8"))
    return summarize([[(i["type"], leaked(i["value"], sent_text)) for i in d["items"]] for d in docs])


if __name__ == "__main__":
    print(json.dumps(scan(Path(sys.argv[1]), Path(sys.argv[2])), indent=1))
