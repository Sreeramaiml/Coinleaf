"""Builds the demo ledger: a separate database and key under <data folder>/demo, seeded
with generator output. The demo key lives in a 0600 file next to the demo database
(synthetic data only), so the demo never touches the Keychain."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from finassist.config.paths import Paths
from finassist.db.audit import open_change_set
from finassist.db.keys import FileKeyStore
from finassist.db.ledger import Ledger, init_ledger
from finassist.db.repo.accounts import create_account, resolve_account
from finassist.db.repo.categories import resolve_category
from finassist.db.repo.transactions import NewTxn, add_transactions
from finassist.demo.generator import ACCOUNTS, CASH, DEMO_TODAY, DemoData, generate
from finassist.ingest.pipeline import Staging, commit_import, stage_import

__all__ = ["DemoResult", "build_demo", "demo_keys"]


@dataclass
class DemoResult:
    ledger: Ledger
    data: DemoData
    imported: list[str]
    left_in_inbox: list[Path]
    today: date


def demo_keys(paths: Paths) -> FileKeyStore:
    return FileKeyStore(paths.demo.home / "keys")


def build_demo(
    paths: Paths, *, reset: bool = False, hold_back_last_card: bool = True, seed: int | None = None
) -> DemoResult:
    demo = paths.demo
    if reset and demo.home.exists():
        shutil.rmtree(demo.home)
    demo.ensure()
    inbox = demo.inbox
    keys = demo_keys(paths)
    ledger, _ = init_ledger(demo.db_file, keys)
    data = generate(inbox, seed=seed) if seed is not None else generate(inbox)

    with ledger.engine.begin() as conn:
        existing = set()
        for a in ACCOUNTS:
            try:
                resolve_account(conn, a.name)
                existing.add(a.name)
            except Exception:
                pass
        if len(existing) != len(ACCOUNTS):
            cs = open_change_set(conn, "system", "create demo accounts")
            for a in ACCOUNTS:
                if a.name not in existing:
                    create_account(
                        conn,
                        cs,
                        name=a.name,
                        nickname=a.nickname,
                        type=a.type,
                        institution=a.institution,
                        last4=a.last4,
                        opening_balance_minor=a.opening_balance_minor,
                        opening_balance_date=a.opening_balance_date,
                    )
            cash = resolve_account(conn, CASH.name)
            cs2 = open_change_set(conn, "user", "demo cash entries")
            add_transactions(
                conn,
                cs2,
                [
                    NewTxn(
                        account=cash,
                        txn_date=date.fromisoformat(e.txn_date),
                        amount_minor=e.amount_minor,
                        raw_description=e.description,
                        payment_method="cash",
                        category_id=resolve_category(conn, e.category).id,
                        classification_source="user",
                        confidence=100,
                        user_locked=True,
                    )
                    for e in data.cash_entries
                ],
                reason="manual entry",
            )

    held = []
    if hold_back_last_card:
        cards = sorted(p for p in data.files if p.name.startswith("card_a_"))
        held = [cards[-1]]
    staging = Staging()
    imported = []
    for f in data.files:
        if f in held:
            continue
        preview = stage_import(ledger.engine, staging, f)
        if preview.status in ("ready",):
            commit_import(ledger.engine, staging, preview.import_id)
            imported.append(f.name)
    return DemoResult(ledger, data, imported, held, DEMO_TODAY)
