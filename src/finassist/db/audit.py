"""Change sets and the append-only audit log (NFR-7, ARCH §6.3 invariant 5).

Every write to a ledger table goes through `insert`, `insert_many`, `update` or
`soft_delete` here. Each takes a ChangeSet and writes the audit rows in the same
database transaction as the change, so there is no write path without a record.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Table, select

from finassist.core.ids import new_id
from finassist.db.schema import audit_log, change_set

__all__ = ["ChangeSet", "insert", "insert_many", "open_change_set", "soft_delete", "update", "utcnow"]

ACTORS = ("user", "rule", "agent", "import", "system")


@dataclass(frozen=True)
class ChangeSet:
    id: str
    actor: str
    summary: str


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _json(v: Any) -> str | None:
    if v is None:
        return None
    return json.dumps(v, default=str, ensure_ascii=False, sort_keys=True)


def _entity_id(table: Table, values: Mapping[str, Any]) -> str:
    pk = [c.name for c in table.primary_key.columns]
    return "|".join(str(values.get(k)) for k in pk)


def open_change_set(conn: Connection, actor: str, summary: str) -> ChangeSet:
    if actor not in ACTORS:
        raise ValueError(f"unknown actor {actor!r}")
    cs = ChangeSet(id=new_id(), actor=actor, summary=summary)
    conn.execute(change_set.insert().values(id=cs.id, actor=actor, summary=summary))
    return cs


def insert(
    conn: Connection, cs: ChangeSet, table: Table, values: Mapping[str, Any], reason: str | None = None
) -> None:
    insert_many(conn, cs, table, [values], reason=reason)


def insert_many(
    conn: Connection,
    cs: ChangeSet,
    table: Table,
    rows: Sequence[Mapping[str, Any]],
    reason: str | None = None,
) -> None:
    if not rows:
        return
    conn.execute(table.insert(), [dict(r) for r in rows])
    conn.execute(
        audit_log.insert(),
        [
            {
                "change_set_id": cs.id,
                "entity": table.name,
                "entity_id": _entity_id(table, r),
                "field": "*created*",
                "old_value": None,
                "new_value": _json(dict(r)),
                "actor": cs.actor,
                "reason": reason,
            }
            for r in rows
        ],
    )


def update(
    conn: Connection,
    cs: ChangeSet,
    table: Table,
    row_id: str,
    changes: Mapping[str, Any],
    reason: str | None = None,
) -> dict[str, tuple[Any, Any]]:
    """Update one row by primary key `id`; audit each field that actually changes.
    Returns {field: (old, new)} for the changed fields."""
    if "id" not in table.c:
        raise ValueError(f"{table.name} has no id column")
    old = conn.execute(select(table).where(table.c.id == row_id)).mappings().first()
    if old is None:
        raise LookupError(f"{table.name} {row_id} not found")
    changed = {k: (old[k], v) for k, v in changes.items() if old[k] != v}
    if not changed:
        return {}
    values: dict[str, Any] = {k: v for k, (_, v) in changed.items()}
    if "updated_at" in table.c:
        values["updated_at"] = utcnow()
    conn.execute(table.update().where(table.c.id == row_id).values(**values))
    conn.execute(
        audit_log.insert(),
        [
            {
                "change_set_id": cs.id,
                "entity": table.name,
                "entity_id": row_id,
                "field": k,
                "old_value": _json(o),
                "new_value": _json(n),
                "actor": cs.actor,
                "reason": reason,
            }
            for k, (o, n) in changed.items()
        ],
    )
    return changed


def soft_delete(
    conn: Connection, cs: ChangeSet, table: Table, row_ids: Iterable[str], reason: str | None = None
) -> int:
    if "deleted_at" not in table.c:
        raise ValueError(f"{table.name} does not support soft delete")
    n = 0
    now = utcnow()
    for rid in row_ids:
        if update(conn, cs, table, rid, {"deleted_at": now}, reason=reason):
            n += 1
    return n
