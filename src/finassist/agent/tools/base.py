"""Tool framework (ARCH §10.2). Every tool has a pydantic input model, an effect tag and a
result split into two parts:

- for_model: compact, redacted, with display strings; this is all the LLM ever sees.
- for_user: full tables rendered locally by the CLI and never sent to the LLM.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine

from finassist.analytics.query import QueryResult
from finassist.config.settings import Settings
from finassist.core.periods import Clock
from finassist.ingest.pipeline import Staging

__all__ = ["Effect", "Preview", "Tool", "ToolContext", "ToolInput", "ToolOutput", "UserTable"]

Effect = Literal["read", "write", "confirm"]


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


@dataclass
class UserTable:
    title: str
    columns: list[str]
    rows: list[list[str]]
    note: str | None = None


@dataclass
class ToolOutput:
    for_model: dict[str, Any]
    for_user: list[UserTable | str] = field(default_factory=list)
    queries: list[QueryResult] = field(default_factory=list)


@dataclass
class Preview:
    lines: list[str]
    digest: str  # identifies exactly what would be written


@dataclass
class ToolContext:
    engine: Engine
    settings: Settings
    clock: Clock
    staging: Staging
    confirm: Callable[[list[str]], bool]  # asks the owner; the model can never answer this
    currency: str = "INR"
    session: dict[str, Any] = field(default_factory=dict)  # e.g. last_query_id for /how


class Tool:
    name: ClassVar[str]
    description: ClassVar[str]
    Input: ClassVar[type[ToolInput]]
    effect: ClassVar[Effect] = "read"

    def run(self, ctx: ToolContext, args: Any) -> ToolOutput:
        raise NotImplementedError

    def preview(self, ctx: ToolContext, args: Any) -> Preview:
        """Required for effect == 'confirm': what would change, without changing it."""
        raise NotImplementedError
