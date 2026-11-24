"""Numeric grounding check (PS §8 contract item 1, ARCH §10.5).

Every number in the model's final answer must appear in tool output (this turn or an
earlier one in the session), in the owner's own message, or be a harmless small number
(days of the month, years). Anything else is reported back to the model once; a second
failure withholds the prose and shows the tool results instead.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from finassist.core.money import round_decimal

__all__ = ["AllowedNumbers", "check_text", "extract_numbers"]

_NUM = re.compile(
    r"(?<![\w.])(?P<cur>₹|Rs\.?\s?|INR\s?)?(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?P<suffix>\s?%|\s?(?:lakhs?|lacs?|crores?|cr)\b|k\b)?",
    re.IGNORECASE,
)
_MULT = {
    "lakh": Decimal(100_000),
    "lakhs": Decimal(100_000),
    "lac": Decimal(100_000),
    "lacs": Decimal(100_000),
    "crore": Decimal(10_000_000),
    "crores": Decimal(10_000_000),
    "cr": Decimal(10_000_000),
    "k": Decimal(1_000),
}


def extract_numbers(text: str) -> list[tuple[str, Decimal]]:
    return [(raw, v) for raw, v, _ in _extract(text)]


def _extract(text: str) -> list[tuple[str, Decimal, bool]]:
    """(raw, value, plain). `plain` is False for amounts (₹…), percentages and lakh/crore/k."""
    out = []
    for m in _NUM.finditer(text):
        raw = m.group(0).strip()
        num = m.group("num").replace(",", "")
        if num.endswith("."):
            num = num[:-1]
        try:
            v = Decimal(num)
        except InvalidOperation:
            continue
        suffix = (m.group("suffix") or "").strip().lower()
        if suffix in _MULT:
            v = v * _MULT[suffix]
        plain = not m.group("cur") and not suffix
        out.append((raw, v, plain))
    return out


def _norm(v: Decimal) -> Decimal:
    return v.normalize() if v == v.to_integral_value() else round_decimal(v, 2).normalize()


class AllowedNumbers:
    def __init__(self) -> None:
        self.values: set[Decimal] = set()

    def add_value(self, v: Decimal) -> None:
        v = abs(v)
        self.values.add(_norm(v))
        self.values.add(_norm(round_decimal(v, 0)))  # whole rupees / whole percent
        self.values.add(_norm(round_decimal(v, 1)))

    def add_text(self, text: str) -> None:
        for _, v in extract_numbers(text):
            self.add_value(v)

    def add_obj(self, obj: Any) -> None:
        if isinstance(obj, bool):
            return
        if isinstance(obj, int):
            self.add_value(Decimal(obj))
        elif isinstance(obj, str):
            self.add_text(obj)
        elif isinstance(obj, dict):
            if isinstance(obj.get("minor"), int) and not isinstance(obj.get("minor"), bool):
                self.add_value(Decimal(obj["minor"]) / 100)
            for k, v in obj.items():
                if k == "minor":
                    continue
                self.add_obj(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                self.add_obj(v)

    def allows(self, v: Decimal, plain: bool = False) -> bool:
        """`plain` numbers (no ₹, %, lakh…) may also be days of the month or years."""
        a = abs(v)
        if plain and a == a.to_integral_value() and (a <= 31 or 1990 <= a <= 2100):
            return True
        return _norm(a) in self.values


def check_text(text: str, allowed: AllowedNumbers) -> list[str]:
    """The numbers in `text` that no tool result supports."""
    return [raw for raw, v, plain in _extract(text) if not allowed.allows(v, plain)]
