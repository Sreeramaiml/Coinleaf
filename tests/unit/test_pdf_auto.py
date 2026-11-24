"""Automatic layout detection for PDF statements from banks without a built-in profile.

The PDFs are synthetic and drawn in the styles Indian banks commonly use (made-up data,
generic headings, no bank branding)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fpdf import FPDF

from finassist.core.errors import ImportRejected
from finassist.ingest.models import SourceFile
from finassist.ingest.parsers.pdf_auto import AUTO_PROFILE_NAME
from finassist.ingest.pipeline import _detect
from finassist.ingest.profiles import builtin_profiles

Col = tuple[float, str]  # width mm, alignment

# Card-statement fine print that mentions dates and number pairs inside sentences, and a bare
# date on its own line: none of these is a transaction.
FINE_PRINT = [
    ">W.e.f. 1 Dec 2026, a fee of 1% of the transaction amount will be applicable on every transport transaction",
    "# with effect from transactions dated 17-Nov-2011.",
    "Finance Charge on `1,200 from 30th April to 20th May (21 Days)",
    "^ Identified under Merchant Category Code (MCC) 8211, 8220, 8249 and 8299, on best effort",
    "15 Sep 2025 onwards, a revised Cheque Processing Fee of Rs.200 applies",
    "07 Nov 2026",
]


def make_pdf(
    path: Path,
    preamble: list[str],
    header_lines: list[list[str]],
    cols: list[Col],
    rows: list[list[str]],
    trailer: list[str],
) -> Path:
    pdf = FPDF(orientation="L")
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_font("Helvetica", size=9)
    for line in preamble:
        pdf.cell(0, 5, line)
        pdf.ln(5)
    pdf.ln(3)

    def head() -> None:
        pdf.set_font("Helvetica", "B", 8)
        for hl in header_lines:
            for (w, align), text in zip(cols, hl, strict=True):
                pdf.cell(w, 4.5, text, align=align)
            pdf.ln(4.5)
        pdf.set_font("Helvetica", size=8)
        pdf.ln(1)

    head()
    for row in rows:
        if pdf.get_y() > 130:  # a short page, so the longer test statement spans two
            pdf.set_y(-12)
            pdf.cell(0, 5, f"Page {pdf.page_no()}", align="C")
            pdf.add_page()
            head()
        for (w, align), text in zip(cols, row, strict=True):
            pdf.cell(w, 5, text, align=align)
        pdf.ln(5)
    pdf.ln(4)
    for line in trailer:
        pdf.cell(0, 5, line)
        pdf.ln(5)
    pdf.output(str(path))
    return path


def parse(path: Path):  # type: ignore[no-untyped-def]
    f = SourceFile.read(path)
    parser, prof = _detect(f, builtin_profiles())
    return prof, parser.parse(f, prof)


def test_debit_credit_layout_with_month_names_totals_and_notes(tmp_path: Path) -> None:
    cols: list[Col] = [(26, "L"), (26, "L"), (90, "L"), (40, "L"), (28, "R"), (28, "R"), (32, "R")]
    rows = [
        [
            "01 Oct 2026",
            "01 Oct 2026",
            "SALARY CREDIT ACME LTD",
            "N123456789",
            "",
            "1,48,000.00",
            "1,58,000.00",
        ],
        [
            "03 Oct 2026",
            "03 Oct 2026",
            "UPI/634512345678/SWIGGY/swiggy@icici",
            "634512345678",
            "412.00",
            "",
            "1,57,588.00",
        ],
        [
            "05 Oct 2026",
            "05 Oct 2026",
            "IMPS/RENT TO LANDLORD",
            "612345678901",
            "28,000.00",
            "",
            "1,29,588.00",
        ],
    ] + [
        [
            f"{d:02d} Oct 2026",
            f"{d:02d} Oct 2026",
            f"POS GROCERY STORE {d}",
            "",
            "100.00",
            "",
            f"{129588 - 100 * (d - 5):,}.00",
        ]
        for d in range(6, 31)
    ]
    pdf = make_pdf(
        tmp_path / "s1.pdf",
        [
            "Savings Account Statement",
            "Account No : XXXXXXXX7788",
            "Statement Period : 01/10/2026 to 31/10/2026",
        ],
        [["Txn Date", "Value Date", "Description", "Ref No./Cheque No.", "Debit", "Credit", "Balance"]],
        cols,
        rows,
        [
            "Total 30,912.00 1,48,000.00",
            "Customers are requested to verify the entries.",
            "Any discrepancy should be reported within 30 days.",
        ],
    )
    prof, r = parse(pdf)
    assert prof.name == AUTO_PROFILE_NAME and not r.errors, r.errors
    assert len(r.rows) == 28
    assert r.rows[0].amount_minor == 14_800_000 and r.rows[0].txn_date == date(2026, 10, 1)
    assert r.rows[1].amount_minor == -41_200 and r.rows[1].external_ref == "634512345678"
    assert sum(x.amount_minor for x in r.rows) == 14_800_000 - 3_091_200  # the Total row is not a transaction
    assert r.meta.account_last4 == "7788" and (r.meta.period_start, r.meta.period_end) == (
        date(2026, 10, 1),
        date(2026, 10, 31),
    )
    assert any("Layout detected automatically" in n and "Money out ← “Debit”" in n for n in r.notes)
    assert any("skipped" in n for n in r.notes)  # total + disclaimer + page footer
    assert {x.page for x in r.rows} == {1, 2}


def test_two_line_headings_serial_column_and_times(tmp_path: Path) -> None:
    cols: list[Col] = [(14, "L"), (34, "L"), (100, "L"), (34, "R"), (34, "R"), (36, "R")]
    rows = [
        ["1", "02/10/2026 09:15", "NEFT-SALARY-NORTHWIND", "", "95,000.00", "1,05,000.00"],
        ["2", "04/10/2026 18:02", "UPI-ZOMATO-ORDER", "560.50", "", "1,04,439.50"],
        ["3", "09/10/2026 11:47", "ATM WDL KORAMANGALA", "5,000.00", "", "99,439.50"],
    ]
    pdf = make_pdf(
        tmp_path / "s2.pdf",
        ["Statement of Account", "A/c No. 001234569911"],
        [
            ["S No.", "Transaction", "Transaction", "Withdrawal", "Deposit", "Balance"],
            ["", "Date", "Remarks", "Amount (INR)", "Amount (INR)", "(INR)"],
        ],
        cols,
        rows,
        [],
    )
    prof, r = parse(pdf)
    assert prof.name == AUTO_PROFILE_NAME and not r.errors, r.errors
    assert [x.amount_minor for x in r.rows] == [9_500_000, -56_050, -500_000]
    assert [x.txn_date.day for x in r.rows] == [2, 4, 9]
    assert r.rows[2].balance_minor == 9_943_950 and r.meta.account_last4 == "9911"


def test_card_statement_with_cr_suffix(tmp_path: Path) -> None:
    cols: list[Col] = [(30, "L"), (150, "L"), (40, "R")]
    rows = [
        ["03/10/2026", "AMAZON PAY INDIA BANGALORE", "1,250.00"],
        ["10/10/2026", "PAYMENT RECEIVED - THANK YOU", "5,000.00 Cr"],
        ["15/10/2026", "SWIGGY BANGALORE", "389.00"],
    ]
    pdf = make_pdf(
        tmp_path / "s3.pdf",
        ["Credit Card Statement", "Card No : 4XXX XXXX XXXX 5521"],
        [["Date", "Transaction Details", "Amount (in Rs.)"]],
        cols,
        rows,
        FINE_PRINT,
    )
    prof, r = parse(pdf)
    assert prof.amount_mode == "suffix_default_debit" and not r.errors, r.errors
    assert [x.amount_minor for x in r.rows] == [-125_000, 500_000, -38_900]
    assert all(x.payment_method == "card" for x in r.rows) and r.meta.account_last4 == "5521"
    assert any("6 lines that aren't transactions" in n for n in r.notes)


def test_a_transaction_that_cannot_be_read_rejects_the_file(tmp_path: Path) -> None:
    cols: list[Col] = [(30, "L"), (120, "L"), (35, "R"), (35, "R"), (40, "R")]
    rows = [
        ["01/10/2026", "OPENING DEPOSIT", "", "1,000.00", "1,000.00"],
        ["32/10/2026", "BAD DATE ROW", "10.00", "", "990.00"],
    ]
    pdf = make_pdf(
        tmp_path / "s4.pdf",
        ["Statement"],
        [["Date", "Narration", "Debit", "Credit", "Balance"]],
        cols,
        rows,
        [],
    )
    _, r = parse(pdf)
    assert r.errors and "32/10/2026" in r.errors[0].message  # reported, never silently dropped


def test_no_table_lists_the_headings_it_found(tmp_path: Path) -> None:
    pdf = make_pdf(
        tmp_path / "s5.pdf",
        ["Annual summary"],
        [["Date", "Narration", "Points"]],
        [(40, "L"), (120, "L"), (40, "R")],
        [["01/10/2026", "BONUS", "120"]],
        [],
    )
    with pytest.raises(ImportRejected, match="recognised") as e:
        parse(pdf)
    assert "“Date | Narration | Points”" in (e.value.hint or "")


def test_amounts_and_balances_with_d_c_markers(tmp_path: Path) -> None:
    """One Amount column where each amount ends in D (debit) or C (credit), as some banks print."""
    cols: list[Col] = [(30, "L"), (120, "L"), (40, "R"), (40, "R")]
    rows = [
        ["01-10-2026", "NEFT CR SALARY", "52,000.00 C", "52,512.46 C"],
        ["02-10-2026", "UPI DR GROCERIES", "512.46 D", "52,000.00 C"],
        ["03-10-2026", "IMPS TO SELF", "1,00,000.00 D", "48,000.00 D"],
    ]
    pdf = make_pdf(
        tmp_path / "s6.pdf",
        ["Account Statement", "Account Number: XXXXXX4410"],
        [["Tran Date", "Particulars", "Amount", "Balance"]],
        cols,
        rows,
        [],
    )
    prof, r = parse(pdf)
    assert prof.amount_mode == "amount_suffix" and not r.errors, r.errors
    assert [x.amount_minor for x in r.rows] == [5_200_000, -51_246, -10_000_000]
    assert [x.balance_minor for x in r.rows] == [5_251_246, 5_200_000, -4_800_000]
    assert any("Cr/C as money in" in n for n in r.notes) and r.meta.account_last4 == "4410"
