"""Coinleaf command line (ARCH §12.1)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from finassist.agent import policy
from finassist.agent.tools.base import ToolContext
from finassist.agent.tools.p0 import AddTransaction, AddTransactionInput
from finassist.cli.env import Env, make_env
from finassist.cli.render import console, render_turn
from finassist.config.paths import default_paths
from finassist.config.settings import ConfigError
from finassist.config.system import IS_WINDOWS, secret_store_name
from finassist.core.errors import FinError
from finassist.core.money import format_minor, parse_amount
from finassist.db.audit import open_change_set
from finassist.db.keys import API_KEYS, ApiKeyError, KeychainStore, KeyStoreUnavailable, clean_api_key
from finassist.db.ledger import LedgerNotInitialised, init_ledger
from finassist.db.repo.accounts import ACCOUNT_TYPES, create_account
from finassist.ingest.pipeline import Staging, commit_import, stage_import
from finassist.llm import netguard

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help="Coinleaf, your personal financial assistant: a local encrypted ledger with a conversational front end.",
)
account_app = typer.Typer(help="Manage accounts.")
config_app = typer.Typer(help="Configuration and secrets.")
password_app = typer.Typer(help="Passwords for the browser app's accounts.")
users_app = typer.Typer(help="The people with a Coinleaf account (sign-ins, not bank accounts).")
app.add_typer(account_app, name="account")
app.add_typer(config_app, name="config")
app.add_typer(password_app, name="password")
app.add_typer(users_app, name="users")

_state: dict[str, Env] = {}


def _env() -> Env:
    return _state["env"]


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    demo: bool = typer.Option(False, "--demo", help="Use the separate demo ledger (synthetic data)."),
) -> None:
    try:
        env = make_env(demo)
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(2) from None
    _state["env"] = env
    command = ctx.invoked_subcommand or "gui"
    # Network guard (ARCH §14.4): only terminal `chat` in cloud mode may reach the API host.
    # Every other command, including the browser app (which uses no AI), runs loopback-only.
    if command == "chat" and env.settings.privacy.llm_mode == "cloud":
        netguard.install("cloud", [env.settings.llm.api_host])
    else:
        netguard.install("strict")
    if ctx.invoked_subcommand is None:
        gui(port=8765, no_browser=False)  # called directly: pass real values, not Typer defaults


@app.command()
def init() -> None:
    """Create the encrypted ledger (key in the OS secret store) and seed the category taxonomy."""
    env = _env()
    if env.demo:
        console.print("Use `coinleaf demo` to build the demo ledger.")
        raise typer.Exit(1)
    env.paths.ensure()
    try:
        ledger, created = init_ledger(env.paths.db_file, env.keys)
    except LedgerNotInitialised as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    ledger.close()
    if created:
        console.print(f"Created an encrypted ledger at {env.paths.db_file}")
        where = (
            f"the {secret_store_name()}"
            if isinstance(env.keys, KeychainStore)
            else f"an owner-only file in {env.paths.home / 'keys'} (FINASSIST_KEYSTORE=file)"
        )
        console.print(f"The database key is stored in {where}.")
    else:
        console.print(f"Ledger already initialised at {env.paths.db_file}; nothing changed.")
    console.print("Next: `coinleaf account add`, then `coinleaf import <statement.csv>`.")


@app.command()
def demo(reset: bool = typer.Option(False, "--reset", help="Delete and rebuild the demo ledger.")) -> None:
    """Build the demo ledger from synthetic data (separate from your real ledger)."""
    from finassist.demo.setup import build_demo

    env = _env()
    if env.main_paths.demo.db_file.exists() and not reset:
        console.print(
            f"Demo ledger already exists at {env.main_paths.demo.db_file}. Use --reset to rebuild it."
        )
        return
    r = build_demo(env.main_paths, reset=reset)
    r.ledger.close()
    console.print(f"Demo ledger built at {env.main_paths.demo.db_file} (synthetic data only).")
    console.print(f"  accounts: Bank A Savings, Card A, Cash · {len(r.data.truth)} transactions generated")
    console.print(f"  imported {len(r.imported)} statement files; demo date pinned to {r.today:%d %b %Y}")
    for p in r.left_in_inbox:
        console.print(f"  left for you to import: {p}")
    console.print("Try: coinleaf --demo chat")


@account_app.command("add")
def account_add(
    name: str = typer.Option(..., prompt=True, help="Full account name, e.g. 'HDFC Savings'."),
    type: str = typer.Option(..., prompt=f"Type ({', '.join(ACCOUNT_TYPES)})"),
    nickname: str = typer.Option("", prompt="Nickname shown to the assistant (blank = same as name)"),
    institution: str = typer.Option("", prompt="Institution (optional)"),
    last4: str = typer.Option("", prompt="Last 4 digits (optional)"),
    opening_balance: str = typer.Option("0", prompt="Opening balance (₹)"),
) -> None:
    """Add an account."""
    env = _env()
    ledger = env.ledger()
    try:
        with ledger.engine.begin() as conn:
            cs = open_change_set(conn, "user", f"add account {name}")
            a = create_account(
                conn,
                cs,
                name=name,
                type=type,
                nickname=nickname or None,
                institution=institution or None,
                last4=last4 or None,
                opening_balance_minor=parse_amount(opening_balance),
            )
        console.print(f"Added {a.name} ({a.type}), opening balance {format_minor(a.opening_balance_minor)}")
    except FinError as e:
        console.print(f"[red]{e.message}[/red]")
        raise typer.Exit(1) from None
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    finally:
        ledger.close()


@account_app.command("list")
def account_list() -> None:
    """List accounts and balances."""
    from finassist.cli.chat import slash

    env = _env()
    ledger = env.ledger()
    ctx = ToolContext(ledger.engine, env.settings, env.clock, Staging(), confirm=lambda _: False)
    r = slash(ctx, "/accounts")
    if r:
        render_turn(r)
    ledger.close()


@app.command("import")
def import_cmd(
    path: Path,
    account: str = typer.Option(
        None, "--account", "-a", help="Account name (optional if the statement names it)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Commit without asking if the preview is clean."),
    ask_password: bool = typer.Option(
        False, "--ask-password", help="Ask for the password of a protected PDF (typed hidden, never saved)."
    ),
) -> None:
    """Preview a CSV or PDF statement, then import it after confirmation."""
    env = _env()
    ledger = env.ledger()
    staging = Staging()
    try:
        password = typer.prompt("PDF password", hide_input=True) if ask_password else None
        p = stage_import(ledger.engine, staging, path, account, password)
        for line in p.summary_lines():
            console.print(line)
        if p.status != "ready":
            raise typer.Exit(0 if p.status in ("already_imported", "nothing_new") else 1)
        if not yes and not typer.confirm(
            f"Commit {p.new_rows} new transactions to {p.account}?", default=False
        ):
            console.print("Nothing imported.")
            raise typer.Exit(0)
        res = commit_import(ledger.engine, staging, p.import_id)
        console.print(f"Imported {res['inserted']} transactions into {res['account']}.")
    except FinError as e:
        console.print(f"[red]{e.message}[/red]" + (f" ({e.hint})" if e.hint else ""))
        raise typer.Exit(1) from None
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    finally:
        ledger.close()


@app.command()
def add(
    account: str = typer.Option(..., prompt=True),
    amount: str = typer.Option(..., prompt="Amount (₹)"),
    direction: str = typer.Option("out", prompt="Money out or in? (out/in)"),
    description: str = typer.Option(..., prompt=True),
    date: str = typer.Option("today", prompt="Date (today, yesterday or YYYY-MM-DD)"),
    category: str = typer.Option("", prompt="Category (optional, e.g. Groceries)"),
) -> None:
    """Add one transaction with a simple form (FR-4)."""
    from finassist.cli.chat import confirm_in_terminal

    env = _env()
    ledger = env.ledger()
    ctx = ToolContext(ledger.engine, env.settings, env.clock, Staging(), confirm=confirm_in_terminal)
    try:
        args = AddTransactionInput(
            account=account,
            amount=amount,
            direction=direction,  # type: ignore[arg-type]
            description=description,
            date=date,
            category=category or None,
        )
        out = policy.execute(AddTransaction(), ctx, args)
        fm = out.for_model
        if fm.get("status") == "saved":
            console.print(f"Saved. {fm['account']} balance is now {fm['account_balance_now']['display']}.")
        else:
            console.print(fm.get("message", "Not saved."))
    except FinError as e:
        console.print(f"[red]{e.message}[/red]" + (f" ({e.hint})" if e.hint else ""))
        raise typer.Exit(1) from None
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    finally:
        ledger.close()


@app.command()
def spend(period: list[str] = typer.Argument(..., help="e.g. october, last month, Q3")) -> None:
    """Total money out for a period (no LLM)."""
    _slash_once("/spend " + " ".join(period))


@app.command()
def income(period: list[str] = typer.Argument(...)) -> None:
    """Total money in for a period (no LLM)."""
    _slash_once("/income " + " ".join(period))


def _slash_once(line: str) -> None:
    from finassist.cli.chat import slash

    env = _env()
    ledger = env.ledger()
    ctx = ToolContext(ledger.engine, env.settings, env.clock, Staging(), confirm=lambda _: False)
    r = slash(ctx, line)
    if r:
        render_turn(r)
    ledger.close()


@app.command()
def gui(
    port: int = typer.Option(8765, help="Local port (another free port is used if it's taken)."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open the browser automatically."),
) -> None:
    """Open the app in your web browser (default command). Runs only on this computer."""
    from finassist.web.server import serve

    env = _env()
    config = (
        Path(os.environ["FINASSIST_CONFIG"])
        if os.environ.get("FINASSIST_CONFIG")
        else env.main_paths.config_file
    )
    env.main_paths.ensure()
    real_keys = make_env(False).keys
    serve(
        env.main_paths,
        config,
        real_keys=real_keys,
        start_ledger="demo" if env.demo or not env.main_paths.db_file.exists() else "mine",
        port=port,
        open_browser=not no_browser,
    )


@app.command()
def chat() -> None:
    """Conversational assistant (default command)."""
    from finassist.cli.chat import run_chat

    env = _env()
    try:
        run_chat(env)
    except LedgerNotInitialised as e:
        console.print(f"[red]{e}[/red]")
        if env.demo:
            console.print("Build it with: coinleaf demo")
        raise typer.Exit(1) from None


@config_app.command("set-api-key")
def set_api_key(
    provider: str = typer.Option(
        None, "--provider", help="gemini or anthropic (default: the configured provider)"
    ),
) -> None:
    """Store an LLM API key in the OS secret store (Keychain / Credential Manager / keyring)."""
    env = _env()
    provider = provider or env.settings.llm.provider
    if provider not in API_KEYS:
        console.print(f"[red]--provider must be one of: {', '.join(API_KEYS)}[/red]")
        raise typer.Exit(2)
    label = {"gemini": "Gemini", "anthropic": "Anthropic"}[provider]
    raw = typer.prompt(f"{label} API key (paste it, then press Enter)", hide_input=True)
    try:
        key = clean_api_key(raw)
    except ApiKeyError as e:
        console.print(f"[red]Not stored: {e}.[/red]")
        raise typer.Exit(1) from None
    if key != raw.strip():
        console.print(
            "[yellow]Removed stray terminal characters (e.g. an arrow key) from what was typed.[/yellow]"
        )
    KeychainStore.for_api_keys().set(API_KEYS[provider], key)
    console.print(
        f"Stored a {len(key)}-character {label} key in the {secret_store_name()}. "
        "Coinleaf never writes it to disk or logs."
    )
    if provider == "gemini" and not env.settings.llm.gemini_paid_tier:
        console.print("[yellow]Note:[/yellow] " + _paid_tier_hint())


def _paid_tier_hint() -> str:
    from finassist.cli.chat import PAID_TIER_HINT

    return PAID_TIER_HINT


@password_app.command("reset")
def password_reset(
    username: str = typer.Argument(None, help="Whose password; not needed when there is only one account."),
) -> None:
    """Set a new password for a Coinleaf account from the terminal (data untouched)."""
    from finassist.config.accounts import AccountStore
    from finassist.config.password import PasswordError

    store = AccountStore(default_paths().home)
    accounts = store.accounts()
    if not accounts:
        console.print("No accounts yet. Start Coinleaf and open the link it prints to create the first one.")
        return
    names = ", ".join(a.username or "(first account)" for a in accounts)
    if username:
        account = store.find(username)
        if account is None:
            console.print(f"[red]No account called {username}.[/red] Accounts: {names}")
            raise typer.Exit(1)
    elif len(accounts) == 1:
        account = accounts[0]
    else:
        console.print(f"There are several accounts ({names}). Run: coinleaf password reset <username>")
        raise typer.Exit(1)
    who = account.username or "the first account"
    new = typer.prompt(f"New password for {who}", hide_input=True, confirmation_prompt=True)
    try:
        store.set_password(account.id, new, new)
    except PasswordError as e:
        console.print(f"[red]Not changed: {e}.[/red]")
        raise typer.Exit(1) from None
    console.print(f"Password changed for {who}. Sign in with it in the browser; the data is unchanged.")


@users_app.command("list")
def users_list() -> None:
    """The people who can sign in to the browser app (not bank accounts)."""
    from finassist.config.accounts import AccountStore

    accounts = AccountStore(default_paths().home).accounts()
    if not accounts:
        console.print("No accounts yet. Start Coinleaf and open the link it prints to create the first one.")
        return
    for a in accounts:
        role = "first account (the original ledger)" if a.owner else f"own folder {a.folder}"
        console.print(
            f"{a.username or '(no username yet)'}  ·  {a.name or '—'}  ·  {role}  ·  since {a.created[:10]}"
        )


def main() -> None:
    if IS_WINDOWS:  # piped or redirected output would otherwise use a legacy code page and fail on "₹"
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure and (stream.encoding or "").lower() != "utf-8":
                reconfigure(encoding="utf-8", errors="replace")
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)
    except KeyStoreUnavailable as e:
        console.print(f"[red]{e.message}[/red]")
        if e.hint:
            console.print(e.hint)
        sys.exit(1)
    except LedgerNotInitialised as e:
        console.print(f"[red]{e}[/red]")
        if _state.get("env") and _state["env"].demo:
            console.print("Build the demo ledger with: coinleaf demo")
        sys.exit(1)


if __name__ == "__main__":
    main()
