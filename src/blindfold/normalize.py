"""Spelling normalisation so one person maps to one placeholder."""
import re
from importlib import resources

import pymorphy3

_APOS_RE = re.compile("[\u02bb\u02bc\u2019\u2018`]")
UZ_SUFFIXES = ("ning", "dagi", "dan", "ga", "ka", "qa", "da", "ni",
               "нинг", "даги", "дан", "га", "ка", "қа", "да", "ни")
NAME_TAGS = {"Name", "Surn", "Patr"}
_CYR2LAT = {"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j",
            "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
            "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
            "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ь": "", "ы": "i", "э": "e", "ю": "yu",
            "я": "ya", "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h"}
_LAT2CYR = [("o'", "ў"), ("g'", "ғ"), ("sh", "ш"), ("ch", "ч"), ("yo", "ё"), ("yu", "ю"),
            ("ya", "я"), ("ts", "ц"), ("a", "а"), ("b", "б"), ("d", "д"), ("e", "е"), ("f", "ф"),
            ("g", "г"), ("h", "ҳ"), ("i", "и"), ("j", "ж"), ("k", "к"), ("l", "л"), ("m", "м"),
            ("n", "н"), ("o", "о"), ("p", "п"), ("q", "қ"), ("r", "р"), ("s", "с"), ("t", "т"),
            ("u", "у"), ("v", "в"), ("x", "х"), ("y", "й"), ("z", "з")]
_morph = None


def morph() -> pymorphy3.MorphAnalyzer:
    global _morph
    if _morph is None:
        _morph = pymorphy3.MorphAnalyzer()
    return _morph


def unify_apos(s: str) -> str:
    return _APOS_RE.sub("'", s)


def is_cyrillic(s: str) -> bool:
    return bool(re.search("[А-Яа-яЁёЎўҚқҒғҲҳ]", s))


def to_latin(s: str) -> str:
    return "".join(_CYR2LAT.get(c, c) for c in unify_apos(s).lower())


def to_cyrillic(s: str) -> str:
    s, out, i = unify_apos(s).lower(), [], 0
    while i < len(s):
        for lat, cyr in _LAT2CYR:
            if s.startswith(lat, i):
                out.append(cyr)
                i += len(lat)
                break
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


LEXICON_LINES = (resources.files("blindfold") / "data" / "first_names.txt").read_text(encoding="utf-8").splitlines()


def _forms(name: str) -> set[str]:
    """A lexicon name plus its Uzbek Cyrillic spelling (word-initial e also as э)."""
    c = to_cyrillic(name)
    return {name, c, "э" + c[1:] if c.startswith("е") else c}


FIRST_NAMES = frozenset(f for x in LEXICON_LINES if x.strip() and not x.startswith("#")
                        for f in _forms(unify_apos(x.strip()).lower()))
_SURNAME_SHAPE = re.compile("[a-z']{3,}(?:ov|ev|yev|in)a?|[а-яёўқғҳ]{3,}"
                            "(?:ов|ев|ёв|ин)а?")


def split_uz_suffix(word: str) -> tuple[str, str]:
    """Split an Uzbek case ending off only when what remains is a surname shape or a known
    first name, so "Malika" stays whole while "Malikaga" and "Karimovga" split."""
    low = unify_apos(word).lower()
    for suf in UZ_SUFFIXES:
        if low.endswith(suf) and len(low) - len(suf) >= 4:
            stem = low[:-len(suf)].split()
            if stem and (_SURNAME_SHAPE.fullmatch(stem[-1]) or stem[-1] in FIRST_NAMES):
                cut = len(word) - len(suf)
                return word[:cut], word[cut:]
    return word, ""


def ru_name(word: str) -> str | None:
    parses = morph().parse(word)
    top = parses[0].score
    # Prefer a surname reading tied for the top score over a given-name reading:
    # pymorphy3's top-scored parse for an unlisted Uzbek surname like "Каримов" is
    # often a spurious plural/genitive of some first name ("Карим"), tied at the
    # same score as the correct Surn reading. Only consider parses tied with the
    # best score, so unrelated low-score Name/Surn readings of ordinary words
    # ("веры", "тома", ...) are never picked up.
    p = next((q for q in parses if q.score == top and "Surn" in q.tag.grammemes), None)
    if p is None:
        p = parses[0] if NAME_TAGS & set(parses[0].tag.grammemes) else None
    if p is None:
        return None
    f = p.inflect({"nomn"})
    return f.word if f else p.normal_form


def _token_key(tok: str) -> str:
    t = unify_apos(tok)
    if is_cyrillic(t):
        r = ru_name(t)
        return to_latin(r if r else split_uz_suffix(t)[0])
    return split_uz_suffix(t.lower())[0]


def entity_key(type_: str, value: str) -> tuple[str, ...]:
    if type_ in ("Person", "Org", "Address"):
        keys = (_token_key(t) for t in re.findall(r"[\w']+", unify_apos(value)))
        return tuple(k for k in keys if len(k) > 1)
    if type_ in ("Num", "Date", "Email"):
        return (value.lower(),)
    return (re.sub(r"[\s\-\u00a0()+]", "", value).upper(),)


def display_form(type_: str, value: str) -> str:
    if type_ not in ("Person", "Org"):
        return value

    def fix(m):
        tok = m.group()
        if is_cyrillic(tok):
            r = ru_name(tok)
            if r:
                return r[:1].upper() + r[1:]
        return tok

    return re.sub(r"[\w']+", fix, value)
