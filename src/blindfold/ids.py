"""Layers L0 and L1: exact-format identifiers with validation."""
import datetime
import re
from importlib import resources

from .types import Span

_W = [7, 3, 1] * 5
_CENTURY = {1: 1800, 2: 1800, 3: 1900, 4: 1900, 5: 2000, 6: 2000}
ACCOUNT_CODES = frozenset(
    (resources.files("blindfold") / "data" / "account_codes.txt").read_text(encoding="utf-8").split())
CURRENCIES = frozenset({"000", "840", "978", "643", "156", "398", "826", "392", "756",
                        "417", "762", "934", "949"})
_FOLD = str.maketrans("АВЕКМНОРСТХ", "ABEKMHOPCTX")  # Cyrillic look-alikes, same length

_SEP = r"[  -]"
PHONE_CODES = r"(?:20|33|50|55|6[1-9]|7[0-9]|8[078]|9[0-9])"
CARD_PREFIX = re.compile(r"^(?:8600|5614|9860|4|5[1-5]|222[1-9]|22[3-9]\d|2[3-6]\d\d|27[01]\d|2720|62)")
CARD_CTX = re.compile(r"(?i)karta|карт|card|humo|uzcard")
ACCT_CTX = re.compile(r"(?i)h/r|hisob\s*raqam|р/с|расч[её]тный\s*сч[её]т|лицевой\s*сч[её]т|account")
PINFL_CTX = re.compile(r"(?i)jshshir|pinfl|пинфл|жшшир")
STIR_CTX = re.compile(r"(?i)stir|стир|инн|\binn\b|soliq")
AMOUNT_AFTER = re.compile(r"^\s*(?:so['ʻʼ’‘`]?m|сўм|сум|uzs|mln|млн|тыс|ming|\$|usd|eur)", re.I)

MASKED_CARD = re.compile(
    r"(?<!\d)(?:8600|5614|9860|4\d{3}|5[1-5]\d{2}|62\d{2})[ \-]?\d{2}[*xX•]{2}[ \-]?[*xX•]{4}[ \-]?\d{4}(?!\d)")
PHONE_998 = re.compile(r"(?<![\d+])\+?998[\s\-]?\(?\d{2}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)")
PHONE_SEP = re.compile(rf"(?<![\d+])\(?{PHONE_CODES}\)?[\s\-]\d{{3}}[\s\-]?\d{{2}}[\s\-]?\d{{2}}(?!\d)")
NINE = re.compile(r"(?<![\d.,+])\d{9}(?![\d]|[.,]\d)")
PASSPORT = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}[  ]?(?:№ ?)?\d{7}(?!\d)")
PLATE = re.compile(r"(?<!\d)(?:01|10|20|25|30|40|50|60|70|75|80|85|90|95) ?"
                   r"(?:[A-Z] ?\d{3} ?[A-Z]{2}|\d{3} ?[A-Z]{3})(?![A-Z0-9])")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
USER_PATH = re.compile(r"(?i)(?:[a-z]:\\+users\\+|/users/|/home/)([^\\/\s\"']+)")


def only_digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def pinfl_ok(d: str) -> bool:
    if not re.fullmatch(r"[1-6]\d{13}", d):
        return False
    try:
        datetime.date(_CENTURY[int(d[0])] + int(d[5:7]), int(d[3:5]), int(d[1:3]))
    except ValueError:
        return False
    return sum(int(c) * _W[i] for i, c in enumerate(d[:13])) % 10 == int(d[13])


def luhn_ok(d: str) -> bool:
    total = 0
    for i, c in enumerate(reversed(d)):
        x = int(c)
        if i % 2:
            x = x * 2 - 9 if x > 4 else x * 2
        total += x
    return total % 10 == 0


def _digit_run(n: int) -> re.Pattern:
    # n digits, optionally grouped by single spaces or hyphens, not part of a longer grouped number
    return re.compile(rf"(?<![\d+])(?<!\d{_SEP})\d(?:{_SEP}?\d){{{n - 1}}}(?!{_SEP}?\d)")


RUN20, RUN16, RUN14 = _digit_run(20), _digit_run(16), _digit_run(14)


def _ctx(text: str, start: int, pat: re.Pattern, width: int = 40) -> bool:
    return bool(pat.search(text[max(0, start - width):start]))


def find_structured(text: str) -> list[Span]:
    spans: list[Span] = []

    def add(m, type_, group=0):
        spans.append(Span(m.start(group), m.end(group), type_, m.group(group)))

    for m in RUN20.finditer(text):
        d = only_digits(m.group())
        if d[:5] in ACCOUNT_CODES or _ctx(text, m.start(), ACCT_CTX):
            add(m, "Account")
    for m in RUN16.finditer(text):
        d = only_digits(m.group())
        if CARD_PREFIX.match(d) and (luhn_ok(d) or _ctx(text, m.start(), CARD_CTX)):
            add(m, "Card")
    for m in MASKED_CARD.finditer(text):
        add(m, "Card")
    for m in RUN14.finditer(text):
        d = only_digits(m.group())
        if pinfl_ok(d) or _ctx(text, m.start(), PINFL_CTX):
            add(m, "PINFL")
    for m in PHONE_998.finditer(text):
        add(m, "Phone")
    for m in PHONE_SEP.finditer(text):
        add(m, "Phone")
    for m in NINE.finditer(text):
        if AMOUNT_AFTER.match(text[m.end():]):
            continue
        d = m.group()
        if _ctx(text, m.start(), STIR_CTX, 25):
            add(m, "STIR")
        elif re.match(PHONE_CODES, d):
            add(m, "Phone")
        elif d[0] in "234567":
            add(m, "STIR")
    folded = text.translate(_FOLD)
    for m in PASSPORT.finditer(folded):
        spans.append(Span(m.start(), m.end(), "Passport", text[m.start():m.end()]))
    for m in PLATE.finditer(folded):
        spans.append(Span(m.start(), m.end(), "Plate", text[m.start():m.end()]))
    for m in EMAIL.finditer(text):
        add(m, "Email")
    for m in USER_PATH.finditer(text):
        add(m, "Person", 1)
    return spans
