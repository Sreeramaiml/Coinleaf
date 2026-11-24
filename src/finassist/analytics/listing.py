"""Row-level transaction listing for the Transactions page and CSV export (FR-21 search,
NFR-12 export). Uses the same filter builder as `run_query`, so a list and a total over
the same filters always cover the same rows. Transfers are listed (they are real
movements), but they never count in money in/out (invariant 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from sqlalchemy import Connection, case, func

from finassist.analytics.query import QuerySpec, TxnFilter, _build
from finassist.core.textnorm import payee_of
from finassist.db.schema import account as account_t
from finassist.db.schema import category, txn

__all__ = ["Listing", "list_transactions"]

METHOD_LABELS = {
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


@dataclass
class Listing:
    rows: list[dict[str, Any]]
    total: int
    money_in: int
    money_out: int
    currency: str = "INR"


def list_transactions(
    conn: Connection,
    start: date,
    end: date,
    *,
    account: str | None = None,
    direction: Literal["in", "out"] | None = None,
    text: str | None = None,
    offset: int = 0,
    limit: int | None = 50,
) -> Listing:
    f = TxnFilter(
        start=start,
        end=end,
        accounts=[account] if account else None,
        direction="credit" if direction == "in" else "debit" if direction == "out" else None,
        text=text or None,
        include_transfers=True,
    )
    b = _build(conn, QuerySpec(filter=f))
    agg = conn.execute(
        b.base.add_columns(
            func.count(),
            func.coalesce(func.sum(case((b.kind_expr == "income", txn.c.amount_minor), else_=0)), 0),
            func.coalesce(func.sum(case(((b.kind_expr == "expense"), -txn.c.amount_minor), else_=0)), 0),
        )
    ).one()
    q = (
        b.base.add_columns(
            txn.c.id,
            txn.c.txn_date,
            txn.c.amount_minor,
            txn.c.currency,
            txn.c.raw_description,
            txn.c.payment_method,
            txn.c.notes,
            account_t.c.nickname,
            category.c.name.label("category"),
            b.kind_expr.label("kind"),
        )
        .order_by(txn.c.txn_date.desc(), txn.c.id.desc())
        .offset(offset)
    )
    if limit is not None:
        q = q.limit(limit)
    rows = []
    for r in conn.execute(q).mappings():
        rows.append(
            {
                "id": r["id"],
                "date": r["txn_date"],
                "amount": int(r["amount_minor"]),
                "currency": r["currency"],
                "description": r["raw_description"],
                "payee": payee_of(r["raw_description"]),
                "account": r["nickname"],
                "category": r["category"] or "Uncategorised",
                "method": METHOD_LABELS.get(r["payment_method"], r["payment_method"]),
                "kind": r["kind"],
                "notes": r["notes"],
            }
        )
    return Listing(rows, int(agg[0]), int(agg[1]), int(agg[2]))
