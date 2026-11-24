"""Mapping profiles (FR-1, NFR-8): one per statement layout. Built-in profiles live as TOML
files in ingest/banks/; adding a simple layout is adding one file there."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from finassist.core.textnorm import norm_header

__all__ = ["ColumnMap", "MappingProfile", "builtin_profiles", "header_signature"]

BANKS_DIR = Path(__file__).parent / "banks"


class ColumnMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    description: str
    value_date: str | None = None
    debit: str | None = None
    credit: str | None = None
    amount: str | None = None
    drcr: str | None = None
    balance: str | None = None
    ref: str | None = None
    orig_amount: str | None = None
    orig_currency: str | None = None


class MappingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    file_kind: Literal["csv", "xlsx", "xls", "pdf"]
    headers: list[str]
    columns: ColumnMap
    date_formats: list[str]
    # suffix_default_debit: card style, "Cr" = money in, "Dr" or no marker = money out
    amount_mode: Literal["debit_credit", "signed", "amount_drcr", "amount_suffix", "suffix_default_debit"]
    default_payment_method: str = "other"
    account_last4_pattern: str | None = None
    period_pattern: str | None = None  # regex with named groups `start` and `end`
    period_date_formats: list[str] = []
    summary_labels: dict[str, str] = {}  # {"opening": "Opening Balance", "closing": "Closing Balance"}
    ignore_patterns: list[str] = []  # PDF only: regexes for page footers and notes between rows

    @model_validator(mode="after")
    def _check(self) -> MappingProfile:
        cols = self.columns
        needed = {
            "debit_credit": [cols.debit, cols.credit],
            "signed": [cols.amount],
            "amount_drcr": [cols.amount, cols.drcr],
            "amount_suffix": [cols.amount],
            "suffix_default_debit": [cols.amount],
        }[self.amount_mode]
        if any(c is None for c in needed):
            raise ValueError(f"amount_mode {self.amount_mode} needs its amount columns mapped")
        header_keys = {norm_header(h) for h in self.headers}
        for name, col in cols.model_dump().items():
            if col is not None and norm_header(col) not in header_keys:
                raise ValueError(f"column {name} = {col!r} is not in headers")
        for k in self.summary_labels:
            if k not in ("opening", "closing"):
                raise ValueError(f"summary label {k!r} must be opening or closing")
        return self

    @property
    def signature(self) -> str:
        return header_signature(self.headers)


def header_signature(headers: list[str]) -> str:
    keys = sorted(k for k in (norm_header(h) for h in headers) if k)
    return hashlib.sha256("|".join(keys).encode()).hexdigest()


def builtin_profiles() -> list[MappingProfile]:
    out = []
    for p in sorted(BANKS_DIR.glob("*.toml")):
        out.append(MappingProfile.model_validate(tomllib.loads(p.read_text(encoding="utf-8"))))
    return out
