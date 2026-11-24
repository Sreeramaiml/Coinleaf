"""Signed-in sessions and protection against password guessing, for the browser app.

Sessions live in memory only: restarting Coinleaf signs everyone out. A session ends after
IDLE_MINUTES without a request (the page also locks itself after the same time without
mouse or keyboard use, so figures don't stay on screen).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable

__all__ = ["IDLE_MINUTES", "Sessions", "Throttle"]

IDLE_MINUTES = 15
FREE_TRIES = 5  # wrong passwords allowed before waiting
FIRST_WAIT = 30  # seconds; doubles with each further wrong password
MAX_WAIT = 15 * 60


class Sessions:
    """Signed-in browsers: random session id -> (account id, time of last use)."""

    def __init__(
        self, idle_seconds: float = IDLE_MINUTES * 60, now: Callable[[], float] = time.monotonic
    ) -> None:
        self.idle = idle_seconds
        self.now = now
        self._s: dict[str, tuple[str, float]] = {}

    def new(self, account_id: str) -> str:
        sid = secrets.token_urlsafe(32)
        self._s[sid] = (account_id, self.now())
        return sid

    def user(self, sid: str | None, *, touch: bool = True) -> str | None:
        """The signed-in account id, if the session exists and was used within the idle time."""
        if not sid:
            return None
        key = next((k for k in self._s if secrets.compare_digest(k, sid)), None)
        if key is None:
            return None
        account_id, last = self._s[key]
        if self.now() - last > self.idle:
            del self._s[key]
            return None
        if touch:
            self._s[key] = (account_id, self.now())
        return account_id

    def end(self, sid: str | None) -> None:
        if sid:
            self._s.pop(sid, None)

    def end_others(self, account_id: str, keep: str | None) -> None:
        """Sign out this account's other browsers (after a password or username change)."""
        self._s = {k: v for k, v in self._s.items() if v[0] != account_id or k == keep}


class Throttle:
    """After FREE_TRIES wrong passwords, each further try waits: 30 s, 60 s, 120 s … up to 15 min."""

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self.now = now
        self.failures = 0
        self.until = 0.0

    def wait_seconds(self) -> int:
        return max(0, int(self.until - self.now() + 0.999))

    def failed(self) -> None:
        self.failures += 1
        if self.failures >= FREE_TRIES:
            wait = min(MAX_WAIT, FIRST_WAIT * 2 ** (self.failures - FREE_TRIES))
            self.until = self.now() + wait

    def succeeded(self) -> None:
        self.failures = 0
        self.until = 0.0
