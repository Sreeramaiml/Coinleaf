"""Dashboard data (FR-22, FR-30). Every total comes from `run_query`, the single aggregation
primitive, so the dashboard always agrees with the CLI and the tests.

Definitions (ARCH §9.2): money in = income-kind rows; money out = expense-kind rows (shown
positive); saved = in − out; savings rate = saved ÷ in. Transfers are excluded.
Balances: opening balance + every transaction, as of the dashboard's "today".
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, func, select

from finassist.analytics.coverage import CoverageReport, coverage_for
from finassist.analytics.query import GroupBy, QuerySpec, TxnFilter, run_query
from finassist.core.money import percent
from finassist.core.periods import ResolvedPeriod, format_period
from finassist.core.textnorm import payee_of
from finassist.db.repo.accounts import balance_minor, list_accounts, resolve_account
from finassist.db.repo.transactions import get_transactions
from finassist.db.schema import txn

__all__ = [
    "LIQUID_TYPES",
    "PAYMENT_LABELS",
    "Dashboard",
    "Totals",
    "build_dashboard",
    "data_months",
    "previous_period",
]

PAYMENT_LABELS = {
    "upi": "UPI",
    "card": "Card",
    "netbanking": "Net banking",
    "neft_imps_rtgs": "Bank transfer",
    "nach": "Auto-debit",
    "atm": "ATM",
    "cash": "Cash",
    "cheque": "Cheque",
    "other": "Other",
}
LIQUID_TYPES = ("savings", "current", "cash", "wallet")


@dataclass
class Totals:
    money_in: int
    money_out: int
    count_in: int
    count_out: int

    @property
    def saved(self) -> int:
        return self.money_in - self.money_out

    @property
    def savings_rate(self) -> Decimal | None:
        return percent(self.saved, self.money_in) if self.money_in > 0 else None


@dataclass
class AccountBalance:
    name: str
    type: str
    balance: int


@dataclass
class Dashboard:
    period: ResolvedPeriod
    previous: ResolvedPeriod
    account: str | None
    today: date
    now: Totals
    before: Totals
    balances: list[AccountBalance]
    monthly: list[dict[str, Any]]  # {month: 'YYYY-MM', in: int, out: int}
    by_method: list[tuple[str, int]]
    top_payees: list[tuple[str, int, int]]  # (payee, money out, count)
    recent: list[dict[str, Any]]
    daily: list[tuple[date, int]] = field(default_factory=list)  # a single-month period only
    coverage: CoverageReport | None = None

    @property
    def available(self) -> int:
        return sum(b.balance for b in self.balances if b.type in LIQUID_TYPES)

    @property
    def card_owed(self) -> int:
        return -sum(b.balance for b in self.balances if b.type == "credit_card" and b.balance < 0)

    @property
    def net_worth(self) -> int:
        return sum(b.balance for b in self.balances)


def _spec(
    start: date,
    end: date,
    acct_id: str | None,
    kind: str,
    group: list[GroupBy] | None = None,
    flip: bool = False,
) -> QuerySpec:
    return QuerySpec(
        filter=TxnFilter(
            start=start,
            end=end,
            kinds=[kind],  # type: ignore[list-item]
            accounts=[acct_id] if acct_id else None,
        ),
        group_by=group or [],
        metrics=["sum", "count"],
        flip_sign=flip,
    )


def _totals(conn: Connection, start: date, end: date, acct_id: str | None) -> Totals:
    out = run_query(conn, _spec(start, end, acct_id, "expense", flip=True), save=False)
    inc = run_query(conn, _spec(start, end, acct_id, "income"), save=False)
    return Totals(
        int(inc.totals["sum"]["minor"]),
        int(out.totals["sum"]["minor"]),
        int(inc.totals["count"]),
        int(out.totals["count"]),
    )


def previous_period(p: ResolvedPeriod) -> ResolvedPeriod:
    """The period of the same shape just before `p` (the previous month for a whole month)."""
    whole_month = p.start.day == 1 and (p.end + timedelta(days=1)).day == 1
    if whole_month and (p.start.year, p.start.month) == (p.end.year, p.end.month):
        end = p.start - timedelta(days=1)
        start = end.replace(day=1)
        return ResolvedPeriod(start, end, f"{start:%B %Y}")
    days = (p.end - p.start).days + 1
    end = p.start - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return ResolvedPeriod(start, end, format_period(start, end))


def _month_add(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


def data_months(conn: Connection) -> list[str]:
    """Months ('YYYY-MM') that have any transactions, newest first."""
    month = func.strftime("%Y-%m", txn.c.txn_date)
    rows = conn.execute(
        select(month).where(txn.c.deleted_at.is_(None)).group_by(month).order_by(month.desc())
    )
    return [r[0] for r in rows]


def build_dashboard(
    conn: Connection, period: ResolvedPeriod, today: date, account: str | None = None, recent: int = 6
) -> Dashboard:
    acct_id = resolve_account(conn, account).id if account else None
    prev = previous_period(period)
    now = _totals(conn, period.start, period.end, acct_id)
    before = _totals(conn, prev.start, prev.end, acct_id)

    accts = list_accounts(conn)
    if acct_id:
        accts = [a for a in accts if a.id == acct_id]
    balances = [AccountBalance(a.nickname, a.type, balance_minor(conn, a.id, as_of=today)) for a in accts]

    # 12 months of money in / out, ending with the period's last month
    last_month = period.end.replace(day=1)
    first_month = _month_add(last_month, -11)
    end = date(last_month.year, last_month.month, calendar.monthrange(last_month.year, last_month.month)[1])
    out_m = run_query(conn, _spec(first_month, end, acct_id, "expense", ["month"], flip=True), save=False)
    in_m = run_query(conn, _spec(first_month, end, acct_id, "income", ["month"]), save=False)
    outs = {r["key"]["month"]: int(r["sum"]["minor"]) for r in out_m.rows}
    ins = {r["key"]["month"]: int(r["sum"]["minor"]) for r in in_m.rows}
    monthly = []
    m = first_month
    while m <= last_month:
        key = f"{m:%Y-%m}"
        monthly.append({"month": key, "in": ins.get(key, 0), "out": outs.get(key, 0)})
        m = _month_add(m, 1)

    spend = run_query(
        conn, _spec(period.start, period.end, acct_id, "expense", ["payment_method"], flip=True), save=False
    )
    by_method = sorted(
        (
            (
                PAYMENT_LABELS.get(str(r["key"]["payment_method"]), str(r["key"]["payment_method"])),
                int(r["sum"]["minor"]),
            )
            for r in spend.rows
            if int(r["sum"]["minor"]) > 0
        ),
        key=lambda x: -x[1],
    )

    # payees: grouped in Python from the same transaction set the totals used
    payee_sum: dict[str, int] = defaultdict(int)
    payee_n: dict[str, int] = defaultdict(int)
    for t in get_transactions(conn, spend.txn_ids):
        p = payee_of(t["raw_description"])
        payee_sum[p] += -int(t["amount_minor"])
        payee_n[p] += 1
    top_payees = sorted(((p, s, payee_n[p]) for p, s in payee_sum.items() if s > 0), key=lambda x: -x[1])[:6]

    daily: list[tuple[date, int]] = []
    if (period.start.year, period.start.month) == (period.end.year, period.end.month):
        by_d = run_query(
            conn, _spec(period.start, period.end, acct_id, "expense", ["day"], flip=True), save=False
        )
        per_day = {str(r["key"]["day"]): int(r["sum"]["minor"]) for r in by_d.rows}
        d = period.start.replace(day=1)
        last = date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
        while d <= last:
            daily.append((d, per_day.get(d.isoformat(), 0)))
            d += timedelta(days=1)

    from finassist.analytics.listing import list_transactions

    recent_rows = list_transactions(conn, period.start, period.end, account=account, limit=recent).rows
    cov = coverage_for(conn, period.start, period.end, [acct_id] if acct_id else None)
    return Dashboard(
        period,
        prev,
        account,
        today,
        now,
        before,
        balances,
        monthly,
        by_method,
        top_payees,
        recent_rows,
        daily,
        cov,
    )
