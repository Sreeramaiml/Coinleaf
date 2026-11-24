"""Money handling: integer minor units only, never floats (NFR-11, ARCH §6.1).

Sign convention (one rule, everywhere): amounts are signed from the account holder's
point of view. Money into the account is positive, money out is negative. On a credit
card a purchase is negative (more owed) and a payment is positive.

Rounding: only derived values (averages, percentages, forecasts, FX) are rounded, always
with ROUND_HALF_EVEN, and only through the helpers in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

__all__ = [
    "CURRENCY_EXPONENT",
    "Money",
    "MoneyParseError",
    "div_round",
    "format_compact_inr",
    "format_inr",
    "format_minor",
    "money_json",
    "parse_amount",
    "percent",
    "round_decimal",
]

CURRENCY_EXPONENT: dict[str, int] = {
    "INR": 2,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "AED": 2,
    "SGD": 2,
    "AUD": 2,
    "CAD": 2,
    "CHF": 2,
    "THB": 2,
    "JPY": 0,
    "KRW": 0,
}

CURRENCY_SYMBOL: dict[str, str] = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}


class MoneyParseError(ValueError):
    """Raised when a string cannot be parsed into an exact amount."""


def _exponent(currency: str) -> int:
    try:
        return CURRENCY_EXPONENT[currency.upper()]
    except KeyError:
        raise MoneyParseError(f"unknown currency {currency!r}") from None


@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount in minor units (paise for INR)."""

    minor: int
    currency: str = "INR"

    def __post_init__(self) -> None:
        # bool is a subclass of int; reject it along with floats and Decimals.
        if type(self.minor) is not int:
            raise TypeError(f"Money.minor must be int minor units, got {type(self.minor).__name__}")
        _exponent(self.currency)

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.minor + other.minor, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.minor - other.minor, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.minor, self.currency)

    def _same_currency(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError("can only combine Money with Money")
        if other.currency != self.currency:
            raise ValueError(f"currency mismatch: {self.currency} vs {other.currency}")

    def to_decimal(self) -> Decimal:
        return Decimal(self.minor).scaleb(-_exponent(self.currency))

    def display(self, paise: bool = True) -> str:
        return format_minor(self.minor, self.currency, paise=paise)


# A trailing debit/credit marker: "Dr"/"Cr", or the single letters "D"/"C" some banks print
# ("512.46 D"). It must follow a digit, space or ")" so ordinary words never match.
DRCR_SUFFIX_RE = re.compile(r"(?<=[\d\s)])(dr|cr|d|c)\.?\s*$", re.IGNORECASE)
_DRCR_RE = DRCR_SUFFIX_RE
_STRIP_RE = re.compile(r"(₹|rs\.?|inr|\s)", re.IGNORECASE)


def parse_amount(text: str, currency: str = "INR") -> int:
    """Parse a statement amount string into signed minor units.

    Accepts Indian or Western digit grouping, an optional currency marker, a leading
    sign, accounting parentheses for negatives, and a trailing Dr/Cr or D/C marker
    (Dr/D = money out = negative). More decimal places than the currency allows is an
    error, never a silent rounding.
    """
    if not isinstance(text, str):
        raise TypeError(f"parse_amount takes a string, got {type(text).__name__}")
    s = text.strip()
    if not s:
        raise MoneyParseError("empty amount")
    sign = 1
    m = _DRCR_RE.search(s)
    if m:
        if m.group(1).lower() in ("dr", "d"):
            sign = -1
        s = s[: m.start()].strip()
    if s.startswith("(") and s.endswith(")"):
        sign = -sign
        s = s[1:-1]
    s = _STRIP_RE.sub("", s)
    s = s.replace("−", "-")  # unicode minus
    if s.startswith("-"):
        sign = -sign
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]
    s = s.replace(",", "")
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        raise MoneyParseError(f"not an amount: {text!r}")
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise MoneyParseError(f"not an amount: {text!r}") from None
    exp = _exponent(currency)
    places = -int(d.as_tuple().exponent) if isinstance(d.as_tuple().exponent, int) else 0
    if places > exp:
        raise MoneyParseError(f"{text!r} has more than {exp} decimal places for {currency}")
    return sign * int(d.scaleb(exp))


def _group_indian(digits: str) -> str:
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def _group_western(digits: str) -> str:
    return f"{int(digits):,}"


def format_minor(minor: int, currency: str = "INR", *, paise: bool = True, symbol: bool = True) -> str:
    """Format minor units for display. INR uses lakh/crore grouping (₹1,23,456.78)."""
    if type(minor) is not int:
        raise TypeError("format_minor takes int minor units")
    exp = _exponent(currency)
    neg = minor < 0
    a = abs(minor)
    if paise or exp == 0:
        whole, frac = divmod(a, 10**exp) if exp else (a, 0)
    else:
        whole = int(round_decimal(Decimal(a).scaleb(-exp), 0))
        frac = 0
    digits = str(whole)
    grouped = _group_indian(digits) if currency.upper() == "INR" else _group_western(digits)
    out = grouped
    if paise and exp:
        out += "." + str(frac).zfill(exp)
    if symbol:
        out = CURRENCY_SYMBOL.get(currency.upper(), currency.upper() + " ") + out
    return ("-" if neg else "") + out


def format_inr(minor: int, *, paise: bool = True, symbol: bool = True) -> str:
    return format_minor(minor, "INR", paise=paise, symbol=symbol)


def round_decimal(value: Decimal, places: int) -> Decimal:
    """The single rounding rule for derived values: ROUND_HALF_EVEN."""
    if not isinstance(value, Decimal):
        raise TypeError("round_decimal takes a Decimal")
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)


def div_round(numerator: int, denominator: int) -> int:
    """Integer division of minor units, rounded half-even (for averages)."""
    if denominator == 0:
        raise ZeroDivisionError("div_round by zero")
    return int(round_decimal(Decimal(numerator) / Decimal(denominator), 0))


def percent(numerator: int, denominator: int, places: int = 1) -> Decimal | None:
    """numerator / denominator × 100, rounded half-even; None when denominator is 0."""
    if denominator == 0:
        return None
    return round_decimal(Decimal(numerator) * 100 / Decimal(denominator), places)


def money_json(minor: int, currency: str = "INR") -> dict[str, object]:
    """The shape every tool uses for an amount: exact minor units plus a display string."""
    return {"minor": minor, "display": format_minor(minor, currency)}


def format_compact_inr(minor: int) -> str:
    """Short Indian form for chart axes and tiles: ₹850, ₹12.3K, ₹1.48L, ₹2.3Cr.
    Rounded half-even for display only; exact figures stay in tables and tooltips."""
    if type(minor) is not int:
        raise TypeError("format_compact_inr takes int minor units")
    sign = "-" if minor < 0 else ""
    rupees = Decimal(abs(minor)) / 100
    for limit, div, suffix in (
        (Decimal(10_000_000), Decimal(10_000_000), "Cr"),
        (Decimal(100_000), Decimal(100_000), "L"),
        (Decimal(1_000), Decimal(1_000), "K"),
    ):
        if rupees >= limit:
            v = rupees / div
            places = 2 if v < 10 and suffix != "K" else 1
            text = str(round_decimal(v, places).normalize())
            if "E" in text:
                text = str(round_decimal(v, 0))
            return f"{sign}₹{text}{suffix}"
    return f"{sign}₹{round_decimal(rupees, 0):.0f}"
