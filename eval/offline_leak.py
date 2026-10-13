"""Leak test without Claude: scrub each synthetic document and look for every seeded value."""
import json
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from blindfold import session as S
from blindfold.config import Config
from blindfold.normalize import to_cyrillic, to_latin
from blindfold.scrub import scrub_text


def variants(value: str) -> set[str]:
    out = {value, re.sub(r"\D", "", value)} - {""}
    for tok in re.findall(r"[\w']{4,}", value):
        out |= {tok, to_cyrillic(tok).capitalize(), to_latin(tok).capitalize()}
    return out


def leaked(value: str, text: str) -> bool:
    digits = re.sub(r"\D", "", text)
    for v in variants(value):
        if v.isdigit() and len(v) >= 7 and v in digits:
            return True
        if not v.isdigit() and re.search(rf"(?<!\w){re.escape(v)}", text, re.I):
            return True
    return False


def summarize(per_doc: list[list[tuple[str, bool]]]) -> dict:
    hit, tot = defaultdict(int), defaultdict(int)
    for items in per_doc:
        for type_, leak in items:
            tot[type_] += 1
            hit[type_] += 0 if leak else 1
    return {"recall_by_type": {t: hit[t] / tot[t] for t in tot},
            "doc_leak_share": sum(any(l for _, l in d) for d in per_doc) / max(1, len(per_doc))}


def scan(docs_dir: Path, cfg) -> dict:
    seeds = json.loads((docs_dir / "seeds.json").read_text(encoding="utf-8"))
    per_doc = []
    with tempfile.TemporaryDirectory() as tmp:
        for k, doc in enumerate(seeds):
            sess = S.activate(f"eval{k}", "normal", tmp)
            out = scrub_text(sess, (docs_dir / doc["file"]).read_text(encoding="utf-8"), cfg)
            per_doc.append([(i["type"], leaked(i["value"], out)) for i in doc["items"]])
    return summarize(per_doc)


if __name__ == "__main__":
    res = scan(Path(sys.argv[1] if len(sys.argv) > 1 else "eval/out/synthetic"), Config())
    print(json.dumps(res, indent=1))
    structured = ("PINFL", "Card", "Account", "Phone", "Passport", "STIR")
    sys.exit(0 if all(res["recall_by_type"].get(t, 1.0) == 1.0 for t in structured) else 1)
