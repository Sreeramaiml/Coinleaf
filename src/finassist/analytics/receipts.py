"""Query receipts (FR-28): every figure's filter and contributing transactions, kept so
"show me how you got this" returns exactly the same set later. Receipts are operational
records, not ledger data, so they are written without a change set."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, select

from finassist.core.errors import NotFound
from finassist.db.schema import query_receipt

__all__ = ["Receipt", "load_receipt", "save_receipt"]


@dataclass
class Receipt:
    query_id: str
    tool: str
    spec: dict[str, Any]
    scope_text: str
    result: dict[str, Any]
    txn_ids: list[str]
    created_at: str


def save_receipt(
    conn: Connection,
    query_id: str,
    tool: str,
    spec_json: str,
    scope_text: str,
    sql_hash: str,
    result_json: str,
    txn_ids: list[str],
) -> None:
    conn.execute(
        query_receipt.insert().values(
            query_id=query_id,
            tool=tool,
            spec_json=spec_json,
            scope_text=scope_text,
            sql_hash=sql_hash,
            result_json=result_json,
            txn_ids_blob="\n".join(txn_ids).encode(),
        )
    )


def load_receipt(conn: Connection, query_id: str) -> Receipt:
    r = conn.execute(select(query_receipt).where(query_receipt.c.query_id == query_id)).mappings().first()
    if r is None:
        raise NotFound(
            f"no figure with id {query_id!r}", hint="ids look like q_7f3a9c and appear under answers"
        )
    blob = r["txn_ids_blob"].decode()
    return Receipt(
        query_id=r["query_id"],
        tool=r["tool"],
        spec=json.loads(r["spec_json"]),
        scope_text=r["scope_text"],
        result=json.loads(r["result_json"]),
        txn_ids=blob.split("\n") if blob else [],
        created_at=r["created_at"],
    )
