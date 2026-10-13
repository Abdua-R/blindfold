"""Synthetic client documents seeded with fake identifiers; safe to send anywhere."""
import json
import random
import sys
from pathlib import Path

FIRST_UZ = ["Bobur", "Aziz", "Dilnoza", "Shahnoza", "Jasur", "Malika", "Otabek", "Nodira"]
LAST_UZ = ["Tursunov", "Karimov", "Rashidova", "Yusupov", "Ergasheva", "Mirzayev", "Qodirov", "Sobirova"]
FIRST_RU = ["Сергей", "Анна", "Дмитрий", "Ольга"]
LAST_RU = ["Иванов", "Петрова", "Смирнов", "Кузнецова"]
TEMPLATES = [
    "Mijoz: {name}, JShShIR {pinfl}. Hisob raqami {account}. Karta {card}. Tel {phone}. "
    "{last}ga kredit bo'yicha 92 kunlik muddati o'tgan qarzdorlik haqida xabar yuborilsin.",
    "Заемщик: {name}, ПИНФЛ {pinfl}, р/с {account}, тел. {phone}. "
    "Просрочка 120 дней. Связаться с клиентом {last} до пятницы.",
    "Qarz oluvchi {name} (pasport {passport}) {org} MChJ direktori. STIR {stir}. "
    "{last}ning arizasi ko'rib chiqildi.",
]
W = [7, 3, 1] * 5


def pinfl(rng):
    while True:
        body = f"{rng.choice('3456')}{rng.randint(1, 28):02d}{rng.randint(1, 12):02d}{rng.randint(60, 99):02d}" \
               f"{rng.randint(100, 999)}{rng.randint(1, 999):03d}"
        return body + str(sum(int(c) * W[i] for i, c in enumerate(body)) % 10)


def card(rng):
    base = "8600" + "".join(str(rng.randint(0, 9)) for _ in range(11))
    for d in range(10):
        s, t = base + str(d), 0
        for i, c in enumerate(reversed(s)):
            x = int(c)
            if i % 2:
                x = x * 2 - 9 if x > 4 else x * 2
            t += x
        if t % 10 == 0:
            return f"{s[:4]} {s[4:8]} {s[8:12]} {s[12:]}"


FEMALE = {"Dilnoza", "Shahnoza", "Malika", "Nodira", "Анна", "Ольга"}


def _agree(last: str, first: str) -> str:
    """Surname gender matches the first name, as in real records (Rashidov/Rashidova, Петров/Петрова)."""
    base = last[:-1] if last.endswith(("a", "а")) else last
    return base + ("а" if base[-1] in "вн" else "a") if first in FEMALE else base


def client(rng, ru: bool) -> dict:
    """One fake client; the k-th call on a fresh Random(seed) gives document k's client."""
    first = rng.choice(FIRST_RU if ru else FIRST_UZ)
    last = _agree(rng.choice(LAST_RU if ru else LAST_UZ), first)
    return {"name": f"{last} {first}", "first": first, "last": last, "pinfl": pinfl(rng),
            "account": f"20208000{rng.randint(1, 9)}{rng.randint(10**7, 10**8 - 1)}{rng.randint(1, 999):03d}",
            "card": card(rng), "phone": f"+998 9{rng.randint(0, 9)} {rng.randint(100, 999)}-"
                                        f"{rng.randint(10, 99)}-{rng.randint(10, 99)}",
            "passport": f"A{rng.choice('ABCD')}{rng.randint(10**6, 10**7 - 1)}",
            "stir": f"3{rng.randint(10**7, 10**8 - 1)}",
            "org": f"\u00ab{rng.choice(['Orient', 'Sayhun', 'Baraka', 'Navro'])} Savdo\u00bb"}


def build(out_dir: Path, n_docs: int = 30, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    seeds = []
    for k in range(n_docs):
        tpl = TEMPLATES[k % len(TEMPLATES)]
        vals = client(rng, "Заемщик" in tpl)
        text = tpl.format(**vals)
        items = [{"type": "Person", "value": vals["name"]}]
        for key, type_ in (("pinfl", "PINFL"), ("account", "Account"), ("card", "Card"), ("phone", "Phone"),
                           ("passport", "Passport"), ("stir", "STIR")):
            if "{" + key + "}" in tpl:
                items.append({"type": type_, "value": vals[key]})
        if "{org}" in tpl:
            items.append({"type": "Org", "value": vals["org"]})
        name = f"doc_{k:02d}.txt"
        (out_dir / name).write_text(text, encoding="utf-8")
        seeds.append({"file": name, "items": items})
    (out_dir / "seeds.json").write_text(json.dumps(seeds, ensure_ascii=False, indent=1), encoding="utf-8")
    return seeds


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "eval/out/synthetic"))
