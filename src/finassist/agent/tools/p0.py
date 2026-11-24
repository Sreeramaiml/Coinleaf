"""P0 tools: accounts, coverage, periods, the query primitive, explain, import, manual entry."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field
from sqlalchemy import func, select

from finassist.agent.tools.base import Preview, Tool, ToolContext, ToolInput, ToolOutput, UserTable
from finassist.analytics.coverage import account_coverage
from finassist.analytics.query import GroupBy, Metric, QuerySpec, TxnFilter, run_query
from finassist.analytics.receipts import load_receipt
from finassist.core.errors import ValidationFailed
from finassist.core.money import format_minor, money_json, parse_amount
from finassist.core.periods import PeriodError, resolve_period
from finassist.db.audit import open_change_set
from finassist.db.repo.accounts import balance_minor, list_accounts, resolve_account
from finassist.db.repo.categories import resolve_category
from finassist.db.repo.transactions import PAYMENT_METHODS, NewTxn, add_transactions, get_transactions
from finassist.db.schema import account as account_t
from finassist.db.schema import category, import_source
from finassist.ingest.pipeline import commit_import, stage_import

__all__ = ["P0_TOOLS"]


def _period(ctx: ToolContext, text: str):  # type: ignore[no-untyped-def]
    try:
        return resolve_period(
            text,
            ctx.clock,
            fy_start_month=ctx.settings.general.fy_start_month,
            quarter_convention=ctx.settings.periods.quarter_convention,
        )
    except PeriodError as e:
        raise ValidationFailed(str(e), hint="give explicit dates, e.g. 2026-10-01 to 2026-10-31") from None


# ----------------------------------------------------------------------------------- accounts


class NoInput(ToolInput):
    pass


class ListAccounts(Tool):
    name = "list_accounts"
    description = (
        "List the owner's accounts with type, currency, current balance (from the ledger) and "
        "where their data comes from. Accounts are referred to by these names."
    )
    Input = NoInput

    def run(self, ctx: ToolContext, args: NoInput) -> ToolOutput:
        today = ctx.clock.today()
        with ctx.engine.connect() as conn:
            accts = list_accounts(conn)
            cov = {c.account_id: c for c in account_coverage(conn)}
            items, rows = [], []
            for a in accts:
                bal = balance_minor(conn, a.id, as_of=today)
                c = cov.get(a.id)
                entry = {
                    "name": a.nickname,
                    "type": a.type,
                    "currency": a.currency,
                    "balance": money_json(bal, a.currency),
                    "data_source": c.kind if c else "no_data",
                }
                if a.type == "credit_card":
                    entry["note"] = "a negative card balance is the amount owed"
                if c and c.intervals:
                    entry["statements_cover"] = [
                        f"{s.isoformat()} to {e.isoformat()}" for s, e in c.intervals
                    ]
                items.append(entry)
                rows.append(
                    [
                        a.nickname,
                        a.name,
                        a.type,
                        a.institution or "",
                        a.last4 or "",
                        format_minor(bal, a.currency),
                    ]
                )
        return ToolOutput(
            {"as_of": today.isoformat(), "accounts": items},
            [UserTable("Accounts", ["Name", "Account", "Type", "Institution", "Last 4", "Balance"], rows)],
        )


class GetDataCoverage(Tool):
    name = "get_data_coverage"
    description = (
        "Which dates each account has data for (from imported statements or manual entries). Use it "
        "to check whether the data can answer a question before answering."
    )
    Input = NoInput

    def run(self, ctx: ToolContext, args: NoInput) -> ToolOutput:
        with ctx.engine.connect() as conn:
            cov = account_coverage(conn)
            counts: dict[str, int] = dict(
                conn.execute(
                    select(import_source.c.account_id, func.count())
                    .where(import_source.c.status == "committed")
                    .group_by(import_source.c.account_id)
                ).all()
            )
        out = []
        for c in cov:
            d = c.as_dict()
            d["statements_imported"] = int(counts.get(c.account_id, 0))
            out.append(d)
        return ToolOutput({"today": ctx.clock.today().isoformat(), "accounts": out})


# ------------------------------------------------------------------------------------ periods


class ResolvePeriodInput(ToolInput):
    text: str = Field(
        description="A period in words, e.g. 'October', 'last month', 'Q3', 'FY2025-26', "
        "'last 3 months', or dates 'YYYY-MM-DD to YYYY-MM-DD'."
    )


class ResolvePeriod(Tool):
    name = "resolve_period"
    description = (
        "Turn a period in words into explicit start and end dates. Months without a year mean the most "
        "recent one; quarters follow the configured convention (Indian FY by default). Never compute "
        "dates yourself."
    )
    Input = ResolvePeriodInput

    def run(self, ctx: ToolContext, args: ResolvePeriodInput) -> ToolOutput:
        p = _period(ctx, args.text)
        return ToolOutput({"period": p.as_dict(), "today": ctx.clock.today().isoformat()})


# ------------------------------------------------------------------------------------- query


class QueryInput(ToolInput):
    period: str = Field(
        description="Period in words or dates (resolved by code), e.g. 'October 2026', "
        "'last month', 'Q3', '2026-10-01 to 2026-10-31'."
    )
    kinds: list[Literal["income", "expense", "investment", "transfer"]] | None = Field(
        default=None, description="Restrict to these kinds. Spending questions use ['expense']."
    )
    accounts: list[str] | None = Field(default=None, description="Account names from list_accounts.")
    categories: list[str] | None = Field(
        default=None,
        description="Category names, e.g. 'Eating out' or "
        "'Lifestyle/Eating out'. A parent includes its children.",
    )
    exclude_categories: list[str] | None = None
    merchants: list[str] | None = Field(
        default=None, description="Merchant names; also matched in descriptions."
    )
    text: str | None = Field(default=None, description="Full-text search in descriptions and notes.")
    tags: list[str] | None = None
    min_amount: str | None = Field(
        default=None, description="Minimum absolute amount in rupees, e.g. '5000'."
    )
    max_amount: str | None = Field(default=None, description="Maximum absolute amount in rupees.")
    direction: Literal["debit", "credit"] | None = None
    group_by: list[GroupBy] | None = Field(
        default=None, description="Group rows, e.g. ['month'] or ['category']."
    )
    metrics: list[Metric] | None = Field(default=None, description="Default ['sum','count'].")
    money_out_positive: bool | None = Field(
        default=None, description="Report money out as positive numbers. Use true for spending questions."
    )
    order_by: Literal["value_desc", "value_asc", "key"] | None = None
    limit: int | None = Field(default=None, ge=1, le=100, description="Top-N groups.")
    top_transactions: int | None = Field(
        default=None, ge=0, le=10, description="Also return the N largest matching transactions."
    )
    include_transfers: bool | None = None


class QueryTransactions(Tool):
    name = "query_transactions"
    description = (
        "The only way to get figures: totals, counts, averages, min/max/median over the ledger, optionally "
        "grouped (by month, category, merchant, account, …). Every amount in the result has an exact 'display' "
        "string to quote. Transfers between the owner's accounts are excluded unless asked for. The result also "
        "reports data coverage and how much is uncategorised. Each result has a query_id for explain."
    )
    Input = QueryInput

    def run(self, ctx: ToolContext, args: QueryInput) -> ToolOutput:
        p = _period(ctx, args.period)
        f = TxnFilter(
            start=p.start,
            end=p.end,
            accounts=args.accounts,
            categories=args.categories,
            exclude_categories=args.exclude_categories,
            kinds=args.kinds,
            merchants=args.merchants,
            text=args.text,
            tags=args.tags,
            amount_min_minor=abs(parse_amount(args.min_amount)) if args.min_amount else None,
            amount_max_minor=abs(parse_amount(args.max_amount)) if args.max_amount else None,
            direction=args.direction,
            include_transfers=bool(args.include_transfers),
        )
        spec = QuerySpec(
            filter=f,
            group_by=args.group_by or [],
            metrics=args.metrics or ["sum", "count"],
            flip_sign=bool(args.money_out_positive),
            order_by=args.order_by or "value_desc",
            limit=args.limit,
        )
        with ctx.engine.begin() as conn:
            res = run_query(
                conn, spec, fy_start_month=ctx.settings.general.fy_start_month, period_label=p.label
            )
            top = []
            tables: list[UserTable | str] = []
            if args.top_transactions:
                txns = get_transactions(conn, res.txn_ids)
                names = {a.id: a.nickname for a in list_accounts(conn, include_inactive=True)}
                sign = -1 if spec.flip_sign else 1
                txns.sort(key=lambda t: sign * t["amount_minor"], reverse=True)
                for t in txns[: args.top_transactions]:
                    top.append(
                        {
                            "date": t["txn_date"].isoformat(),
                            "account": names.get(t["account_id"]),
                            "description": t["raw_description"],
                            "amount": money_json(sign * t["amount_minor"], t["currency"]),
                        }
                    )
        fm = res.for_model()
        if top:
            fm["top_transactions"] = top
        if res.rows:
            cols: list[str] = [str(c) for c in res.spec.group_by] + [str(m) for m in res.spec.metrics]
            rows = []
            for r in res.rows:
                vals = [str(r["key"][g]) for g in res.spec.group_by]
                for m in res.spec.metrics:
                    v = r.get(m)
                    vals.append(v["display"] if isinstance(v, dict) else ("" if v is None else str(v)))
                rows.append(vals)
            tables.append(UserTable(f"Result {res.query_id}", cols, rows, note=res.scope_text))
        return ToolOutput(fm, tables, [res])


class ExplainInput(ToolInput):
    query_id: str = Field(description="The query_id of an earlier result, e.g. q_7f3a9c.")


class Explain(Tool):
    name = "explain"
    description = (
        "'Show me how you got this': the filter used for an earlier figure and the transactions behind "
        "it. The full list is shown to the owner directly; you receive a summary."
    )
    Input = ExplainInput

    def run(self, ctx: ToolContext, args: ExplainInput) -> ToolOutput:
        with ctx.engine.connect() as conn:
            rc = load_receipt(conn, args.query_id.strip())
            txns = get_transactions(conn, rc.txn_ids)
            names = {a.id: a.nickname for a in list_accounts(conn, include_inactive=True)}
            cats = {r.id: r.name for r in conn.execute(select(category.c.id, category.c.name))}
        flip = bool(rc.spec.get("flip_sign"))
        sign = -1 if flip else 1
        rows = [
            [
                t["txn_date"].isoformat(),
                names.get(t["account_id"], "?"),
                t["raw_description"],
                format_minor(sign * t["amount_minor"], t["currency"]),
                cats.get(t["category_id"], "Uncategorised"),
            ]
            for t in txns
        ]
        sample = [
            {
                "date": t["txn_date"].isoformat(),
                "account": names.get(t["account_id"]),
                "description": t["raw_description"],
                "amount": money_json(sign * t["amount_minor"], t["currency"]),
            }
            for t in txns[:5]
        ]
        fm = {
            "query_id": rc.query_id,
            "scope": rc.scope_text,
            "filter": rc.spec.get("filter"),
            "totals": rc.result.get("totals"),
            "transactions": len(txns),
            "first_transactions": sample,
            "shown_to_owner": "the full transaction list is displayed to the owner locally",
        }
        table = UserTable(
            f"How {rc.query_id} was computed — {len(txns)} transactions",
            ["Date", "Account", "Description", "Amount", "Category"],
            rows,
            note=rc.scope_text,
        )
        return ToolOutput(fm, [table])


# ------------------------------------------------------------------------------------ import


class ImportFileInput(ToolInput):
    path: str = Field(description="Path to a statement file on the owner's machine.")
    account: str | None = Field(
        default=None, description="Account name; optional when the statement names it."
    )


class ImportFile(Tool):
    name = "import_file"
    description = (
        "Parse and stage a statement file. Writes nothing: returns a preview (rows parsed, new vs already "
        "imported, totals, file balance check) and an import_id for commit_import."
    )
    Input = ImportFileInput

    def run(self, ctx: ToolContext, args: ImportFileInput) -> ToolOutput:
        preview = stage_import(ctx.engine, ctx.staging, Path(args.path), args.account)
        return ToolOutput(preview.as_dict(), ["\n".join(preview.summary_lines(ctx.currency))])


class CommitImportInput(ToolInput):
    import_id: str = Field(description="The import_id returned by import_file.")


class CommitImport(Tool):
    name = "commit_import"
    description = (
        "Commit a staged import into the ledger. The owner is always asked to confirm in the terminal "
        "first; you cannot confirm on their behalf."
    )
    Input = CommitImportInput
    effect = "confirm"

    def preview(self, ctx: ToolContext, args: CommitImportInput) -> Preview:
        staged = ctx.staging.get(args.import_id)
        p = staged.preview
        lines = p.summary_lines(ctx.currency) + [f"Commit {p.new_rows} new transactions to {p.account}?"]
        return Preview(lines, hashlib.sha256(f"{p.import_id}|{p.sha256}|{p.new_rows}".encode()).hexdigest())

    def run(self, ctx: ToolContext, args: CommitImportInput) -> ToolOutput:
        return ToolOutput(commit_import(ctx.engine, ctx.staging, args.import_id))


# ------------------------------------------------------------------------------ manual entry


class AddTransactionInput(ToolInput):
    account: str = Field(description="Account name, e.g. 'Cash'.")
    amount: str = Field(description="Amount in rupees as written by the owner, e.g. '450' or '1,250.50'.")
    direction: Literal["out", "in"] = Field(
        default="out", description="'out' for spending, 'in' for money received."
    )
    description: str = Field(description="What it was for, in the owner's words.")
    date: str | None = Field(default=None, description="'today', 'yesterday' or YYYY-MM-DD. Default today.")
    category: str | None = Field(
        default=None, description="Category name if the owner gave or clearly implied one."
    )
    payment_method: Literal[PAYMENT_METHODS] | None = None  # type: ignore[valid-type]


class AddTransaction(Tool):
    name = "add_transaction"
    description = (
        "Record one transaction the owner tells you about (e.g. 'spent 450 cash on auto today'). "
        "The owner confirms the exact entry in the terminal before it is saved."
    )
    Input = AddTransactionInput
    effect = "confirm"

    def _build(self, ctx: ToolContext, args: AddTransactionInput) -> NewTxn:
        with ctx.engine.connect() as conn:
            acct = resolve_account(conn, args.account)
            cat_id = resolve_category(conn, args.category).id if args.category else None
        minor = abs(parse_amount(args.amount, acct.currency))
        if minor == 0:
            raise ValidationFailed("amount cannot be zero")
        p = _period(ctx, args.date or "today")
        if p.start != p.end:
            raise ValidationFailed("give a single date for a transaction")
        d: date = p.start
        method = args.payment_method or ("cash" if acct.type == "cash" else "other")
        return NewTxn(
            account=acct,
            txn_date=d,
            amount_minor=-minor if args.direction == "out" else minor,
            raw_description=args.description.strip(),
            payment_method=method,
            category_id=cat_id,
            classification_source="user" if cat_id else None,
            confidence=100 if cat_id else None,
            user_locked=bool(cat_id),
            needs_review=cat_id is None,
        )

    def preview(self, ctx: ToolContext, args: AddTransactionInput) -> Preview:
        t = self._build(ctx, args)
        cat = args.category or "uncategorised"
        line = (
            f"Add: {t.txn_date:%d %b %Y} · {t.account.nickname} · {format_minor(t.amount_minor, t.account.currency)}"
            f" · {t.raw_description} · {cat} · {t.payment_method}"
        )
        digest = hashlib.sha256(
            f"{t.account.id}|{t.txn_date}|{t.amount_minor}|{t.raw_description}|{cat}".encode()
        )
        return Preview([line, "Save this transaction?"], digest.hexdigest())

    def run(self, ctx: ToolContext, args: AddTransactionInput) -> ToolOutput:
        t = self._build(ctx, args)
        with ctx.engine.begin() as conn:
            cs = open_change_set(conn, "agent", f"manual entry: {t.raw_description}")
            add_transactions(conn, cs, [t], reason="confirmed by owner")
            bal = balance_minor(conn, t.account.id)
            acct_name = conn.execute(
                select(account_t.c.nickname).where(account_t.c.id == t.account.id)
            ).scalar()
        return ToolOutput(
            {
                "status": "saved",
                "date": t.txn_date.isoformat(),
                "account": acct_name,
                "amount": money_json(t.amount_minor, t.account.currency),
                "description": t.raw_description,
                "categorised": t.category_id is not None,
                "account_balance_now": money_json(bal, t.account.currency),
            }
        )


P0_TOOLS: list[Tool] = [
    ListAccounts(),
    GetDataCoverage(),
    ResolvePeriod(),
    QueryTransactions(),
    Explain(),
    ImportFile(),
    CommitImport(),
    AddTransaction(),
]
