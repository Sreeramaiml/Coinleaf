"""Transactions: manual entry and bulk insert used by imports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import Connection, select

from finassist.core.errors import ValidationFailed
from finassist.core.ids import new_id
from finassist.db.audit import ChangeSet, insert_many
from finassist.db.repo.accounts import AccountRow
from finassist.db.schema import txn

__all__ = ["NewTxn", "PAYMENT_METHODS", "add_transactions", "existing_fingerprints", "get_transactions"]

PAYMENT_METHODS = ("upi", "card", "netbanking", "neft_imps_rtgs", "nach", "atm", "cash", "cheque", "other")


@dataclass
class NewTxn:
    account: AccountRow
    txn_date: date
    amount_minor: int
    raw_description: str
    value_date: date | None = None
    payment_method: str = "other"
    category_id: str | None = None
    classification_source: str | None = None
    confidence: int | None = None
    needs_review: bool = False
    user_locked: bool = False
    fingerprint: str | None = None
    external_ref: str | None = None
    notes: str | None = None
    orig_amount_minor: int | None = None
    orig_currency: str | None = None
    id: str = field(default_factory=new_id)

    def validate(self) -> None:
        if type(self.amount_minor) is not int:
            raise ValidationFailed("amount must be integer minor units")
        if self.amount_minor == 0:
            raise ValidationFailed("amount cannot be zero")
        if self.payment_method not in PAYMENT_METHODS:
            raise ValidationFailed(f"payment method must be one of {', '.join(PAYMENT_METHODS)}")
        if not self.raw_description.strip():
            raise ValidationFailed("description cannot be empty")

    def row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "account_id": self.account.id,
            "txn_date": self.txn_date,
            "value_date": self.value_date,
            "amount_minor": self.amount_minor,
            "currency": self.account.currency,
            "orig_amount_minor": self.orig_amount_minor,
            "orig_currency": self.orig_currency,
            "raw_description": self.raw_description,
            "category_id": self.category_id,
            "payment_method": self.payment_method,
            "classification_source": self.classification_source,
            "confidence": self.confidence,
            "needs_review": self.needs_review,
            "user_locked": self.user_locked,
            "fingerprint": self.fingerprint,
            "external_ref": self.external_ref,
            "notes": self.notes,
        }


def add_transactions(
    conn: Connection, cs: ChangeSet, items: Sequence[NewTxn], reason: str | None = None
) -> list[str]:
    for t in items:
        t.validate()
    insert_many(conn, cs, txn, [t.row() for t in items], reason=reason)
    return [t.id for t in items]


def existing_fingerprints(conn: Connection, account_id: str, fingerprints: Sequence[str]) -> set[str]:
    found: set[str] = set()
    fps = list(fingerprints)
    for i in range(0, len(fps), 500):
        chunk = fps[i : i + 500]
        q = select(txn.c.fingerprint).where(txn.c.account_id == account_id, txn.c.fingerprint.in_(chunk))
        found.update(r[0] for r in conn.execute(q))
    return found


def get_transactions(conn: Connection, ids: Sequence[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    id_list = list(ids)
    for i in range(0, len(id_list), 500):
        q = select(txn).where(txn.c.id.in_(id_list[i : i + 500]))
        out.extend(dict(r) for r in conn.execute(q).mappings())
    order = {tid: n for n, tid in enumerate(id_list)}
    out.sort(key=lambda r: order[r["id"]])
    return out
