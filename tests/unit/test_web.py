"""GUI tests: security, every page, and every flow that changes the ledger (no network, no Keychain)."""

from __future__ import annotations

import csv
import io
import re
import stat
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from finassist.config.paths import Paths
from finassist.db.keys import MemoryKeyStore
from finassist.demo.setup import build_demo
from finassist.web.app import create_app
from finassist.web.security import COOKIE, Guard
from finassist.web.state import WebConfig

PASSWORD = "correct horse battery"


class Web:
    def __init__(self, tmp: Path, build: bool = True) -> None:
        self.paths = Paths(tmp / "home", tmp / "home" / "logs", tmp / "home" / "reports")
        if build:
            build_demo(self.paths).ledger.close()
        self.cfg = WebConfig(
            main_paths=self.paths, real_keys=MemoryKeyStore(), config_path=tmp / "config.toml"
        )
        self.guard = Guard({"testserver"}, tmp / "home" / "app-password.json")
        self.client = TestClient(create_app(self.cfg, self.guard))
        # first start: the launch link leads to creating the password, which signs this browser in
        r = self.client.get(self.guard.launch_path(), follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/setup"
        self.client.headers["X-CSRF-Token"] = self.guard.csrf
        r = self.client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
        assert r.headers.get("x-redirect") == "/dashboard", r.text

    def post(self, url: str, data: dict[str, Any] | None = None, **kw: Any) -> Any:
        return self.client.post(url, data=data or {}, **kw)


def token_of(html: str) -> str:
    m = re.search(r'name="token" value="([0-9a-f]{64})"', html)
    assert m, html
    return m.group(1)


def import_id_of(html: str) -> str:
    m = re.search(r'name="import_id" value="([^"]+)"', html)
    assert m, html
    return m.group(1)


def add(web: Web, **form: str) -> str:
    preview = web.post("/api/add/preview", form).text
    return str(web.post("/api/add/save", {**form, "token": token_of(preview), "answer": "yes"}).text)


@pytest.fixture
def web(tmp_path: Path) -> Web:
    return Web(tmp_path)


# ---------------------------------------------------------------------------------- security


def test_access_needs_a_signed_in_session(tmp_path: Path) -> None:
    w = Web(tmp_path, build=False)
    anon = TestClient(create_app(w.cfg, w.guard))
    r = anon.get("/dashboard", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert anon.get("/?t=wrong", follow_redirects=False).headers["location"] == "/login?expired=1"
    r = anon.get(w.guard.launch_path(), follow_redirects=False)  # password already set: sign in
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert anon.get("/dashboard", follow_redirects=False).status_code == 303  # the link alone isn't enough
    anon.headers["X-CSRF-Token"] = w.guard.csrf
    r = anon.post("/login", data={"password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and COOKIE in cookie
    assert anon.get("/dashboard", follow_redirects=False).status_code == 200


def test_host_header_csrf_and_headers(web: Web) -> None:
    assert web.client.get("/dashboard", headers={"host": "evil.example"}).status_code == 400
    assert (
        web.client.post("/api/ledger", data={"kind": "demo"}, headers={"X-CSRF-Token": "nope"}).status_code
        == 403
    )
    r = web.client.get("/dashboard")
    csp = r.headers["content-security-policy"]
    assert (
        "default-src 'self'" in csp and "script-src 'self'" in csp and r.headers["x-frame-options"] == "DENY"
    )
    assert "<script>" not in r.text  # no inline scripts; the CSP would block them anyway


def test_no_chat_in_the_gui(web: Web) -> None:
    assert web.client.get("/chat").status_code == 404
    assert web.client.post("/api/chat", data={"message": "hi"}).status_code in (404, 405)
    assert ">Chat<" not in web.client.get("/dashboard").text


# ---------------------------------------------------------------------------------- dashboard


def test_dashboard_figures_match_the_ledger(web: Web) -> None:
    html = web.client.get("/dashboard").text  # default period: the latest month with data (Oct 2026)
    for figure in (
        "October 2026",
        "₹13,69,304.08",  # available balance: savings + cash
        "₹2,06,818.19",  # net worth
        "₹11,62,485.89",  # owed on cards
        "₹1,48,000.00",  # money in
        "₹79,787.87",  # money out
        "₹68,212.13",  # saved
        "Savings rate 46.1%",
        "vs September 2026",
    ):
        assert figure in html, figure
    assert "Some data is missing for October 2026" in html  # the October card statement isn't imported yet
    assert "Ramesh Iyer" in html and "Top payees" in html
    assert "How you paid" in html and "Bank transfer" in html
    assert html.count("Show as table") >= 3


def test_dashboard_filters(web: Web) -> None:
    sep = web.client.get("/dashboard?period=2026-09").text
    assert "₹1,59,954.91" in sep and "Some data is missing" not in sep
    card = web.client.get("/dashboard?period=2026-09&account=Credit+card").text
    assert "₹1,59,954.91" not in card and "Credit card" in card
    assert "Choose a single month" in web.client.get("/dashboard?period=last+3+months").text
    assert "use that period" in web.client.get("/dashboard?period=since+diwali").text


def test_statement_text_is_escaped(web: Web) -> None:
    add(
        web,
        account="Cash",
        amount="10",
        direction="out",
        description="<img src=x onerror=alert(1)>",
        date="2026-10-30",
        category="",
    )
    html = (
        web.client.get("/dashboard?period=2026-10").text + web.client.get("/transactions?period=2026-10").text
    )
    assert "<img src=x" not in html and "&lt;img src=x" in html


# ---------------------------------------------------------------------------------- transactions


def test_transactions_page_filters_and_pages(web: Web) -> None:
    html = web.client.get("/transactions?period=2026-09").text
    assert "Page 1 of 2" in html and "Money out <strong>₹1,59,954.91</strong>" in html and "Older →" in html
    page2 = web.client.get("/transactions?period=2026-09&page_no=2").text
    assert "Page 2 of 2" in page2 and "← Newer" in page2
    swiggy = web.client.get("/transactions?period=last+12+months&q=swiggy").text
    assert "Swiggy" in swiggy and "Ramesh Iyer" not in swiggy
    income = web.client.get("/transactions?period=2026-09&type=in").text
    assert "+₹1,48,000.00" in income and "Money out <strong>₹0.00</strong>" in income
    assert "No transactions match" in web.client.get("/transactions?period=2026-09&q=zzzz").text


def test_csv_export_matches_and_is_injection_safe(web: Web) -> None:
    add(
        web,
        account="Cash",
        amount="5",
        direction="out",
        description='=HYPERLINK("x")',
        date="2026-09-30",
        category="",
    )
    r = web.client.get("/transactions.csv?period=2026-09&type=out")
    assert (
        r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    )
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][:2] == ["Date", "Account"] and len(rows) == 1 + 77
    total = sum(-int(x[-1].replace(".", "")) for x in rows[1:])  # exact: amounts are written with 2 decimals
    assert total == 15_995_491 + 500
    assert any(x[3].startswith("'=HYPERLINK") for x in rows[1:])


# ---------------------------------------------------------------------------------- import


def test_import_from_demo_inbox_needs_a_valid_click(web: Web) -> None:
    page = web.client.get("/import").text
    assert "card_a_2026-10.csv" in page and "Not imported" in page
    preview = web.post("/api/import/preview", {"inbox_name": "card_a_2026-10.csv"}).text
    assert "Import 52 transactions" in preview and "₹99,722.24" in preview and "Balances check out" in preview
    bad = web.post(
        "/api/import/commit", {"import_id": import_id_of(preview), "token": "0" * 64, "answer": "yes"}
    ).text
    assert "data changed" in bad
    preview = web.post("/api/import/preview", {"inbox_name": "card_a_2026-10.csv"}).text
    ok = web.post(
        "/api/import/commit",
        {"import_id": import_id_of(preview), "token": token_of(preview), "answer": "yes"},
    ).text
    assert "Imported 52 transactions into Credit card" in ok
    dash = web.client.get("/dashboard?period=2026-10").text
    assert "₹1,79,510.11" in dash and "Some data is missing" not in dash
    assert "already imported" in web.post("/api/import/preview", {"inbox_name": "card_a_2026-10.csv"}).text


def test_upload_preview_cancel_and_rejection(web: Web) -> None:
    src = web.paths.demo.inbox / "card_a_2026-10.csv"
    preview = web.post(
        "/api/import/preview", files={"file": ("my statement.csv", src.read_bytes(), "text/csv")}
    ).text
    assert "Import 52 transactions" in preview and "my_statement.csv" in preview
    uploads = list((web.paths.home / "uploads").rglob("*.csv"))
    assert len(uploads) == 1 and stat.S_IMODE(uploads[0].stat().st_mode) == 0o600
    out = web.post(
        "/api/import/commit", {"import_id": import_id_of(preview), "token": token_of(preview), "answer": "no"}
    ).text
    assert "Nothing was imported" in out
    assert list((web.paths.home / "uploads").iterdir()) == []  # the uploaded copy is deleted
    broken = src.read_text(encoding="utf-8-sig").replace("-10-2026,", "-13-2026,", 1).encode()
    rej = web.post("/api/import/preview", files={"file": ("b.csv", broken, "text/csv")}).text
    assert "could not be parsed" in rej and "Line " in rej
    weird = web.post("/api/import/preview", files={"file": ("w.csv", b"When,What\n1,2\n", "text/csv")}).text
    assert "unknown statement layout" in weird


# ---------------------------------------------------------------------------------- add / accounts / settings


def test_add_transaction_preview_then_save(web: Web) -> None:
    page = web.client.get("/add").text
    assert 'type="date"' in page and "<optgroup" in page
    form = {
        "account": "Cash",
        "amount": "450",
        "direction": "out",
        "description": "Auto rickshaw",
        "date": "2026-11-05",
        "category": "Essentials/Transport/Fuel",
    }
    preview = web.post("/api/add/preview", form).text
    assert "-₹450.00" in preview and "Thursday, 05 November 2026" in preview and "Transport/Fuel" in preview
    assert "Not saved" in web.post("/api/add/save", {**form, "token": token_of(preview), "answer": "no"}).text
    ok = web.post("/api/add/save", {**form, "token": token_of(preview), "answer": "yes"}).text
    assert "Saved. Cash balance is now ₹1,986.00." in ok
    assert "decimal places" in web.post("/api/add/preview", {**form, "amount": "12.345"}).text


def test_my_ledger_accounts_and_dashboard(tmp_path: Path) -> None:
    w = Web(tmp_path, build=False)
    w.post("/api/ledger", {"kind": "mine"})
    assert "Create your ledger" in w.client.get("/dashboard").text
    assert w.post("/api/ledger/create").json() == {"ok": True}
    assert "No accounts yet" in w.client.get("/accounts").text
    out = w.post(
        "/api/account/add",
        {
            "name": "HDFC Savings",
            "type": "savings",
            "nickname": "Savings",
            "last4": "1234",
            "opening": "1,00,000",
        },
    ).text
    assert "Added HDFC Savings" in out
    acc = w.client.get("/accounts").text
    assert "₹1,00,000.00" in acc and "•••• 1234" in acc and "No data yet" in acc
    dash = w.client.get("/dashboard").text  # no transactions yet: the getting-started checklist
    assert "Welcome to Coinleaf" in dash and "Import a statement" in dash and ">Done<" in dash
    add(
        w,
        account="Savings",
        amount="250",
        direction="out",
        description="Groceries",
        date="2026-11-05",
        category="",
    )
    dash = w.client.get("/dashboard").text
    assert "Welcome to Coinleaf" not in dash and "₹99,750.00" in dash


def test_settings_and_demo_rebuild(web: Web) -> None:
    html = web.client.get("/settings").text
    assert "This app makes no internet connections" in html and "Rebuild the demo" in html
    preview = web.post("/api/import/preview", {"inbox_name": "card_a_2026-10.csv"}).text
    web.post(
        "/api/import/commit",
        {"import_id": import_id_of(preview), "token": token_of(preview), "answer": "yes"},
    )
    assert "₹1,79,510.11" in web.client.get("/dashboard?period=2026-10").text
    assert web.post("/api/ledger/create").json() == {"ok": True}  # rebuild
    assert "₹79,787.87" in web.client.get("/dashboard?period=2026-10").text  # the fresh demo again
