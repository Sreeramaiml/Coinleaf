"""Sign-up for new users: the form, its checks, and each account's own private ledger."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_web import Web, add

from finassist.config.accounts import check_email, check_mobile, check_name, format_mobile
from finassist.config.password import PasswordError
from finassist.web.app import create_app

RAVI = {
    "name": "Ravi Kumar",
    "mobile": "+91 91234 56789",
    "email": "Ravi.K@Example.COM",
    "username": "ravi",
    "password": "ravi secret 1",
    "confirm": "ravi secret 1",
}


def browser(w: Web) -> TestClient:
    c = TestClient(create_app(w.cfg, w.guard))
    c.headers["X-CSRF-Token"] = w.guard.csrf
    return c


def signup(c: TestClient, **changes: str) -> object:
    return c.post("/signup", data={**RAVI, **changes})


def test_signup_page_has_every_field(tmp_path: Path) -> None:
    page = browser(Web(tmp_path)).get("/signup").text
    assert "Create your account" in page and "its own private, encrypted ledger" in page
    for name, auto in (("name", "name"), ("mobile", "tel"), ("email", "email"), ("username", "username")):
        assert f'name="{name}"' in page and f'autocomplete="{auto}"' in page
    assert 'type="tel"' in page and 'type="email"' in page and 'autocomplete="new-password"' in page
    assert "Already have an account?" in page and "never sends anything" in page


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("name", "", "enter your name"),
        ("name", "12", "enter your name"),
        ("mobile", "12345", "enter a mobile number"),
        ("mobile", "", "enter a mobile number"),
        ("email", "ravi@", "enter an email address"),
        ("email", "", "enter an email address"),
        ("username", "r", "the username needs"),
        ("password", "short", "at least 8 characters"),
    ],
)
def test_signup_checks_each_field(tmp_path: Path, field: str, value: str, message: str) -> None:
    w = Web(tmp_path)
    form = {field: value} | ({"confirm": value} if field == "password" else {})
    r = signup(browser(w), **form)
    assert message in r.text and "x-redirect" not in r.headers  # type: ignore[attr-defined]
    assert len(w.guard.accounts.accounts()) == 1  # nothing created


def test_username_and_email_must_be_new(tmp_path: Path) -> None:
    w = Web(tmp_path)
    c = browser(w)
    assert "that username is taken" in signup(c, username="ASHA").text  # type: ignore[attr-defined]
    assert "this email already exists" in signup(c, email="ASHA@example.com").text  # type: ignore[attr-defined]


def test_new_user_gets_an_own_private_ledger(tmp_path: Path) -> None:
    w = Web(tmp_path)
    # the owner records something in their own real ledger
    w.post("/api/ledger", {"kind": "mine"})
    assert w.post("/api/ledger/create").json()["ok"]
    out = w.post(
        "/api/account/add", {"name": "Owner Wallet", "type": "cash", "nickname": "Wallet", "opening": "500"}
    )
    assert "Added Owner Wallet" in out.text
    saved = add(
        w, account="Wallet", amount="120", direction="out", description="owner chai", date="2026-11-03"
    )
    assert "Saved" in saved

    c = browser(w)
    r = signup(c)
    assert r.headers["x-redirect"] == "/dashboard"  # type: ignore[attr-defined]
    ravi = w.guard.accounts.find("ravi")
    assert ravi and ravi.folder == f"users/{ravi.id}" and ravi.email == "Ravi.K@example.com"
    assert ravi.mobile == "+919123456789" and ravi.name == "Ravi Kumar"
    dash = c.get("/dashboard").text
    assert "Logged in as <strong>Ravi Kumar</strong>" in dash
    # Ravi sees none of the owner's data, in either ledger
    c.post("/api/ledger", data={"kind": "mine"})
    assert "owner chai" not in c.get("/transactions").text and "Wallet" not in c.get("/accounts").text
    assert c.post("/api/ledger/create", headers={"X-CSRF-Token": w.guard.csrf}).json()["ok"]
    assert (w.paths.home / ravi.folder / "ledger.db").exists()
    settings = c.get("/settings").text
    # the folder path is shown with \\ on Windows, so look for the account id in it
    assert ravi.id in settings and "Ravi Kumar" in settings and "+91 91234 56789" in settings
    # and the owner still has theirs, untouched
    assert "owner chai" in w.client.get("/transactions").text


def test_profile_can_be_edited(tmp_path: Path) -> None:
    w = Web(tmp_path)
    assert (
        "Profile saved"
        in w.post("/api/profile", {"name": "Asha R", "mobile": "09876543211", "email": "a@b.co"}).text
    )
    me = w.guard.accounts.find("Asha")
    assert me and (me.name, me.mobile, me.email) == ("Asha R", "+919876543211", "a@b.co")
    assert (
        "enter an email"
        in w.post("/api/profile", {"name": "Asha", "mobile": "9876543210", "email": "x"}).text
    )


def test_signing_out_and_in_switches_people(tmp_path: Path) -> None:
    w = Web(tmp_path)
    c = browser(w)
    signup(c)
    c.post("/logout")
    assert (
        c.post("/login", data={"username": "Asha", "password": "correct horse battery"}).headers["x-redirect"]
        == "/dashboard"
    )
    assert "Logged in as <strong>Asha Rao</strong>" in c.get("/dashboard").text


def test_field_rules() -> None:
    assert (
        check_mobile("98765 43210")
        == check_mobile("098765-43210")
        == check_mobile("919876543210")
        == "+919876543210"
    )
    assert check_mobile("+1 (415) 555-0100") == "+14155550100"
    assert format_mobile("+919876543210") == "+91 98765 43210"
    assert check_email("  Me.Name+x@Mail.Example.IN ") == "Me.Name+x@mail.example.in"
    assert check_name("  Priya   S.  ") == "Priya S."
    for bad in ("12345", "5876543210", "+12"):
        with pytest.raises(PasswordError):
            check_mobile(bad)
