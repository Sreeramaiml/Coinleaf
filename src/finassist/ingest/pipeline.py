"""Import pipeline (ARCH §7.1): detect → parse → fingerprint → PREVIEW, then commit.

`stage_import` writes nothing to the ledger. `commit_import` applies a staged import in
one transaction (BEGIN IMMEDIATE … COMMIT); any exception rolls everything back and the
failure is recorded in a separate small transaction so it is visible (NFR-6).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, select

from finassist.core.errors import ImportRejected, NotFound, ValidationFailed
from finassist.core.ids import new_id
from finassist.core.money import format_minor
from finassist.core.periods import format_period
from finassist.db.audit import insert, insert_many, open_change_set
from finassist.db.repo.accounts import AccountRow, list_accounts, resolve_account
from finassist.db.repo.transactions import NewTxn, add_transactions, existing_fingerprints
from finassist.db.schema import import_source, source_row, statement_balance, transaction_source, txn
from finassist.ingest.fingerprint import fingerprints
from finassist.ingest.models import ParseResult, RowError, SourceFile
from finassist.ingest.parsers.base import Parser
from finassist.ingest.parsers.csv_table import CsvTableParser
from finassist.ingest.parsers.pdf_table import PdfTableParser
from finassist.ingest.profiles import MappingProfile, builtin_profiles

__all__ = ["ImportPreview", "Staging", "commit_import", "stage_import"]

PARSERS: list[Parser] = [CsvTableParser(), PdfTableParser()]
MAX_ERRORS_SHOWN = 10


@dataclass
class ImportPreview:
    import_id: str
    file_name: str
    sha256: str
    status: str  # ready | already_imported | nothing_new | rejected
    account: str | None = None  # full name, shown locally
    account_nickname: str | None = None  # the only account label sent to an LLM
    account_id: str | None = None
    profile: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    rows_parsed: int = 0
    new_rows: int = 0
    already_in_ledger: int = 0
    errors: list[RowError] = field(default_factory=list)
    file_check: str | None = None  # opening + rows = closing, within the file
    debits_minor: int = 0  # signed (≤ 0); shown as a positive 'money out'
    credits_minor: int = 0
    message: str = ""
    notes: list[str] = field(default_factory=list)

    def summary_lines(self, currency: str = "INR") -> list[str]:
        head = f"{self.file_name}"
        if self.account:
            head += f" → {self.account}"
        if self.period_start and self.period_end:
            from datetime import date

            head += f"   period {format_period(date.fromisoformat(self.period_start), date.fromisoformat(self.period_end))}"
        lines = [head]
        if self.status == "already_imported":
            lines.append("  already imported: nothing to do")
            return lines
        if self.status == "rejected":
            lines.append(f"  rejected: {self.message}")
            for e in self.errors[:MAX_ERRORS_SHOWN]:
                lines.append(f"    line {e.row_index}: {e.message}")
            if len(self.errors) > MAX_ERRORS_SHOWN:
                lines.append(f"    … and {len(self.errors) - MAX_ERRORS_SHOWN} more")
            return lines
        lines.append(f"  {self.rows_parsed} rows parsed · 0 parse errors · profile: {self.profile}")
        lines.append(f"  {self.new_rows} new · {self.already_in_ledger} already in ledger (skipped)")
        lines.append(
            f"  money out {format_minor(-self.debits_minor, currency)} · money in "
            f"{format_minor(self.credits_minor, currency)} (new rows)"
        )
        if self.file_check:
            lines.append(f"  {self.file_check}")
        lines += [f"  {n}" for n in self.notes]
        return lines

    def as_dict(self) -> dict[str, Any]:
        return {
            "import_id": self.import_id,
            "file": self.file_name,
            "status": self.status,
            "account": self.account_nickname,
            "profile": self.profile,
            "period": {"start": self.period_start, "end": self.period_end},
            "rows_parsed": self.rows_parsed,
            "new_rows": self.new_rows,
            "already_in_ledger": self.already_in_ledger,
            "money_out_new": {"minor": -self.debits_minor, "display": format_minor(-self.debits_minor)},
            "money_in_new": {"minor": self.credits_minor, "display": format_minor(self.credits_minor)},
            "file_check": self.file_check,
            "errors": [{"line": e.row_index, "message": e.message} for e in self.errors[:MAX_ERRORS_SHOWN]],
            "message": self.message,
            "notes": self.notes,
        }


@dataclass
class _Staged:
    preview: ImportPreview
    file: SourceFile
    account: AccountRow
    result: ParseResult
    fingerprints: list[str]
    skip: set[int]  # indexes into result.rows already in the ledger


class Staging:
    """Staged imports for one session. Nothing here is persisted."""

    def __init__(self) -> None:
        self._items: dict[str, _Staged] = {}

    def put(self, s: _Staged) -> None:
        self._items[s.preview.import_id] = s

    def get(self, import_id: str) -> _Staged:
        try:
            return self._items[import_id]
        except KeyError:
            raise NotFound(f"no staged import {import_id!r}", hint="run import_file first") from None

    def pop(self, import_id: str) -> None:
        self._items.pop(import_id, None)


def _detect(file: SourceFile, profiles: list[MappingProfile]) -> tuple[Any, MappingProfile]:
    for parser in PARSERS:
        prof = parser.match_profile(file, profiles)
        if prof is not None:
            return parser, prof
    if file.kind == "pdf":
        raise ImportRejected(
            "unknown PDF statement layout: no mapping profile matches this PDF's column headings",
            hint="PDF statements from Bank A and Card A are supported now; more banks arrive later. "
            "If your bank offers a CSV download, try that",
        )
    if file.kind != "text":
        raise ImportRejected(
            f"{file.kind.upper()} statements are not supported yet",
            hint="CSV and PDF statements are supported now; Excel (XLSX) arrives in P1",
        )
    raise ImportRejected(
        "unknown statement layout: no mapping profile matches this file's header row",
        hint="guided mapping for new layouts arrives in P1; see docs for adding a profile",
    )


def _pick_account(conn: Connection, account_ref: str | None, last4: str | None) -> AccountRow:
    if account_ref:
        acct = resolve_account(conn, account_ref)
        if last4 and acct.last4 and acct.last4 != last4:
            raise ValidationFailed(
                f"the statement is for an account ending {last4}, but {acct.name} ends {acct.last4}"
            )
        if last4 and not acct.last4:
            owner = [a for a in list_accounts(conn) if a.last4 == last4]
            if owner:
                raise ValidationFailed(
                    f"the statement is for an account ending {last4}, which is {owner[0].name}, "
                    f"not {acct.name}"
                )
        return acct
    if last4:
        matches = [a for a in list_accounts(conn) if a.last4 == last4]
        if len(matches) == 1:
            return matches[0]
    raise ValidationFailed(
        "which account is this statement for?",
        hint="choose the account in the Account box; your accounts: "
        + ", ".join(a.nickname for a in list_accounts(conn)),
    )


def stage_import(
    engine: Engine, staging: Staging, path: Path, account_ref: str | None = None, password: str | None = None
) -> ImportPreview:
    """`password` opens a protected PDF in memory; it is never stored, staged or logged."""
    path = path.expanduser()
    if not path.is_file():
        raise NotFound(f"no file at {path}")
    file = SourceFile.read(path, password=password)
    import_id = "imp_" + new_id()[-8:].lower()
    with engine.connect() as conn:
        parser, profile = _detect(file, builtin_profiles())
        result: ParseResult = parser.parse(file, profile)
        account = _pick_account(conn, account_ref, result.meta.account_last4)
        preview = ImportPreview(
            import_id=import_id,
            file_name=path.name,
            sha256=file.sha256,
            status="ready",
            account=account.name,
            account_nickname=account.nickname,
            notes=list(result.notes),
            account_id=account.id,
            profile=profile.name,
        )
        if result.meta.period_start:
            preview.period_start = result.meta.period_start.isoformat()
        if result.meta.period_end:
            preview.period_end = result.meta.period_end.isoformat()

        already = conn.execute(
            select(import_source.c.id).where(
                import_source.c.sha256 == file.sha256,
                import_source.c.account_id == account.id,
                import_source.c.status == "committed",
            )
        ).first()
        if already:
            preview.status = "already_imported"
            preview.message = "this exact file was already imported for this account"
            return preview
        if result.errors:
            preview.status = "rejected"
            preview.errors = result.errors
            preview.message = f"{len(result.errors)} row(s) could not be parsed; nothing was imported"
            return preview
        if not result.rows:
            preview.status = "rejected"
            preview.message = "no transactions found in the file"
            return preview

        fps = fingerprints(account.id, result.rows)
        existing = existing_fingerprints(conn, account.id, fps)
        refs = [r.external_ref for r in result.rows if r.external_ref]
        existing_refs: set[str] = set()
        for i in range(0, len(refs), 500):
            existing_refs |= {
                x[0]
                for x in conn.execute(
                    select(txn.c.external_ref).where(
                        txn.c.account_id == account.id, txn.c.external_ref.in_(refs[i : i + 500])
                    )
                )
            }

    # A reference repeated inside one file is not an identifier for that file.
    ref_counts: dict[str, int] = {}
    for r in result.rows:
        if r.external_ref:
            ref_counts[r.external_ref] = ref_counts.get(r.external_ref, 0) + 1
    for r in result.rows:
        if r.external_ref and ref_counts[r.external_ref] > 1:
            r.external_ref = None

    skip = {
        i
        for i, (r, fp) in enumerate(zip(result.rows, fps, strict=True))
        if fp in existing or (r.external_ref and r.external_ref in existing_refs)
    }
    preview.rows_parsed = len(result.rows)
    preview.already_in_ledger = len(skip)
    preview.new_rows = len(result.rows) - len(skip)
    preview.debits_minor = sum(
        r.amount_minor for i, r in enumerate(result.rows) if i not in skip and r.amount_minor < 0
    )
    preview.credits_minor = sum(
        r.amount_minor for i, r in enumerate(result.rows) if i not in skip and r.amount_minor > 0
    )
    meta = result.meta
    if meta.opening_minor is not None and meta.closing_minor is not None:
        net = sum(r.amount_minor for r in result.rows)
        expected = meta.opening_minor + net
        cur = account.currency
        if expected == meta.closing_minor:
            preview.file_check = (
                f"file check: opening {format_minor(meta.opening_minor, cur)} + net "
                f"{format_minor(net, cur)} = {format_minor(expected, cur)} ✓ matches closing balance"
            )
        else:
            preview.file_check = (
                f"file check: opening {format_minor(meta.opening_minor, cur)} + net "
                f"{format_minor(net, cur)} = {format_minor(expected, cur)} ✗ closing balance is "
                f"{format_minor(meta.closing_minor, cur)} (difference "
                f"{format_minor(meta.closing_minor - expected, cur)})"
            )
    if preview.new_rows == 0:
        preview.status = "nothing_new"
        preview.message = "every row is already in the ledger"
    staging.put(_Staged(preview, file, account, result, fps, skip))
    return preview


def commit_import(
    engine: Engine, staging: Staging, import_id: str, decisions: dict[str, Any] | None = None
) -> dict[str, Any]:
    staged = staging.get(import_id)
    p = staged.preview
    if p.status not in ("ready", "nothing_new"):
        raise ImportRejected(f"import {import_id} cannot be committed: {p.status}")
    decisions = decisions or {}
    skip_lines = set(decisions.get("skip_lines", []))
    src_id = new_id()
    try:
        with engine.begin() as conn:
            cs = open_change_set(conn, "import", f"import {p.file_name} into {staged.account.name}")
            meta = staged.result.meta
            insert(
                conn,
                cs,
                import_source,
                {
                    "id": src_id,
                    "file_name": p.file_name,
                    "sha256": p.sha256,
                    "source_type": f"statement_{staged.file.kind}"
                    if staged.file.kind == "pdf"
                    else "statement_csv",
                    "account_id": staged.account.id,
                    "mapping_profile": p.profile,
                    "row_count": len(staged.result.rows),
                    "period_start": meta.period_start,
                    "period_end": meta.period_end,
                    "statement_opening_minor": meta.opening_minor,
                    "statement_closing_minor": meta.closing_minor,
                    "status": "committed",
                },
            )
            src_rows, new_txns, links = [], [], []
            for i, (r, fp) in enumerate(zip(staged.result.rows, staged.fingerprints, strict=True)):
                sr_id = new_id()
                src_rows.append(
                    {
                        "id": sr_id,
                        "import_source_id": src_id,
                        "row_index": r.row_index,
                        "page": r.page,
                        "raw_text": r.raw_text,
                        "raw_fields_json": json.dumps(r.raw_fields, ensure_ascii=False),
                        "parsed_ok": True,
                    }
                )
                if i in staged.skip or r.row_index in skip_lines:
                    continue
                t = NewTxn(
                    account=staged.account,
                    txn_date=r.txn_date,
                    value_date=r.value_date,
                    amount_minor=r.amount_minor,
                    raw_description=r.raw_description,
                    payment_method=r.payment_method,
                    fingerprint=fp,
                    external_ref=r.external_ref,
                    orig_amount_minor=r.orig_amount_minor,
                    orig_currency=r.orig_currency,
                )
                new_txns.append(t)
                links.append(
                    {"txn_id": t.id, "source_row_id": sr_id, "role": "primary", "merge_confidence": None}
                )
            insert_many(conn, cs, source_row, src_rows)
            add_transactions(conn, cs, new_txns)
            insert_many(conn, cs, transaction_source, links)
            balances = []
            if meta.opening_minor is not None and meta.period_start:
                balances.append(
                    {
                        "id": new_id(),
                        "account_id": staged.account.id,
                        "import_source_id": src_id,
                        "as_of_date": meta.period_start,
                        "kind": "opening",
                        "balance_minor": meta.opening_minor,
                        "source_row_id": None,
                    }
                )
            if meta.closing_minor is not None and meta.period_end:
                balances.append(
                    {
                        "id": new_id(),
                        "account_id": staged.account.id,
                        "import_source_id": src_id,
                        "as_of_date": meta.period_end,
                        "kind": "closing",
                        "balance_minor": meta.closing_minor,
                        "source_row_id": None,
                    }
                )
            insert_many(conn, cs, statement_balance, balances)
            if decisions.get("_fail_after_insert"):  # test hook: prove rollback leaves nothing
                raise RuntimeError("simulated failure during commit")
    except Exception as exc:
        with engine.begin() as conn:
            cs = open_change_set(conn, "import", f"failed import {p.file_name}")
            insert(
                conn,
                cs,
                import_source,
                {
                    "id": new_id(),
                    "file_name": p.file_name,
                    "sha256": p.sha256,
                    "source_type": f"statement_{staged.file.kind}"
                    if staged.file.kind == "pdf"
                    else "statement_csv",
                    "account_id": staged.account.id,
                    "mapping_profile": p.profile,
                    "row_count": len(staged.result.rows),
                    "status": "failed",
                    "error": type(exc).__name__,
                },
            )
        raise
    staging.pop(import_id)
    return {
        "import_id": import_id,
        "status": "committed",
        "account": staged.account.nickname,
        "inserted": len(new_txns),
        "skipped_existing": p.already_in_ledger,
        "change_set": cs.id,
    }
