"""Synthetic text-PDF statements for the demo and tests (made-up data only).

Printed like a real bank statement: landscape A4, headings aligned like their column's
data (amounts right-aligned), long narrations wrapped onto following lines, the header
row repeated on each page, a "Page n of m" footer, summary rows at the end. Optionally
password-protected (AES-128), as Indian banks commonly do.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import EncryptionMethod

__all__ = ["BANK_COLUMNS", "CARD_COLUMNS", "write_statement_pdf"]

# (width in mm, alignment) per column, in header order
BANK_COLUMNS = [(22, "L"), (92, "L"), (30, "L"), (22, "L"), (32, "R"), (32, "R"), (37, "R")]
CARD_COLUMNS = [(30, "L"), (95, "L"), (52, "L"), (28, "R"), (26, "L"), (36, "R")]
ROW_H = 5.0
FOOT_NOTE = "This is a computer generated statement and does not need a signature."


class _StatementPdf(FPDF):
    def footer(self) -> None:
        self.set_y(-10)
        self.set_font("Helvetica", size=7.5)
        self.set_text_color(110, 110, 110)
        self.cell(0, 5, f"Page {self.page_no()} of {{nb}}", align="C")
        self.set_text_color(0, 0, 0)


def write_statement_pdf(
    path: Path,
    preamble: list[str],
    header: list[str],
    rows: list[list[str]],
    summary: list[tuple[str, str]],
    columns: list[tuple[int, str]],
    wrap_col: int,
    password: str | None = None,
) -> Path:
    pdf = _StatementPdf(orientation="L", unit="mm", format="A4")
    pdf.set_creation_date(datetime(2026, 11, 5, tzinfo=UTC))
    pdf.set_auto_page_break(False)
    pdf.set_margins(10, 10, 10)
    pdf.alias_nb_pages()
    pdf.add_page()

    marker, title, *details = preamble
    pdf.set_font("Helvetica", size=7)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(0, 4, marker)
    pdf.ln(5)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 7, title)
    pdf.ln(8)
    pdf.set_font("Helvetica", size=9)
    for line in details:
        pdf.cell(0, 5, line)
        pdf.ln(5)
    pdf.ln(3)

    def header_row() -> None:
        pdf.set_font("Helvetica", "B", 8)
        for (w, align), h in zip(columns, header, strict=True):
            pdf.cell(w, 6, h, align=align)
        pdf.ln(6)
        pdf.line(10, pdf.get_y(), 10 + sum(w for w, _ in columns), pdf.get_y())
        pdf.ln(1)
        pdf.set_font("Helvetica", size=8)

    header_row()
    for row in rows:
        wrapped = pdf.multi_cell(columns[wrap_col][0] - 1, ROW_H, row[wrap_col], dry_run=True, output="LINES")
        lines: list[str] = list(wrapped) if isinstance(wrapped, list) else [row[wrap_col]]
        if pdf.get_y() + ROW_H * len(lines) > pdf.h - 18:
            pdf.add_page()
            header_row()
        for k, text in enumerate(lines):
            for j, ((w, align), cell) in enumerate(zip(columns, row, strict=True)):
                pdf.cell(w, ROW_H, text if j == wrap_col else (cell if k == 0 else ""), align=align)
            pdf.ln(ROW_H)

    if pdf.get_y() + 30 > pdf.h - 18:
        pdf.add_page()
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 9)
    for label, value in summary:
        pdf.cell(50, 6, label)
        pdf.cell(45, 6, value, align="R")
        pdf.ln(6)
    pdf.ln(4)
    pdf.set_font("Helvetica", size=7.5)
    pdf.cell(0, 5, FOOT_NOTE)
    if password:
        pdf.set_encryption(
            owner_password=password + "-owner",
            user_password=password,
            encryption_method=EncryptionMethod.AES_128,
        )
    pdf.output(str(path))
    return path
