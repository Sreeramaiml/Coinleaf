"""Server-side state for the GUI: for each account, one session per ledger (demo, mine).

Each account has its own data folder (config/accounts.py): the owner's is the data folder
itself, later accounts get users/<id>/. Everything an account opens, uploads or stages lives
in its own UserSpace, so one account never reaches another's ledger.

The GUI never uses a language model, so it never needs the network (ARCH §14.4: it runs
under the strict, loopback-only network guard).
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Callable
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

__all__ = ["AppState", "LedgerSession", "UserSpace", "WebConfig"]


@dataclass
class WebConfig:
    main_paths: Paths
    real_keys: KeyStore  # holds the owner's real-ledger database key
    config_path: Path
    start_ledger: str = "demo"  # the owner's first view
    # The key store for another account's ledger, given its data folder. The default keeps
    # each account's key in the same kind of store as the owner's, under its own folder.
    keys_for: Callable[[Path], KeyStore] | None = None


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


class UserSpace:
    """One account's data: its folder, its ledger key store, and its open ledgers."""

    def __init__(self, paths: Paths, keys: KeyStore, settings: Any, start: str) -> None:
        self.paths = paths
        self.keys = keys
        self.settings = settings
        self.start = start
        self.sessions: dict[str, LedgerSession] = {}
        self.uploads = paths.home / "uploads"
        self.lock = threading.Lock()

    def _clock(self, demo: bool) -> Clock:
        if demo:
            return FixedClock(self.settings.demo.today or DEMO_TODAY)
        if self.settings.demo.today:
            return FixedClock(self.settings.demo.today)
        return SystemClock(self.settings.general.timezone)

    def env(self, kind: str) -> Env:
        main = self.paths
        if kind == "demo":
            return Env(True, main.demo, main, self.settings, demo_keys(main), self._clock(True))
        return Env(False, main, main, self.settings, self.keys, self._clock(False))

    def default_kind(self) -> str:
        return self.start if self.start in ("demo", "mine") else "demo"

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


class AppState:
    def __init__(self, cfg: WebConfig) -> None:
        self.cfg = cfg
        self.settings = load_settings(cfg.config_path)
        self.spaces: dict[str, UserSpace] = {}
        self.lock = threading.Lock()
        home = cfg.main_paths.home
        for leftover in [home / "uploads", *home.glob("users/*/uploads")]:  # from a previous run
            if leftover.is_dir():
                shutil.rmtree(leftover)

    def space(self, account_id: str, folder: str) -> UserSpace:
        with self.lock:
            if account_id not in self.spaces:
                main = self.cfg.main_paths
                if folder == "":
                    self.spaces[account_id] = UserSpace(
                        main, self.cfg.real_keys, self.settings, self.cfg.start_ledger
                    )
                else:
                    home = main.home / folder
                    paths = Paths(home=home, logs=home / "logs", reports=home / "reports")
                    keys = self.cfg.keys_for(home) if self.cfg.keys_for else self._default_keys(home)
                    start = "mine" if paths.db_file.exists() else "demo"
                    self.spaces[account_id] = UserSpace(paths, keys, self.settings, start)
            return self.spaces[account_id]

    def _default_keys(self, home: Path) -> KeyStore:
        from finassist.db.keys import FileKeyStore, KeychainStore

        if isinstance(self.cfg.real_keys, FileKeyStore):
            return FileKeyStore(home / "keys")
        return KeychainStore(home)  # one secret-store entry per account folder
