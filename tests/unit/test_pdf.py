"""PDF statements: same transactions as the CSV twin, passwords, scans, idempotency, web upload."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fpdf import FPDF
from sqlalchemy import select

from finassist.config.paths import Paths
from finassist.core.errors import ImportRejected
from finassist.demo.generator import DEMO_PDF_PASSWORD, generate
from finassist.demo.setup import build_demo
from finassist.ingest.models import SourceFile
from finassist.ingest.pipeline import PARSERS, Staging, _detect, commit_import, stage_import
from finassist.ingest.profiles import builtin_profiles

FIELDS = (
    "txn_date",
    "value_date",
    "amount_minor",
    "raw_description",
    "balance_minor",
    "external_ref",
    "orig_amount_minor",
    "orig_currency",
    "payment_method",
)


@pytest.fixture(scope="module")
def files(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("pdfs")
    generate(d)
    return d


def parse(path: Path, password: str | None = None):  # type: ignore[no-untyped-def]
    f = SourceFile.read(path, password=password)
    parser, prof = _detect(f, builtin_profiles())
    return prof.name, parser.parse(f, prof)


@pytest.mark.parametrize(
    "name,password,profile",
    [
        ("bank_a_2026-10", DEMO_PDF_PASSWORD, "Bank A savings PDF"),
        ("card_a_2026-10", None, "Card A credit card PDF"),
    ],
)
def test_pdf_gives_exactly_the_csv_transactions(
    files: Path, name: str, password: str | None, profile: str
) -> None:
    prof, pdf = parse(files / f"{name}.pdf", password)
    _, csv = parse(files / f"{name}.csv")
    assert prof == profile and not pdf.errors
    assert [[getattr(r, f) for f in FIELDS] for r in pdf.rows] == [
        [getattr(r, f) for f in FIELDS] for r in csv.rows
    ]
    assert (
        pdf.meta.account_last4,
        pdf.meta.period_start,
        pdf.meta.period_end,
        pdf.meta.opening_minor,
        pdf.meta.closing_minor,
    ) == (
        csv.meta.account_last4,
        csv.meta.period_start,
        csv.meta.period_end,
        csv.meta.opening_minor,
        csv.meta.closing_minor,
    )
    assert {r.page for r in pdf.rows} == {1, 2}  # spans two pages: repeated header and footer skipped
    assert any(
        "Bank/Payment from Phone" in r.raw_description or "BANGALORE IN" in r.raw_description
        for r in pdf.rows
    )  # wrapped descriptions are joined back together


def test_password_missing_or_wrong(files: Path) -> None:
    with pytest.raises(ImportRejected, match="password-protected") as e:
        parse(files / "bank_a_2026-10.pdf")
    assert "enter its password" in (e.value.hint or "")
    with pytest.raises(ImportRejected, match="didn't open"):
        parse(files / "bank_a_2026-10.pdf", "wrong-password")


def test_scanned_and_unknown_pdfs_are_refused(tmp_path: Path) -> None:
    img = FPDF()
    img.add_page()
    img.rect(10, 10, 100, 60, style="F")  # an image-only page: no text layer
    scan = tmp_path / "scan.pdf"
    img.output(str(scan))
    with pytest.raises(ImportRejected, match="no text layer") as e:
        parse(scan)
    assert "OCR" in (e.value.hint or "")
    other = FPDF()
    other.add_page()
    other.set_font("Helvetica", size=10)
    other.cell(0, 8, "Some other bank   When   What   How much")
    unknown = tmp_path / "other.pdf"
    other.output(str(unknown))
    with pytest.raises(ImportRejected, match="layout isn.t recognised"):
        parse(unknown)


def test_pdf_and_csv_of_the_same_statement_import_once(tmp_path: Path) -> None:
    paths = Paths(tmp_path / "home", tmp_path / "logs", tmp_path / "reports")
    r = build_demo(paths)  # the October card statement is held back
    eng, st = r.ledger.engine, Staging()
    inbox = paths.demo.inbox
    p = stage_import(eng, st, inbox / "card_a_2026-10.pdf")
    assert (p.status, p.new_rows, p.account) == ("ready", 52, "Card A") and "✓" in (p.file_check or "")
    commit_import(eng, st, p.import_id)
    again = stage_import(eng, st, inbox / "card_a_2026-10.csv")  # the CSV twin adds nothing
    assert (again.status, again.new_rows, again.already_in_ledger) == ("nothing_new", 0, 52)
    bank = stage_import(eng, st, inbox / "bank_a_2026-10.pdf", password=DEMO_PDF_PASSWORD)
    assert (bank.status, bank.new_rows) == ("nothing_new", 0)  # its CSV was imported by the demo
    from finassist.db.schema import import_source

    with eng.connect() as c:
        kinds = {
            row[0]: row[1]
            for row in c.execute(select(import_source.c.file_name, import_source.c.source_type))
        }
    assert kinds["card_a_2026-10.pdf"] == "statement_pdf" and kinds["card_a_2026-09.csv"] == "statement_csv"
    r.ledger.close()


def test_password_is_never_kept(files: Path) -> None:
    f = SourceFile.read(files / "bank_a_2026-10.pdf", password=DEMO_PDF_PASSWORD)
    assert DEMO_PDF_PASSWORD not in repr(f)
    assert PARSERS[1].kind == "pdf"


def test_web_pdf_upload_with_password(tmp_path: Path) -> None:
    from test_web import Web, import_id_of, token_of

    w = Web(tmp_path)
    page = w.client.get("/import").text
    assert (
        "card_a_2026-10.pdf" in page
        and "bank_a_2026-10.pdf" in page
        and f"<code>{DEMO_PDF_PASSWORD}</code>" in page
    )
    assert 'accept=".csv,.pdf' in page and 'name="password"' in page
    assert (
        '<option value="">Detect from the statement</option>' in page
        and "<option>Credit card</option>" in page
    )
    pdf_bytes = (w.paths.demo.inbox / "bank_a_2026-10.pdf").read_bytes()
    no_pw = w.post(
        "/api/import/preview", files={"file": ("statement.pdf", pdf_bytes, "application/pdf")}
    ).text
    assert "password-protected" in no_pw
    assert list((w.paths.home / "uploads").iterdir()) == []  # a refused upload is deleted at once
    ok = w.post(
        "/api/import/preview",
        data={"password": DEMO_PDF_PASSWORD},
        files={"file": ("statement.pdf", pdf_bytes, "application/pdf")},
    ).text
    assert "already in your ledger" in ok and DEMO_PDF_PASSWORD not in ok
    card = w.post("/api/import/preview", {"inbox_name": "card_a_2026-10.pdf"}).text
    assert "Import 52 transactions" in card
    done = w.post(
        "/api/import/commit", {"import_id": import_id_of(card), "token": token_of(card), "answer": "yes"}
    ).text
    assert "Imported 52 transactions into Credit card" in done
    assert "₹1,79,510.11" in w.client.get("/dashboard?period=2026-10").text
    assert re.search(
        r"card_a_2026-10\.csv.*?Not imported", w.client.get("/import").text, re.S
    )  # CSV twin unused
