"""The GUI: a local web app over the same ledger, tools and policy gate as the CLI.

Pages: Dashboard, Transactions (+ CSV export), Import, Add transaction, Accounts, Settings.
Every change to the ledger goes through a preview and an explicit click
(policy.prepare / policy.apply). Templates autoescape everything: statement text is
shown as text, never as HTML. The GUI uses no language model and no network.
"""

from __future__ import annotations

import csv
import io
import os
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy import select

from finassist.agent import policy
from finassist.agent.tools.p0 import (
    AddTransaction,
    AddTransactionInput,
    CommitImport,
    CommitImportInput,
    GetDataCoverage,
    NoInput,
)
from finassist.analytics.dashboard import Dashboard, build_dashboard, data_months
from finassist.analytics.listing import list_transactions
from finassist.config.accounts import Account
from finassist.config.password import MIN_LENGTH, PasswordError
from finassist.config.system import O_BINARY, computer_name, secret_store_name
from finassist.core.errors import FinError
from finassist.core.money import format_compact_inr, format_inr, parse_amount, percent
from finassist.core.periods import PeriodError, ResolvedPeriod, resolve_period
from finassist.db.audit import open_change_set
from finassist.db.ledger import init_ledger
from finassist.db.repo.accounts import ACCOUNT_TYPES, balance_minor, create_account, list_accounts
from finassist.db.repo.categories import all_categories
from finassist.db.schema import import_source
from finassist.demo.generator import DEMO_PDF_PASSWORD
from finassist.ingest.pipeline import stage_import
from finassist.web import charts
from finassist.web.auth import IDLE_MINUTES
from finassist.web.security import COOKIE, Guard, GuardMiddleware
from finassist.web.state import AppState, LedgerSession, UserSpace, WebConfig

__all__ = ["create_app"]

HERE = Path(__file__).parent
LEDGER_COOKIE = "finassist_ledger"
MAX_UPLOAD = 20 * 1024 * 1024
PAGE_SIZE = 50
SLOTS = [f"s{i}" for i in range(1, 9)]  # categorical slots in fixed order
PRESETS = [
    ("this month", "This month"),
    ("last month", "Last month"),
    ("last 3 months", "Last 3 months"),
    ("last 12 months", "Last 12 months"),
    ("this fy", "This financial year"),
    ("last fy", "Last financial year"),
    ("this year", "This calendar year"),
]
TYPE_LABELS = {
    "savings": "Savings",
    "current": "Current",
    "credit_card": "Credit card",
    "cash": "Cash",
    "wallet": "Wallet",
    "loan": "Loan",
    "investment": "Investment",
}
ADD_FIELDS = ("account", "amount", "direction", "description", "date", "category")


def _error_text(e: Exception) -> str:
    if isinstance(e, FinError):
        return e.message + (f" ({e.hint})" if e.hint else "")
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in er['loc'])}: {er['msg']}" for er in e.errors())
    return str(e)


def _delta(now: int, before: int, up_is_good: bool) -> dict[str, Any] | None:
    """Signed change vs the previous period; tone = direction × whether up is good."""
    if before == 0:
        return None
    pct = percent(now - before, abs(before))
    if pct is None:
        return None
    if pct == 0:
        return {"text": "no change", "arrow": "", "tone": "neutral", "aria": "no change"}
    up = pct > 0
    return {
        "text": f"{abs(pct)}%",
        "arrow": "▲" if up else "▼",
        "tone": "good" if up == up_is_good else "bad",
        "aria": f"{'up' if up else 'down'} {abs(pct)} percent",
    }


def _initials(name: str) -> str:
    words = [w for w in re.split(r"[\s.]+", name) if w and w[0].isalnum()]
    return ("".join(w[0] for w in words[:2]) or "?").upper()


def _csv_safe(v: str) -> str:
    # Spreadsheet formula injection: a cell starting with = + - @ is kept as text.
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def create_app(cfg: WebConfig, guard: Guard) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GuardMiddleware, guard=guard)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["inr"] = format_inr
    templates.env.filters["inr_short"] = format_compact_inr
    templates.env.filters["initials"] = _initials
    templates.env.globals["computer"] = computer_name()  # "this Mac" / "this computer"
    templates.env.globals["secret_store"] = secret_store_name()  # e.g. "Windows Credential Manager"
    state = AppState(cfg)
    app.state.finassist = state

    # ---------------------------------------------------------------------------- helpers
    def me(request: Request) -> Account:
        """The signed-in account (the middleware lets no signed-out request reach a page)."""
        a = guard.accounts.get(guard.user_id(request, touch=False))
        if a is None:
            raise FinError("you're logged out", hint="log in again")
        return a

    def space(request: Request) -> UserSpace:
        a = me(request)
        return state.space(a.id, a.folder)

    def kind_of(request: Request) -> str:
        k = request.cookies.get(LEDGER_COOKIE) or space(request).default_kind()
        return k if k in ("demo", "mine") else "demo"

    def sess(request: Request) -> LedgerSession:
        return space(request).session(kind_of(request))

    def page(request: Request, name: str, active: str, **extra: Any) -> Response:
        s = sess(request)
        ctx: dict[str, Any] = {
            "csrf": guard.csrf,
            "idle_minutes": IDLE_MINUTES,
            "me": me(request),
            "kind": s.kind,
            "available": s.available,
            "active": active,
            "today": s.env.clock.today(),
        }
        ctx.update(extra)
        return templates.TemplateResponse(request, name, ctx)

    def fragment(name: str, **ctx: Any) -> HTMLResponse:
        return HTMLResponse(templates.get_template(name).render(**ctx))

    def problem(e: Exception) -> HTMLResponse:
        return fragment("partials/message.html", kind="error", text=_error_text(e))

    def ready(request: Request) -> LedgerSession:
        s = sess(request)
        if not s.available:
            raise FinError("this ledger hasn't been created yet", hint="use the button on the page")
        s.open()
        return s

    def not_ready(request: Request, name: str, active: str, **extra: Any) -> Response | None:
        """The page to show instead when the ledger is missing or can't be opened."""
        s = sess(request)
        if not s.available:
            return page(request, name, active, **extra)
        try:
            s.open()
        except Exception as e:  # noqa: BLE001 - e.g. the key is missing; shown on the page
            return page(request, name, active, open_error=_error_text(e), **extra)
        return None

    def period_options(s: LedgerSession) -> tuple[list[tuple[str, str]], str]:
        assert s.ctx is not None
        with s.ctx.engine.connect() as conn:
            months = data_months(conn)
        opts = [(m, date.fromisoformat(m + "-01").strftime("%B %Y")) for m in months]
        return opts + PRESETS, (months[0] if months else "this month")

    def resolve(s: LedgerSession, value: str | None, default: str) -> tuple[ResolvedPeriod, str, str | None]:
        text = (value or default).strip()
        fy = s.env.settings.general.fy_start_month
        qc = s.env.settings.periods.quarter_convention
        try:
            return resolve_period(text, s.env.clock, fy_start_month=fy, quarter_convention=qc), text, None
        except PeriodError as e:
            fallback = resolve_period(default, s.env.clock, fy_start_month=fy, quarter_convention=qc)
            return fallback, default, f"Couldn't use that period ({e}). Showing {default} instead."

    def account_names(s: LedgerSession) -> list[str]:
        assert s.ctx is not None
        with s.ctx.engine.connect() as conn:
            return [a.nickname for a in list_accounts(conn)]

    # ---------------------------------------------------------------------------- pages
    @app.get("/")
    def root() -> Response:
        return RedirectResponse("/dashboard", status_code=303)

    # ---------------------------------------------------------------------------- sign-in
    def auth_page(request: Request, name: str, **extra: Any) -> Response:
        ctx: dict[str, Any] = {"csrf": guard.csrf, "idle_minutes": IDLE_MINUTES, "min_length": MIN_LENGTH}
        ctx.update(extra)
        return templates.TemplateResponse(request, name, ctx)

    def go(target: str, *, session: str | None = None) -> Response:
        """Tell the page script to navigate (forms on the sign-in pages post with fetch)."""
        resp = Response(status_code=204)
        resp.headers["X-Redirect"] = target
        if session:
            resp.set_cookie(COOKIE, session, httponly=True, samesite="strict", path="/")
        return resp

    def too_many() -> Response | None:
        wait = guard.throttle.wait_seconds()
        if not wait:
            return None
        text = f"Too many wrong attempts. Try again in {wait} seconds."
        return fragment("partials/message.html", kind="error", text=text)

    def wrong(text: str) -> Response:
        guard.throttle.failed()
        wait = guard.throttle.wait_seconds()
        return fragment(
            "partials/message.html",
            kind="error",
            text=text + (f" Try again in {wait} seconds." if wait else ""),
        )

    @app.get("/login")
    def login_page(request: Request, locked: int = 0, signed_out: int = 0, expired: int = 0) -> Response:
        if guard.signed_in(request, touch=False):
            return RedirectResponse("/dashboard", status_code=303)
        if not guard.accounts.has_owner():
            if guard.launched(request):
                return RedirectResponse("/setup", status_code=303)
            return auth_page(request, "login.html", needs_launch=True)
        note = (
            f"You were logged out after {IDLE_MINUTES} minutes without use."
            if locked
            else "You've logged out."
            if signed_out
            else "That link was from an earlier start of Coinleaf. Log in here instead."
            if expired
            else None
        )
        owner = guard.accounts.owner()
        # An owner sign-in made before usernames existed: the owner picks one now.
        choose = owner is not None and owner.username is None
        return auth_page(request, "login.html", note=note, choose_username=choose)

    @app.post("/login")
    def login(username: str = Form(""), password: str = Form("")) -> Response:
        if not guard.accounts.has_owner():
            return fragment("partials/message.html", kind="error", text="Set up Coinleaf first.")
        blocked = too_many()
        if blocked:
            return blocked
        owner = guard.accounts.owner()
        if owner is not None and owner.username is None:  # older sign-in without a username
            if not guard.accounts.password_ok(owner.id, password):
                return wrong("That password isn't right.")
            try:
                account = guard.accounts.set_username(owner.id, username)
            except PasswordError as e:
                return fragment("partials/message.html", kind="error", text=f"Choose a username: {e}.")
        else:
            found = guard.accounts.verify(username, password)
            if found is None:
                return wrong("That username or password isn't right.")
            account = found
        guard.throttle.succeeded()
        return go("/dashboard", session=guard.sessions.new(account.id))

    def _create(request: Request, form: dict[str, str], *, owner: bool) -> Response:
        try:
            account = guard.accounts.create(**form, owner=owner)
        except PasswordError as e:
            return fragment("partials/message.html", kind="error", text=f"Not saved: {e}.")
        resp = go("/dashboard", session=guard.sessions.new(account.id))
        resp.delete_cookie(LEDGER_COOKIE, path="/")  # start on this account's own default view
        return resp

    def _signup_form(
        name: str, mobile: str, email: str, username: str, password: str, confirm: str
    ) -> dict[str, str]:
        return {
            "name": name,
            "mobile": mobile,
            "email": email,
            "username": username,
            "password": password,
            "confirm": confirm,
        }

    @app.get("/setup")
    def setup_page(request: Request) -> Response:
        if guard.accounts.has_owner() or not guard.launched(request):
            return RedirectResponse("/login", status_code=303)
        return auth_page(request, "signup.html", first=True)

    @app.post("/setup")
    def setup(
        request: Request,
        name: str = Form(""),
        mobile: str = Form(""),
        email: str = Form(""),
        username: str = Form(""),
        password: str = Form(""),
        confirm: str = Form(""),
    ) -> Response:
        if guard.accounts.has_owner() or not guard.launched(request):
            return go("/login")
        return _create(request, _signup_form(name, mobile, email, username, password, confirm), owner=True)

    @app.get("/signup")
    def signup_page(request: Request) -> Response:
        if guard.signed_in(request, touch=False):
            return RedirectResponse("/dashboard", status_code=303)
        if not guard.accounts.has_owner():  # the first account comes from the launch link
            return RedirectResponse("/setup" if guard.launched(request) else "/login", status_code=303)
        return auth_page(request, "signup.html", first=False)

    @app.post("/signup")
    def signup(
        request: Request,
        name: str = Form(""),
        mobile: str = Form(""),
        email: str = Form(""),
        username: str = Form(""),
        password: str = Form(""),
        confirm: str = Form(""),
    ) -> Response:
        if not guard.accounts.has_owner():
            return go("/login")
        return _create(request, _signup_form(name, mobile, email, username, password, confirm), owner=False)

    @app.post("/logout")
    def logout(request: Request) -> Response:
        guard.sessions.end(request.cookies.get(COOKIE))
        resp = go("/login?signed_out=1")
        resp.delete_cookie(COOKIE, path="/")
        resp.delete_cookie(LEDGER_COOKIE, path="/")  # the next person starts on their own view
        return resp

    @app.post("/api/profile")
    def change_profile(
        request: Request, name: str = Form(""), mobile: str = Form(""), email: str = Form("")
    ) -> Response:
        try:
            guard.accounts.update_profile(me(request).id, name=name, mobile=mobile, email=email)
        except PasswordError as e:
            return fragment("partials/message.html", kind="error", text=f"Not saved: {e}.")
        return fragment("partials/message.html", kind="ok", text="Profile saved.")

    @app.post("/api/password")
    def change_sign_in(
        request: Request,
        current: str = Form(""),
        username: str = Form(""),
        password: str = Form(""),
        confirm: str = Form(""),
    ) -> Response:
        """Change the username, the password, or both; the current password confirms it's the owner."""
        blocked = too_many()
        if blocked:
            return blocked
        account = me(request)
        if not guard.accounts.password_ok(account.id, current):
            return wrong("Your current password isn't right.")
        guard.throttle.succeeded()
        before = account.username
        user = username.strip() or before or ""
        try:
            changed_user = user != before
            if changed_user:
                user = guard.accounts.set_username(account.id, user).username or user
            if password or confirm:
                guard.accounts.set_password(account.id, password, confirm)
                done = "Username and password saved." if changed_user else "Password changed."
            elif changed_user:
                done = f"Username changed to {user}."
            else:
                return fragment("partials/message.html", kind="info", text="Nothing to change.")
        except PasswordError as e:
            return fragment("partials/message.html", kind="error", text=f"Not changed: {e}.")
        guard.sessions.end_others(account.id, request.cookies.get(COOKIE))  # other browsers sign in again
        return fragment("partials/message.html", kind="ok", text=done)

    @app.get("/dashboard")
    def dashboard_page(request: Request, period: str | None = None, account: str | None = None) -> Response:
        early = not_ready(request, "dashboard.html", "dashboard")
        if early:
            return early
        s = sess(request)
        assert s.ctx is not None
        with s.ctx.engine.connect() as conn:
            has_accounts = bool(list_accounts(conn))
            has_txns = bool(data_months(conn))
        if not has_txns:  # nothing to chart yet: show the getting-started checklist instead of zeros
            return page(request, "dashboard.html", "dashboard", onboarding={"accounts": has_accounts})
        options, default = period_options(s)
        p, chosen, warn = resolve(s, period, default)
        accts = account_names(s)
        acct = account if account in accts else None
        with s.ctx.engine.connect() as conn:
            d: Dashboard = build_dashboard(conn, p, s.env.clock.today(), acct)
            slot_of = {a.nickname: SLOTS[i % len(SLOTS)] for i, a in enumerate(list_accounts(conn))}
        if chosen not in {o[0] for o in options}:
            options = [(chosen, p.label)] + options
        months = d.monthly
        positive = [(b.name, b.balance) for b in d.balances if b.balance > 0]
        kpis = [
            {
                "label": "Money in",
                "value": d.now.money_in,
                "count": d.now.count_in,
                "type": "in",
                "delta": _delta(d.now.money_in, d.before.money_in, True),
                "spark": charts.sparkline([m["in"] for m in months]),
            },
            {
                "label": "Money out",
                "value": d.now.money_out,
                "count": d.now.count_out,
                "type": "out",
                "delta": _delta(d.now.money_out, d.before.money_out, False),
                "spark": charts.sparkline([m["out"] for m in months]),
            },
            {
                "label": "Saved",
                "value": d.now.saved,
                "rate": d.now.savings_rate,
                "type": "",
                "delta": _delta(d.now.saved, d.before.saved, True),
                "spark": charts.sparkline([m["in"] - m["out"] for m in months]),
            },
        ]
        return page(
            request,
            "dashboard.html",
            "dashboard",
            d=d,
            options=options,
            chosen=chosen,
            accounts=accts,
            account=acct,
            warn=warn,
            kpis=kpis,
            line=charts.line_chart(months, [("in", "Money in", "s1"), ("out", "Money out", "s2")]),
            daily=charts.columns(d.daily, "s2", f"Spending per day, {p.start:%B %Y}") if d.daily else None,
            methods=charts.hbars(d.by_method, "s2", "How you paid"),
            seg=charts.segments(positive, [slot_of.get(n, "s1") for n, _ in positive]),
            peak_out=max(months, key=lambda m: m["out"]) if months else None,
            peak_in=max(months, key=lambda m: m["in"]) if months else None,
            gaps=d.coverage.gaps if d.coverage and d.coverage.status != "full" else [],
            top_payee_max=max((v for _, v, _ in d.top_payees), default=0),
            month_name=lambda k: date.fromisoformat(k + "-01").strftime("%b %Y"),
            type_labels=TYPE_LABELS,
        )

    def _listing_args(
        s: LedgerSession, period: str | None, account: str | None, kind: str | None, q: str | None
    ) -> dict[str, Any]:
        options, default = period_options(s)
        p, chosen, warn = resolve(s, period, default)
        if chosen not in {o[0] for o in options}:
            options = [(chosen, p.label)] + options
        accts = account_names(s)
        return {
            "p": p,
            "chosen": chosen,
            "warn": warn,
            "options": options,
            "accounts": accts,
            "account": account if account in accts else None,
            "type": kind if kind in ("in", "out") else None,
            "q": (q or "").strip()[:100] or None,
        }

    @app.get("/transactions")
    def transactions_page(
        request: Request,
        period: str | None = None,
        account: str | None = None,
        type: str | None = None,
        q: str | None = None,
        page_no: int = 1,
    ) -> Response:
        early = not_ready(request, "transactions.html", "transactions")
        if early:
            return early
        s = sess(request)
        assert s.ctx is not None
        a = _listing_args(s, period, account, type, q)
        page_no = max(page_no, 1)
        with s.ctx.engine.connect() as conn:
            lst = list_transactions(
                conn,
                a["p"].start,
                a["p"].end,
                account=a["account"],
                direction=a["type"],
                text=a["q"],
                offset=(page_no - 1) * PAGE_SIZE,
                limit=PAGE_SIZE,
            )
        pages = max((lst.total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
        query = {
            "period": a["chosen"],
            "account": a["account"] or "",
            "type": a["type"] or "",
            "q": a["q"] or "",
        }
        return page(
            request,
            "transactions.html",
            "transactions",
            lst=lst,
            page_no=page_no,
            pages=pages,
            query=query,
            **a,
        )

    @app.get("/transactions.csv")
    def transactions_csv(
        request: Request,
        period: str | None = None,
        account: str | None = None,
        type: str | None = None,
        q: str | None = None,
    ) -> Response:
        s = ready(request)
        assert s.ctx is not None
        a = _listing_args(s, period, account, type, q)
        with s.ctx.engine.connect() as conn:
            lst = list_transactions(
                conn,
                a["p"].start,
                a["p"].end,
                account=a["account"],
                direction=a["type"],
                text=a["q"],
                limit=None,
            )
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["Date", "Account", "Payee", "Description", "Category", "Paid by", "Amount (INR)"])
        for r in lst.rows:
            amount = Decimal(r["amount"]).scaleb(-2)
            w.writerow(
                [
                    r["date"].isoformat(),
                    _csv_safe(r["account"]),
                    _csv_safe(r["payee"]),
                    _csv_safe(r["description"]),
                    _csv_safe(r["category"]),
                    r["method"],
                    f"{amount:.2f}",
                ]
            )
        name = f"coinleaf-{s.kind}-{a['p'].start:%Y%m%d}-{a['p'].end:%Y%m%d}.csv"
        return Response(
            buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @app.get("/import")
    def import_page(request: Request) -> Response:
        early = not_ready(request, "import.html", "import", inbox=[], accounts=[])
        if early:
            return early
        s = sess(request)
        assert s.ctx is not None
        inbox: list[dict[str, Any]] = []
        with s.ctx.engine.connect() as conn:
            accounts = [a.nickname for a in list_accounts(conn)]
        if s.kind == "demo" and s.env.paths.inbox.exists():
            with s.ctx.engine.connect() as conn:
                done = {
                    r[0]
                    for r in conn.execute(
                        select(import_source.c.file_name).where(import_source.c.status == "committed")
                    )
                }
            files = list(s.env.paths.inbox.glob("*.csv")) + list(s.env.paths.inbox.glob("*.pdf"))
            inbox = [
                {
                    "name": f.name,
                    "imported": f.name in done,
                    "pdf": f.suffix == ".pdf",
                    "password": DEMO_PDF_PASSWORD
                    if f.name.startswith("bank_a_") and f.suffix == ".pdf"
                    else None,
                }
                for f in sorted(files, key=lambda f: (f.stem, f.suffix), reverse=True)
            ]
        return page(request, "import.html", "import", inbox=inbox, accounts=accounts)

    @app.get("/add")
    def add_page(request: Request) -> Response:
        early = not_ready(request, "add.html", "add", accounts=[], groups={})
        if early:
            return early
        s = sess(request)
        assert s.ctx is not None
        with s.ctx.engine.connect() as conn:
            accounts = [a.nickname for a in list_accounts(conn)]
            groups: dict[str, list[str]] = {}
            for c in all_categories(conn):
                if c.parent_id and c.kind in ("expense", "income"):
                    groups.setdefault(c.parent_name or "", []).append(c.name)
        return page(request, "add.html", "add", accounts=accounts, groups=groups)

    @app.get("/accounts")
    def accounts_page(request: Request) -> Response:
        early = not_ready(
            request,
            "accounts.html",
            "accounts",
            account_types=ACCOUNT_TYPES,
            type_labels=TYPE_LABELS,
            rows=[],
        )
        if early:
            return early
        s = sess(request)
        assert s.ctx is not None
        today = s.env.clock.today()
        cov = {c["account"]: c for c in GetDataCoverage().run(s.ctx, NoInput()).for_model["accounts"]}
        with s.ctx.engine.connect() as conn:
            rows = [
                {
                    "name": a.nickname,
                    "full": a.name,
                    "type": TYPE_LABELS.get(a.type, a.type),
                    "institution": a.institution or "",
                    "last4": a.last4 or "",
                    "balance": balance_minor(conn, a.id, as_of=today),
                    "coverage": cov.get(a.nickname, {}),
                }
                for a in list_accounts(conn)
            ]
        return page(
            request,
            "accounts.html",
            "accounts",
            rows=rows,
            account_types=ACCOUNT_TYPES,
            type_labels=TYPE_LABELS,
        )

    @app.get("/settings")
    def settings_page(request: Request) -> Response:
        s = sess(request)
        return page(
            request,
            "settings.html",
            "settings",
            data_folder=space(request).paths.home,
            db_file=s.env.paths.db_file,
            config_path=cfg.config_path,
        )

    # ---------------------------------------------------------------------------- ledgers
    @app.post("/api/ledger")
    def switch_ledger(kind: str = Form(...)) -> Response:
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            LEDGER_COOKIE, "mine" if kind == "mine" else "demo", httponly=True, samesite="strict", path="/"
        )
        return resp

    @app.post("/api/ledger/create")
    def create_ledger(request: Request) -> Response:
        s = sess(request)
        try:
            if s.kind == "demo":
                from finassist.demo.setup import build_demo

                sp = space(request)
                sp.reset("demo")
                build_demo(sp.paths, reset=True).ledger.close()
            else:
                sp = space(request)
                sp.paths.ensure()
                init_ledger(sp.paths.db_file, sp.keys)[0].close()
                sp.reset("mine")
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": _error_text(e)})
        return JSONResponse({"ok": True})

    # ----------------------------------------------------------------------------- import
    def _preview_import(
        s: LedgerSession, path: Path, account: str | None, upload: bool, password: str | None = None
    ) -> HTMLResponse:
        assert s.ctx is not None
        try:
            # The password opens a protected PDF in memory and is dropped right after.
            fm = stage_import(s.ctx.engine, s.ctx.staging, path, account or None, password or None).as_dict()
        except Exception:
            if upload:
                _remove_upload(path)
            raise
        token = None
        if fm["status"] == "ready":
            _, token = policy.prepare(CommitImport(), s.ctx, CommitImportInput(import_id=fm["import_id"]))
            s.pending[fm["import_id"]] = {"path": path if upload else None}
        elif upload:
            _remove_upload(path)
        return fragment("partials/import_preview.html", fm=fm, token=token)

    def _remove_upload(path: Path) -> None:
        path.unlink(missing_ok=True)
        if path.parent.parent.name == "uploads":  # the per-upload folder made below
            path.parent.rmdir()

    @app.post("/api/import/preview")
    async def import_preview(
        request: Request,
        file: UploadFile | None = File(None),
        inbox_name: str = Form(""),
        password: str = Form(""),
        account: str = Form(""),
    ) -> Response:
        try:
            s = ready(request)
            if file is not None and file.filename:
                data = await file.read(MAX_UPLOAD + 1)
                if len(data) > MAX_UPLOAD:
                    raise FinError("the file is larger than 20 MB")
                safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(file.filename).name)[:120] or "statement"
                folder = space(request).upload_dir() / os.urandom(6).hex()
                folder.mkdir(mode=0o700)
                dest = folder / safe
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_BINARY, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(data)
                return _preview_import(s, dest, account, upload=True, password=password)
            if inbox_name and s.kind == "demo":
                p = s.env.paths.inbox / Path(inbox_name).name
                if not p.is_file():
                    raise FinError("no such file in the demo inbox")
                return _preview_import(s, p, account, upload=False, password=password)
            raise FinError("choose a statement file first")
        except Exception as e:  # noqa: BLE001
            return problem(e)

    @app.post("/api/import/commit")
    def import_commit(
        request: Request, import_id: str = Form(...), token: str = Form(...), answer: str = Form("yes")
    ) -> Response:
        try:
            s = ready(request)
            assert s.ctx is not None
            meta = s.pending.pop(import_id, None)
            try:
                if answer != "yes":
                    s.ctx.staging.pop(import_id)
                    return fragment(
                        "partials/message.html", kind="info", text="Cancelled. Nothing was imported."
                    )
                out = policy.apply(CommitImport(), s.ctx, CommitImportInput(import_id=import_id), token)
            finally:
                if meta and meta.get("path"):
                    _remove_upload(Path(meta["path"]))
        except Exception as e:  # noqa: BLE001
            return problem(e)
        fm = out.for_model
        return fragment(
            "partials/message.html",
            kind="ok",
            link=("/dashboard", "See the dashboard"),
            text=f"Imported {fm['inserted']} transactions into {fm['account']}.",
        )

    # ---------------------------------------------------------------------------- add txn
    def _add_args(form: dict[str, str]) -> AddTransactionInput:
        return AddTransactionInput(
            account=form.get("account", ""),
            amount=form.get("amount", ""),
            direction="in" if form.get("direction") == "in" else "out",
            description=form.get("description", ""),
            date=form.get("date") or "today",
            category=form.get("category") or None,
        )

    @app.post("/api/add/preview")
    async def add_preview(request: Request) -> Response:
        form = {k: str(v) for k, v in (await request.form()).items() if k in ADD_FIELDS}
        try:
            s = ready(request)
            assert s.ctx is not None
            args = _add_args(form)
            _, token = policy.prepare(AddTransaction(), s.ctx, args)
            t = AddTransaction()._build(s.ctx, args)
        except Exception as e:  # noqa: BLE001
            return problem(e)
        return fragment(
            "partials/add_preview.html",
            t=t,
            category=args.category or "Uncategorised",
            token=token,
            form=form,
        )

    @app.post("/api/add/save")
    async def add_save(request: Request) -> Response:
        raw = {k: str(v) for k, v in (await request.form()).items()}
        form = {k: v for k, v in raw.items() if k in ADD_FIELDS}
        try:
            s = ready(request)
            assert s.ctx is not None
            if raw.get("answer") != "yes":
                return fragment("partials/message.html", kind="info", text="Not saved. Nothing was changed.")
            out = policy.apply(AddTransaction(), s.ctx, _add_args(form), raw.get("token", ""))
        except Exception as e:  # noqa: BLE001
            return problem(e)
        fm = out.for_model
        return fragment(
            "partials/message.html",
            kind="ok",
            link=("/transactions", "See transactions"),
            text=f"Saved. {fm['account']} balance is now {fm['account_balance_now']['display']}.",
        )

    # ---------------------------------------------------------------------------- account
    @app.post("/api/account/add")
    def account_add(
        request: Request,
        name: str = Form(...),
        type: str = Form(...),
        nickname: str = Form(""),
        institution: str = Form(""),
        last4: str = Form(""),
        opening: str = Form("0"),
    ) -> Response:
        try:
            s = ready(request)
            assert s.ctx is not None
            with s.ctx.engine.begin() as conn:
                cs = open_change_set(conn, "user", f"add account {name}")
                a = create_account(
                    conn,
                    cs,
                    name=name.strip(),
                    type=type,
                    nickname=nickname.strip() or None,
                    institution=institution.strip() or None,
                    last4=last4.strip() or None,
                    opening_balance_minor=parse_amount(opening or "0"),
                )
        except Exception as e:  # noqa: BLE001
            return problem(e)
        return fragment(
            "partials/message.html",
            kind="ok",
            link=("/accounts", "Refresh the list"),
            text=f"Added {a.name}.",
        )

    return app
