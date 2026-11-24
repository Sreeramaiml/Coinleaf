from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from conftest import BANK_HEADER, BANK_PREAMBLE, write_csv
from finassist.core.errors import ImportRejected, ValidationFailed
from finassist.db.ledger import Ledger
from finassist.db.repo.accounts import AccountRow, balance_minor
from finassist.db.schema import (
    audit_log,
    import_source,
    source_row,
    statement_balance,
    transaction_source,
    txn,
)
from finassist.ingest.models import sniff_kind
from finassist.ingest.narration import infer_payment_method, upi_reference
from finassist.ingest.pipeline import Staging, commit_import, stage_import
from finassist.ingest.profiles import builtin_profiles


def bank_file(
    tmp: Path,
    name: str,
    rows: list[str],
    opening: str = "41,220.15",
    closing: str = "32,304.75",
    preamble: list[str] | None = None,
) -> Path:
    lines = (
        (preamble if preamble is not None else BANK_PREAMBLE)
        + [BANK_HEADER]
        + rows
        + ["", f'Opening Balance,"{opening}"', f'Closing Balance,"{closing}"']
    )
    return write_csv(tmp / name, lines)


OCT_ROWS = [
    '01/10/2026,NEFT CR-BNKA0009876-ACME TECHNOLOGIES PVT LTD-SALARY,N123456789012,01/10/2026,,"1,48,000.00","1,89,220.15"',
    '05/10/2026,IMPS/P2A/612345678901/RAMESH IYER/RENT,612345678901,05/10/2026,"28,000.00",,"1,61,220.15"',
    '07/10/2026,UPI/634512345678/SWIGGY/swiggy@icici/ICICI Bank/Payment,,07/10/2026,412.00,,"1,60,808.15"',
    '07/10/2026,UPI/SAME/CHAI POINT/chaipoint@paytm/ICICI Bank/tea,,07/10/2026,20.00,,"1,60,788.15"',
    '07/10/2026,UPI/SAME/CHAI POINT/chaipoint@paytm/ICICI Bank/tea,,07/10/2026,20.00,,"1,60,768.15"',
    '20/10/2026,ACH D- HOMEFIN LOANS LTD-EMI 123456789,1234567890,20/10/2026,"1,28,463.40",,"32,304.75"',
]


def counts(ledger: Ledger) -> dict[str, int]:
    with ledger.engine.connect() as c:
        return {
            t.name: c.execute(select(func.count()).select_from(t)).scalar_one()
            for t in (txn, import_source, source_row, transaction_source, audit_log, statement_balance)
        }


def stage_commit(ledger: Ledger, path: Path, account: str | None = None) -> dict:  # type: ignore[type-arg]
    st = Staging()
    p = stage_import(ledger.engine, st, path, account)
    assert p.status == "ready", p.summary_lines()
    return commit_import(ledger.engine, st, p.import_id)


def test_profiles_load_and_sniff() -> None:
    names = {p.name for p in builtin_profiles()}
    assert {"Bank A savings CSV", "Card A credit card CSV"} <= names
    assert sniff_kind(b"%PDF-1.7") == "pdf"
    assert sniff_kind(b"PK\x03\x04....") == "xlsx"
    assert sniff_kind(b"Date,Narration") == "text"


def test_narration_helpers() -> None:
    assert infer_payment_method("UPI/634512345678/SWIGGY/x@y") == "upi"
    assert infer_payment_method("NEFT CR-ABCD0123456-X") == "neft_imps_rtgs"
    assert infer_payment_method("ACH D- HOMEFIN") == "nach"
    assert infer_payment_method("SOMETHING", "card") == "card"
    assert upi_reference("UPI/634512345678/SWIGGY/x") == "634512345678"
    assert upi_reference("UPI/SAME/CHAI") is None


def test_import_parses_and_commits(ledger: Ledger, savings: AccountRow, tmp_path: Path) -> None:
    f = bank_file(tmp_path, "oct.csv", OCT_ROWS)
    st = Staging()
    p = stage_import(ledger.engine, st, f)  # account found from the statement's last 4 digits
    assert p.account == "Bank A Savings"
    assert (p.rows_parsed, p.new_rows, p.already_in_ledger) == (6, 6, 0)
    assert (p.period_start, p.period_end) == ("2026-10-01", "2026-10-31")
    assert p.file_check and "✓" in p.file_check
    assert "money out ₹1,56,915.40 · money in ₹1,48,000.00" in "\n".join(p.summary_lines())
    assert p.as_dict()["account"] == "Savings account"  # only the nickname is ever sent to an LLM
    assert counts(ledger)["txn"] == 0  # staging writes nothing
    res = commit_import(ledger.engine, st, p.import_id)
    assert res["inserted"] == 6
    with ledger.engine.connect() as c:
        rows = c.execute(
            select(txn.c.amount_minor, txn.c.payment_method, txn.c.external_ref).order_by(
                txn.c.txn_date, txn.c.amount_minor
            )
        ).all()
        assert rows[0] == (14_800_000, "neft_imps_rtgs", "N123456789012")
        assert (-41_200, "upi", "634512345678") in rows
        assert balance_minor(c, savings.id) == 4_122_015 + sum(r[0] for r in rows)
        sb = c.execute(select(statement_balance.c.kind, statement_balance.c.balance_minor)).all()
        assert sorted(sb) == [("closing", 3_230_475), ("opening", 4_122_015)]
        # provenance: every transaction links to its source row
        assert c.execute(select(func.count()).select_from(transaction_source)).scalar() == 6


def test_identical_same_day_rows_without_balance_both_survive(
    ledger: Ledger, savings: AccountRow, tmp_path: Path
) -> None:
    import csv
    import io

    rows = []
    for r in OCT_ROWS:  # same rows with the running balance column emptied
        cells = next(csv.reader([r]))
        cells[-1] = ""
        buf = io.StringIO()
        csv.writer(buf, lineterminator="").writerow(cells)
        rows.append(buf.getvalue())
    f = bank_file(tmp_path, "nobal.csv", rows)
    res = stage_commit(ledger, f)
    assert res["inserted"] == 6
    with ledger.engine.connect() as c:
        tea = c.execute(select(func.count()).where(txn.c.raw_description.contains("CHAI POINT"))).scalar()
    assert tea == 2


def test_reimport_same_file_changes_nothing(ledger: Ledger, savings: AccountRow, tmp_path: Path) -> None:
    f = bank_file(tmp_path, "oct.csv", OCT_ROWS)
    stage_commit(ledger, f)
    before = counts(ledger)
    p = stage_import(ledger.engine, Staging(), f)
    assert p.status == "already_imported"
    assert counts(ledger) == before


def test_overlapping_statement_imports_shared_rows_once(
    ledger: Ledger, savings: AccountRow, tmp_path: Path
) -> None:
    stage_commit(ledger, bank_file(tmp_path, "a.csv", OCT_ROWS[:4], closing="1,60,788.15"))
    # second statement repeats rows 3–4 and adds two more; a different file (different sha)
    f2 = bank_file(tmp_path, "b.csv", OCT_ROWS[2:], opening="1,61,220.15")
    st = Staging()
    p = stage_import(ledger.engine, st, f2)
    assert (p.rows_parsed, p.already_in_ledger, p.new_rows) == (4, 2, 2)
    commit_import(ledger.engine, st, p.import_id)
    assert counts(ledger)["txn"] == 6


def test_one_bad_row_rejects_whole_file(ledger: Ledger, savings: AccountRow, tmp_path: Path) -> None:
    bad = (
        OCT_ROWS[:2]
        + ['32/10/2026,BROKEN DATE,,,10.00,,"1.00"']
        + OCT_ROWS[2:]
        + [
            '21/10/2026,TWO AMOUNTS,,21/10/2026,10.00,20.00,"1.00"',
            '22/10/2026,BAD AMOUNT,,22/10/2026,1.234,,"1.00"',
        ]
    )
    f = bank_file(tmp_path, "bad.csv", bad)
    p = stage_import(ledger.engine, Staging(), f)
    assert p.status == "rejected"
    lines = {e.row_index for e in p.errors}
    assert lines == {9, 14, 15}  # 1-based line numbers in the file
    assert "nothing was imported" in p.message
    with pytest.raises(Exception):  # noqa: B017 - not staged, cannot commit
        commit_import(ledger.engine, Staging(), p.import_id)
    assert counts(ledger)["txn"] == 0


def test_failed_commit_leaves_no_rows_and_records_failure(
    ledger: Ledger, savings: AccountRow, tmp_path: Path
) -> None:
    f = bank_file(tmp_path, "oct.csv", OCT_ROWS)
    st = Staging()
    p = stage_import(ledger.engine, st, f)
    with pytest.raises(RuntimeError, match="simulated"):
        commit_import(ledger.engine, st, p.import_id, {"_fail_after_insert": True})
    c = counts(ledger)
    assert (c["txn"], c["source_row"], c["transaction_source"], c["statement_balance"]) == (0, 0, 0, 0)
    with ledger.engine.connect() as conn:
        statuses = conn.execute(select(import_source.c.status)).scalars().all()
    assert statuses == ["failed"]
    # and a retry succeeds afterwards
    p2 = stage_import(ledger.engine, st, f)
    assert commit_import(ledger.engine, st, p2.import_id)["inserted"] == 6


def test_wrong_account_and_unknown_layout(
    ledger: Ledger, savings: AccountRow, cash: AccountRow, tmp_path: Path
) -> None:
    f = bank_file(tmp_path, "oct.csv", OCT_ROWS)
    with pytest.raises(ValidationFailed, match="ending 4821"):
        stage_import(ledger.engine, Staging(), f, "Cash")
    g = write_csv(tmp_path / "weird.csv", ["When,What,How much", "01/10/2026,thing,10"])
    with pytest.raises(ImportRejected, match="unknown statement layout"):
        stage_import(ledger.engine, Staging(), g)
    h = tmp_path / "s.pdf"
    h.write_bytes(b"%PDF-1.7 ...")
    with pytest.raises(ImportRejected, match="PDF"):
        stage_import(ledger.engine, Staging(), h)


def test_file_check_reports_mismatch(ledger: Ledger, savings: AccountRow, tmp_path: Path) -> None:
    f = bank_file(tmp_path, "oct.csv", OCT_ROWS, closing="32,000.00")
    p = stage_import(ledger.engine, Staging(), f)
    assert p.file_check and "✗" in p.file_check and "₹304.75" in p.file_check


def test_continuation_lines_join_description(ledger: Ledger, savings: AccountRow, tmp_path: Path) -> None:
    rows = [OCT_ROWS[0], ",SALARY FOR OCTOBER,,,,,"]
    f = bank_file(tmp_path, "cont.csv", rows, closing="1,89,220.15")
    st = Staging()
    p = stage_import(ledger.engine, st, f)
    assert p.status == "ready" and p.rows_parsed == 1
    commit_import(ledger.engine, st, p.import_id)
    with ledger.engine.connect() as c:
        d = c.execute(select(txn.c.raw_description)).scalar()
    assert d.endswith("SALARY FOR OCTOBER")


def test_card_layout_with_suffix_and_foreign_amounts(ledger: Ledger, tmp_path: Path) -> None:
    from finassist.db.audit import open_change_set
    from finassist.db.repo.accounts import create_account

    with ledger.engine.begin() as c:
        cs = open_change_set(c, "user", "card")
        create_account(c, cs, name="Card A", type="credit_card", last4="9013")
    lines = [
        "Synthetic data generated by the finassist demo generator - not a real statement",
        "Card Number : XXXX XXXX XXXX 9013",
        "Billing Period : 01-10-2026 to 31-10-2026",
        "",
        "Transaction Date,Details,Reference,Foreign Amount,Foreign Currency,Amount",
        "07-10-2026,NETFLIX.COM MUMBAI IN,74512345678901234567890,,,649.00 Dr",
        '09-10-2026,AWS EMEA AWS.AMAZON.CO LU,74512345678901234567891,12.40,USD,"1,054.00 Dr"',
        "10-10-2026,MERCHANT REFUND,74512345678901234567892,,,100.00 Cr",
        "",
        'Previous Balance,"0.00 Cr"',
        'Current Balance,"1,603.00 Dr"',
    ]
    f = write_csv(tmp_path / "card.csv", lines)
    st = Staging()
    p = stage_import(ledger.engine, st, f)
    assert p.account == "Card A" and "✓" in (p.file_check or "")
    commit_import(ledger.engine, st, p.import_id)
    with ledger.engine.connect() as c:
        rows = c.execute(
            select(
                txn.c.amount_minor, txn.c.orig_amount_minor, txn.c.orig_currency, txn.c.payment_method
            ).order_by(txn.c.txn_date)
        ).all()
    assert rows == [
        (-64_900, None, None, "card"),
        (-105_400, -1_240, "USD", "card"),
        (10_000, None, None, "card"),
    ]
    assert date(2026, 10, 7)  # dates parsed as DD-MM-YYYY
