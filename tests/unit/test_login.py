"""Sign-in for the browser app: the first account, username + password sign-in, guessing,
idle lock, lock, changing the username or password, terminal reset, and upgrading a sign-in
made before accounts existed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from test_web import OWNER, PASSWORD, USERNAME, Web

from finassist.web.app import create_app
from finassist.web.auth import FREE_TRIES, Sessions
from finassist.web.security import COOKIE

GOOD = {"username": USERNAME, "password": PASSWORD}


def browser(w: Web) -> TestClient:
    """Another browser on the same running app (no cookies)."""
    c = TestClient(create_app(w.cfg, w.guard))
    c.headers["X-CSRF-Token"] = w.guard.csrf
    return c


def no_accounts(w: Web) -> None:
    (w.paths.home / "users.json").unlink()


def test_first_account_needs_the_launch_link(tmp_path: Path) -> None:
    w = Web(tmp_path)
    no_accounts(w)
    other = browser(w)
    assert other.get("/setup", follow_redirects=False).headers["location"] == "/login"
    assert other.get("/signup", follow_redirects=False).headers["location"] == "/login"
    page = other.get("/login").text
    assert "Set up Coinleaf" in page and "open the link that Coinleaf printed" in page
    assert other.post("/setup", data={**OWNER, "username": "intruder"}).headers["x-redirect"] == "/login"
    assert other.post("/signup", data={**OWNER, "username": "intruder"}).headers["x-redirect"] == "/login"
    assert not w.guard.accounts.has_owner()  # nobody without the launch link can take the existing ledger


def test_first_account_setup_page(tmp_path: Path) -> None:
    w = Web(tmp_path)
    no_accounts(w)
    c = browser(w)
    assert c.get(w.guard.launch_path(), follow_redirects=False).headers["location"] == "/setup"
    page = c.get("/setup").text
    assert "Set up Coinleaf" in page and "owns the ledger already on" in page
    for field in ('name="name"', 'name="mobile"', 'name="email"', 'name="username"', 'name="password"'):
        assert field in page
    r = c.post("/setup", data=OWNER)
    assert (
        r.headers["x-redirect"] == "/dashboard"
        and c.get("/dashboard", follow_redirects=False).status_code == 200
    )
    owner = w.guard.accounts.owner()
    assert owner and owner.folder == "" and owner.name == "Asha Rao" and owner.mobile == "+919876543210"
    assert c.get("/setup", follow_redirects=False).headers["location"] == "/login"  # only once


def test_sign_in_needs_both_username_and_password(tmp_path: Path) -> None:
    w = Web(tmp_path)
    c = browser(w)
    page = c.get("/login").text
    assert "Welcome back" in page and "Enter your username and password" in page
    assert 'autocomplete="username"' in page and 'autocomplete="current-password"' in page
    assert "<script>" not in page and "Forgot your username or password?" in page
    assert 'href="/signup"' in page and "Create an account" in page
    assert c.get("/static/app.css").status_code == 200  # the sign-in page's own styles need no session
    for bad in (
        {"username": "someone", "password": PASSWORD},
        {"username": USERNAME, "password": "wrong pw"},
    ):
        r = c.post("/login", data=bad)
        # the same message for either mistake: nothing reveals which part was right
        assert "That username or password isn&#39;t right." in r.text
        assert COOKIE not in r.headers.get("set-cookie", "")
    assert c.get("/dashboard", follow_redirects=False).headers["location"] == "/login"
    r = c.post("/login", data={"username": USERNAME.upper(), "password": PASSWORD})  # username ignores case
    assert r.headers["x-redirect"] == "/dashboard"
    assert c.get("/login", follow_redirects=False).headers["location"] == "/dashboard"
    assert "Logged in as <strong>Asha Rao</strong>" in c.get("/dashboard").text


def test_guessing_is_slowed_down(tmp_path: Path) -> None:
    w = Web(tmp_path)
    c = browser(w)
    for _ in range(FREE_TRIES - 1):
        assert "Try again in" not in c.post("/login", data={"username": USERNAME, "password": "guess"}).text
    assert (
        "Try again in 30 seconds" in c.post("/login", data={"username": "nobody", "password": "guess"}).text
    )
    blocked = c.post("/login", data=GOOD)  # even the right details wait
    assert "Too many wrong attempts" in blocked.text and "x-redirect" not in blocked.headers
    w.guard.throttle.until = 0  # time passes
    assert c.post("/login", data=GOOD).headers["x-redirect"] == "/dashboard"
    assert w.guard.throttle.failures == 0


def test_locks_after_the_idle_time(tmp_path: Path) -> None:
    w = Web(tmp_path)
    clock = [1000.0]
    w.guard.sessions = Sessions(idle_seconds=15 * 60, now=lambda: clock[0])
    w.client.cookies.clear()
    assert w.post("/login", GOOD).headers["x-redirect"] == "/dashboard"
    clock[0] += 14 * 60
    assert w.client.get("/dashboard", follow_redirects=False).status_code == 200  # use keeps it open
    clock[0] += 14 * 60
    assert w.client.get("/dashboard", follow_redirects=False).status_code == 200
    clock[0] += 15 * 60 + 1
    assert w.client.get("/dashboard", follow_redirects=False).headers["location"] == "/login"
    r = w.post("/api/add/preview", {"amount": "1"})  # a form posted from a page left open
    assert r.status_code == 401 and r.headers["x-redirect"] == "/login?locked=1"
    assert "logged out after 15 minutes" in w.client.get("/login?locked=1").text


def test_lock_button_signs_out(tmp_path: Path) -> None:
    w = Web(tmp_path)
    dash = w.client.get("/dashboard").text
    assert 'data-idle-minutes="15"' in dash and "data-lock" in dash and ">Log out<" in dash
    r = w.post("/logout")
    assert r.headers["x-redirect"] == "/login?signed_out=1"
    assert w.client.get("/dashboard", follow_redirects=False).headers["location"] == "/login"
    assert "You&#39;ve logged out" in w.client.get("/login?signed_out=1").text


def test_change_password_username_or_both(tmp_path: Path) -> None:
    w = Web(tmp_path)
    other = browser(w)
    assert other.post("/login", data=GOOD).headers["x-redirect"] == "/dashboard"
    settings = w.client.get("/settings").text
    assert "Sign-in" in settings and f'value="{USERNAME}"' in settings and "Save changes" in settings
    accounts = w.guard.accounts

    def save(**form: str) -> str:
        return str(w.post("/api/password", {"current": PASSWORD, "username": USERNAME, **form}).text)

    assert "isn&#39;t right" in w.post("/api/password", {"current": "nope", "username": USERNAME}).text
    assert "Nothing to change" in save()
    assert "don&#39;t match" in save(password="a brand new secret", confirm="x")
    assert "the username needs" in save(username="x")
    assert "Username changed to asha.k" in save(username="asha.k")
    assert accounts.verify("asha.k", PASSWORD) is not None and accounts.verify(USERNAME, PASSWORD) is None
    assert other.get("/dashboard", follow_redirects=False).status_code == 303  # others sign in again
    new = "a brand new secret"
    ok = w.post("/api/password", {"current": PASSWORD, "username": "asha.k", "password": new, "confirm": new})
    assert "Password changed" in ok.text
    assert w.client.get("/dashboard", follow_redirects=False).status_code == 200  # this browser stays in
    assert accounts.verify("asha.k", PASSWORD) is None and accounts.verify("asha.k", new) is not None


def test_sign_in_from_before_accounts_is_upgraded(tmp_path: Path) -> None:
    """An app-password.json (password only) becomes the first account; its username is chosen
    at the next sign-in, and the existing ledger stays with it."""
    w = Web(tmp_path)
    rec = json.loads((w.paths.home / "users.json").read_text(encoding="utf-8"))["users"][0]
    legacy = {k: rec[k] for k in ("salt", "hash", "n", "r", "p")} | {"version": 1, "kdf": "scrypt"}
    no_accounts(w)
    (w.paths.home / "app-password.json").write_text(json.dumps(legacy), encoding="utf-8")
    c = browser(w)
    page = c.get("/login").text
    assert "Choose a username" in page and "now signs in with a username" in page
    assert not (w.paths.home / "app-password.json").exists()  # moved into users.json
    owner = w.guard.accounts.owner()
    assert owner and owner.folder == "" and owner.username is None and not owner.profile_complete
    assert (
        "That password isn&#39;t right"
        in c.post("/login", data={"username": "asha", "password": "nope"}).text
    )
    assert "Choose a username" in c.post("/login", data={"username": "a", "password": PASSWORD}).text
    assert (
        c.post("/login", data={"username": "asha", "password": PASSWORD}).headers["x-redirect"]
        == "/dashboard"
    )
    assert w.guard.accounts.verify("asha", PASSWORD) is not None
    assert "Add your name, mobile number and email" in c.get("/settings").text


def test_only_a_salted_hash_of_the_password_is_stored(tmp_path: Path) -> None:
    w = Web(tmp_path)
    f = w.paths.home / "users.json"
    raw = f.read_text(encoding="utf-8")
    rec = json.loads(raw)["users"][0]
    assert PASSWORD not in raw and rec["username"] == USERNAME and len(bytes.fromhex(rec["salt"])) == 16
    if os.name != "nt":
        assert f.stat().st_mode & 0o077 == 0


def _cli(args: list[str], home: Path, stdin: str = "") -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "FINASSIST_HOME": str(home)}
    return subprocess.run(
        [sys.executable, "-m", "finassist", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_cli_users_list_and_password_reset(tmp_path: Path) -> None:
    w = Web(tmp_path)
    out = _cli(["users", "list"], w.paths.home).stdout
    assert USERNAME in out and "Asha Rao" in out and "first account" in out
    r = _cli(["password", "reset"], w.paths.home, "new password 1\nnew password 1\n")
    assert r.returncode == 0 and f"Password changed for {USERNAME}" in r.stdout, r.stdout + r.stderr
    assert w.guard.accounts.verify(USERNAME, "new password 1") is not None
    assert "No account called ghost" in _cli(["password", "reset", "ghost"], w.paths.home).stdout
    short = _cli(["password", "reset", USERNAME], w.paths.home, "short\nshort\n")
    assert short.returncode == 1 and "at least 8 characters" in short.stdout
