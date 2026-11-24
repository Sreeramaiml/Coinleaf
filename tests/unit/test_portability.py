"""Running on macOS, Windows and Linux: secret store, file privacy, wording, launchers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from finassist.config import system
from finassist.db.keys import FileKeyStore, KeychainStore, KeyStoreUnavailable

ROOT = Path(__file__).parents[2]


@pytest.fixture
def no_keyring() -> object:
    """A machine with no usable secret store (e.g. a Linux server without a keyring daemon)."""
    import keyring
    from keyring.backends import fail

    before = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    yield
    keyring.set_keyring(before)


def test_missing_secret_store_is_a_clear_error_not_a_crash(no_keyring: object, tmp_path: Path) -> None:
    store = KeychainStore(tmp_path)
    for call in (
        lambda: store.get("db-key"),
        lambda: store.set("db-key", "x"),
        lambda: store.delete("db-key"),
    ):
        with pytest.raises(KeyStoreUnavailable) as e:
            call()
        assert system.secret_store_name() in e.value.message and e.value.hint


def test_linux_hint_explains_the_keyring_and_the_file_option(monkeypatch: pytest.MonkeyPatch) -> None:
    from finassist.db import keys

    monkeypatch.setattr(keys, "secret_store_name", lambda: "system keyring")
    e = keys._unavailable(RuntimeError("no backend"))
    assert "GNOME Keyring or KWallet" in (e.hint or "") and "FINASSIST_KEYSTORE=file" in (e.hint or "")


def test_secret_store_and_computer_names(monkeypatch: pytest.MonkeyPatch) -> None:
    for mac, win, store, computer in [
        (True, False, "macOS Keychain", "this Mac"),
        (False, True, "Windows Credential Manager", "this computer"),
        (False, False, "system keyring", "this computer"),
    ]:
        monkeypatch.setattr(system, "IS_MAC", mac)
        monkeypatch.setattr(system, "IS_WINDOWS", win)
        assert (system.secret_store_name(), system.computer_name()) == (store, computer)


def test_privacy_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "k"
    f.write_text("x", encoding="utf-8")
    if os.name != "nt":
        f.chmod(0o644)
        assert not system.is_private(f)
        f.chmod(0o600)
        assert system.is_private(f)
    monkeypatch.setattr(system, "IS_WINDOWS", True)  # Windows: the profile folder's ACL protects it
    f.chmod(0o644)
    assert system.is_private(f)


def test_file_key_store_round_trip_is_exact(tmp_path: Path) -> None:
    store = FileKeyStore(tmp_path / "keys")
    store.set("db-key", "ab" * 32)
    assert store.get("db-key") == "ab" * 32
    assert (tmp_path / "keys" / "db-key.key").read_bytes() == b"ab" * 32  # no newline translation
    if os.name != "nt":
        (tmp_path / "keys" / "db-key.key").chmod(0o644)
        with pytest.raises(PermissionError, match="chmod 600"):
            store.get("db-key")


def test_gui_wording_follows_the_computer(tmp_path: Path) -> None:
    from test_web import Web

    w = Web(tmp_path)
    page = w.client.get("/settings").text
    assert f"reachable only from {system.computer_name()}" in page
    if sys.platform != "darwin":
        assert "Mac" not in page


def test_launchers_exist_for_each_system() -> None:
    mac, win, linux = ROOT / "Coinleaf.command", ROOT / "Coinleaf.bat", ROOT / "coinleaf.sh"
    assert "uv" in mac.read_text() and "uv" in linux.read_text()
    bat = win.read_bytes()
    assert b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"")  # cmd.exe needs CRLF line endings
    assert b"install.ps1" in bat and b"run coinleaf" in bat
    if os.name != "nt":
        assert os.access(mac, os.X_OK) and os.access(linux, os.X_OK)


def test_name_and_logo() -> None:
    import tomllib

    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert (
        scripts["coinleaf"] == scripts["finassist"] == "finassist.cli.app:main"
    )  # old name kept as an alias
    icon = (ROOT / "src/finassist/web/static/icon.svg").read_text(encoding="utf-8")
    assert 'aria-label="Coinleaf"' in icon and "#476c24" in icon


def test_gui_shows_the_coinleaf_name(tmp_path: Path) -> None:
    from test_web import Web

    page = Web(tmp_path).client.get("/dashboard").text
    assert (
        "· Coinleaf</title>" in page and "<span>Coinleaf</span>" in page and "finassist" not in page.lower()
    )


def test_theme_uses_the_logo_green() -> None:
    css = (ROOT / "src/finassist/web/static/app.css").read_text(encoding="utf-8")
    assert "--accent: #476c24;" in css and css.count("--accent: #a8c96e;") == 2  # light, dark x2
    assert "0f766e" not in css and "2dd4bf" not in css  # the old teal is gone
