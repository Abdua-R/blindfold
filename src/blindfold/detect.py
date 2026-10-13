"""Union of detection layers L1 to L5. L6 (session-wide spreading) lives in scrub.py."""
import re
import time
from importlib import resources

from . import ids, names, ner
from .llm import llm_find
from .normalize import unify_apos
from .types import Span

PUBLIC = tuple(unify_apos(x.strip()) for x in
               (resources.files("blindfold") / "data" / "public_names.txt").read_text(encoding="utf-8").splitlines()
               if x.strip())
# Words that carry no identity of their own: bank words and legal-form markers. An Org span is
# public only when nothing alphanumeric is left after deleting public names and these words.
GENERIC = re.compile(r"(?<!\w)(?:bank|banki|банк|банки|"
                     r"mchj|мчж|ооо|оао|зао|"
                     r"пао|aj|аж|ао|xk|хк|qk|қк|oaj|"
                     r"yatt|ятт|ип|чп|сп|akb|акб|"
                     r"atb|атб|duk|дук)(?!\w)")
_PUBLIC_LONGEST = sorted(PUBLIC, key=len, reverse=True)
L5_COOLDOWN_S = 300.0
_l5_down: dict[str, float] = {}   # llm_base_url -> time of its last failure
_now = time.monotonic


def _llm(text, cfg):
    return llm_find(text, cfg)


def locate(text: str, items: list[tuple[str, str]]) -> list[Span]:
    spans = []
    for s, type_ in items:
        if not s:
            continue
        for m in re.finditer(re.escape(s), text):
            spans.append(Span(m.start(), m.end(), type_, s))
    return spans


def resolve(tagged: list[tuple[int, Span]]) -> list[Span]:
    """Longest span wins; on equal length the earlier layer wins; no overlaps."""
    taken: list[Span] = []
    for _, s in sorted(tagged, key=lambda t: (-(t[1].end - t[1].start), t[0], t[1].start)):
        if all(s.end <= o.start or s.start >= o.end for o in taken):
            taken.append(s)
    return sorted(taken, key=lambda s: s.start)


def _public(s: Span) -> bool:
    """Person: public only on exact equality. Org: public only when it is made of public names
    and generic words alone. Address: never public (a public city inside it hides nothing)."""
    v = unify_apos(s.value).lower()
    if s.type == "Person":
        return v in PUBLIC
    if s.type != "Org":
        return False
    for p in _PUBLIC_LONGEST:
        v = v.replace(p, " ")
    return not re.search(r"[^\W_]", GENERIC.sub(" ", v))


def _mask(text: str, spans: list[Span]) -> str:
    """Mask the union of all spans, so overlapping spans leak no gap between them."""
    merged: list[list[int]] = []
    for start, end in sorted((s.start, s.end) for s in spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    out, pos = [], 0
    for start, end in merged:
        out += [text[pos:start], "[ID]"]
        pos = end
    return "".join(out) + text[pos:]


def detect(text: str, cfg, depth: str = "normal", notices: set | None = None) -> list[Span]:
    tagged = [(1, s) for s in ids.find_structured(text)]
    tagged += [(2, s) for s in names.find_labels(text) + names.find_label_columns(text)]
    tagged += [(3, s) for s in names.find_name_shapes(text)]
    if cfg.use_ner:
        tagged += [(4, s) for s in ner.find(text)]
        if notices is not None and ner.unavailable():
            notices.add("ner_unavailable")
    if cfg.llm_base_url:
        url = cfg.llm_base_url
        down = url in _l5_down and _now() - _l5_down[url] < L5_COOLDOWN_S
        found = None if down else _llm(_mask(text, [s for _, s in tagged]), cfg)
        if found is None:
            if not down:
                _l5_down[url] = _now()
            if notices is not None:
                notices.add("llm_unavailable")
        else:
            _l5_down.pop(url, None)
            tagged += [(5, s) for s in locate(text, found)]
    if depth == "normal":
        tagged = [(l, s) for l, s in tagged if not _public(s)]
    return resolve(tagged)
