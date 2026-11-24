"""Schema migrations (ARCH §6.4): Alembic, run programmatically on the keyed engine."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

__all__ = ["current_revision", "head_revision", "upgrade"]

_DIR = Path(__file__).parent / "migrations"


def _config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_DIR))
    return cfg


def head_revision() -> str | None:
    return ScriptDirectory.from_config(_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def upgrade(engine: Engine, revision: str = "head") -> None:
    cfg = _config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


def autogenerate(engine: Engine, message: str) -> None:
    """Developer helper: write a new migration from the difference to schema.py."""
    cfg = _config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.revision(cfg, message=message, autogenerate=True)
