import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DatabaseError, IntegrityError

from finassist.db.audit import insert, open_change_set, update
from finassist.db.engine import WrongKeyError, make_engine
from finassist.db.keys import DB_KEY, FileKeyStore, MemoryKeyStore
from finassist.db.ledger import Ledger, LedgerNotInitialised, init_ledger, open_ledger
from finassist.db.repo.accounts import AccountRow, balance_minor
from finassist.db.repo.categories import TAXONOMY, all_categories
from finassist.db.repo.transactions import NewTxn, add_transactions
from finassist.db.schema import audit_log, category, change_set, txn

SRC = Path(__file__).parents[2] / "src" / "finassist"


def test_file_is_encrypted_and_wrong_key_fails(ledger: Ledger) -> None:
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(ledger.db_path).execute("select * from sqlite_master").fetchall()
    head = ledger.db_path.read_bytes()[:16]
    assert head != b"SQLite format 3\x00"
    wrong = make_engine(ledger.db_path, "0" * 64)
    with pytest.raises(WrongKeyError):
        wrong.connect()
    wrong.dispose()


def test_key_must_be_hex(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make_engine(tmp_path / "x.db", 'not-a-key"; DROP TABLE x;--')


def test_init_twice_changes_nothing(tmp_path: Path) -> None:
    keys = MemoryKeyStore()
    led, created = init_ledger(tmp_path / "l.db", keys)
    with led.engine.connect() as c:
        before = (
            c.execute(select(func.count()).select_from(category)).scalar(),
            c.execute(select(func.count()).select_from(change_set)).scalar(),
            c.execute(select(func.count()).select_from(audit_log)).scalar(),
        )
    led.close()
    led2, created2 = init_ledger(tmp_path / "l.db", keys)
    with led2.engine.connect() as c:
        after = (
            c.execute(select(func.count()).select_from(category)).scalar(),
            c.execute(select(func.count()).select_from(change_set)).scalar(),
            c.execute(select(func.count()).select_from(audit_log)).scalar(),
        )
    led2.close()
    assert (created, created2) == (True, False)
    assert before == after


def test_missing_key_for_existing_db_is_refused(tmp_path: Path) -> None:
    led, _ = init_ledger(tmp_path / "l.db", MemoryKeyStore())
    led.close()
    with pytest.raises(LedgerNotInitialised):
        init_ledger(tmp_path / "l.db", MemoryKeyStore())
    with pytest.raises(LedgerNotInitialised):
        open_ledger(tmp_path / "missing.db", MemoryKeyStore())


def test_file_keystore_refuses_readable_key(tmp_path: Path) -> None:
    ks = FileKeyStore(tmp_path / "keys")
    ks.set(DB_KEY, "a" * 64)
    assert ks.get(DB_KEY) == "a" * 64
    (tmp_path / "keys" / f"{DB_KEY}.key").chmod(0o644)
    with pytest.raises(PermissionError):
        ks.get(DB_KEY)


def test_taxonomy_seeded_with_kinds(ledger: Ledger) -> None:
    with ledger.engine.connect() as c:
        cats = {x.path: x for x in all_categories(c)}
    expected = sum(1 + len(children) for _, _, _, children in TAXONOMY)
    assert len(cats) == expected == 44
    assert all(x.kind in ("income", "expense", "transfer", "investment") for x in cats.values())
    assert cats["Other/Transfer between my accounts"].kind == "transfer"
    assert cats["Other/Cash withdrawal"].kind == "transfer"  # ARCH A-3
    assert cats["Obligations/Credit-card payment"].kind == "expense"  # A-1: untracked card only
    assert cats["Savings & Investments/SIP/Mutual funds"].kind == "investment"
    assert cats["Essentials/Groceries"].is_essential and not cats["Lifestyle/Eating out"].is_essential


def _add(ledger: Ledger, acct: AccountRow, amount: int, **kw: object) -> str:
    with ledger.engine.begin() as c:
        cs = open_change_set(c, "user", "t")
        return add_transactions(
            c,
            cs,
            [
                NewTxn(
                    account=acct,
                    txn_date=date(2026, 10, 2),
                    amount_minor=amount,
                    raw_description="test",
                    **kw,
                )
            ],
        )[0]  # type: ignore[arg-type]


def test_direction_is_generated_and_not_writable(ledger: Ledger, savings: AccountRow) -> None:
    tid = _add(ledger, savings, -500)
    with ledger.engine.begin() as c:
        assert c.execute(select(txn.c.direction).where(txn.c.id == tid)).scalar() == "debit"
    with pytest.raises(DatabaseError), ledger.engine.begin() as c:
        c.execute(text("UPDATE txn SET direction = 'credit' WHERE id = :i"), {"i": tid})


def test_audit_log_is_append_only(ledger: Ledger, savings: AccountRow) -> None:
    _add(ledger, savings, -500)
    with pytest.raises(DatabaseError, match="append-only"), ledger.engine.begin() as c:
        c.execute(text("UPDATE audit_log SET actor = 'agent'"))
    with pytest.raises(DatabaseError, match="append-only"), ledger.engine.begin() as c:
        c.execute(text("DELETE FROM audit_log"))


def test_unique_fingerprint_and_external_ref(ledger: Ledger, savings: AccountRow) -> None:
    _add(ledger, savings, -500, fingerprint="f" * 64, external_ref="UTR123456789")
    with pytest.raises(IntegrityError):
        _add(ledger, savings, -700, fingerprint="f" * 64)
    with pytest.raises(IntegrityError):
        _add(ledger, savings, -700, external_ref="UTR123456789")


def test_currency_must_match_account(ledger: Ledger, savings: AccountRow) -> None:
    with pytest.raises(DatabaseError, match="currency"), ledger.engine.begin() as c:
        cs = open_change_set(c, "user", "t")
        insert(
            c,
            cs,
            txn,
            {
                "id": "X" * 26,
                "account_id": savings.id,
                "txn_date": date(2026, 1, 1),
                "amount_minor": -1,
                "currency": "USD",
                "raw_description": "x",
            },
        )


def test_split_child_amount_is_locked(ledger: Ledger, savings: AccountRow) -> None:
    parent = _add(ledger, savings, -300000)
    with ledger.engine.begin() as c:
        cs = open_change_set(c, "user", "split")
        insert(
            c,
            cs,
            txn,
            {
                "id": "C" * 26,
                "account_id": savings.id,
                "txn_date": date(2026, 10, 2),
                "amount_minor": -240000,
                "currency": "INR",
                "raw_description": "groceries part",
                "split_parent_id": parent,
            },
        )
    with pytest.raises(DatabaseError, match="split"), ledger.engine.begin() as c:
        c.execute(text("UPDATE txn SET amount_minor = -1 WHERE id = :i"), {"i": "C" * 26})


def test_every_write_is_audited_in_same_transaction(ledger: Ledger, savings: AccountRow) -> None:
    tid = _add(ledger, savings, -500)
    with ledger.engine.begin() as c:
        cs = open_change_set(c, "user", "edit note")
        changed = update(c, cs, txn, tid, {"notes": "lunch", "amount_minor": -500})
        assert set(changed) == {"notes"}  # unchanged fields are not logged
        rows = c.execute(select(audit_log).where(audit_log.c.change_set_id == cs.id)).mappings().all()
        assert [(r["field"], r["old_value"], r["new_value"]) for r in rows] == [("notes", None, '"lunch"')]
        created = c.execute(
            select(audit_log).where(audit_log.c.entity_id == tid, audit_log.c.field == "*created*")
        ).first()
        assert created is not None
    # a failed transaction leaves neither the change nor its audit rows
    with pytest.raises(RuntimeError), ledger.engine.begin() as c:
        cs = open_change_set(c, "user", "will fail")
        update(c, cs, txn, tid, {"notes": "dinner"})
        raise RuntimeError("boom")
    with ledger.engine.connect() as c:
        assert c.execute(select(txn.c.notes).where(txn.c.id == tid)).scalar() == "lunch"
        assert (
            c.execute(
                select(func.count()).select_from(change_set).where(change_set.c.summary == "will fail")
            ).scalar()
            == 0
        )


def test_balance_is_opening_plus_sum(ledger: Ledger, savings: AccountRow) -> None:
    _add(ledger, savings, -100_00)
    _add(ledger, savings, 250_00)
    with ledger.engine.connect() as c:
        assert balance_minor(c, savings.id) == 4_122_015 - 100_00 + 250_00


def test_repo_writes_only_through_audit_helpers() -> None:
    """Invariant 5: no raw INSERT/UPDATE/DELETE on ledger tables outside db/audit.py."""
    pattern = re.compile(
        r"\.(insert|update|delete)\(\)|\bexecute\(\s*text\(\s*[\"'](INSERT|UPDATE|DELETE)", re.I
    )
    offenders = []
    for path in (SRC / "db" / "repo").glob("*.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    for path in (SRC / "ingest").rglob("*.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert offenders == []


def test_no_floats_in_money_code() -> None:
    offenders = []
    for folder in ("core", "db", "analytics"):
        for path in (SRC / folder).rglob("*.py"):
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"\bfloat\(", line):
                    offenders.append(f"{folder}/{path.name}:{n}")
    assert offenders == []
