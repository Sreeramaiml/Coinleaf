"""Coinleaf accounts: who can sign in to the browser app, and where each person's data lives.

Each account has a name, mobile number, email, username and a salted scrypt hash of its
password (never the password). They are kept in `users.json` in the data folder, an
owner-only file. Each account's money lives in its own folder with its own encrypted ledger,
its own demo and its own uploads, so people never see each other's data:

- the first account (the "owner") uses the data folder itself, where the ledger created
  before accounts existed already lives (`folder == ""`);
- every account created later by sign-up gets `users/<id>/`.

The first account can only be created from the launch link printed in the terminal (see
web/security.py), so nobody else at the computer can take over the existing ledger. The
mobile number and email are stored as given; Coinleaf makes no network connections, so it
cannot send anything to them to check them.

Migration: an `app-password.json` from before accounts existed (password only, or username
and password) becomes the owner account the first time this store is read.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import threading
import unicodedata
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from finassist.config.password import (
    SCRYPT_P,
    SCRYPT_R,
    PasswordError,
    _hash,
    _normal,
    check_new,
    check_username,
)
from finassist.config.system import O_BINARY

__all__ = [
    "ACCOUNTS_FILE",
    "Account",
    "AccountStore",
    "check_email",
    "check_mobile",
    "check_name",
    "format_mobile",
]

ACCOUNTS_FILE = "users.json"
LEGACY_FILE = "app-password.json"
EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


# ------------------------------------------------------------------------------ validation
def check_name(name: str) -> str:
    n = " ".join(unicodedata.normalize("NFC", name).split())
    if not 2 <= len(n) <= 80 or not any(ch.isalpha() for ch in n):
        raise PasswordError("enter your name (2 to 80 characters)")
    if any(unicodedata.category(ch).startswith("C") for ch in n):
        raise PasswordError("the name has characters that can't be used")
    return n


def check_mobile(mobile: str) -> str:
    """Stored as + and digits. Ten digits starting 6–9 are taken as an Indian number (+91)."""
    s = re.sub(r"[\s\-().]", "", mobile)
    if s.startswith("+") and s[1:].isdigit() and 8 <= len(s) - 1 <= 15:
        return s
    if s.isdigit():
        if len(s) == 10 and s[0] in "6789":
            return "+91" + s
        if len(s) == 11 and s[0] == "0" and s[1] in "6789":
            return "+91" + s[1:]
        if len(s) == 12 and s.startswith("91") and s[2] in "6789":
            return "+" + s
    raise PasswordError("enter a mobile number: 10 digits, or with a country code like +91 98765 43210")


def format_mobile(mobile: str) -> str:
    if mobile.startswith("+91") and len(mobile) == 13:
        return f"+91 {mobile[3:8]} {mobile[8:]}"
    return mobile


def check_email(email: str) -> str:
    e = email.strip()
    if len(e) > 254 or not EMAIL.fullmatch(e):
        raise PasswordError("enter an email address like name@example.com")
    local, domain = e.rsplit("@", 1)
    return f"{local}@{domain.lower()}"


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(_normal(a.strip().casefold()), _normal(b.strip().casefold()))


# --------------------------------------------------------------------------------- model
@dataclass
class Account:
    id: str
    username: str | None  # None only for an owner migrated from a password-only sign-in
    name: str
    mobile: str
    email: str
    folder: str  # relative to the data folder; "" for the owner
    created: str
    salt: str
    hash: str
    n: int
    r: int
    p: int

    @property
    def owner(self) -> bool:
        return self.folder == ""

    @property
    def display_name(self) -> str:
        return self.name or self.username or "Owner"

    @property
    def mobile_display(self) -> str:
        return format_mobile(self.mobile) if self.mobile else ""

    @property
    def profile_complete(self) -> bool:
        return bool(self.name and self.mobile and self.email)


def _hashed(password: str) -> dict[str, Any]:
    from finassist.config import password as pw  # read the cost at call time (tests lower it)

    salt = secrets.token_bytes(16)
    return {
        "salt": salt.hex(),
        "hash": _hash(password, salt, pw.SCRYPT_N, SCRYPT_R, SCRYPT_P).hex(),
        "n": pw.SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
    }


def _password_matches(a: Account, password: str) -> bool:
    try:
        got = _hash(password[:256], bytes.fromhex(a.salt), a.n, a.r, a.p)
        return hmac.compare_digest(got, bytes.fromhex(a.hash))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------------- store
class AccountStore:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.path = home / ACCOUNTS_FILE
        self.legacy = home / LEGACY_FILE
        self._lock = threading.Lock()
        # a fixed record to hash against when a username doesn't exist, so a wrong username
        # takes as long as a wrong password
        self._decoy: dict[str, Any] | None = None

    # -- file
    def _load(self) -> list[Account]:
        if not self.path.is_file() and self.legacy.is_file():
            self._migrate()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        users = data.get("users", []) if isinstance(data, dict) else []
        out = []
        for u in users:
            try:
                out.append(Account(**u))
            except TypeError:
                continue  # a damaged entry never signs anyone in
        return out

    def _save(self, users: list[Account]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | O_BINARY, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(json.dumps({"version": 1, "users": [asdict(u) for u in users]}, indent=1).encode("utf-8"))
        os.replace(tmp, self.path)  # a crash never leaves a half-written file

    def _migrate(self) -> None:
        try:
            old = json.loads(self.legacy.read_text(encoding="utf-8"))
            owner = Account(
                id=secrets.token_hex(6),
                username=old.get("username") or None,
                name="",
                mobile="",
                email="",
                folder="",
                created=datetime.now(UTC).isoformat(timespec="seconds"),
                salt=old["salt"],
                hash=old["hash"],
                n=old["n"],
                r=old["r"],
                p=old["p"],
            )
        except (OSError, ValueError, KeyError, TypeError):
            return
        self._save([owner])
        self.legacy.unlink(missing_ok=True)

    # -- reading
    def accounts(self) -> list[Account]:
        with self._lock:
            return self._load()

    def has_owner(self) -> bool:
        return any(a.owner for a in self.accounts())

    def owner(self) -> Account | None:
        return next((a for a in self.accounts() if a.owner), None)

    def get(self, account_id: str | None) -> Account | None:
        if not account_id:
            return None
        return next((a for a in self.accounts() if a.id == account_id), None)

    def find(self, username: str) -> Account | None:
        return next((a for a in self.accounts() if a.username and _same(a.username, username)), None)

    def folder_of(self, account: Account) -> Path:
        return self.home / account.folder if account.folder else self.home

    # -- signing in
    def verify(self, username: str, password: str) -> Account | None:
        """The account if both match. A hash is computed even for an unknown username."""
        a = self.find(username)
        if a is None:
            if self._decoy is None:
                self._decoy = _hashed(secrets.token_urlsafe(12))
            d = self._decoy
            _hash(password[:256], bytes.fromhex(d["salt"]), d["n"], d["r"], d["p"])
            return None
        return a if _password_matches(a, password) else None

    def password_ok(self, account_id: str, password: str) -> bool:
        a = self.get(account_id)
        return a is not None and _password_matches(a, password)

    # -- changing
    def create(
        self,
        *,
        name: str,
        mobile: str,
        email: str,
        username: str,
        password: str,
        confirm: str,
        owner: bool,
    ) -> Account:
        clean_name, clean_mobile = check_name(name), check_mobile(mobile)
        clean_email, clean_user = check_email(email), check_username(username)
        check_new(password, confirm)
        with self._lock:
            users = self._load()
            self._unique(users, clean_user, clean_email, None)
            if owner and any(u.owner for u in users):
                raise PasswordError("this Coinleaf already has its first account")
            new_id = secrets.token_hex(6)
            a = Account(
                id=new_id,
                username=clean_user,
                name=clean_name,
                mobile=clean_mobile,
                email=clean_email,
                folder="" if owner else f"users/{new_id}",
                created=datetime.now(UTC).isoformat(timespec="seconds"),
                **_hashed(password),
            )
            users.append(a)
            self._save(users)
        return a

    @staticmethod
    def _unique(users: list[Account], username: str | None, email: str | None, me: str | None) -> None:
        for u in users:
            if u.id == me:
                continue
            if username and u.username and _same(u.username, username):
                raise PasswordError("that username is taken; choose another")
            if email and u.email and _same(u.email, email):
                raise PasswordError("an account with this email already exists")

    def _update(self, account_id: str, **changes: Any) -> Account:
        with self._lock:
            users = self._load()
            a = next((u for u in users if u.id == account_id), None)
            if a is None:
                raise PasswordError("this account no longer exists")
            self._unique(users, changes.get("username"), changes.get("email"), account_id)
            for k, v in changes.items():
                setattr(a, k, v)
            self._save(users)
            return a

    def update_profile(self, account_id: str, *, name: str, mobile: str, email: str) -> Account:
        return self._update(
            account_id, name=check_name(name), mobile=check_mobile(mobile), email=check_email(email)
        )

    def set_username(self, account_id: str, username: str) -> Account:
        return self._update(account_id, username=check_username(username))

    def set_password(self, account_id: str, password: str, confirm: str) -> Account:
        check_new(password, confirm)
        return self._update(account_id, **_hashed(password))
