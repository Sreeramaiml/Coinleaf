"""Derived metrics (FR-22, ARCH §9.2). P0 provides income and expense; the rest arrive in P2/P3.

All metrics exclude transfers, split parents and soft-deleted rows, because they are built
on `run_query`. Uncategorised debits count as expense and uncategorised credits as income;
callers report how much of a total is uncategorised.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Connection

from finassist.analytics.query import QueryResult, QuerySpec, TxnFilter, run_query

__all__ = ["expense", "income"]


def expense(conn: Connection, start: date, end: date, *, save: bool = False) -> QueryResult:
    """Money spent: −Σ amount over expense-kind rows (linked refunds reduce it)."""
    spec = QuerySpec(
        filter=TxnFilter(start=start, end=end, kinds=["expense"]), metrics=["sum", "count"], flip_sign=True
    )
    return run_query(conn, spec, save=save, tool="metrics.expense")


def income(conn: Connection, start: date, end: date, *, save: bool = False) -> QueryResult:
    spec = QuerySpec(filter=TxnFilter(start=start, end=end, kinds=["income"]), metrics=["sum", "count"])
    return run_query(conn, spec, save=save, tool="metrics.income")
