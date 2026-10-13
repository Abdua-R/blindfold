"""Layers L2 (labels, column headers) and L3 (name shapes)."""
import re

from .normalize import FIRST_NAMES, NAME_TAGS, morph, split_uz_suffix, unify_apos
from .types import Span

A = "'\u02bb\u02bc\u2019\u2018`"
UP = "A-ZА-ЯЁЎҚҒҲ"
LO = "a-zа-яёўқғҳ"
CAP = rf"[{UP}][{LO}{A}]+"
PERSON_LABELS = (r"F\.?\s?I\.?\s?Sh\.?|FISh|FISH|Ф\.?\s?И\.?\s?О\.?|ФИО|Mijoz|Клиент|Qarz\s+oluvchi|"
                 r"Заемщик|Заёмщик|Kafil|Поручитель|Direktor|Директор|Bosh\s+hisobchi|"
                 r"Главный\s+бухгалтер|Rahbar|Руководитель")
ADDRESS_LABELS = r"Manzil|Манзил|Адрес|Address|Yashash\s+joyi|Место\s+жительства"
PERSON_LABEL_RE = re.compile(rf"(?i)(?<!\w)(?:{PERSON_LABELS})\s*[:\-\u2013]\s*(?P<v>[^\n,;|\t]{{2,60}})")
ADDRESS_LABEL_RE = re.compile(rf"(?i)(?<!\w)(?:{ADDRESS_LABELS})\s*[:\-\u2013]\s*(?P<v>[^\n|\t]{{2,160}})")
HEADER_RE = re.compile(rf"(?i)^(?:{PERSON_LABELS}|Ism|Familiya|Имя|Фамилия|Name|Full\s*name|"
                       rf"Client|Mijoz\s+nomi|{ADDRESS_LABELS})$")
ADDRESS_HEADER_RE = re.compile(rf"(?i)^(?:{ADDRESS_LABELS})$")

PATR = re.compile(rf"(?:{CAP}\s+){{0,2}}{CAP}(?:ovich|evich|ovna|evna|ович|евич|овна|евна|ична|инична)[{LO}]{{0,3}}(?![\w])")
OGLI = re.compile(rf"(?:{CAP}\s+){{0,2}}{CAP}\s+(?:o[{A}]?g[{A}]?li|qizi|ўғли|қизи|угли|кизи|оглы|кызы)(?![\w])")
INIT = re.compile(rf"(?:[{UP}]\.\s?){{1,2}}{CAP}|{CAP}\s(?:[{UP}]\.\s?){{1,2}}")
CAP_TOKEN = re.compile(rf"(?<![\w{A}]){CAP}")
ORG = re.compile(
    r"(?:(?:MChJ|МЧЖ|ООО|ОАО|ЗАО|ПАО|AJ|АЖ|АО|XK|ХК|QK|ҚК|OAJ|YaTT|ЯТТ|ИП|ЧП|СП|AKB|АКБ|ATB|АТБ|DUK|ДУК)"
    r"\s*[\u00ab\"\u201c][^\u00bb\"\u201d]{2,80}[\u00bb\"\u201d])|(?:[\u00ab\"\u201c][^\u00bb\"\u201d]{2,80}[\u00bb\"\u201d]\s*(?:MChJ|МЧЖ|ООО|AJ|АЖ|АО|XK|ХК|QK|ҚК|"
    r"YaTT|ЯТТ|ИП|ЧП|AKB|АКБ|ATB|АТБ|bank|banki|банк))")
SUR_LAT = re.compile(rf"[A-Z][a-z']{{2,}}(?:ov|ev|yev|in)(?:a|ning|ga|dan|da|ni|aning|aga|adan|ani)?")
SUR_CYR = re.compile(r"[А-ЯЁЎҚҒҲ][а-яёўқғҳ]{2,}(?:ов|ев|ёв|ин)(?:а|у|ым|ой|ою|е|ых|ыми|нинг|га|дан|да|ни)?")
STOP = {"to'lov", "so'rov", "garov", "o'lchov", "ishlov", "sinov", "qarov", "tekin", "erkin",
        "mumkin", "keyin", "oldin", "lekin", "tўлов", "тўлов", "сўров", "гаров", "ўлчов", "ишлов",
        "синов", "мумкин", "кейин", "олдин", "лекин", "министров", "гражданин", "лицевой",
        "основ", "готов", "часов", "слов", "домов", "банков", "счетов", "документов"}


def cells(line: str) -> list[tuple[int, int]]:
    """Cell offsets of a table-like line, or [] for ordinary prose."""
    for sep, need in (("\t", 1), ("|", 1), (";", 2), (",", 2)):
        if line.count(sep) >= need:
            out, pos = [], 0
            for part in line.split(sep):
                a = pos + len(part) - len(part.lstrip())
                b = pos + len(part.rstrip())
                if b > a:
                    out.append((a, b))
                pos += len(part) + 1
            return out
    out = []
    for m in re.finditer(r"\S+(?: \S+)*", line):
        out.append((m.start(), m.end()))
    return out if len(out) >= 2 and re.search(r"\S {2,}\S", line) else ([(0, len(line))] if line.strip() else [])


def find_labels(text: str) -> list[Span]:
    spans = []
    for pat, type_ in ((PERSON_LABEL_RE, "Person"), (ADDRESS_LABEL_RE, "Address")):
        for m in pat.finditer(text):
            v = m.group("v").rstrip(" .")
            spans.append(Span(m.start("v"), m.start("v") + len(v), type_, v))
    return spans


def find_label_columns(text: str) -> list[Span]:
    spans, lines, offs, pos = [], text.split("\n"), [], 0
    for ln in lines:
        offs.append(pos)
        pos += len(ln) + 1
    i = 0
    while i < len(lines):
        head = cells(lines[i])
        hits = [k for k, (a, b) in enumerate(head) if HEADER_RE.match(lines[i][a:b].strip())]
        if len(head) >= 2 and hits:
            j = i + 1
            while j < len(lines):
                row = cells(lines[j])
                if len(row) < 2 or abs(len(row) - len(head)) > 1:
                    break
                shift = 1 if len(row) == len(head) + 1 else 0
                for k in hits:
                    if k + shift < len(row):
                        a, b = row[k + shift]
                        type_ = "Address" if ADDRESS_HEADER_RE.match(lines[i][head[k][0]:head[k][1]].strip()) else "Person"
                        spans.append(Span(offs[j] + a, offs[j] + b, type_, lines[j][a:b]))
                j += 1
            i = j
        else:
            i += 1
    return spans


def _is_surname(tok: str) -> bool:
    t = unify_apos(tok)
    if t.lower() in STOP:
        return False
    return bool(SUR_LAT.fullmatch(t) or SUR_CYR.fullmatch(t))


def find_name_shapes(text: str) -> list[Span]:
    spans = [Span(m.start(), m.end(), "Person", m.group()) for pat in (PATR, OGLI) for m in pat.finditer(text)]
    for m in INIT.finditer(text):
        if any(_is_surname(t) for t in re.findall(rf"[{UP}][{LO}{A}]+", m.group())):
            spans.append(Span(m.start(), m.end(), "Person", m.group()))
    toks = list(CAP_TOKEN.finditer(text))
    for k, m in enumerate(toks):
        tok = m.group()
        hit = _is_surname(tok)
        if not hit and re.search("[А-ЯЁ]", tok[:1]) and tok.lower() not in STOP:
            hit = bool(NAME_TAGS & set(morph().parse(tok)[0].tag.grammemes))
        first = not hit and unify_apos(split_uz_suffix(tok)[0]).lower() in FIRST_NAMES
        if not (hit or first):
            continue
        a, b = m.start(), m.end()
        nxt = toks[k + 1] if k + 1 < len(toks) else None
        if nxt and re.fullmatch(r"\s", text[b:nxt.start()] or "x") and (hit or _is_surname(nxt.group())):
            b = nxt.end()
        spans.append(Span(a, b, "Person", text[a:b]))
    spans += [Span(m.start(), m.end(), "Org", m.group()) for m in ORG.finditer(text)]
    return spans
