"""Text level: replace identifiers with placeholders, spread known names (L6), restore."""
import re

from .detect import detect, resolve
from .names import cells
from .normalize import UZ_SUFFIXES, is_cyrillic, split_uz_suffix, to_cyrillic, to_latin, unify_apos
from .types import Span

TYPES = "Person|Org|Account|Card|PINFL|STIR|Phone|Passport|Address|Email|Plate|Num|Date"
PH_RE = re.compile(rf"\[(?:{TYPES})_\d+\]")
ROW_PH_RE = re.compile(r"\[(?:Person|Org|Account|Card|PINFL|STIR|Phone|Passport|Address|Email|Plate)_\d+\]")
DATE_RE = re.compile(r"(?<!\d)(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{2,4})(?!\d)")
NUM_RE = re.compile(r"[-\u2212]?\d+(?:[.,\u00a0 ]\d+)*")
_PARTIAL = re.compile(r"\[[A-Za-z]{0,8}(?:_\d{0,6})?$")
WITHHELD = "[blindfold: {r} rows x {c} columns withheld, row-level client data]"
# Tables with up to this many rows carrying an identifier go out with placeholders; larger ones are withheld
# whole (decision 10 as revised by the user on 2026-10-12).
MAX_TABLE_ROWS = 50


def _spread(sess, text: str) -> list[Span]:
    """L6: every later mention of a known person or organisation, in either script."""
    spans = []
    for e in sess.name_entities():
        for tok in re.findall(r"[\w']+", unify_apos(e["value"])):
            stem = split_uz_suffix(tok)[0]
            if len(stem) < 4:
                continue
            variants = {stem, to_latin(stem) if is_cyrillic(stem) else to_cyrillic(stem)}
            for v in variants:
                for m in re.finditer(rf"(?<!\w){re.escape(v)}[\w'\u02bb\u02bc\u2019\u2018`]{{0,6}}(?!\w)", text, re.I):
                    if m.group()[0].isupper():
                        spans.append(Span(m.start(), m.end(), e["type"], m.group()))
    return spans


_SUF = "|".join(sorted(UZ_SUFFIXES, key=len, reverse=True))


def _known(sess, text: str) -> list[Span]:
    """Exact-value pass: every occurrence of an original value already in the session map.
    Person/Org may carry an Uzbek case ending, which stays outside the span (and the bracket)."""
    norm = unify_apos(text)  # same length as text: apostrophe variants map one to one
    spans = []
    for type_, form in sess.known_values(sess.depth):
        f = unify_apos(form).strip()
        if len(f) < 3:
            continue
        tail = rf"(?=(?:{_SUF})?(?!\w))" if type_ in ("Person", "Org") else r"(?!\w)"
        flags = re.I if type_ in ("Person", "Org", "Address", "Email") else 0
        for m in re.finditer(rf"(?<!\w){re.escape(f)}{tail}", norm, flags):
            if type_ == "Person" and not m.group()[0].isupper():
                continue   # same rule as _spread: "umid" (hope) is a word, "Umid" a name
            spans.append(Span(m.start(), m.end(), type_, text[m.start():m.end()]))
    return spans


def _outside_placeholders(text: str, fn) -> str:
    out, pos = [], 0
    for m in PH_RE.finditer(text):
        out += [fn(text[pos:m.start()]), m.group()]
        pos = m.end()
    return "".join(out) + fn(text[pos:])


def mask_numbers(sess, text: str) -> str:
    def seg(t):
        t = DATE_RE.sub(lambda m: sess.placeholder("Date", m.group()), t)
        return _outside_placeholders(t, lambda u: NUM_RE.sub(lambda m: sess.placeholder("Num", m.group()), u))
    return _outside_placeholders(text, seg)


def withhold_tables(text: str) -> str:
    lines, out, i = text.split("\n"), [], 0
    n = [len(cells(ln)) for ln in lines]
    while i < len(lines):
        j = i
        if n[i] >= 2:
            while j + 1 < len(lines) and n[j + 1] >= 2 and abs(n[j + 1] - n[i]) <= 1:
                j += 1
        run = lines[i:j + 1]
        if len(run) >= 3 and sum(bool(ROW_PH_RE.search(ln)) for ln in run) > MAX_TABLE_ROWS:
            out.append(WITHHELD.format(r=len(run) - 1, c=max(n[i:j + 1])))
        else:
            out.extend(run)
        i = j + 1
    return "\n".join(out)


def scrub_text(sess, text: str, cfg, tables: bool = True, notices: set | None = None) -> str:
    if not text:
        return text
    known = _known(sess, text)
    found = detect(text, cfg, sess.depth, notices)
    spans = resolve([(0, s) for s in found] + [(6, s) for s in _spread(sess, text)] + [(7, s) for s in known])
    exact = set(known)
    holes = [(m.start(), m.end()) for m in PH_RE.finditer(text)]
    spans = [s for s in spans if all(s.end <= a or s.start >= b for a, b in holes)]
    out, pos = [], 0
    for s in spans:
        out.append(text[pos:s.start])
        value, suffix = s.value, ""
        if s.type in ("Person", "Org") and s not in exact:
            value, suffix = split_uz_suffix(value)
        out.append(sess.placeholder(s.type, value) + suffix)
        pos = s.end
    res = "".join(out) + text[pos:]
    if sess.depth == "strict":
        res = mask_numbers(sess, res)
    return withhold_tables(res) if tables else res


def restore_text(sess, text: str) -> str:
    return PH_RE.sub(lambda m: sess.value_of(m.group()) or m.group(), text)


def restore_json(sess, obj):
    if isinstance(obj, str):
        return restore_text(sess, obj)
    if isinstance(obj, list):
        return [restore_json(sess, x) for x in obj]
    if isinstance(obj, dict):
        return {k: restore_json(sess, v) for k, v in obj.items()}
    return obj


class StreamRestorer:
    """Restores placeholders in streamed text, holding back only a possible partial placeholder."""

    def __init__(self, sess):
        self.sess, self.buf = sess, ""

    def feed(self, text: str) -> str:
        self.buf += text
        m = _PARTIAL.search(self.buf)
        emit, self.buf = (self.buf[:m.start()], self.buf[m.start():]) if m else (self.buf, "")
        return restore_text(self.sess, emit)

    def flush(self) -> str:
        out, self.buf = restore_text(self.sess, self.buf), ""
        return out
