"""Opening a ledger: keys, engine, migrations, seed data. Used by `init`, `demo` and tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, select

from finassist.db import migrate
from finassist.db.audit import open_change_set
from finassist.db.engine import make_engine
from finassist.db.keys import DB_KEY, KeyStore, new_db_key
from finassist.db.repo.categories import seed_taxonomy
from finassist.db.schema import category

__all__ = ["Ledger", "LedgerNotInitialised", "init_ledger", "open_ledger"]


class LedgerNotInitialised(RuntimeError):
    pass


@dataclass
class Ledger:
    db_path: Path
    engine: Engine
    keys: KeyStore

    def close(self) -> None:
        self.engine.dispose()


def init_ledger(db_path: Path, keys: KeyStore) -> tuple[Ledger, bool]:
    """Create (or open) the ledger, run migrations, seed the taxonomy.
    Returns (ledger, created). Safe to run repeatedly."""
    key = keys.get(DB_KEY)
    created = False
    if key is None:
        if db_path.exists():
            raise LedgerNotInitialised(
                f"{db_path} exists but its key is missing from the key store; restore the key "
                "or move the file aside"
            )
        key = new_db_key()
        keys.set(DB_KEY, key)
        created = True
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(db_path, key)
    migrate.upgrade(engine)
    db_path.chmod(0o600)
    with engine.begin() as conn:
        if conn.execute(select(category.c.id).limit(1)).first() is None:
            cs = open_change_set(conn, "system", "initialise ledger")
            seed_taxonomy(conn, cs)
    return Ledger(db_path, engine, keys), created


def open_ledger(db_path: Path, keys: KeyStore) -> Ledger:
    key = keys.get(DB_KEY)
    if key is None or not db_path.exists():
        raise LedgerNotInitialised(f"no ledger at {db_path}; run `coinleaf init` first")
    engine = make_engine(db_path, key)
    if migrate.current_revision(engine) != migrate.head_revision():
        migrate.upgrade(engine)  # backups before migrations arrive in P1 (db/backup.py)
    return Ledger(db_path, engine, keys)
