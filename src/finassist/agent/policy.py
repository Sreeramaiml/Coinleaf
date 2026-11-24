"""Write policy (PS §8 contract item 4, ARCH §10.4).

- read: runs freely.
- write: runs; recorded in a change set.
- confirm: the tool previews what it would change; the owner answers; only a yes runs it.
  The confirmation token binds the answer to that exact preview and to the ledger's
  state: if either changes before it is applied, nothing is written. The model has no
  input that can supply a confirmation.

`execute` asks through `ctx.confirm` in one call (terminal, or a GUI turn waiting for a
click). `prepare` + `apply` split the same check across two steps for forms in the GUI.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import func, select

from finassist.agent.tools.base import Preview, Tool, ToolContext, ToolOutput
from finassist.core.errors import Conflict
from finassist.db.schema import change_set

__all__ = ["apply", "execute", "prepare"]


def _data_version(ctx: ToolContext) -> str:
    with ctx.engine.connect() as conn:
        n, last = conn.execute(select(func.count(), func.max(change_set.c.id))).one()
    return f"{n}|{last}"


def prepare(tool: Tool, ctx: ToolContext, args: Any) -> tuple[Preview, str]:
    """The preview and its confirmation token. Writes nothing."""
    preview = tool.preview(ctx, args)
    token = hashlib.sha256(f"{tool.name}|{preview.digest}|{_data_version(ctx)}".encode()).hexdigest()
    return preview, token


def apply(tool: Tool, ctx: ToolContext, args: Any, token: str) -> ToolOutput:
    """Run a confirmed tool, if the preview and the ledger are unchanged since `token`."""
    _, again = prepare(tool, ctx, args)
    if again != token:
        raise Conflict(
            "the data changed while waiting for confirmation; nothing was written", hint="preview again"
        )
    out = tool.run(ctx, args)
    out.for_model.setdefault("confirmed_by_owner", True)
    return out


def execute(tool: Tool, ctx: ToolContext, args: Any) -> ToolOutput:
    if tool.effect != "confirm":
        return tool.run(ctx, args)
    preview, token = prepare(tool, ctx, args)
    if not ctx.confirm(preview.lines):
        return ToolOutput({"status": "declined", "message": "The owner declined. Nothing was changed."})
    return apply(tool, ctx, args, token)
