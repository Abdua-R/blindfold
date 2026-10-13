from dataclasses import dataclass

ID_TYPES = ("Person", "Org", "Account", "Card", "PINFL", "STIR", "Phone",
            "Passport", "Address", "Email", "Plate")
ROW_TYPES = frozenset(ID_TYPES)


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    type: str
    value: str
