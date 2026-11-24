"""Accounts. The current balance is never stored: opening balance + Σ amounts (ARCH §6.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Connection, func, select

from finassist.core.errors import Ambiguous, NotFound, ValidationFailed
from finassist.core.ids import new_id
from finassist.core.money import CURRENCY_EXPONENT
from finassist.db.audit import ChangeSet, insert
from finassist.db.schema import account, txn

__all__ = ["AccountRow", "balance_minor", "create_account", "list_accounts", "resolve_account"]

ACCOUNT_TYPES = ("savings", "current", "credit_card", "cash", "wallet", "loan", "investment")


@dataclass(frozen=True)
class AccountRow:
    id: str
    name: str
    nickname: str
    type: str
    institution: str | None
    last4: str | None
    currency: str
    opening_balance_minor: int
    opening_balance_date: date | None
    is_active: bool


def _row(r: Any) -> AccountRow:
    return AccountRow(
        id=r.id,
        name=r.name,
        nickname=r.nickname,
        type=r.type,
        institution=r.institution,
        last4=r.last4,
        currency=r.currency,
        opening_balance_minor=r.opening_balance_minor,
        opening_balance_date=r.opening_balance_date,
        is_active=bool(r.is_active),
    )


def create_account(
    conn: Connection,
    cs: ChangeSet,
    *,
    name: str,
    type: str,
    nickname: str | None = None,
    institution: str | None = None,
    last4: str | None = None,
    currency: str = "INR",
    opening_balance_minor: int = 0,
    opening_balance_date: date | None = None,
) -> AccountRow:
    if type not in ACCOUNT_TYPES:
        raise ValidationFailed(f"account type must be one of {', '.join(ACCOUNT_TYPES)}")
    if currency not in CURRENCY_EXPONENT:
        raise ValidationFailed(f"unknown currency {currency!r}")
    if last4 is not None and not (len(last4) == 4 and last4.isdigit()):
        raise ValidationFailed("last4 must be four digits")
    if isinstance(opening_balance_minor, bool) or not isinstance(opening_balance_minor, int):
        raise ValidationFailed("opening balance must be integer minor units")
    if conn.execute(select(account.c.id).where(account.c.name == name)).first():
        raise ValidationFailed(f"an account named {name!r} already exists")
    acct_id = new_id()
    values = {
        "id": acct_id,
        "name": name,
        "nickname": nickname or name,
        "type": type,
        "institution": institution,
        "last4": last4,
        "currency": currency,
        "opening_balance_minor": opening_balance_minor,
        "opening_balance_date": opening_balance_date,
        "is_active": True,
    }
    insert(conn, cs, account, values)
    return resolve_account(conn, acct_id)


def list_accounts(conn: Connection, include_inactive: bool = False) -> list[AccountRow]:
    q = select(account).order_by(account.c.name)
    if not include_inactive:
        q = q.where(account.c.is_active.is_(True))
    return [_row(r) for r in conn.execute(q)]


def resolve_account(conn: Connection, ref: str) -> AccountRow:
    """By id, exact name, nickname, last4, or a unique case-insensitive name prefix."""
    accts = list_accounts(conn, include_inactive=True)
    key = ref.strip().lower()
    for a in accts:
        if a.id == ref:
            return a
    exact = [a for a in accts if key in (a.name.lower(), a.nickname.lower()) or a.last4 == key]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise Ambiguous(f"{ref!r} matches several accounts", hint=", ".join(a.name for a in exact))
    partial = [a for a in accts if key in a.name.lower() or key in a.nickname.lower()]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise Ambiguous(f"{ref!r} matches several accounts", hint=", ".join(a.name for a in partial))
    raise NotFound(f"no account {ref!r}", hint="accounts: " + ", ".join(a.name for a in accts))


def balance_minor(conn: Connection, account_id: str, as_of: date | None = None) -> int:
    a = resolve_account(conn, account_id)
    q = select(func.coalesce(func.sum(txn.c.amount_minor), 0)).where(
        txn.c.account_id == account_id, txn.c.deleted_at.is_(None), txn.c.is_split_parent.is_(False)
    )
    if as_of is not None:
        q = q.where(txn.c.txn_date <= as_of)
    total = int(conn.execute(q).scalar_one())
    return a.opening_balance_minor + total
