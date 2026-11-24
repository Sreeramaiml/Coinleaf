from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from finassist.analytics.coverage import coverage_for
from finassist.analytics.metrics import expense, income
from finassist.analytics.query import QuerySpec, TxnFilter, run_query
from finassist.analytics.receipts import load_receipt
from finassist.db.audit import open_change_set, update
from finassist.db.ledger import Ledger
from finassist.db.repo.accounts import AccountRow
from finassist.db.repo.categories import resolve_category
from finassist.db.repo.transactions import NewTxn, add_transactions
from finassist.db.schema import txn
from finassist.demo.setup import DemoResult


def oct_spec(**kw: object) -> QuerySpec:
    return QuerySpec(filter=TxnFilter(start=date(2026, 10, 1), end=date(2026, 10, 31), **kw))  # type: ignore[arg-type]


def sql_total(demo: DemoResult, where: str) -> int:
    with demo.ledger.engine.connect() as c:
        return int(c.execute(text(f"SELECT COALESCE(SUM(amount_minor),0) FROM txn WHERE {where}")).scalar())


@pytest.mark.parametrize(
    "spec_kw,flip,where",
    [
        ({"kinds": ["expense"]}, True, "amount_minor < 0 AND txn_date BETWEEN '2026-10-01' AND '2026-10-31'"),
        ({"kinds": ["income"]}, False, "amount_minor > 0 AND txn_date BETWEEN '2026-10-01' AND '2026-10-31'"),
        (
            {"accounts": ["Credit card"], "kinds": ["expense"]},
            True,
            "amount_minor < 0 AND txn_date BETWEEN '2026-10-01' AND '2026-10-31' AND account_id = "
            "(SELECT id FROM account WHERE name = 'Card A')",
        ),
        (
            {"merchants": ["swiggy"], "kinds": ["expense"]},
            True,
            "amount_minor < 0 AND txn_date BETWEEN '2026-10-01' AND '2026-10-31' AND lower(raw_description) LIKE '%swiggy%'",
        ),
        (
            {"amount_min_minor": 500_000},
            False,
            "abs(amount_minor) >= 500000 AND txn_date BETWEEN '2026-10-01' AND '2026-10-31'",
        ),
    ],
)
def test_totals_equal_hand_written_sql(demo: DemoResult, spec_kw: dict, flip: bool, where: str) -> None:  # type: ignore[type-arg]
    with demo.ledger.engine.begin() as c:
        res = run_query(c, oct_spec(**spec_kw).model_copy(update={"flip_sign": flip}))
    expected = sql_total(demo, where)
    assert res.totals["sum"]["minor"] == (-expected if flip else expected)


def test_group_by_month_matches_sql_and_ground_truth(demo: DemoResult) -> None:
    spec = QuerySpec(
        filter=TxnFilter(start=date(2025, 9, 1), end=date(2026, 10, 31), kinds=["expense"]),
        group_by=["month"],
        metrics=["sum", "count", "avg", "median", "min", "max"],
        flip_sign=True,
        order_by="key",
    )
    with demo.ledger.engine.begin() as c:
        res = run_query(c, spec)
    assert len(res.rows) == 14
    truth: dict[str, int] = {}
    for t in demo.data.truth:
        if t.kind == "expense":
            truth[t.txn_date[:7]] = truth.get(t.txn_date[:7], 0) - t.amount_minor
    for row in res.rows:
        assert row["sum"]["minor"] == truth[row["key"]["month"]]
        assert isinstance(row["avg"]["minor"], int) and isinstance(row["median"]["minor"], int)
        assert row["min"]["minor"] <= row["median"]["minor"] <= row["max"]["minor"]
    assert res.totals["sum"]["minor"] == sum(truth.values())


def test_avg_is_integer_half_even(ledger: Ledger, savings: AccountRow) -> None:
    with ledger.engine.begin() as c:
        cs = open_change_set(c, "user", "t")
        add_transactions(
            c,
            cs,
            [
                NewTxn(account=savings, txn_date=date(2026, 10, 2), amount_minor=-a, raw_description=f"x{a}")
                for a in (1, 2)
            ],
        )
        res = run_query(
            c,
            oct_spec(kinds=["expense"]).model_copy(update={"flip_sign": True, "metrics": ["avg", "median"]}),
        )
    assert res.totals["avg"]["minor"] == 2  # 3/2 = 1.5 → 2 (half-even)
    assert res.totals["median"]["minor"] == 2


def test_parent_category_includes_children_and_exclusion(ledger: Ledger, savings: AccountRow) -> None:
    with ledger.engine.begin() as c:
        cs = open_change_set(c, "user", "t")
        g = resolve_category(c, "Groceries").id
        rent = resolve_category(c, "Rent/Housing").id
        eat = resolve_category(c, "Eating out").id
        add_transactions(
            c,
            cs,
            [
                NewTxn(
                    account=savings,
                    txn_date=date(2026, 10, 3),
                    amount_minor=-1000_00,
                    raw_description="g",
                    category_id=g,
                ),
                NewTxn(
                    account=savings,
                    txn_date=date(2026, 10, 4),
                    amount_minor=-28000_00,
                    raw_description="r",
                    category_id=rent,
                ),
                NewTxn(
                    account=savings,
                    txn_date=date(2026, 10, 5),
                    amount_minor=-500_00,
                    raw_description="e",
                    category_id=eat,
                ),
            ],
        )
        ess = run_query(c, oct_spec(categories=["Essentials"]).model_copy(update={"flip_sign": True}))
        assert ess.totals["sum"]["minor"] == 29000_00
        no_rent = run_query(
            c,
            oct_spec(kinds=["expense"], exclude_categories=["Rent/Housing"]).model_copy(
                update={"flip_sign": True}
            ),
        )
        assert no_rent.totals["sum"]["minor"] == 1500_00
        by_cat = run_query(
            c,
            oct_spec(kinds=["expense"]).model_copy(
                update={"flip_sign": True, "group_by": ["parent_category"]}
            ),
        )
        assert {r["key"]["parent_category"]: r["sum"]["minor"] for r in by_cat.rows} == {
            "Essentials": 29000_00,
            "Lifestyle": 500_00,
        }


def test_transfers_excluded_unless_asked(ledger: Ledger, savings: AccountRow, cash: AccountRow) -> None:
    with ledger.engine.begin() as c:
        cs = open_change_set(c, "user", "t")
        ids = add_transactions(
            c,
            cs,
            [
                NewTxn(
                    account=savings, txn_date=date(2026, 10, 3), amount_minor=-2000_00, raw_description="ATM"
                ),
                NewTxn(
                    account=cash, txn_date=date(2026, 10, 3), amount_minor=2000_00, raw_description="ATM in"
                ),
                NewTxn(
                    account=savings, txn_date=date(2026, 10, 4), amount_minor=-300_00, raw_description="shop"
                ),
            ],
        )
        for i in ids[:2]:
            update(c, cs, txn, i, {"is_transfer": True})
        assert (
            run_query(c, oct_spec(kinds=["expense"]).model_copy(update={"flip_sign": True})).totals["sum"][
                "minor"
            ]
            == 300_00
        )
        assert run_query(c, oct_spec()).totals["sum"]["minor"] == -300_00
        assert run_query(c, oct_spec(include_transfers=True)).totals["count"] == 3
        assert run_query(c, oct_spec(kinds=["transfer"])).totals["count"] == 2


def test_coverage_none_partial_full(demo: DemoResult) -> None:
    with demo.ledger.engine.connect() as c:
        assert coverage_for(c, date(2019, 3, 1), date(2019, 3, 31)).status == "none"
        assert coverage_for(c, date(2026, 10, 1), date(2026, 10, 31)).status == "full"
        p = coverage_for(c, date(2026, 10, 15), date(2026, 11, 5))
        assert p.status == "partial" and any("covers only" in g for g in p.gaps)
    with demo.ledger.engine.begin() as c:
        res = run_query(c, QuerySpec(filter=TxnFilter(start=date(2019, 3, 1), end=date(2019, 3, 31))))
        assert res.coverage.status == "none" and res.notes


def test_receipt_round_trip(demo: DemoResult) -> None:
    with demo.ledger.engine.begin() as c:
        res = run_query(c, oct_spec(kinds=["expense"]).model_copy(update={"flip_sign": True}))
        rc = load_receipt(c, res.query_id)
    assert rc.txn_ids == res.txn_ids and rc.scope_text == res.scope_text
    assert rc.result["totals"]["sum"]["minor"] == res.totals["sum"]["minor"]


def test_metrics_income_expense(demo: DemoResult) -> None:
    with demo.ledger.engine.connect() as c:
        e = expense(c, date(2026, 10, 1), date(2026, 10, 31))
        i = income(c, date(2026, 10, 1), date(2026, 10, 31))
    t_exp = -sum(
        t.amount_minor for t in demo.data.truth if t.txn_date.startswith("2026-10") and t.kind == "expense"
    )
    t_inc = sum(
        t.amount_minor for t in demo.data.truth if t.txn_date.startswith("2026-10") and t.kind == "income"
    )
    assert (e.totals["sum"]["minor"], i.totals["sum"]["minor"]) == (t_exp, t_inc)
    assert isinstance(Decimal(e.totals["sum"]["minor"]), Decimal)


def test_text_search_uses_fts(demo: DemoResult) -> None:
    with demo.ledger.engine.begin() as c:
        res = run_query(c, oct_spec(text="netflix"))
    assert res.totals["count"] == 1
