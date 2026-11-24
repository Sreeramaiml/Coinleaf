"""Data passed between ingest stages."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

__all__ = ["ParseResult", "ParsedRow", "RowError", "SourceFile", "StatementMeta", "sniff_kind"]


def sniff_kind(data: bytes) -> str:
    """File kind from magic bytes, not the extension (ARCH §7.2)."""
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        return "xlsx"
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls"
    return "text"


@dataclass(frozen=True)
class SourceFile:
    path: Path
    data: bytes
    sha256: str
    kind: str
    # Password for a protected PDF: used to open the file in memory, never stored or logged.
    password: str | None = field(default=None, repr=False, compare=False)

    @classmethod
    def read(cls, path: Path, password: str | None = None) -> SourceFile:
        data = path.read_bytes()
        return cls(
            path=path,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            kind=sniff_kind(data),
            password=password or None,
        )


@dataclass
class ParsedRow:
    row_index: int  # position in the file (1-based line/row number of the record)
    txn_date: date
    amount_minor: int
    raw_description: str
    raw_text: str
    raw_fields: dict[str, str]
    value_date: date | None = None
    balance_minor: int | None = None
    external_ref: str | None = None
    payment_method: str = "other"
    orig_amount_minor: int | None = None
    orig_currency: str | None = None
    page: int | None = None


@dataclass
class StatementMeta:
    period_start: date | None = None
    period_end: date | None = None
    opening_minor: int | None = None
    closing_minor: int | None = None
    account_last4: str | None = None


@dataclass(frozen=True)
class RowError:
    row_index: int
    message: str


@dataclass
class ParseResult:
    rows: list[ParsedRow]
    meta: StatementMeta
    errors: list[RowError] = field(default_factory=list)
    profile_name: str | None = None
    notes: list[str] = field(default_factory=list)  # shown in the preview, e.g. detected columns
