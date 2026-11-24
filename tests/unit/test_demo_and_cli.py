import hashlib
from datetime import date
from pathlib import Path

import pytest

from finassist.analytics.metrics import expense, income
from finassist.demo.generator import GENERATOR_MARKER, generate
from finassist.demo.setup import DemoResult

ROOT = Path(__file__).parents[2]


def _digest(folder: Path) -> dict[str, str]:
    """Byte digests of every file except PDFs: an encrypted PDF gets a fresh random salt each
    time by design, so PDFs are compared by content in the test below."""
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.iterdir())
        if p.suffix != ".pdf"
    }


def _pdf_rows(path: Path, password: str | None = None) -> list[tuple[object, ...]]:
    from finassist.ingest.models import SourceFile
    from finassist.ingest.pipeline import PARSERS
    from finassist.ingest.profiles import builtin_profiles

    f = SourceFile.read(path, password=password)
    parser = PARSERS[1]
    prof = parser.match_profile(f, builtin_profiles())
    assert prof is not None
    return [
        (r.txn_date, r.amount_minor, r.raw_description, r.balance_minor) for r in parser.parse(f, prof).rows
    ]


def test_generator_is_deterministic(tmp_path: Path) -> None:
    from finassist.demo.generator import DEMO_PDF_PASSWORD

    a, b = generate(tmp_path / "a"), generate(tmp_path / "b")
    assert _digest(tmp_path / "a") == _digest(tmp_path / "b")
    assert (
        [p.name for p in a.pdf_files]
        == [p.name for p in b.pdf_files]
        == ["bank_a_2026-10.pdf", "card_a_2026-10.pdf"]
    )
    for pa, pb in zip(a.pdf_files, b.pdf_files, strict=True):
        pw = DEMO_PDF_PASSWORD if pa.name.startswith("bank") else None
        assert _pdf_rows(pa, pw) == _pdf_rows(pb, pw)
    generate(tmp_path / "c", seed=1)
    assert _digest(tmp_path / "a") != _digest(tmp_path / "c")


def test_every_generated_file_carries_the_marker(tmp_path: Path) -> None:
    data = generate(tmp_path)
    for f in data.files:
        assert GENERATOR_MARKER in f.read_bytes()[:200].decode("utf-8-sig")
    assert GENERATOR_MARKER in data.truth_file.read_text()


def test_ledger_equals_ground_truth_every_month(demo: DemoResult) -> None:
    """P0 'done when': imported totals match the generator's ground truth exactly."""
    assert len(demo.imported) == 28
    months = sorted({t.txn_date[:7] for t in demo.data.truth})
    assert len(months) == 14
    import calendar

    with demo.ledger.engine.connect() as c:
        for m in months:
            y, mo = map(int, m.split("-"))
            start, end = date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1])
            e = expense(c, start, end).totals["sum"]["minor"]
            i = income(c, start, end).totals["sum"]["minor"]
            te = -sum(
                t.amount_minor for t in demo.data.truth if t.txn_date.startswith(m) and t.kind == "expense"
            )
            ti = sum(
                t.amount_minor for t in demo.data.truth if t.txn_date.startswith(m) and t.kind == "income"
            )
            assert (e, i) == (te, ti), m


def test_demo_statement_files_balance(demo: DemoResult) -> None:
    from finassist.ingest.pipeline import Staging, stage_import

    # every file's own opening + rows = closing check passes (shown in the import preview)
    for f in demo.data.files[:4]:
        p = stage_import(demo.ledger.engine, Staging(), f)
        assert p.status == "already_imported"


def test_fixture_check_script(tmp_path: Path) -> None:
    import subprocess
    import sys

    good = tmp_path / "good.csv"
    good.write_text(GENERATOR_MARKER + "\nDate,x\n")
    bad = tmp_path / "bad.csv"
    bad.write_text("Date,Narration\n01/10/2026,REAL STATEMENT\n")
    script = ROOT / "scripts" / "check_fixtures.py"
    ok = subprocess.run([sys.executable, str(script), str(good)], capture_output=True, text=True)
    no = subprocess.run([sys.executable, str(script), str(good), str(bad)], capture_output=True, text=True)
    assert ok.returncode == 0 and no.returncode == 1 and "bad.csv" in no.stdout


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FINASSIST_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _cli(args: list[str], stdin: str = "") -> str:
    # each invocation runs in a subprocess so the network guard of one run never affects another
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "finassist", *args], input=stdin, capture_output=True, text=True, timeout=120
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_cli_demo_then_slash_commands(cli_home: Path) -> None:
    out = _cli(["demo"])
    assert "Demo ledger built" in out and "card_a_2026-10.csv" in out
    out = _cli(["--demo", "chat"], "/spend october\n/coverage\n/spend march 2019\n/quit\n")
    assert "Money out October 2026: ₹79,787.87" in out
    assert "Credit card has no statement" in out
    assert "No data for March 2019" in out
    out = _cli(["--demo", "spend", "september"])
    assert "Money out September 2026" in out


def test_cli_import_held_back_file_and_reimport(cli_home: Path) -> None:
    _cli(["demo"])
    held = next((cli_home / "home" / "demo" / "inbox").glob("card_a_2026-10.csv"))
    out = _cli(["--demo", "import", str(held)], "y\n")
    assert "Imported" in out
    out = _cli(["--demo", "import", str(held)])
    assert "already imported: nothing to do" in out


def test_cli_real_ledger_init_and_manual_entry(cli_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    monkeypatch.setenv("FINASSIST_KEYSTORE", "file")  # never touch the real Keychain in tests
    r = subprocess.run(
        [sys.executable, "-m", "finassist", "spend", "october"], capture_output=True, text=True
    )
    assert r.returncode == 1 and "coinleaf init" in r.stdout and "Traceback" not in r.stderr
    assert "Created an encrypted ledger" in _cli(["init"])
    assert "nothing changed" in _cli(["init"])
    out = _cli(["account", "add"], "Cash\ncash\n\n\n\n1000\n")
    assert "Added Cash (cash), opening balance ₹1,000.00" in out
    out = _cli(["add"], "Cash\n450\nout\nauto rickshaw\n2026-10-03\nTransport/Fuel\ny\n")
    assert "Saved. Cash balance is now ₹550.00." in out
    assert "Money out October 2026: ₹450.00 (1 transaction)" in _cli(["spend", "october", "2026"])


def test_cli_add_with_bad_amount_fails_cleanly(cli_home: Path) -> None:
    import subprocess
    import sys

    _cli(["demo"])
    r = subprocess.run(
        [sys.executable, "-m", "finassist", "--demo", "add"],
        input="Cash\n12.345\nout\nauto\ntoday\n\n",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert (
        r.returncode == 1
        and "more than 2 decimal places" in r.stdout
        and "Traceback" not in r.stderr + r.stdout
    )


def test_bare_finassist_starts_the_gui(cli_home: Path) -> None:
    """`finassist` with no arguments (what Finassist.command runs) must reach serve() with real values."""
    import subprocess
    import sys

    code = (
        "import finassist.web.server as s\n"
        "def fake(main_paths, config, *, real_keys, start_ledger, port, open_browser):\n"
        "    print('SERVE', type(port).__name__, port, open_browser, start_ledger)\n"
        "s.serve = fake\n"
        "from finassist.cli.app import main\n"
        "main()\n"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "SERVE int 8765 True demo" in r.stdout
