"""SQLCipher engine (ARCH §3.1, §6). The 256-bit key comes from a KeyStore and is passed
as a raw key, so there is no per-connection key derivation. Every transaction starts
with BEGIN IMMEDIATE, so a write never interleaves with another writer."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import sqlcipher3
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import QueuePool

__all__ = ["WrongKeyError", "make_engine"]

_HEX64 = re.compile(r"[0-9a-f]{64}")


class WrongKeyError(RuntimeError):
    pass


def make_engine(db_path: Path, key_hex: str) -> Engine:
    if not _HEX64.fullmatch(key_hex):
        raise ValueError("database key must be 64 lowercase hex characters")

    def creator() -> Any:
        conn = sqlcipher3.connect(str(db_path), isolation_level=None, check_same_thread=False)
        conn.execute(f"PRAGMA key = \"x'{key_hex}'\"")
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlcipher3.DatabaseError:
            conn.close()
            raise WrongKeyError(f"cannot open {db_path.name}: wrong key or not a Coinleaf database") from None
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    engine = create_engine(
        "sqlite+pysqlite://",
        module=sqlcipher3,
        creator=creator,
        poolclass=QueuePool,
        pool_size=2,
        max_overflow=2,
    )

    @event.listens_for(engine, "begin")
    def _begin(conn: Any) -> None:
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine
