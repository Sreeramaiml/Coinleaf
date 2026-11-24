"""Turning statement rows (lists of cell strings, already split into the profile's columns)
into ParsedRows. Shared by the CSV and PDF parsers so both read a statement by exactly the
same rules: the same row from either format gives the same transaction and fingerprint.

Rules: summary rows (opening/closing balance) are recognised by their label in the first
cell; a row with no date, a description and no amount continues the previous row's
description; every other unparseable row is reported (the whole file is then rejected
upstream, PS §13).
"""

from __future__ import annotations

import re
from datetime import date, datetime

from finassist.core.money import DRCR_SUFFIX_RE, MoneyParseError, parse_amount
from finassist.core.textnorm import collapse_ws, norm_header
from finassist.ingest.models import ParsedRow, ParseResult, RowError, StatementMeta
from finassist.ingest.narration import infer_payment_method, upi_reference
from finassist.ingest.profiles import MappingProfile

__all__ = ["parse_date", "parse_table", "read_preamble"]


def parse_date(value: str, formats: list[str]) -> date:
    """The date at the start of the cell; a trailing time ("05/10/2026 10:42") is ignored."""
    v = " ".join(value.split())
    tokens = v.split(" ")
    for candidate in [v] + [" ".join(tokens[:k]) for k in (3, 2, 1) if k < len(tokens)]:
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    raise ValueError(f"date {value!r} does not match {', '.join(formats)}")


def _amount_or_none(value: str, currency: str = "INR") -> int | None:
    v = value.strip()
    if v in ("", "-"):
        return None
    return parse_amount(v, currency)


def read_preamble(meta: StatementMeta, profile: MappingProfile, preamble: str) -> None:
    """Account last-4 digits and statement period from the text above the header row."""
    if profile.account_last4_pattern:
        m = re.search(profile.account_last4_pattern, preamble)
        if m:
            meta.account_last4 = m.group(1)
    if profile.period_pattern:
        m = re.search(profile.period_pattern, preamble)
        if m:
            fmts = profile.period_date_formats or profile.date_formats
            meta.period_start = parse_date(m.group("start"), fmts)
            meta.period_end = parse_date(m.group("end"), fmts)


def parse_table(
    rows: list[tuple[int, list[str]]],
    headers: list[str],
    profile: MappingProfile,
    meta: StatementMeta,
    *,
    joiner: str = ",",
    page_of: dict[int, int] | None = None,
) -> ParseResult:
    """rows: (1-based line number, cells) for every line after the header row."""
    col = {norm_header(h): i for i, h in enumerate(headers)}
    cm = profile.columns

    def idx(name: str | None) -> int | None:
        return None if name is None else col[norm_header(name)]

    i_date, i_desc = idx(cm.date), idx(cm.description)
    i_vdate, i_debit, i_credit = idx(cm.value_date), idx(cm.debit), idx(cm.credit)
    i_amount, i_drcr, i_bal, i_ref = idx(cm.amount), idx(cm.drcr), idx(cm.balance), idx(cm.ref)
    i_orig_amt, i_orig_cur = idx(cm.orig_amount), idx(cm.orig_currency)
    summary = {norm_header(v): k for k, v in profile.summary_labels.items()}
    out: list[ParsedRow] = []
    errors: list[RowError] = []

    def cell(row: list[str], i: int | None) -> str:
        return row[i] if i is not None and i < len(row) else ""

    for n, row in rows:
        if not any(c.strip() for c in row):
            continue
        first = norm_header(row[0]) if row else ""
        if first in summary:
            value = next((c for c in row[1:] if c.strip()), "")
            try:
                amount = parse_amount(value)
            except MoneyParseError as e:
                errors.append(RowError(n, f"summary row {row[0]!r}: {e}"))
                continue
            if summary[first] == "opening":
                meta.opening_minor = amount
            else:
                meta.closing_minor = amount
            continue
        date_cell = cell(row, i_date)
        desc = collapse_ws(cell(row, i_desc))
        if not date_cell.strip():
            amounts_present = any(
                cell(row, i).strip() for i in (i_debit, i_credit, i_amount) if i is not None
            )
            if desc and not amounts_present and out:
                out[-1].raw_description = collapse_ws(out[-1].raw_description + " " + desc)
                out[-1].raw_text += " | " + joiner.join(row)
                continue
            errors.append(RowError(n, "row has no date"))
            continue
        try:
            txn_date = parse_date(date_cell, profile.date_formats)
            vdate = (
                parse_date(cell(row, i_vdate), profile.date_formats) if cell(row, i_vdate).strip() else None
            )
            if profile.amount_mode == "debit_credit":
                d = _amount_or_none(cell(row, i_debit)) or 0
                c = _amount_or_none(cell(row, i_credit)) or 0
                if (d != 0) == (c != 0):
                    raise ValueError("exactly one of withdrawal/deposit must be filled")
                amount = -abs(d) if d else abs(c)
            elif profile.amount_mode == "signed":
                amount = parse_amount(cell(row, i_amount))
            elif profile.amount_mode == "amount_drcr":
                a = abs(parse_amount(cell(row, i_amount)))
                flag = cell(row, i_drcr).strip().lower()
                if flag in ("dr", "d", "debit"):
                    amount = -a
                elif flag in ("cr", "c", "credit"):
                    amount = a
                else:
                    raise ValueError(f"Dr/Cr column has {cell(row, i_drcr)!r}")
            elif profile.amount_mode == "suffix_default_debit":
                raw = cell(row, i_amount)
                mark = DRCR_SUFFIX_RE.search(raw)
                a = abs(parse_amount(raw[: mark.start()] if mark else raw))
                amount = a if mark and mark.group(1).lower() in ("cr", "c") else -a
            else:  # amount_suffix
                raw = cell(row, i_amount)
                if not DRCR_SUFFIX_RE.search(raw):
                    raise ValueError(f"amount {raw!r} has no Dr/Cr marker")
                amount = parse_amount(raw)
            if amount == 0:
                raise ValueError("amount is zero")
            bal = _amount_or_none(cell(row, i_bal)) if i_bal is not None else None
            orig_amt = orig_cur = None
            if i_orig_cur is not None and cell(row, i_orig_cur).strip():
                orig_cur = cell(row, i_orig_cur).strip().upper()
                oa = parse_amount(cell(row, i_orig_amt), orig_cur)
                orig_amt = -abs(oa) if amount < 0 else abs(oa)
        except (ValueError, MoneyParseError) as e:
            errors.append(RowError(n, str(e)))
            continue
        if not desc:
            errors.append(RowError(n, "row has no description"))
            continue
        ref = cell(row, i_ref).strip() or upi_reference(desc)
        if ref and (len(ref.strip("0")) < 6):
            ref = None  # placeholder references such as 0000000 are not identifiers
        out.append(
            ParsedRow(
                row_index=n,
                txn_date=txn_date,
                value_date=vdate,
                amount_minor=amount,
                raw_description=desc,
                raw_text=joiner.join(row),
                raw_fields={h: cell(row, i) for i, h in enumerate(headers)},
                balance_minor=bal,
                external_ref=ref,
                payment_method=infer_payment_method(desc, profile.default_payment_method),
                orig_amount_minor=orig_amt,
                orig_currency=orig_cur,
                page=(page_of or {}).get(n),
            )
        )
    if out and meta.period_start is None:
        meta.period_start = min(r.txn_date for r in out)
        meta.period_end = max(r.txn_date for r in out)
    return ParseResult(out, meta, errors, profile.name)
