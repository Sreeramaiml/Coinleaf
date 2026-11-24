"""`query_transactions`: the single aggregation primitive behind every figure (FR-21, ARCH §9.1).

- SQL is built with SQLAlchemy Core from a typed spec; no SQL ever comes from the model.
- sum/count/min/max run in SQL on integer paise; avg and median are computed in Python
  with integers and half-even rounding (SQLite AVG returns a float, so it is never used).
- Transfers are excluded unless the caller asks for them (invariant 2).
- Each result is saved as a query receipt so "show me how" returns the same set later.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Connection, Select, and_, case, cast, func, literal, or_, select
from sqlalchemy import Integer as SAInteger
from sqlalchemy.sql.elements import ColumnElement

from finassist.analytics.coverage import CoverageReport, coverage_for
from finassist.analytics.receipts import save_receipt
from finassist.core.ids import new_query_id
from finassist.core.money import div_round, format_minor, money_json
from finassist.core.periods import format_period, fy_label
from finassist.db.repo.accounts import resolve_account
from finassist.db.repo.categories import UNCATEGORISED, descendants, resolve_category
from finassist.db.schema import account, category, merchant, tag, transaction_tag, txn

__all__ = ["GroupBy", "Metric", "QueryResult", "QuerySpec", "TxnFilter", "run_query"]

GroupBy = Literal[
    "month",
    "week",
    "day",
    "fy",
    "category",
    "parent_category",
    "merchant",
    "account",
    "tag",
    "payment_method",
    "kind",
]
Metric = Literal["sum", "count", "avg", "min", "max", "median"]
Kind = Literal["income", "expense", "investment", "transfer"]


class TxnFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    accounts: list[str] | None = None
    categories: list[str] | None = None
    exclude_categories: list[str] | None = None
    kinds: list[Kind] | None = None
    merchants: list[str] | None = None
    tags: list[str] | None = None
    text: str | None = None
    amount_min_minor: int | None = Field(default=None, ge=0)
    amount_max_minor: int | None = Field(default=None, ge=0)
    direction: Literal["debit", "credit"] | None = None
    payment_methods: list[str] | None = None
    include_transfers: bool = False
    only_needs_review: bool = False
    only_uncategorised: bool = False

    @model_validator(mode="after")
    def _dates(self) -> TxnFilter:
        if self.end < self.start:
            raise ValueError("end is before start")
        return self


class QuerySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filter: TxnFilter
    group_by: list[GroupBy] = []
    metrics: list[Metric] = ["sum", "count"]
    flip_sign: bool = False  # report money out as positive (spending questions)
    order_by: Literal["value_desc", "value_asc", "key"] = "value_desc"
    limit: int | None = Field(default=None, ge=1, le=500)


@dataclass
class QueryResult:
    query_id: str
    spec: QuerySpec
    rows: list[dict[str, Any]]
    totals: dict[str, Any]
    txn_ids: list[str]
    coverage: CoverageReport
    quality: dict[str, Any]
    scope_text: str
    period_label: str
    notes: list[str] = field(default_factory=list)

    def for_model(self, max_rows: int = 25) -> dict[str, Any]:
        rows = self.rows[:max_rows]
        out: dict[str, Any] = {
            "query_id": self.query_id,
            "scope": self.scope_text,
            "period": {
                "start": self.spec.filter.start.isoformat(),
                "end": self.spec.filter.end.isoformat(),
                "label": self.period_label,
            },
            "totals": self.totals,
            "rows": rows,
            "coverage": self.coverage.as_dict(),
            "quality": self.quality,
        }
        if len(self.rows) > max_rows:
            out["rows_omitted"] = len(self.rows) - max_rows
        if self.notes:
            out["notes"] = self.notes
        return out


# ---------------------------------------------------------------------------------------------

parent_cat = category.alias("parent_cat")


def _uncategorised_id(conn: Connection) -> str | None:
    return conn.execute(
        select(category.c.id).where(category.c.name == UNCATEGORISED, category.c.is_system.is_(True))
    ).scalar()


def effective_kind(uncat_id: str | None) -> ColumnElement[Any]:
    """Transfers first; uncategorised rows count by sign (debit → expense, credit → income)."""
    uncategorised: ColumnElement[bool] = category.c.id.is_(None)
    if uncat_id is not None:
        uncategorised = or_(uncategorised, category.c.id == uncat_id)
    return case(
        (txn.c.is_transfer.is_(True), literal("transfer")),
        (uncategorised, case((txn.c.amount_minor < 0, literal("expense")), else_=literal("income"))),
        else_=category.c.kind,
    )


def _group_expr(g: GroupBy, kind_expr: ColumnElement[Any], fy_start_month: int) -> ColumnElement[Any]:
    d = txn.c.txn_date
    if g == "month":
        return func.strftime("%Y-%m", d)
    if g == "week":
        return func.strftime("%Y-W%W", d)
    if g == "day":
        return func.strftime("%Y-%m-%d", d)
    if g == "fy":
        y = cast(func.strftime("%Y", d), SAInteger)
        m = cast(func.strftime("%m", d), SAInteger)
        return case((m >= fy_start_month, y), else_=y - 1)
    if g == "category":
        return func.coalesce(category.c.name, UNCATEGORISED)
    if g == "parent_category":
        return func.coalesce(parent_cat.c.name, category.c.name, UNCATEGORISED)
    if g == "merchant":
        return func.coalesce(merchant.c.canonical_name, "(no merchant yet)")
    if g == "account":
        return account.c.nickname
    if g == "tag":
        return func.coalesce(tag.c.name, "(untagged)")
    if g == "payment_method":
        return txn.c.payment_method
    if g == "kind":
        return kind_expr
    raise ValueError(g)


def _fts_query(text: str) -> str:
    tokens = [t.replace('"', "") for t in text.split() if t.strip()]
    return " ".join(f'"{t}"' for t in tokens if t)


@dataclass
class _Built:
    base: Select[Any]
    val: ColumnElement[Any]
    kind_expr: ColumnElement[Any]
    uses_tag_join: bool
    account_names: list[str]
    category_names: list[str]
    excluded_names: list[str]


def _build(conn: Connection, spec: QuerySpec) -> _Built:
    f = spec.filter
    uncat_id = _uncategorised_id(conn)
    kind_expr = effective_kind(uncat_id)
    uses_tag = bool(f.tags) or "tag" in spec.group_by
    j = (
        txn.join(account, txn.c.account_id == account.c.id)
        .outerjoin(category, txn.c.category_id == category.c.id)
        .outerjoin(parent_cat, category.c.parent_id == parent_cat.c.id)
        .outerjoin(merchant, txn.c.merchant_id == merchant.c.id)
    )
    if uses_tag:
        j = j.outerjoin(transaction_tag, transaction_tag.c.txn_id == txn.c.id).outerjoin(
            tag, transaction_tag.c.tag_id == tag.c.id
        )
    conds: list[ColumnElement[Any]] = [
        txn.c.deleted_at.is_(None),
        txn.c.is_split_parent.is_(False),
        txn.c.txn_date >= f.start,
        txn.c.txn_date <= f.end,
    ]
    account_names: list[str] = []
    if f.accounts:
        accts = [resolve_account(conn, a) for a in f.accounts]
        account_names = [a.nickname for a in accts]
        conds.append(txn.c.account_id.in_([a.id for a in accts]))
    category_names: list[str] = []
    if f.categories:
        ids: list[str] = []
        include_uncat = False
        for ref in f.categories:
            c = resolve_category(conn, ref)
            category_names.append(c.path)
            ids += descendants(conn, c.id)
            if c.id == uncat_id:
                include_uncat = True
        cat_cond: ColumnElement[bool] = txn.c.category_id.in_(ids)
        if include_uncat:
            cat_cond = or_(cat_cond, txn.c.category_id.is_(None))
        conds.append(cat_cond)
    excluded_names: list[str] = []
    if f.exclude_categories:
        ex: list[str] = []
        for ref in f.exclude_categories:
            c = resolve_category(conn, ref)
            excluded_names.append(c.path)
            ex += descendants(conn, c.id)
        conds.append(or_(txn.c.category_id.is_(None), txn.c.category_id.not_in(ex)))
    if f.kinds:
        # Naming "transfer" in kinds is an explicit request for transfers.
        conds.append(kind_expr.in_(f.kinds))
    elif not f.include_transfers:
        conds.append(kind_expr != "transfer")
    if f.merchants:
        names = [m.lower() for m in f.merchants]
        conds.append(
            or_(
                func.lower(merchant.c.canonical_name).in_(names),
                *[func.lower(txn.c.raw_description).contains(n) for n in names],
            )
        )
    if f.tags:
        conds.append(func.lower(tag.c.name).in_([t.lower() for t in f.tags]))
    if f.text:
        q = _fts_query(f.text)
        if q:
            from sqlalchemy import text as sql_text

            fts_ids = (
                select(sql_text("txn_id"))
                .select_from(sql_text("txn_fts"))
                .where(sql_text("txn_fts MATCH :fts_q").bindparams(fts_q=q))
            )
            conds.append(txn.c.id.in_(fts_ids))
    if f.amount_min_minor is not None:
        conds.append(func.abs(txn.c.amount_minor) >= f.amount_min_minor)
    if f.amount_max_minor is not None:
        conds.append(func.abs(txn.c.amount_minor) <= f.amount_max_minor)
    if f.direction == "debit":
        conds.append(txn.c.amount_minor < 0)
    elif f.direction == "credit":
        conds.append(txn.c.amount_minor > 0)
    if f.payment_methods:
        conds.append(txn.c.payment_method.in_(f.payment_methods))
    if f.only_needs_review:
        conds.append(txn.c.needs_review.is_(True))
    if f.only_uncategorised:
        un: ColumnElement[bool] = txn.c.category_id.is_(None)
        if uncat_id:
            un = or_(un, txn.c.category_id == uncat_id)
        conds.append(un)
    val = -txn.c.amount_minor if spec.flip_sign else txn.c.amount_minor
    base = select().select_from(j).where(and_(*conds))
    return _Built(base, val, kind_expr, uses_tag, account_names, category_names, excluded_names)


def _metrics_row(
    sums: dict[str, int | None], count: int, values: list[int] | None, metrics: list[Metric], currency: str
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    s = int(sums.get("sum") or 0)
    for m in metrics:
        if m == "sum":
            out["sum"] = money_json(s, currency)
        elif m == "count":
            out["count"] = count
        elif m == "avg":
            out["avg"] = money_json(div_round(s, count), currency) if count else None
        elif m in ("min", "max"):
            v = sums.get(m)
            out[m] = money_json(int(v), currency) if v is not None else None
        elif m == "median":
            if values:
                vs = sorted(values)
                n = len(vs)
                med = vs[n // 2] if n % 2 else div_round(vs[n // 2 - 1] + vs[n // 2], 2)
                out["median"] = money_json(med, currency)
            else:
                out["median"] = None
    return out


def _scope_text(spec: QuerySpec, b: _Built) -> str:
    f = spec.filter
    parts = [format_period(f.start, f.end)]
    parts.append(", ".join(b.account_names) if b.account_names else "all accounts")
    if b.category_names:
        parts.append("categories: " + ", ".join(b.category_names))
    if b.excluded_names:
        parts.append("excluding " + ", ".join(b.excluded_names))
    if f.kinds:
        parts.append("kinds: " + ", ".join(f.kinds))
    if f.merchants:
        parts.append("merchants: " + ", ".join(f.merchants))
    if f.tags:
        parts.append("tags: " + ", ".join(f.tags))
    if f.text:
        parts.append(f"text: {f.text!r}")
    if f.amount_min_minor is not None or f.amount_max_minor is not None:
        lo = format_minor(f.amount_min_minor) if f.amount_min_minor is not None else "any"
        hi = format_minor(f.amount_max_minor) if f.amount_max_minor is not None else "any"
        parts.append(f"amount {lo}–{hi}")
    if f.direction:
        parts.append(f"{f.direction}s only")
    if not f.include_transfers and not (f.kinds and "transfer" in f.kinds):
        parts.append("excl. transfers")
    if spec.flip_sign:
        parts.append("money out shown as positive")
    return " · ".join(parts)


def run_query(
    conn: Connection,
    spec: QuerySpec,
    *,
    fy_start_month: int = 4,
    currency: str = "INR",
    period_label: str | None = None,
    save: bool = True,
    tool: str = "query_transactions",
) -> QueryResult:
    b = _build(conn, spec)
    val = b.val
    count_expr = (
        func.count(func.distinct(txn.c.id))
        if b.uses_tag_join and "tag" not in spec.group_by
        else func.count()
    )
    aggs = [
        func.sum(val).label("sum"),
        count_expr.label("count"),
        func.min(val).label("min"),
        func.max(val).label("max"),
    ]
    want_median = "median" in spec.metrics
    keys = [_group_expr(g, b.kind_expr, fy_start_month).label(f"k_{g}") for g in spec.group_by]

    # totals (distinct transactions, even when grouped by tag)
    tb = _build(conn, spec.model_copy(update={"group_by": [g for g in spec.group_by if g != "tag"]}))
    id_rows = conn.execute(
        tb.base.add_columns(txn.c.id, tb.val.label("v")).distinct().order_by(txn.c.txn_date, txn.c.id)
    ).all()
    txn_ids = [r[0] for r in id_rows]
    total_values = [int(r[1]) for r in id_rows]
    totals = _metrics_row(
        {
            "sum": sum(total_values),
            "min": min(total_values) if total_values else None,
            "max": max(total_values) if total_values else None,
        },
        len(total_values),
        total_values if want_median else None,
        spec.metrics,
        currency,
    )

    rows: list[dict[str, Any]] = []
    if keys:
        q = b.base.add_columns(*keys, *aggs).group_by(*keys)
        grouped = conn.execute(q).mappings().all()
        values_by_key: dict[tuple[Any, ...], list[int]] = {}
        if want_median:
            vq = b.base.add_columns(*keys, val.label("v"))
            for r in conn.execute(vq).mappings():
                values_by_key.setdefault(tuple(r[f"k_{g}"] for g in spec.group_by), []).append(int(r["v"]))
        for r in grouped:
            key_tuple = tuple(r[f"k_{g}"] for g in spec.group_by)
            key = {}
            for g, kv in zip(spec.group_by, key_tuple, strict=True):
                key[g] = fy_label(int(kv)) if g == "fy" else kv
            row: dict[str, Any] = {"key": key}
            row.update(
                _metrics_row(dict(r), int(r["count"]), values_by_key.get(key_tuple), spec.metrics, currency)
            )
            row["_sort"] = (
                int(r["sum"] or 0) if "sum" in spec.metrics or not spec.metrics else int(r["count"])
            )
            rows.append(row)
        if spec.order_by == "key":
            rows.sort(key=lambda x: tuple(str(v) for v in x["key"].values()))
        else:
            rows.sort(key=lambda x: x["_sort"], reverse=spec.order_by == "value_desc")
        for r2 in rows:
            r2.pop("_sort", None)
        if spec.limit:
            rows = rows[: spec.limit]

    cov = coverage_for(
        conn,
        spec.filter.start,
        spec.filter.end,
        [resolve_account(conn, a).id for a in spec.filter.accounts] if spec.filter.accounts else None,
    )
    quality = _quality(conn, txn_ids, currency)
    result = QueryResult(
        query_id=new_query_id(),
        spec=spec,
        rows=rows,
        totals=totals,
        txn_ids=txn_ids,
        coverage=cov,
        quality=quality,
        scope_text=_scope_text(spec, b),
        period_label=period_label or format_period(spec.filter.start, spec.filter.end),
    )
    if cov.status == "none":
        result.notes.append("No imported data covers this period; figures are not meaningful.")
    elif cov.status == "partial":
        result.notes.append("Some accounts have no data for part of this period: " + "; ".join(cov.gaps))
    if save:
        sql_hash = hashlib.sha256(str(b.base.compile()).encode()).hexdigest()
        save_receipt(
            conn,
            result.query_id,
            tool,
            spec.model_dump_json(),
            result.scope_text,
            sql_hash,
            json.dumps({"totals": totals, "rows": rows[:50]}, default=str),
            txn_ids,
        )
    return result


def _quality(conn: Connection, txn_ids: list[str], currency: str) -> dict[str, Any]:
    if not txn_ids:
        return {"transactions": 0, "uncategorised": {"count": 0}, "needs_review": {"count": 0}}
    uncat_id = _uncategorised_id(conn)
    un_count = un_sum = rev_count = 0
    for i in range(0, len(txn_ids), 500):
        chunk = txn_ids[i : i + 500]
        un: ColumnElement[bool] = txn.c.category_id.is_(None)
        if uncat_id:
            un = or_(un, txn.c.category_id == uncat_id)
        r = conn.execute(
            select(func.count(), func.coalesce(func.sum(func.abs(txn.c.amount_minor)), 0)).where(
                txn.c.id.in_(chunk), un
            )
        ).one()
        un_count += int(r[0])
        un_sum += int(r[1])
        rev_count += int(
            conn.execute(
                select(func.count()).where(txn.c.id.in_(chunk), txn.c.needs_review.is_(True))
            ).scalar_one()
        )
    return {
        "transactions": len(txn_ids),
        "uncategorised": {"count": un_count, "amount": money_json(un_sum, currency)},
        "needs_review": {"count": rev_count},
    }
