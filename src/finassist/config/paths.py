"""Data locations (ARCH §15). $FINASSIST_HOME overrides the data folder (used by tests)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir, user_documents_dir, user_log_dir

__all__ = ["Paths", "default_paths"]

APP = "finassist"


@dataclass(frozen=True)
class Paths:
    home: Path  # data folder: config, DB, backups, originals
    logs: Path
    reports: Path

    @property
    def config_file(self) -> Path:
        return self.home / "config.toml"

    @property
    def db_file(self) -> Path:
        return self.home / "ledger.db"

    @property
    def backups(self) -> Path:
        return self.home / "backups"

    @property
    def demo(self) -> Paths:
        return Paths(home=self.home / "demo", logs=self.logs / "demo", reports=self.reports / "demo")

    @property
    def inbox(self) -> Path:
        return self.home / "inbox"

    def ensure(self) -> None:
        for p in (self.home, self.logs):
            p.mkdir(parents=True, exist_ok=True)
            os.chmod(p, 0o700)


def default_paths() -> Paths:
    override = os.environ.get("FINASSIST_HOME")
    if override:
        home = Path(override)
        return Paths(home=home, logs=home / "logs", reports=home / "reports")
    return Paths(
        home=Path(user_data_dir(APP, appauthor=False)),
        logs=Path(user_log_dir(APP, appauthor=False)),
        reports=Path(user_documents_dir()) / APP / "reports",
    )
