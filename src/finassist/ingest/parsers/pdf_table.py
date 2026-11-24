"""Text-based PDF statements (FR-1).

How it reads a page: pdfplumber gives every word with its position. Words are grouped into
lines by vertical position. The table's heading row is found either by a built-in profile's
exact headings, or, for any other bank, by automatic detection of common heading names
(parsers/pdf_auto.py). The headings give each column's left and right edge. Each later
line is split into cells at clear gaps between words, and each cell goes to the column it
lines up with: the same left edge (text, dates) or the same right edge (amounts).

Which lines are transactions: a line whose date cell holds a date. A line just below a
transaction with only description text continues that description (wrapped narration).
Headings repeated on later pages, footers, totals, notes and fine print are skipped and
counted in the preview. A line whose date cell starts with something shaped like a date but
can't be read rejects the whole file, so nothing is lost silently (PS §13); prose that only
mentions a date inside a sentence is fine print, not a transaction.

Passwords open the file in memory only. Scanned PDFs (no text layer) are refused with a
clear message: OCR arrives in a later version (ARCH §7.2).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Any, ClassVar

from finassist.core.errors import ImportRejected
from finassist.core.textnorm import collapse_ws, norm_header
from finassist.ingest.models import ParseResult, RowError, SourceFile, StatementMeta
from finassist.ingest.parsers.pdf_auto import (
    AUTO_PROFILE_NAME,
    build_profile,
    detect_layout,
    heading_candidates,
    split_cells,
)
from finassist.ingest.parsers.table import parse_date, parse_table, read_preamble
from finassist.ingest.profiles import MappingProfile

__all__ = ["PdfTableParser"]

Word = dict[str, Any]
Line = list[Word]
DEFAULT_IGNORE = [r"^page \d+( of \d+)?$", r"^this is a computer generated"]
LINE_TOLERANCE = 3.0  # points: words this close vertically are on the same line
CONTINUATION_GAP = 20.0  # points: a wrapped description line sits at most this far below its row
# A cell that starts with something shaped like a date ("32/10/2026", "07 Nov 26"): if it
# doesn't parse, the row is reported, never dropped. Prose (fee notices, footnotes) that merely
# mentions a date somewhere is not a transaction and is skipped.
_DATE_LIKE = re.compile(r"^\d{1,4}[/\-. ](?:\d{1,2}|[A-Za-z]{3,9})[/\-. ]\d{2,4}\b")
_TIME = re.compile(r"^(\d{1,2}:\d{2}(:\d{2})?|am|pm|hrs?)$", re.IGNORECASE)
MAX_DATE_TOKENS = 4  # a date cell holds a date and maybe a time, never a sentence


def _is_date_cell(text: str, formats: list[str]) -> bool:
    """True when the cell is a date, optionally followed by a time, and nothing else."""
    tokens = text.split()
    for k in range(1, min(len(tokens), 3) + 1):
        try:
            parse_date(" ".join(tokens[:k]), formats)
        except ValueError:
            continue
        return all(_TIME.match(t) for t in tokens[k:])
    return False


def _open_pages(file: SourceFile) -> list[list[Line]]:
    """Every page as a list of lines (each a list of words, left to right)."""
    import pdfplumber
    from pdfplumber.utils.exceptions import PdfminerException

    try:
        pdf = pdfplumber.open(io.BytesIO(file.data), password=file.password or "")
    except PdfminerException as e:
        if "Password" in str(e) or "Encrypt" in str(e):
            if file.password:
                raise ImportRejected(
                    "that password didn't open this PDF", hint="check the password and preview again"
                ) from None
            raise ImportRejected(
                "this PDF is password-protected", hint="enter its password and preview again"
            ) from None
        raise ImportRejected("this PDF could not be read; it may be damaged") from None
    except Exception:  # noqa: BLE001 - any other reader failure is a clear refusal, not a crash
        raise ImportRejected("this PDF could not be read; it may be damaged") from None
    pages: list[list[Line]] = []
    chars = 0
    with pdf:
        for page in pdf.pages:
            chars += len(page.chars)
            words = sorted(
                page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False),
                key=lambda w: (round(w["top"]), w["x0"]),
            )
            lines: list[Line] = []
            for w in words:
                if lines and abs(w["top"] - lines[-1][0]["top"]) <= LINE_TOLERANCE:
                    lines[-1].append(w)
                else:
                    lines.append([w])
            pages.append([sorted(line, key=lambda w: w["x0"]) for line in lines])
    if chars == 0:
        raise ImportRejected(
            "this PDF has no text layer; it looks like a scanned image",
            hint="scanned statements need OCR, which arrives in a later version; "
            "download a text PDF or CSV from your bank instead",
        )
    return pages


def _line_text(line: Line) -> str:
    return collapse_ws(" ".join(w["text"] for w in line))


def _header_spans(line: Line, headers: list[str]) -> list[tuple[float, float]] | None:
    """The horizontal span of each heading in this line, or None if the line isn't the header."""
    tokens: list[tuple[str, int]] = []  # (token, index of the word it came from)
    for i, w in enumerate(line):
        for t in norm_header(w["text"]).split():
            tokens.append((t, i))
    spans = []
    pos = 0
    for h in headers:
        want = norm_header(h).split()
        found = None
        for start in range(pos, len(tokens) - len(want) + 1):
            if [t for t, _ in tokens[start : start + len(want)]] == want:
                found = start
                break
        if found is None:
            return None
        first, last = tokens[found][1], tokens[found + len(want) - 1][1]
        spans.append((float(line[first]["x0"]), float(line[last]["x1"])))
        pos = found + len(want)
    if pos != len(tokens):
        return None  # extra words: not exactly the header row
    return spans


@dataclass
class _Layout:
    profile: MappingProfile
    page: int
    first: int  # first heading line
    last: int  # last heading line (headings may be printed over two lines)
    spans: list[tuple[float, float]]
    notes: list[str]


class PdfTableParser:
    kind: ClassVar[str] = "pdf"

    def __init__(self) -> None:
        self._pages_cache: dict[tuple[str, str | None], list[list[Line]]] = {}
        self._layouts: dict[tuple[str, str | None], _Layout] = {}

    def _pages(self, file: SourceFile) -> list[list[Line]]:
        key = (file.sha256, file.password)
        if key not in self._pages_cache:
            self._pages_cache.clear()  # keep at most one file in memory
            self._layouts.clear()
            self._pages_cache[key] = _open_pages(file)
        return self._pages_cache[key]

    # ------------------------------------------------------------------------- detection
    def _builtin(self, pages: list[list[Line]], profile: MappingProfile) -> _Layout | None:
        for p, lines in enumerate(pages):
            for i, line in enumerate(lines):
                spans = _header_spans(line, profile.headers)
                if spans:
                    return _Layout(profile, p, i, i, spans, [])
        return None

    def _auto(self, pages: list[list[Line]]) -> _Layout | None:
        found = detect_layout(pages)
        if found is None:
            return None
        preamble = "\n".join(_line_text(line) for lines in pages[: found.page] for line in lines)
        preamble += "\n" + "\n".join(_line_text(line) for line in pages[found.page][: found.first])
        below = pages[found.page][found.last + 1 : found.last + 15]
        spans = [(c.x0, c.x1) for c in found.cells]
        sample = [self._cells(line, spans) for line in below]
        profile, notes = build_profile(found, sample, preamble)
        return _Layout(profile, found.page, found.first, found.last, spans, notes)

    def match_profile(self, file: SourceFile, profiles: list[MappingProfile]) -> MappingProfile | None:
        if file.kind != "pdf":
            return None
        pages = self._pages(file)  # raises a clear ImportRejected for passwords and scans
        key = (file.sha256, file.password)
        for p in profiles:
            if p.file_kind == "pdf":
                layout = self._builtin(pages, p)
                if layout:
                    self._layouts[key] = layout
                    return p
        layout = self._auto(pages)
        if layout:
            self._layouts[key] = layout
            return layout.profile
        found = heading_candidates(pages)
        hint = (
            "headings found: " + " / ".join(f"“{h}”" for h in found) + ". Send these headings to add support "
            "for this bank, or try the bank's CSV download"
            if found
            else "no table with Date, Description and amount columns was found. If your bank offers a CSV "
            "download, try that"
        )
        raise ImportRejected("this PDF's statement layout isn't recognised yet", hint=hint)

    # ---------------------------------------------------------------------------- parsing
    @staticmethod
    def _cells(line: Line, spans: list[tuple[float, float]]) -> list[str]:
        """Each cell (words split at clear gaps) goes to the column it lines up with."""
        cells = [""] * len(spans)
        for chunk in split_cells(line):
            x0, x1 = chunk[0]["x0"], chunk[-1]["x1"]
            j = min(range(len(spans)), key=lambda k: min(abs(x0 - spans[k][0]), abs(x1 - spans[k][1])))
            cells[j] = (cells[j] + " " + " ".join(w["text"] for w in chunk)).strip()
        return cells

    def parse(self, file: SourceFile, profile: MappingProfile) -> ParseResult:
        pages = self._pages(file)
        layout = self._layouts.get((file.sha256, file.password))
        if layout is None or layout.profile.name != profile.name:
            layout = self._builtin(pages, profile) if profile.name != AUTO_PROFILE_NAME else self._auto(pages)
        if layout is None:
            return ParseResult(
                [], StatementMeta(), [RowError(0, f"header row for {profile.name} not found")], profile.name
            )
        prof = layout.profile
        meta = StatementMeta()
        preamble = [_line_text(line) for lines in pages[: layout.page] for line in lines] + [
            _line_text(line) for line in pages[layout.page][: layout.first]
        ]
        read_preamble(meta, prof, "\n".join(preamble))

        col = {norm_header(h): i for i, h in enumerate(prof.headers)}
        cm = prof.columns
        i_date = col[norm_header(cm.date)]
        i_desc = col[norm_header(cm.description)]
        amount_cols = [col[norm_header(c)] for c in (cm.debit, cm.credit, cm.amount) if c]
        heading_texts = {_line_text(pages[layout.page][k]) for k in range(layout.first, layout.last + 1)}
        ignore = [re.compile(p, re.IGNORECASE) for p in DEFAULT_IGNORE + prof.ignore_patterns]
        labels = {norm_header(v): v for v in prof.summary_labels.values()}

        rows: list[tuple[int, list[str]]] = []
        page_of: dict[int, int] = {}
        skipped = 0
        last_row: tuple[int, float] | None = None  # (page, top) of the last transaction line kept
        n = 0
        for p, lines in enumerate(pages):
            for i, line in enumerate(lines):
                n += 1
                if (p, i) <= (layout.page, layout.last):
                    continue
                text = _line_text(line)
                if (
                    text in heading_texts
                    or _header_spans(line, prof.headers)
                    or any(rx.search(text) for rx in ignore)
                ):
                    continue
                label = next(
                    (lab for key, lab in labels.items() if norm_header(text).startswith(key + " ")), None
                )
                if label is not None:
                    rest = " ".join(text.split()[len(label.split()) :])  # the words after the label
                    rows.append((n, [label, rest]))
                    page_of[n] = p + 1
                    continue
                cells = self._cells(line, layout.spans)
                top = float(line[0]["top"])
                date_cell = cells[i_date].strip()
                date_ok = _is_date_cell(date_cell, prof.date_formats)
                if date_ok and any(c.strip() for k, c in enumerate(cells) if k != i_date):
                    rows.append((n, cells))
                    page_of[n] = p + 1
                    last_row = (p, top)
                    continue
                others = [c for k, c in enumerate(cells) if k != i_desc and c.strip()]
                if (
                    not date_cell
                    and cells[i_desc].strip()
                    and not others
                    and last_row
                    and last_row[0] == p
                    and 0 < top - last_row[1] <= CONTINUATION_GAP
                ):
                    rows.append((n, cells))  # a wrapped description: joined onto the row above
                    page_of[n] = p + 1
                    last_row = (p, top)
                    continue
                if not date_ok and _DATE_LIKE.search(date_cell) and len(date_cell.split()) <= MAX_DATE_TOKENS:
                    rows.append((n, cells))  # looks like a transaction but can't be read: reported
                    page_of[n] = p + 1
                    continue
                skipped += 1  # headings, totals, summaries, notes and other non-transaction text
                last_row = None if any(cells[k].strip() for k in amount_cols) else last_row
        result = parse_table(rows, prof.headers, prof, meta, joiner=" | ", page_of=page_of)
        result.notes = list(layout.notes)
        if skipped:
            result.notes.append(
                f"{skipped} line{'s' if skipped != 1 else ''} that aren't transactions "
                "(headings, totals, notes) were skipped."
            )
        return result
