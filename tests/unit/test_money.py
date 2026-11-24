from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finassist.core.money import (
    Money,
    MoneyParseError,
    div_round,
    format_inr,
    format_minor,
    parse_amount,
    percent,
    round_decimal,
)


@pytest.mark.parametrize(
    "text,minor",
    [
        ("1,23,456.78", 12345678),
        ("123,456.78", 12345678),
        ("450", 45000),
        ("450.5", 45050),
        ("-450", -45000),
        ("+450.00", 45000),
        ("(450.00)", -45000),
        ("450.00 Dr", -45000),
        ("450.00 Cr", 45000),
        ("450.00Dr", -45000),
        ("₹1,48,000.00", 14800000),
        ("Rs. 99.99", 9999),
        ("INR 10", 1000),
        ("−25.00", -2500),
        ("0.01", 1),
    ],
)
def test_parse_amount(text: str, minor: int) -> None:
    assert parse_amount(text) == minor


@pytest.mark.parametrize("text", ["", "abc", "12.345", "1.2.3", "12,34a", "--5"])
def test_parse_amount_rejects(text: str) -> None:
    with pytest.raises(MoneyParseError):
        parse_amount(text)


def test_three_decimals_rejected_not_rounded() -> None:
    with pytest.raises(MoneyParseError, match="decimal places"):
        parse_amount("10.005")


def test_currency_exponent() -> None:
    assert parse_amount("500", "JPY") == 500
    with pytest.raises(MoneyParseError):
        parse_amount("500.5", "JPY")


def test_money_rejects_float_and_bool() -> None:
    with pytest.raises(TypeError):
        Money(12.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money(True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money(Decimal("1"))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        parse_amount(12.5)  # type: ignore[arg-type]


def test_money_arithmetic_and_currency_mismatch() -> None:
    assert (Money(100) + Money(250)).minor == 350
    assert (-Money(100)).minor == -100
    with pytest.raises(ValueError):
        Money(1, "INR") + Money(1, "USD")


@pytest.mark.parametrize(
    "minor,text",
    [
        (0, "₹0.00"),
        (5, "₹0.05"),
        (99999, "₹999.99"),
        (100000, "₹1,000.00"),
        (12345678, "₹1,23,456.78"),
        (1234567890, "₹1,23,45,678.90"),
        (100000000000, "₹1,00,00,00,000.00"),
        (-4500, "-₹45.00"),
    ],
)
def test_format_inr_lakh_crore_grouping(minor: int, text: str) -> None:
    assert format_inr(minor) == text


def test_format_other_currency_and_whole_rupees() -> None:
    assert format_minor(123456789, "USD") == "$1,234,567.89"
    assert format_inr(12345650, paise=False) == "₹1,23,456"  # .50 rounds half-even to even
    assert format_inr(12345750, paise=False) == "₹1,23,458"


@given(st.integers(min_value=-(10**14), max_value=10**14))
def test_parse_format_round_trip(minor: int) -> None:
    assert parse_amount(format_inr(minor)) == minor
    assert parse_amount(format_inr(minor, symbol=False)) == minor


def test_rounding_is_half_even() -> None:
    assert round_decimal(Decimal("2.5"), 0) == Decimal("2")
    assert round_decimal(Decimal("3.5"), 0) == Decimal("4")
    assert div_round(5, 2) == 2
    assert div_round(7, 2) == 4
    assert percent(1, 3) == Decimal("33.3")
    assert percent(1, 0) is None
    with pytest.raises(TypeError):
        round_decimal(2.5, 0)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text,minor",
    [
        ("512.46 D", -51_246),
        ("12,000.00 C", 1_200_000),
        ("1,00,000.00 C", 10_000_000),
        ("16,720.52 d", -1_672_052),
        ("450.00Dr", -45_000),
        ("450.00 Cr.", 45_000),
    ],
)
def test_single_letter_debit_credit_markers(text: str, minor: int) -> None:
    assert parse_amount(text) == minor


@pytest.mark.parametrize("text", ["D", "12 CD", "abc"])
def test_markers_never_make_words_into_amounts(text: str) -> None:
    with pytest.raises(MoneyParseError):
        parse_amount(text)
