"""Secret storage (NFR-4, ARCH §14.5).

- KeychainStore: the operating system's secret store via `keyring` (macOS Keychain,
  Windows Credential Manager, Linux Secret Service / KWallet). Used for the real ledger.
- FileKeyStore: owner-only files in a private folder. Used for the demo ledger, whose data
  is synthetic, and, when the owner opts in with FINASSIST_KEYSTORE=file, on a Linux
  machine that has no secret store.
- MemoryKeyStore: tests.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import secrets
from pathlib import Path
from typing import Protocol

from finassist.config.system import O_BINARY, is_private, make_private_hint, secret_store_name
from finassist.core.errors import FinError

__all__ = [
    "API_KEYS",
    "ApiKeyError",
    "clean_api_key",
    "FileKeyStore",
    "KeyStore",
    "KeychainStore",
    "KeyStoreUnavailable",
    "MemoryKeyStore",
    "new_db_key",
]

DB_KEY = "db-key"
API_KEY = "anthropic-api-key"
GEMINI_API_KEY = "gemini-api-key"
# Keychain item name per LLM provider.
API_KEYS = {"anthropic": API_KEY, "gemini": GEMINI_API_KEY}


# Terminal escape sequences that end up in a key typed or pasted at a hidden prompt:
# arrow keys (ESC[C), bracketed paste markers (ESC[200~ … ESC[201~), and similar.
_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]?")
_KEY_CHARS = re.compile(r"[A-Za-z0-9._~-]+")


class ApiKeyError(ValueError):
    pass


def clean_api_key(raw: str) -> str:
    """Remove terminal escape sequences, control characters and surrounding whitespace.
    Raises ApiKeyError if what remains has characters no API key uses."""
    s = _ESCAPES.sub("", raw)
    s = "".join(ch for ch in s if ch.isprintable()).strip().strip("'\"")
    if not s:
        raise ApiKeyError("the API key is empty")
    if not _KEY_CHARS.fullmatch(s):
        raise ApiKeyError(
            "the API key contains characters an API key never has (spaces or punctuation); "
            "copy it again from the provider's console"
        )
    return s


def new_db_key() -> str:
    """256 random bits as 64 hex characters (used as a raw SQLCipher key, no KDF)."""
    return secrets.token_hex(32)


class KeyStoreUnavailable(FinError):
    code = "keystore_unavailable"


def _unavailable(e: Exception) -> KeyStoreUnavailable:
    name = secret_store_name()
    return KeyStoreUnavailable(
        f"the {name} could not be used ({type(e).__name__})",
        hint="on Linux, install and unlock GNOME Keyring or KWallet (any Secret Service keyring), then "
        "try again. Or set FINASSIST_KEYSTORE=file to keep the ledger key in an owner-only file in the "
        "data folder instead (anyone who can read your account's files could then open the ledger)"
        if name == "system keyring"
        else f"unlock the {name} and try again",
    )


class KeyStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class KeychainStore:
    """Database keys use one Keychain service per data folder, so a test or second ledger
    never reads the real ledger's key. API keys belong to the person, not to a ledger, so
    they share one service (`KeychainStore.for_api_keys()`)."""

    API_SERVICE = "finassist:api-keys"

    def __init__(self, home: Path | None = None, *, service: str | None = None) -> None:
        if service is None:
            if home is None:
                raise ValueError("give a data folder or a service name")
            digest = hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:8]
            service = f"finassist:{digest}"
        self.service = service

    @classmethod
    def for_api_keys(cls) -> KeychainStore:
        return cls(service=cls.API_SERVICE)

    def get(self, name: str) -> str | None:
        import keyring
        from keyring.errors import KeyringError

        try:
            return keyring.get_password(self.service, name)
        except KeyringError as e:
            raise _unavailable(e) from None

    def set(self, name: str, value: str) -> None:
        import keyring
        from keyring.errors import KeyringError

        try:
            keyring.set_password(self.service, name, value)
        except KeyringError as e:
            raise _unavailable(e) from None

    def delete(self, name: str) -> None:
        import keyring
        from keyring.errors import KeyringError, PasswordDeleteError

        try:
            with contextlib.suppress(PasswordDeleteError):
                keyring.delete_password(self.service, name)
        except KeyringError as e:
            raise _unavailable(e) from None


class FileKeyStore:
    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def _path(self, name: str) -> Path:
        if not name.replace("-", "").isalnum():
            raise ValueError("bad key name")
        return self.folder / f"{name}.key"

    def get(self, name: str) -> str | None:
        p = self._path(name)
        if not p.exists():
            return None
        if not is_private(p):
            raise PermissionError(f"{p} is readable by other users; {make_private_hint(p)}")
        return p.read_text(encoding="utf-8").strip()

    def set(self, name: str, value: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)
        p = self._path(name)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | O_BINARY, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(value.encode("utf-8"))
        os.chmod(p, 0o600)

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)


class MemoryKeyStore:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._data.get(name)

    def set(self, name: str, value: str) -> None:
        self._data[name] = value

    def delete(self, name: str) -> None:
        self._data.pop(name, None)
