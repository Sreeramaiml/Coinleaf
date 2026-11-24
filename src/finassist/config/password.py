"""The password that unlocks the browser app.

Only a salted scrypt hash is stored (never the password), in an owner-only file in the data
folder. What it protects: the open app in the browser, so someone else at the computer can't
see your finances while Coinleaf is running or after it locks. What it does not replace: the
ledger stays encrypted with a key in the OS secret store, and anyone signed in to your
computer account can reset this password from the terminal (`coinleaf password reset`).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import unicodedata
from pathlib import Path

from finassist.config.system import O_BINARY

__all__ = ["FILE_NAME", "MIN_LENGTH", "PasswordError", "PasswordStore", "check_new"]

FILE_NAME = "app-password.json"  # in the data folder
MIN_LENGTH = 8
MAX_LENGTH = 256
# scrypt cost. Tests lower SCRYPT_N; each stored hash records the parameters it was made with.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1


class PasswordError(ValueError):
    pass


def _normal(password: str) -> bytes:
    # The same password typed on another keyboard or system gives the same bytes.
    return unicodedata.normalize("NFC", password).encode("utf-8")


def _hash(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(_normal(password), salt=salt, n=n, r=r, p=p, maxmem=256 * n * r, dklen=32)


def check_new(password: str, confirm: str) -> None:
    """Raise PasswordError with a message a person can act on."""
    if len(password) < MIN_LENGTH:
        raise PasswordError(f"use at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        raise PasswordError(f"use at most {MAX_LENGTH} characters")
    if not password.strip():
        raise PasswordError("the password can't be only spaces")
    if password != confirm:
        raise PasswordError("the two passwords don't match")


class PasswordStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def is_set(self) -> bool:
        return self.path.is_file()

    def set(self, password: str, confirm: str) -> None:
        check_new(password, confirm)
        salt = secrets.token_bytes(16)
        record = {
            "version": 1,
            "kdf": "scrypt",
            "n": SCRYPT_N,
            "r": SCRYPT_R,
            "p": SCRYPT_P,
            "salt": salt.hex(),
            "hash": _hash(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P).hex(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | O_BINARY, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(json.dumps(record).encode("utf-8"))
        os.replace(tmp, self.path)  # a crash never leaves a half-written password file

    def verify(self, password: str) -> bool:
        try:
            rec = json.loads(self.path.read_text(encoding="utf-8"))
            expected = bytes.fromhex(rec["hash"])
            got = _hash(password[:MAX_LENGTH], bytes.fromhex(rec["salt"]), rec["n"], rec["r"], rec["p"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        return hmac.compare_digest(got, expected)

    def clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
