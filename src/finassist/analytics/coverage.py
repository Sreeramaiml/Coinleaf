"""Data coverage (ARCH §2 A-15): for which dates each account has data. Makes "the data
cannot support this question" (A8) decidable in code.

- Statement-fed accounts are covered by the union of their committed statement periods.
- Accounts with only manual entries (e.g. Cash) are reported as `manual`; they never make
  a query `partial` or `none` on their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Connection, func, select

from finassist.core.periods import format_period
from finassist.db.schema import account, import_source, txn

__all__ = ["AccountCoverage", "CoverageReport", "account_coverage", "coverage_for"]


@dataclass
class AccountCoverage:
    account_id: str
    nickname: str
    kind: str  # statements | manual | no_data
    intervals: list[tuple[date, date]] = field(default_factory=list)
    first_txn: date | None = None
    last_txn: date | None = None

    def covered_days(self, start: date, end: date) -> int:
        days = 0
        for s, e in self.intervals:
            lo, hi = max(s, start), min(e, end)
            if lo <= hi:
                days += (hi - lo).days + 1
        return days

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"account": self.nickname, "source": self.kind}
        if self.intervals:
            d["covered"] = [format_period(s, e) for s, e in self.intervals]
        if self.first_txn and self.last_txn:
            d["transactions_from"] = self.first_txn.isoformat()
            d["transactions_to"] = self.last_txn.isoformat()
        return d


@dataclass
class CoverageReport:
    status: str  # full | partial | none
    accounts: list[dict[str, Any]]
    gaps: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "gaps": self.gaps, "accounts": self.accounts}


def _merge(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1] + timedelta(days=1):
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def account_coverage(conn: Connection, account_ids: list[str] | None = None) -> list[AccountCoverage]:
    q = select(account.c.id, account.c.nickname).where(account.c.is_active.is_(True)).order_by(account.c.name)
    if account_ids:
        q = q.where(account.c.id.in_(account_ids))
    out = []
    for a in conn.execute(q):
        periods = [
            (r[0], r[1])
            for r in conn.execute(
                select(import_source.c.period_start, import_source.c.period_end).where(
                    import_source.c.account_id == a.id,
                    import_source.c.status == "committed",
                    import_source.c.period_start.is_not(None),
                    import_source.c.period_end.is_not(None),
                )
            )
        ]
        lo, hi = conn.execute(
            select(func.min(txn.c.txn_date), func.max(txn.c.txn_date)).where(
                txn.c.account_id == a.id, txn.c.deleted_at.is_(None)
            )
        ).one()
        kind = "statements" if periods else ("manual" if lo else "no_data")
        out.append(AccountCoverage(a.id, a.nickname, kind, _merge(periods), lo, hi))
    return out


def coverage_for(
    conn: Connection, start: date, end: date, account_ids: list[str] | None = None
) -> CoverageReport:
    accts = account_coverage(conn, account_ids)
    total_days = (end - start).days + 1
    statuses = []
    gaps: list[str] = []
    for a in accts:
        if a.kind != "statements":
            continue
        covered = a.covered_days(start, end)
        if covered >= total_days:
            statuses.append("full")
        elif covered > 0:
            statuses.append("partial")
            gaps.append(f"{a.nickname} covers only {covered} of {total_days} days")
        else:
            statuses.append("none")
            gaps.append(f"{a.nickname} has no statement for {format_period(start, end)}")
    manual_has_data = any(
        a.kind == "manual" and a.first_txn and a.last_txn and a.first_txn <= end and a.last_txn >= start
        for a in accts
    )
    if not statuses:
        status = "partial" if manual_has_data else "none"
        gaps.append(
            "only manual entries so far; no statements have been imported"
            if manual_has_data
            else "no statements have been imported"
        )
    elif all(s == "full" for s in statuses):
        status = "full"
    elif all(s == "none" for s in statuses) and not manual_has_data:
        status = "none"
    else:
        status = "partial"
    return CoverageReport(status, [a.as_dict() for a in accts], gaps)
