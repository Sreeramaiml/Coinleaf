"""Terminal rendering with Rich."""

from __future__ import annotations

import sys
from typing import Literal

from rich.console import Console
from rich.table import Table

from finassist.agent.loop import TurnResult
from finassist.agent.tools.base import UserTable

__all__ = ["console", "render_tables", "render_turn"]

# Piped or redirected output (scripts, logs, tests) isn't hard-wrapped mid-sentence; tables
# still get a fixed width there.
_tty = sys.stdout.isatty()
console = Console(width=None if _tty else 118, highlight=False, soft_wrap=not _tty)

MAX_ROWS = 60


def render_tables(items: list[UserTable | str]) -> None:
    for item in items:
        if isinstance(item, str):
            console.print(item)
            continue
        t = Table(title=item.title, title_justify="left", show_lines=False, header_style="bold")
        for c in item.columns:
            justify: Literal["left", "right"] = (
                "right"
                if c.lower() in ("amount", "balance", "sum", "avg", "min", "max", "median", "count")
                else "left"
            )
            t.add_column(c, justify=justify, overflow="fold")
        for row in item.rows[:MAX_ROWS]:
            t.add_row(*row)
        console.print(t)
        if len(item.rows) > MAX_ROWS:
            console.print(f"[dim]… {len(item.rows) - MAX_ROWS} more rows (export arrives in P2)[/dim]")
        if item.note:
            console.print(f"[dim]{item.note}[/dim]")


def render_turn(r: TurnResult, show_tables: bool = True) -> None:
    if r.text:
        console.print(r.text)
    if show_tables:
        render_tables(r.tables)
    for w in r.warnings:
        console.print(f"[yellow]! {w}[/yellow]")
    for f in r.footers:
        console.print(f"[dim]Scope {f} · /how {f.split(':', 1)[0]}[/dim]")
