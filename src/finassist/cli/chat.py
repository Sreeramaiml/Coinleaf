"""Chat REPL (FR-29) with deterministic slash commands that work without any LLM (ARCH §10.8)."""

from __future__ import annotations

import shlex
import sys
from collections.abc import Callable
from typing import Any

from rich.prompt import Confirm

from finassist.agent import policy
from finassist.agent.loop import Agent, TurnResult
from finassist.agent.registry import Registry
from finassist.agent.tools.base import ToolContext
from finassist.agent.tools.p0 import (
    P0_TOOLS,
    CommitImport,
    CommitImportInput,
    Explain,
    ExplainInput,
    GetDataCoverage,
    ImportFile,
    ImportFileInput,
    ListAccounts,
    NoInput,
    QueryInput,
    QueryTransactions,
)
from finassist.cli.env import Env, resolve_api_key
from finassist.cli.render import console, render_tables, render_turn
from finassist.core.errors import FinError
from finassist.ingest.pipeline import Staging
from finassist.llm.provider import LLMProvider

__all__ = ["confirm_in_terminal", "run_chat", "slash"]

HELP = """Ask a question in plain words, or use a command:
  /spend <period>        total money out, e.g. /spend october
  /income <period>       total money in
  /accounts              accounts and balances
  /coverage              which dates each account has data for
  /how [query_id]        the transactions behind a figure (default: the last one)
  /import <path> [acct]  preview and import a statement
  /help                  this list
  /quit                  leave"""


def confirm_in_terminal(lines: list[str]) -> bool:
    console.print("[bold]Confirmation needed[/bold]")
    for line in lines:
        console.print("  " + line)
    if not sys.stdin.isatty():
        answer = sys.stdin.readline()
        console.print(f"  [dim]answer: {answer.strip()}[/dim]")
        return answer.strip().lower() in ("y", "yes")
    return Confirm.ask("  Proceed?", default=False)


def _total_line(label: str, out: Any) -> str:
    fm = out.for_model
    total = fm["totals"]
    if fm["coverage"]["status"] == "none":
        return f"No data for {fm['period']['label']}: " + "; ".join(fm["coverage"]["gaps"])
    n = total["count"]
    return (
        f"{label} {fm['period']['label']}: {total['sum']['display']} ({n} transaction{'' if n == 1 else 's'})"
    )


def slash(ctx: ToolContext, line: str) -> TurnResult | None:
    """Run one slash command; returns None for /quit."""
    try:
        parts = shlex.split(line[1:])
    except ValueError as e:
        return TurnResult(text=f"Could not read that command: {e}")
    if not parts:
        return TurnResult(text=HELP)
    cmd, rest = parts[0].lower(), parts[1:]
    try:
        if cmd in ("quit", "exit", "q"):
            return None
        if cmd == "help":
            return TurnResult(text=HELP)
        if cmd == "accounts":
            out = ListAccounts().run(ctx, NoInput())
            return TurnResult(text="", tables=out.for_user)
        if cmd == "coverage":
            out = GetDataCoverage().run(ctx, NoInput())
            lines = []
            for a in out.for_model["accounts"]:
                cov = ", ".join(a.get("covered", [])) or (
                    f"{a.get('transactions_from')} to {a.get('transactions_to')}"
                    if a.get("transactions_from")
                    else "no data"
                )
                lines.append(f"  {a['account']:<18} {a['source']:<11} {cov}")
            return TurnResult(
                text="Data coverage (today " + out.for_model["today"] + "):\n" + "\n".join(lines)
            )
        if cmd in ("spend", "income"):
            if not rest:
                return TurnResult(text=f"Usage: /{cmd} <period>, e.g. /{cmd} last month")
            period = " ".join(rest)
            args = QueryInput(
                period=period,
                kinds=["expense"] if cmd == "spend" else ["income"],
                money_out_positive=cmd == "spend",
            )
            out = QueryTransactions().run(ctx, args)
            r = TurnResult(text=_total_line("Money out" if cmd == "spend" else "Money in", out))
            q = out.queries[0]
            ctx.session["last_query_id"] = q.query_id
            tx = q.quality["transactions"]
            un = q.quality.get("uncategorised", {})
            footer = f"{q.query_id}: {q.scope_text} · {tx} transaction{'' if tx == 1 else 's'}"
            if un.get("count"):
                footer += f" · {un['count']} uncategorised ({un['amount']['display']})"
            r.footers.append(footer)
            r.warnings.extend(q.notes)
            return r
        if cmd == "how":
            qid = rest[0] if rest else ctx.session.get("last_query_id")
            if not qid:
                return TurnResult(text="Usage: /how <query_id> (no figure has been shown yet)")
            out = Explain().run(ctx, ExplainInput(query_id=qid))
            return TurnResult(text=f"Filter: {out.for_model['scope']}", tables=out.for_user)
        if cmd == "import":
            if not rest:
                return TurnResult(text="Usage: /import <path> [account]")
            out = ImportFile().run(ctx, ImportFileInput(path=rest[0], account=" ".join(rest[1:]) or None))
            render_tables(out.for_user)
            if out.for_model["status"] != "ready":
                return TurnResult(text="")
            res = policy.execute(CommitImport(), ctx, CommitImportInput(import_id=out.for_model["import_id"]))
            fm = res.for_model
            if fm.get("status") == "committed":
                return TurnResult(text=f"Imported {fm['inserted']} transactions into {fm['account']}.")
            return TurnResult(text=fm.get("message", "Nothing was imported."))
    except FinError as e:
        return TurnResult(text=f"{e.message}" + (f" ({e.hint})" if e.hint else ""))
    except ValueError as e:
        return TurnResult(text=str(e))
    return TurnResult(text=f"Unknown command /{cmd}. " + HELP)


PAID_TIER_HINT = (
    "Gemini's free tier lets Google use what you send to improve its products, and human reviewers may "
    "read it; Google's terms say not to send personal information to it. Your ledger is therefore not "
    "sent to Gemini until you confirm your API key belongs to a billing-enabled Google Cloud project: set "
    "`gemini_paid_tier = true` under [llm] in config.toml. Slash commands work meanwhile."
)


def make_provider(env: Env) -> tuple[LLMProvider | None, str]:
    """The configured provider, or None with the reason chat is slash-commands-only."""
    llm = env.settings.llm
    mode = env.settings.privacy.llm_mode
    if mode == "off":
        return None, "LLM off (privacy.llm_mode = off): slash commands only"
    if mode == "local":
        return None, "local-model mode arrives in P2: slash commands only for now"
    label = {"gemini": "Gemini", "anthropic": "Anthropic"}[llm.provider]
    key, source = resolve_api_key(env)
    if key is None and source.startswith("invalid key"):
        return None, f"{source}. Slash commands only for now."
    if key is None:
        return None, (
            f"no {label} API key found (environment, OS secret store or ./.env): slash commands only. "
            f"Add one with `coinleaf config set-api-key --provider {llm.provider}`."
        )
    if llm.provider == "gemini":
        if not llm.gemini_paid_tier and not env.demo:
            return None, PAID_TIER_HINT
        from finassist.llm.gemini_provider import GeminiProvider

        tier = "paid tier confirmed" if llm.gemini_paid_tier else "demo data only; paid tier not confirmed"
        return GeminiProvider(llm, api_key=key), f"Gemini {llm.chat_model} (key from {source}; {tier})"
    from finassist.llm.anthropic_provider import AnthropicProvider

    return AnthropicProvider(llm, api_key=key), f"Anthropic {llm.chat_model} (key from {source})"


def run_chat(env: Env, provider: LLMProvider | None = None, read: Callable[[], str] | None = None) -> None:
    ledger = env.ledger()
    ctx = ToolContext(
        engine=ledger.engine,
        settings=env.settings,
        clock=env.clock,
        staging=Staging(),
        confirm=confirm_in_terminal,
    )
    status = "provided"
    if provider is None:
        provider, status = make_provider(env)
    agent = (
        Agent(
            ctx,
            provider,
            Registry(
                P0_TOOLS, strict=env.settings.llm.provider == "anthropic" and env.settings.llm.strict_tools
            ),
            model=env.settings.llm.chat_model,
            max_iterations=env.settings.llm.max_iterations,
        )
        if provider
        else None
    )
    which = "DEMO ledger (synthetic data)" if env.demo else "your ledger"
    console.print(f"[bold]Coinleaf[/bold] · {which} · today {env.clock.today():%d %b %Y} · {status}")
    console.print("[dim]Type a question, /help for commands, /quit to leave.[/dim]")

    if read is None:
        if sys.stdin.isatty():
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import InMemoryHistory

            session: PromptSession[str] = PromptSession(history=InMemoryHistory())  # nothing written to disk

            def read() -> str:
                return session.prompt("› ")
        else:

            def read() -> str:
                line = sys.stdin.readline()
                if line == "":
                    raise EOFError
                console.print(f"\n[bold]› {line.rstrip()}[/bold]")
                return line

    while True:
        try:
            line = read().strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line.startswith("/"):
            r = slash(ctx, line)
            if r is None:
                break
            render_turn(r)
            continue
        if agent is None:
            console.print("[yellow]No language model is available; use slash commands (/help).[/yellow]")
            continue
        turn = agent.turn(line)
        if turn.footers:
            ctx.session["last_query_id"] = turn.footers[-1].split(":", 1)[0]
        render_turn(turn)
    ledger.close()
