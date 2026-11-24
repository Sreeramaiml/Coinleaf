from datetime import date

import pytest

from finassist.core.periods import FixedClock, PeriodError, fy_label, fy_of, resolve_period


def r(text: str, today: date, **kw: object):  # type: ignore[no-untyped-def]
    return resolve_period(text, FixedClock(today), **kw)  # type: ignore[arg-type]


def test_month_without_year_is_most_recent_started() -> None:
    p = r("October", date(2026, 11, 5))
    assert (p.start, p.end, p.to_date) == (date(2026, 10, 1), date(2026, 10, 31), False)
    p = r("October", date(2026, 10, 15))
    assert (p.start, p.end, p.to_date) == (date(2026, 10, 1), date(2026, 10, 15), True)
    p = r("december", date(2026, 11, 5))
    assert p.start == date(2025, 12, 1)


def test_month_forms() -> None:
    today = date(2026, 11, 5)
    for text in ("oct 2026", "October-2026", "2026-10", "10/2026", "oct'26"):
        assert r(text, today).start == date(2026, 10, 1), text
    assert r("sept", today).start == date(2026, 9, 1)


def test_last_month_across_year_boundary() -> None:
    p = r("last month", date(2027, 1, 1))
    assert (p.start, p.end) == (date(2026, 12, 1), date(2026, 12, 31))


def test_fy_boundaries() -> None:
    assert fy_of(date(2026, 3, 31)) == 2025
    assert fy_of(date(2026, 4, 1)) == 2026
    assert fy_label(2026) == "FY2026-27"
    p = r("FY2025-26", date(2026, 11, 5))
    assert (p.start, p.end) == (date(2025, 4, 1), date(2026, 3, 31))
    p = r("fy 25-26", date(2026, 11, 5))
    assert p.start == date(2025, 4, 1)
    p = r("this fy", date(2026, 11, 5))
    assert (p.start, p.end, p.to_date) == (date(2026, 4, 1), date(2026, 11, 5), True)
    p = r("last fy", date(2026, 11, 5))
    assert p.label == "FY2025-26"


def test_quarters_fy_vs_calendar() -> None:
    today = date(2026, 11, 5)
    q3 = r("Q3", today)
    assert (q3.start, q3.label) == (date(2026, 10, 1), "Q3 FY2026-27 (to date)")
    q2 = r("Q2", today)
    assert (q2.start, q2.end) == (date(2026, 7, 1), date(2026, 9, 30))
    q4 = r("Q4", today)  # Q4 FY2026-27 (Jan–Mar 2027) hasn't started → previous FY's Q4
    assert (q4.start, q4.end) == (date(2026, 1, 1), date(2026, 3, 31))
    cq3 = r("Q3", today, quarter_convention="calendar")
    assert (cq3.start, cq3.end) == (date(2026, 7, 1), date(2026, 9, 30))
    assert r("Q3 FY2025-26", today).start == date(2025, 10, 1)


def test_ranges_and_relative() -> None:
    today = date(2026, 11, 5)
    p = r("2026-09-01 to 2026-10-31", today)
    assert (p.start, p.end) == (date(2026, 9, 1), date(2026, 10, 31))
    p = r("last 3 months", today)
    assert (p.start, p.end) == (date(2026, 8, 1), date(2026, 10, 31))
    p = r("last 7 days", today)
    assert (p.start, p.end) == (date(2026, 10, 30), today)
    assert r("yesterday", today).start == date(2026, 11, 4)
    assert r("today", today).start == today


def test_errors() -> None:
    today = date(2026, 11, 5)
    for bad in ("since diwali", "", "december 2027", "2026-13"):
        with pytest.raises(PeriodError):
            r(bad, today)


def test_pinned_clock_is_deterministic() -> None:
    a = r("last month", date(2026, 11, 5))
    b = r("last month", date(2026, 11, 5))
    assert a == b
