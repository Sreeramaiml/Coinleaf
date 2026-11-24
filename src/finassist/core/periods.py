"""Period resolution (ARCH §9.3). The model never does date arithmetic: it passes text,
and this module turns it into explicit inclusive dates relative to an injected clock.

Rules:
- A month without a year means its most recent occurrence that has started
  ("October" asked in November 2026 → October 2026; asked in October → October so far).
- Periods that include today are clipped to today and labelled "to date".
- Quarters follow the configured convention: "fy" (Indian FY, Q1 = Apr–Jun) or "calendar".
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

__all__ = [
    "Clock",
    "FixedClock",
    "PeriodError",
    "ResolvedPeriod",
    "SystemClock",
    "fy_label",
    "fy_of",
    "format_period",
    "resolve_period",
]

QuarterConvention = Literal["fy", "calendar"]


class Clock(Protocol):
    def today(self) -> date: ...


class SystemClock:
    def __init__(self, zone: str = "Asia/Kolkata") -> None:
        self._zone = ZoneInfo(zone)

    def today(self) -> date:
        return datetime.now(self._zone).date()


@dataclass(frozen=True)
class FixedClock:
    fixed: date

    def today(self) -> date:
        return self.fixed


class PeriodError(ValueError):
    """The text could not be resolved to dates; the caller should ask for dates."""


@dataclass(frozen=True)
class ResolvedPeriod:
    start: date
    end: date  # inclusive
    label: str
    to_date: bool = False  # True when clipped to today

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise PeriodError(f"period ends before it starts: {self.start} – {self.end}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def as_dict(self) -> dict[str, object]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "label": self.label,
            "dates": format_period(self.start, self.end),
            "to_date": self.to_date,
        }


MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
MONTHS["sept"] = 9


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def fy_of(d: date, fy_start_month: int = 4) -> int:
    """The calendar year in which d's financial year starts."""
    return d.year if d.month >= fy_start_month else d.year - 1


def fy_label(start_year: int) -> str:
    return f"FY{start_year}-{(start_year + 1) % 100:02d}"


def _fy_bounds(start_year: int, fy_start_month: int) -> tuple[date, date]:
    start = date(start_year, fy_start_month, 1)
    end_month = fy_start_month - 1 or 12
    end_year = start_year + 1 if fy_start_month != 1 else start_year
    return start, _month_end(end_year, end_month)


def format_period(start: date, end: date) -> str:
    """Human dates: '1–31 Oct 2026', '1 Sep – 31 Oct 2026', '1 Apr 2026 – 31 Mar 2027'."""
    if start == end:
        return f"{start.day} {start:%b %Y}"
    if start.year == end.year and start.month == end.month:
        return f"{start.day}–{end.day} {end:%b %Y}"
    if start.year == end.year:
        return f"{start.day} {start:%b} – {end.day} {end:%b %Y}"
    return f"{start.day} {start:%b %Y} – {end.day} {end:%b %Y}"


def _clip(start: date, end: date, label: str, today: date) -> ResolvedPeriod:
    if start > today:
        raise PeriodError(f"{label} has not started yet (today is {today.isoformat()})")
    if end >= today:
        return ResolvedPeriod(start, today, f"{label} (to date)", to_date=True)
    return ResolvedPeriod(start, end, label)


def _parse_iso(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise PeriodError(f"not a date: {s!r}") from None


def _fy_from_token(tok: str) -> int:
    """'2026-27', '26-27', '2026', '26' → FY start year."""
    tok = tok.strip()
    m = re.fullmatch(r"(\d{2}|\d{4})(?:\s*[-/]\s*(\d{2}|\d{4}))?", tok)
    if not m:
        raise PeriodError(f"not a financial year: {tok!r}")
    first = int(m.group(1))
    if first < 100:
        first += 2000
    if m.group(2):
        second = int(m.group(2))
        second = second + 2000 if second < 100 else second
        if second != first + 1:
            raise PeriodError(f"financial year {tok!r} must span consecutive years")
        return first
    # "FY2026" alone is read as the FY ending in that year's March (common Indian usage)
    return first - 1


def resolve_period(
    text: str,
    clock: Clock,
    *,
    fy_start_month: int = 4,
    quarter_convention: QuarterConvention = "fy",
) -> ResolvedPeriod:
    today = clock.today()
    raw = text
    t = " ".join(text.strip().lower().replace(",", " ").split())
    if not t:
        raise PeriodError("empty period")

    # explicit ranges: "2026-10-01 to 2026-10-31", "2026-10-01..2026-10-31"
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\s*(?:to|\.\.|–|-|until|through)\s*(\d{4}-\d{2}-\d{2})", t)
    if m:
        s, e = _parse_iso(m.group(1)), _parse_iso(m.group(2))
        return (
            _clip(s, e, format_period(s, e), today)
            if e >= today
            else ResolvedPeriod(s, e, format_period(s, e))
        )
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
        d = _parse_iso(t)
        return ResolvedPeriod(d, d, format_period(d, d))

    if t in ("today",):
        return ResolvedPeriod(today, today, "today", to_date=True)
    if t == "yesterday":
        yday = today - timedelta(days=1)
        return ResolvedPeriod(yday, yday, "yesterday")
    if t in ("this month", "current month", "mtd", "month to date"):
        return _clip(today.replace(day=1), _month_end(today.year, today.month), f"{today:%B %Y}", today)
    if t in ("last month", "previous month", "prior month"):
        first = today.replace(day=1)
        prev_end = first - timedelta(days=1)
        return ResolvedPeriod(prev_end.replace(day=1), prev_end, f"{prev_end:%B %Y}")
    if t in ("this year", "current year", "ytd", "year to date", "this calendar year"):
        return _clip(date(today.year, 1, 1), date(today.year, 12, 31), str(today.year), today)
    if t in ("last year", "previous year", "last calendar year"):
        y = today.year - 1
        return ResolvedPeriod(date(y, 1, 1), date(y, 12, 31), str(y))
    if t in ("this fy", "this financial year", "current fy", "current financial year", "fytd"):
        fy = fy_of(today, fy_start_month)
        s, e = _fy_bounds(fy, fy_start_month)
        return _clip(s, e, fy_label(fy), today)
    if t in ("last fy", "last financial year", "previous fy", "previous financial year"):
        fy = fy_of(today, fy_start_month) - 1
        s, e = _fy_bounds(fy, fy_start_month)
        return ResolvedPeriod(s, e, fy_label(fy))

    # last N days / weeks / months
    m = re.fullmatch(r"(?:last|past|previous)\s+(\d+)\s+(day|days|week|weeks|month|months)", t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if n <= 0:
            raise PeriodError("count must be positive")
        if unit.startswith("day"):
            return ResolvedPeriod(today - timedelta(days=n - 1), today, f"last {n} days", to_date=True)
        if unit.startswith("week"):
            return ResolvedPeriod(today - timedelta(days=7 * n - 1), today, f"last {n} weeks", to_date=True)
        # last N complete months, not including the current one
        end = today.replace(day=1) - timedelta(days=1)
        y, mo = end.year, end.month
        for _ in range(n - 1):
            mo -= 1
            if mo == 0:
                y, mo = y - 1, 12
        start = date(y, mo, 1)
        return ResolvedPeriod(start, end, f"last {n} months ({format_period(start, end)})")

    # financial year: "fy2026-27", "fy 26-27", "fy2026"
    m = re.fullmatch(r"fy\s*([\d\s/-]+)", t)
    if m:
        fy = _fy_from_token(m.group(1))
        s, e = _fy_bounds(fy, fy_start_month)
        return _clip(s, e, fy_label(fy), today) if e >= today else ResolvedPeriod(s, e, fy_label(fy))

    # quarters: "q3", "q3 fy2026-27", "q3 2026", "q3 fy26-27"
    m = re.fullmatch(r"q([1-4])(?:\s+(fy)?\s*([\d\s/-]+))?", t)
    if m:
        q = int(m.group(1))
        explicit_fy = bool(m.group(2))
        year_tok = m.group(3)
        convention: QuarterConvention = "fy" if explicit_fy else quarter_convention
        if convention == "fy":

            def fy_quarter(fy: int) -> tuple[date, date]:
                sm = (fy_start_month - 1 + 3 * (q - 1)) % 12 + 1
                sy = fy + ((fy_start_month - 1 + 3 * (q - 1)) // 12)
                em = (sm + 2 - 1) % 12 + 1
                ey = sy + (1 if sm + 2 > 12 else 0)
                return date(sy, sm, 1), _month_end(ey, em)

            if year_tok:
                fy = _fy_from_token(year_tok) if explicit_fy or "-" in year_tok else int(year_tok)
                s, e = fy_quarter(fy)
            else:
                fy = fy_of(today, fy_start_month)
                s, e = fy_quarter(fy)
                if s > today:
                    fy -= 1
                    s, e = fy_quarter(fy)
            label = f"Q{q} {fy_label(fy)}"
        else:

            def cal_quarter(y: int) -> tuple[date, date]:
                sm = 3 * (q - 1) + 1
                return date(y, sm, 1), _month_end(y, sm + 2)

            if year_tok:
                y = int(year_tok.strip())
                s, e = cal_quarter(y)
            else:
                y = today.year
                s, e = cal_quarter(y)
                if s > today:
                    y -= 1
                    s, e = cal_quarter(y)
            label = f"Q{q} {y}"
        return _clip(s, e, label, today) if e >= today else ResolvedPeriod(s, e, label)

    # "2026-10", "10/2026"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", t) or None
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return _month(y, mo, today)
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", t)
    if m:
        return _month(int(m.group(2)), int(m.group(1)), today)

    # "october", "oct 2026", "october-2026", "oct'26"
    m = re.fullmatch(r"([a-z]+)\.?(?:[\s'-]+(\d{2}|\d{4}))?", t)
    if m and m.group(1) in MONTHS:
        mo = MONTHS[m.group(1)]
        if m.group(2):
            y = int(m.group(2))
            y = y + 2000 if y < 100 else y
        else:
            y = today.year if mo <= today.month else today.year - 1
        return _month(y, mo, today)

    # a bare year: "2025"
    m = re.fullmatch(r"(\d{4})", t)
    if m:
        y = int(m.group(1))
        s, e = date(y, 1, 1), date(y, 12, 31)
        return _clip(s, e, str(y), today) if e >= today else ResolvedPeriod(s, e, str(y))

    raise PeriodError(
        f"could not understand the period {raw!r}; please give dates (YYYY-MM-DD to YYYY-MM-DD)"
    )


def _month(y: int, mo: int, today: date) -> ResolvedPeriod:
    if not 1 <= mo <= 12:
        raise PeriodError(f"no month {mo}")
    s, e = date(y, mo, 1), _month_end(y, mo)
    label = f"{s:%B %Y}"
    if s > today:
        raise PeriodError(f"{label} has not started yet (today is {today.isoformat()})")
    if e >= today:
        return ResolvedPeriod(s, today, f"{label} (to date)", to_date=True)
    return ResolvedPeriod(s, e, label)
