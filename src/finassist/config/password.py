"""Password hashing and the rules for usernames and passwords (used by config/accounts.py).

Passwords are never stored: only a salted scrypt hash, with the cost parameters it was made
with, so the cost can rise later without breaking existing sign-ins.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

__all__ = ["MIN_LENGTH", "PasswordError", "check_new", "check_username"]

MIN_LENGTH = 8
MAX_LENGTH = 256
USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{2,31}")
# scrypt cost. Tests lower SCRYPT_N; each stored hash records the parameters it was made with.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1


class PasswordError(ValueError):
    """A message a person can act on (shown as "Not saved: …")."""


def _normal(text: str) -> bytes:
    # The same text typed on another keyboard or system gives the same bytes.
    return unicodedata.normalize("NFC", text).encode("utf-8")


def _hash(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(_normal(password), salt=salt, n=n, r=r, p=p, maxmem=256 * n * r, dklen=32)


def check_username(username: str) -> str:
    """The username as it will be stored."""
    u = unicodedata.normalize("NFC", username.strip())
    if not USERNAME.fullmatch(u):
        raise PasswordError(
            "the username needs 3 to 32 characters: letters, digits, and . _ - @ (starting with a letter or digit)"
        )
    return u


def check_new(password: str, confirm: str) -> None:
    if len(password) < MIN_LENGTH:
        raise PasswordError(f"use at least {MIN_LENGTH} characters for the password")
    if len(password) > MAX_LENGTH:
        raise PasswordError(f"use at most {MAX_LENGTH} characters for the password")
    if not password.strip():
        raise PasswordError("the password can't be only spaces")
    if password != confirm:
        raise PasswordError("the two passwords don't match")
