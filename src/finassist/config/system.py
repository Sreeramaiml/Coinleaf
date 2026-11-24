"""What differs between macOS, Windows and Linux, kept in one place.

- Where secrets live: the operating system's own secret store, reached through `keyring`
  (macOS Keychain, Windows Credential Manager, Linux Secret Service / KWallet).
- File privacy: on macOS and Linux a private file has no group/other permission bits
  (mode 0600). Windows has no such bits; files under the user's profile (where the data
  folder lives) are private to that user by the folder's access-control list, so the mode
  check does not apply there.
- Binary writes through os.open need O_BINARY on Windows, or newlines get translated.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "IS_MAC",
    "IS_WINDOWS",
    "O_BINARY",
    "computer_name",
    "is_private",
    "make_private_hint",
    "secret_store_name",
]

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
O_BINARY: int = getattr(os, "O_BINARY", 0)


def secret_store_name() -> str:
    if IS_MAC:
        return "macOS Keychain"
    if IS_WINDOWS:
        return "Windows Credential Manager"
    return "system keyring"


def computer_name() -> str:
    """How the app refers to the machine it runs on, in messages."""
    return "this Mac" if IS_MAC else "this computer"


def is_private(path: Path) -> bool:
    """True if only the owner can read the file (always true on Windows; see the module note)."""
    if IS_WINDOWS:
        return True
    return not (path.stat().st_mode & 0o077)


def make_private_hint(path: Path) -> str:
    return f"run: chmod 600 {path}"
