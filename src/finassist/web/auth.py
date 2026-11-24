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
    def __init__(
        self, idle_seconds: float = IDLE_MINUTES * 60, now: Callable[[], float] = time.monotonic
    ) -> None:
        self.idle = idle_seconds
        self.now = now
        self._last: dict[str, float] = {}

    def new(self) -> str:
        sid = secrets.token_urlsafe(32)
        self._last[sid] = self.now()
        return sid

    def valid(self, sid: str | None, *, touch: bool = True) -> bool:
        """True if the session exists and has been used within the idle time."""
        if not sid:
            return False
        last = next((v for k, v in self._last.items() if secrets.compare_digest(k, sid)), None)
        if last is None:
            return False
        if self.now() - last > self.idle:
            self._last.pop(sid, None)
            return False
        if touch:
            self._last[sid] = self.now()
        return True

    def end(self, sid: str | None) -> None:
        if sid:
            self._last.pop(sid, None)

    def end_all_except(self, keep: str | None) -> None:
        self._last = {k: v for k, v in self._last.items() if k == keep}


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
