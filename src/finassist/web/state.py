"""Server-side state for the GUI: one session per ledger (demo, mine).

The GUI never uses a language model, so it never needs the network (ARCH §14.4: it runs
under the strict, loopback-only network guard).
"""

from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from finassist.agent.tools.base import ToolContext
from finassist.cli.env import Env
from finassist.config.paths import Paths
from finassist.config.settings import load_settings
from finassist.core.periods import Clock, FixedClock, SystemClock
from finassist.db.keys import KeyStore
from finassist.db.ledger import Ledger
from finassist.demo.generator import DEMO_TODAY
from finassist.demo.setup import demo_keys
from finassist.ingest.pipeline import Staging

__all__ = ["AppState", "LedgerSession", "WebConfig"]


@dataclass
class WebConfig:
    main_paths: Paths
    real_keys: KeyStore  # holds the real ledger's database key
    config_path: Path
    start_ledger: str = "demo"


@dataclass
class LedgerSession:
    kind: str  # demo | mine
    env: Env
    ledger: Ledger | None = None
    ctx: ToolContext | None = None
    pending: dict[str, Any] = field(default_factory=dict)  # import previews awaiting a click

    @property
    def available(self) -> bool:
        return self.env.paths.db_file.exists()

    def open(self) -> ToolContext:
        if self.ctx is None:
            self.ledger = self.env.ledger()
            # The GUI asks for confirmation with preview pages (policy.prepare/apply), never
            # through this callback; it refuses by default.
            self.ctx = ToolContext(
                engine=self.ledger.engine,
                settings=self.env.settings,
                clock=self.env.clock,
                staging=Staging(),
                confirm=lambda lines: False,
            )
        return self.ctx

    def close(self) -> None:
        if self.ledger is not None:
            self.ledger.close()
        self.ledger = self.ctx = None


class AppState:
    def __init__(self, cfg: WebConfig) -> None:
        self.cfg = cfg
        self.settings = load_settings(cfg.config_path)
        self.sessions: dict[str, LedgerSession] = {}
        self.uploads = cfg.main_paths.home / "uploads"
        if self.uploads.exists():
            shutil.rmtree(self.uploads)  # leftovers from a previous run
        self.lock = threading.Lock()

    def _clock(self, demo: bool) -> Clock:
        if demo:
            return FixedClock(self.settings.demo.today or DEMO_TODAY)
        if self.settings.demo.today:
            return FixedClock(self.settings.demo.today)
        return SystemClock(self.settings.general.timezone)

    def env(self, kind: str) -> Env:
        main = self.cfg.main_paths
        if kind == "demo":
            return Env(True, main.demo, main, self.settings, demo_keys(main), self._clock(True))
        return Env(False, main, main, self.settings, self.cfg.real_keys, self._clock(False))

    def session(self, kind: str) -> LedgerSession:
        if kind not in ("demo", "mine"):
            kind = "demo"
        with self.lock:
            if kind not in self.sessions:
                self.sessions[kind] = LedgerSession(kind, self.env(kind))
            return self.sessions[kind]

    def reset(self, kind: str) -> None:
        with self.lock:
            s = self.sessions.pop(kind, None)
            if s:
                s.close()

    def upload_dir(self) -> Path:
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.uploads.chmod(0o700)
        return self.uploads
