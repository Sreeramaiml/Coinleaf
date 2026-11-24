"""Automatic layout detection for text-PDF statements from banks without a built-in profile.

It looks for the table's heading row by the column names Indian banks commonly print
(Date / Txn Date / Value Date, Narration / Description / Particulars / Remarks,
Chq/Ref No, Withdrawal / Debit, Deposit / Credit, Amount, Balance), including headings
printed over two lines. From that row it builds a mapping profile on the fly; the preview
then says which heading became which column, so the owner checks before importing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from finassist.core.money import DRCR_SUFFIX_RE
from finassist.core.textnorm import collapse_ws, norm_header
from finassist.ingest.profiles import ColumnMap, MappingProfile

__all__ = [
    "AUTO_PROFILE_NAME",
    "GENERIC_DATE_FORMATS",
    "AutoLayout",
    "detect_layout",
    "heading_candidates",
    "role_of",
    "split_cells",
]

AUTO_PROFILE_NAME = "Auto-detected PDF layout"
CELL_GAP = 4.5  # points: a wider horizontal gap between words starts a new cell
GENERIC_DATE_FORMATS = [
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
    "%d-%m-%y",
    "%d.%m.%y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d %b %Y",
    "%d %b %y",
    "%d/%b/%Y",
    "%d/%b/%y",
    "%d %B %Y",
    "%d-%B-%Y",
    "%b %d, %Y",
    "%Y-%m-%d",
]
# One capture group: the last 4 digits after "A/c No", "Account Number", "Card No", masked or not.
LAST4 = r"(?i)(?:a/?c|account|card)(?:\s*(?:no|number|num|#))?\.?\s*[:\-]?\s*[X*x\d\s\-]*?(\d{4})\b"
_DATE_TOKEN = r"\d{1,2}[/\-. ](?:\d{1,2}|[A-Za-z]{3,9})[/\-. ]\d{2,4}"
PERIOD = (
    rf"(?i)(?:from|period|statement\s+period|for\s+the\s+period)\s*:?\s*(?:from\s*)?(?P<start>{_DATE_TOKEN})"
    rf"\s*(?:to|till|-|–)\s*(?P<end>{_DATE_TOKEN})"
)

Word = dict[str, Any]
Line = list[Word]


def role_of(text: str) -> str | None:
    """What a heading means, by the words in it."""
    t = norm_header(text)
    w = set(t.split())
    if not t:
        return None
    if ({"debit", "credit"} <= w) or ({"dr", "cr"} <= w) or t in ("type", "txn type", "transaction type"):
        return "drcr"
    if "value" in w and w & {"date", "dt"}:
        return "value_date"
    if w & {"withdrawal", "withdrawals", "debit", "debits", "paid", "payments"} or t in ("dr", "out"):
        return "debit"
    if w & {"deposit", "deposits", "credit", "credits", "received", "receipts"} or t in ("cr", "in"):
        return "credit"
    if "balance" in w or t in ("bal", "closing bal"):
        return "balance"
    if w & {"date", "dt"}:
        return "date"
    if w & {"amount", "amt"}:
        return "amount"
    if w & {"narration", "description", "particulars", "details", "remarks", "merchant", "transaction"}:
        return "description"
    if w & {"chq", "cheque", "ref", "reference", "utr", "instrument"}:
        return "ref"
    if t in ("s no", "sl no", "sr no", "serial no", "no", "sno", "s n") or w & {"branch", "init", "points"}:
        return "ignore"
    return None


def split_cells(line: Line) -> list[Line]:
    """Words grouped into cells at clear horizontal gaps."""
    chunks: list[Line] = []
    for w in line:
        if chunks and w["x0"] - chunks[-1][-1]["x1"] <= CELL_GAP:
            chunks[-1].append(w)
        else:
            chunks.append([w])
    return chunks


@dataclass
class Cell:
    text: str
    x0: float
    x1: float


@dataclass
class AutoLayout:
    page: int
    first: int  # index of the heading line (first of two for stacked headings)
    last: int
    cells: list[Cell]
    roles: dict[str, int]  # role -> column index


def _cells(line: Line) -> list[Cell]:
    return [
        Cell(collapse_ws(" ".join(w["text"] for w in c)), float(c[0]["x0"]), float(c[-1]["x1"]))
        for c in split_cells(line)
    ]


def _looks_like_data(text: str) -> bool:
    return bool(re.search(_DATE_TOKEN, text) or re.search(r"\d[\d,]*\.\d{2}\b", text))


def _merge(top: list[Cell], below: list[Cell]) -> list[Cell] | None:
    """Stack a second heading line onto the first ("Withdrawal" over "Amount (INR)")."""
    if not below or any(_looks_like_data(c.text) or len(c.text.split()) > 3 for c in below):
        return None
    cells = [Cell(c.text, c.x0, c.x1) for c in top]
    for b in below:
        target = next((c for c in cells if b.x0 <= c.x1 + 6 and b.x1 >= c.x0 - 6), None)
        if target:
            target.text = f"{target.text} {b.text}"
            target.x0, target.x1 = min(target.x0, b.x0), max(target.x1, b.x1)
        else:
            cells.append(Cell(b.text, b.x0, b.x1))
    return sorted(cells, key=lambda c: c.x0)


def _assign(cells: list[Cell]) -> dict[str, int] | None:
    if len(cells) < 3 or any(_looks_like_data(c.text) for c in cells):
        return None
    roles: dict[str, int] = {}
    known = 0
    for i, c in enumerate(cells):
        r = role_of(c.text)
        if r is None:
            continue
        known += 1
        if r == "date" and "date" in roles:
            continue  # the first date column is the transaction date
        roles.setdefault(r, i)
    if "date" not in roles and "value_date" in roles:
        roles["date"] = roles.pop("value_date")
    has_amounts = ("debit" in roles and "credit" in roles) or "amount" in roles
    if "date" not in roles or "description" not in roles or not has_amounts or known < 0.6 * len(cells):
        return None
    return roles


def detect_layout(pages: list[list[Line]]) -> AutoLayout | None:
    for p, lines in enumerate(pages[:3]):
        for i, line in enumerate(lines):
            top = _cells(line)
            roles = _assign(top)
            if roles:
                return AutoLayout(p, i, i, top, roles)
            if i + 1 < len(lines) and abs(lines[i + 1][0]["top"] - line[0]["top"]) <= 18:
                merged = _merge(top, _cells(lines[i + 1]))
                roles = _assign(merged) if merged else None
                if merged and roles:
                    return AutoLayout(p, i, i + 1, merged, roles)
    return None


def heading_candidates(pages: list[list[Line]], limit: int = 3) -> list[str]:
    """Lines that look like headings (two or more known column names), for error messages."""
    out = []
    for lines in pages[:3]:
        for line in lines:
            cells = _cells(line)
            if sum(1 for c in cells if role_of(c.text)) >= 2 and not any(
                _looks_like_data(c.text) for c in cells
            ):
                out.append(" | ".join(c.text for c in cells))
                if len(out) >= limit:
                    return out
    return out


def build_profile(
    layout: AutoLayout, sample: list[list[str]], preamble: str
) -> tuple[MappingProfile, list[str]]:
    """A mapping profile for the detected table, plus notes describing what was detected."""
    headers: list[str] = []
    seen: dict[str, int] = {}
    for c in layout.cells:
        key = norm_header(c.text) or "column"
        seen[key] = seen.get(key, 0) + 1
        headers.append(c.text if seen[key] == 1 else f"{c.text} ({seen[key]})")
    r = layout.roles

    def h(role: str) -> str | None:
        return headers[r[role]] if role in r else None

    if "debit" in r and "credit" in r:
        mode = "debit_credit"
    elif "amount" in r and "drcr" in r:
        mode = "amount_drcr"
    else:
        amounts = [a for row in sample if r["amount"] < len(row) and (a := row[r["amount"]].strip())]
        marked = [a for a in amounts if DRCR_SUFFIX_RE.search(a)]
        if amounts and len(marked) == len(amounts):
            mode = "amount_suffix"  # every amount carries its own Dr/Cr (or D/C) marker
        elif marked or re.search(r"\bcard\b", preamble, re.I):
            mode = "suffix_default_debit"
        else:
            mode = "signed"
    profile = MappingProfile(
        name=AUTO_PROFILE_NAME,
        file_kind="pdf",
        headers=headers,
        columns=ColumnMap(
            date=h("date") or "",
            description=h("description") or "",
            value_date=h("value_date"),
            debit=h("debit") if mode == "debit_credit" else None,
            credit=h("credit") if mode == "debit_credit" else None,
            amount=h("amount") if mode != "debit_credit" else None,
            drcr=h("drcr") if mode == "amount_drcr" else None,
            balance=h("balance"),
            ref=h("ref"),
        ),
        date_formats=GENERIC_DATE_FORMATS,
        amount_mode=mode,  # type: ignore[arg-type]
        default_payment_method="card" if mode == "suffix_default_debit" else "other",
        account_last4_pattern=LAST4,
        period_pattern=PERIOD,
        period_date_formats=GENERIC_DATE_FORMATS,
        summary_labels={"opening": "Opening Balance", "closing": "Closing Balance"},
        ignore_patterns=[
            r"^(grand\s+)?total\b",
            r"^balance\s+(b/?f|brought|carried|c/?f)",
            r"^(opening|closing)\s+balance\b.*\b(dr|cr)\s+count\b",
        ],
    )
    labels = {
        "date": "Date",
        "value_date": "Value date",
        "description": "Description",
        "ref": "Reference",
        "debit": "Money out",
        "credit": "Money in",
        "amount": "Amount",
        "drcr": "Dr/Cr",
        "balance": "Balance",
    }
    mapped = ", ".join(
        f"{labels[k]} ← “{headers[i]}”" for k, i in sorted(r.items(), key=lambda kv: kv[1]) if k in labels
    )
    notes = [f"Layout detected automatically. Columns: {mapped}. Check the totals before importing."]
    if mode == "amount_suffix":
        notes.append("Each amount's Dr/D marker is read as money out and Cr/C as money in.")
    elif mode == "suffix_default_debit":
        notes.append("Amounts without “Cr” are read as money out (card statement style).")
    elif mode == "signed":
        notes.append("Amounts are read as signed numbers: negative = money out.")
    return profile, notes
