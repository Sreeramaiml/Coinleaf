"""Sign-in for the browser app: first-time setup, sign-in, guessing, idle lock, lock, change, reset."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from test_web import PASSWORD, Web

from finassist.web.app import create_app
from finassist.web.auth import FREE_TRIES, Sessions
from finassist.web.security import COOKIE


def fresh(tmp: Path) -> tuple[Web, TestClient]:
    """A second browser on the same running app (no cookies)."""
    w = Web(tmp)
    c = TestClient(create_app(w.cfg, w.guard))
    c.headers["X-CSRF-Token"] = w.guard.csrf
    return w, c


def test_first_time_setup_needs_the_launch_link(tmp_path: Path) -> None:
    w = Web(tmp_path)
    w.guard.passwords.clear()  # as if never set
    other = TestClient(create_app(w.cfg, w.guard))
    other.headers["X-CSRF-Token"] = w.guard.csrf
    assert other.get("/setup", follow_redirects=False).headers["location"] == "/login"
    page = other.get("/login").text
    assert "Set up Coinleaf" in page and "open the link that Coinleaf printed" in page
    assert (
        other.post("/setup", data={"password": "x" * 12, "confirm": "x" * 12}).headers["x-redirect"]
        == "/login"
    )
    assert not w.guard.passwords.is_set()  # someone without the launch link can't claim the app


def test_setup_checks_the_new_password(tmp_path: Path) -> None:
    w = Web(tmp_path)
    w.guard.passwords.clear()
    c = TestClient(create_app(w.cfg, w.guard))
    c.headers["X-CSRF-Token"] = w.guard.csrf
    assert c.get(w.guard.launch_path(), follow_redirects=False).headers["location"] == "/setup"
    page = c.get("/setup").text
    assert (
        'autocomplete="new-password"' in page and "At least 8 characters" in page and "data-pw-toggle" in page
    )
    assert (
        "use at least 8 characters" in c.post("/setup", data={"password": "short", "confirm": "short"}).text
    )
    assert (
        "don&#39;t match"
        in c.post("/setup", data={"password": "long enough 1", "confirm": "long enough 2"}).text
    )
    assert "only spaces" in c.post("/setup", data={"password": " " * 9, "confirm": " " * 9}).text
    assert not w.guard.passwords.is_set()
    r = c.post("/setup", data={"password": "long enough 1", "confirm": "long enough 1"})
    assert (
        r.headers["x-redirect"] == "/dashboard"
        and c.get("/dashboard", follow_redirects=False).status_code == 200
    )
    assert c.get("/setup", follow_redirects=False).headers["location"] == "/login"  # only once


def test_sign_in_page_and_wrong_password(tmp_path: Path) -> None:
    w, c = fresh(tmp_path)
    page = c.get("/login").text
    assert "Welcome back" in page and 'autocomplete="current-password"' in page and "<script>" not in page
    assert "Forgot your password?" in page and "coinleaf password reset" in page
    assert c.get("/static/app.css").status_code == 200  # the sign-in page's own styles need no session
    r = c.post("/login", data={"password": "wrong password"})
    assert "isn&#39;t right" in r.text and COOKIE not in r.headers.get("set-cookie", "")
    assert c.get("/dashboard", follow_redirects=False).headers["location"] == "/login"
    r = c.post("/login", data={"password": PASSWORD})
    assert r.headers["x-redirect"] == "/dashboard"
    assert c.get("/login", follow_redirects=False).headers["location"] == "/dashboard"


def test_guessing_is_slowed_down(tmp_path: Path) -> None:
    w, c = fresh(tmp_path)
    for _ in range(FREE_TRIES - 1):
        assert "Try again in" not in c.post("/login", data={"password": "guess"}).text
    assert "Try again in 30 seconds" in c.post("/login", data={"password": "guess"}).text
    blocked = c.post("/login", data={"password": PASSWORD})  # even the right one waits
    assert "Too many wrong passwords" in blocked.text and "x-redirect" not in blocked.headers
    w.guard.throttle.until = 0  # time passes
    assert c.post("/login", data={"password": PASSWORD}).headers["x-redirect"] == "/dashboard"
    assert w.guard.throttle.failures == 0


def test_locks_after_the_idle_time(tmp_path: Path) -> None:
    w = Web(tmp_path)
    clock = [1000.0]
    w.guard.sessions = Sessions(idle_seconds=15 * 60, now=lambda: clock[0])
    w.client.cookies.clear()
    assert w.post("/login", {"password": PASSWORD}).headers["x-redirect"] == "/dashboard"
    clock[0] += 14 * 60
    assert w.client.get("/dashboard", follow_redirects=False).status_code == 200  # use keeps it open
    clock[0] += 14 * 60
    assert w.client.get("/dashboard", follow_redirects=False).status_code == 200
    clock[0] += 15 * 60 + 1
    assert w.client.get("/dashboard", follow_redirects=False).headers["location"] == "/login"
    r = w.post("/api/add/preview", {"amount": "1"})  # a form posted from a page left open
    assert r.status_code == 401 and r.headers["x-redirect"] == "/login?locked=1"
    assert "locked itself after 15 minutes" in w.client.get("/login?locked=1").text


def test_lock_button_signs_out(tmp_path: Path) -> None:
    w = Web(tmp_path)
    dash = w.client.get("/dashboard").text
    assert 'data-idle-minutes="15"' in dash and "data-lock" in dash and ">Lock<" in dash
    r = w.post("/logout")
    assert r.headers["x-redirect"] == "/login?signed_out=1"
    assert w.client.get("/dashboard", follow_redirects=False).headers["location"] == "/login"
    assert "You&#39;ve locked Coinleaf" in w.client.get("/login?signed_out=1").text


def test_change_password(tmp_path: Path) -> None:
    w, other = fresh(tmp_path)
    assert other.post("/login", data={"password": PASSWORD}).headers["x-redirect"] == "/dashboard"
    assert "Change password" in w.client.get("/settings").text
    new = "a brand new secret"
    assert (
        "isn&#39;t right"
        in w.post("/api/password", {"current": "nope", "password": new, "confirm": new}).text
    )
    assert (
        "don&#39;t match"
        in w.post("/api/password", {"current": PASSWORD, "password": new, "confirm": "x"}).text
    )
    ok = w.post("/api/password", {"current": PASSWORD, "password": new, "confirm": new}).text
    assert "Password changed" in ok
    assert w.client.get("/dashboard", follow_redirects=False).status_code == 200  # this browser stays in
    assert other.get("/dashboard", follow_redirects=False).status_code == 303  # others must sign in again
    assert not w.guard.passwords.verify(PASSWORD) and w.guard.passwords.verify(new)


def test_only_a_salted_hash_is_stored(tmp_path: Path) -> None:
    w = Web(tmp_path)
    f = w.guard.passwords.path
    raw = f.read_text(encoding="utf-8")
    rec = json.loads(raw)
    assert PASSWORD not in raw and rec["kdf"] == "scrypt" and len(bytes.fromhex(rec["salt"])) == 16
    if os.name != "nt":
        assert f.stat().st_mode & 0o077 == 0
    assert w.guard.passwords.verify(PASSWORD) and not w.guard.passwords.verify(PASSWORD + " ")


def test_cli_password_reset(tmp_path: Path) -> None:
    home = tmp_path / "home"
    env = {**os.environ, "FINASSIST_HOME": str(home)}
    w = Web(tmp_path)
    assert w.guard.passwords.path == home / "app-password.json" and w.guard.passwords.is_set()
    r = subprocess.run(
        [sys.executable, "-m", "finassist", "password", "reset", "--yes"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert r.returncode == 0 and "Password forgotten" in r.stdout, r.stdout + r.stderr
    assert not w.guard.passwords.is_set()
    again = subprocess.run(
        [sys.executable, "-m", "finassist", "password", "reset"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert "No password is set" in again.stdout
