"""Dashboard data, transaction listing, payee names, compact amounts and chart geometry."""

from __future__ import annotations

import re
from datetime import date

import pytest

from finassist.analytics.dashboard import build_dashboard, data_months, previous_period
from finassist.analytics.listing import list_transactions
from finassist.core.money import format_compact_inr
from finassist.core.periods import ResolvedPeriod
from finassist.core.textnorm import payee_of
from finassist.demo.setup import DemoResult
from finassist.web import charts

OCT = ResolvedPeriod(date(2026, 10, 1), date(2026, 10, 31), "October 2026")


def truth_sum(demo: DemoResult, start: str, end: str, kind: str) -> int:
    s = sum(t.amount_minor for t in demo.data.truth if start <= t.txn_date <= end and t.kind == kind)
    return -s if kind == "expense" else s


@pytest.mark.parametrize(
    "raw,name",
    [
        ("UPI/634512345678/SWIGGY/swiggy@icici/ICICI Bank/Payment from Phone", "Swiggy"),
        ("IMPS/P2A/612345678901/RAMESH IYER/RENT OCT", "Ramesh Iyer"),
        ("NEFT CR-BNKA0009876-ACME TECHNOLOGIES PVT LTD-SALARY OCT 2026-N123", "Acme Technologies"),
        ("ACH D- HOMEFIN LOANS LTD-EMI 123456789", "Homefin Loans"),
        ("BIL/ONL/123456789/STATE ELECTRICITY BOARD/CONS 1", "State Electricity Board"),
        ("INT.PD:2026-09 SAVINGS INTEREST", "Savings interest"),
        ("SWIGGY*ORDER BANGALORE IN", "Swiggy"),
        ("AMAZON PAY INDIA PRIVATE LIMITED BANGALORE IN", "Amazon Pay"),
        ("HPCL FUEL STATION KORAMANGALA IN", "Hpcl Fuel Station"),
        ("auto rickshaw", "Auto Rickshaw"),
    ],
)
def test_payee_of(raw: str, name: str) -> None:
    assert payee_of(raw) == name


@pytest.mark.parametrize(
    "minor,text",
    [
        (0, "₹0"),
        (85_000, "₹850"),
        (1_234_567, "₹12.3K"),
        (14_800_000, "₹1.48L"),
        (2_300_000_000, "₹2.3Cr"),
        (-7_978_787, "-₹79.8K"),
    ],
)
def test_compact_inr(minor: int, text: str) -> None:
    assert format_compact_inr(minor) == text


def test_previous_period() -> None:
    assert previous_period(OCT) == ResolvedPeriod(date(2026, 9, 1), date(2026, 9, 30), "September 2026")
    p = previous_period(ResolvedPeriod(date(2026, 8, 1), date(2026, 10, 31), "x"))
    assert (p.start, p.end) == (date(2026, 5, 1), date(2026, 7, 31))  # the same 92 days, just before


def test_dashboard_totals_match_ground_truth(demo: DemoResult) -> None:
    with demo.ledger.engine.connect() as c:
        d = build_dashboard(c, OCT, date(2026, 11, 5))
        assert d.now.money_out == truth_sum(demo, "2026-10-01", "2026-10-31", "expense")
        assert d.now.money_in == truth_sum(demo, "2026-10-01", "2026-10-31", "income")
        assert d.before.money_out == truth_sum(demo, "2026-09-01", "2026-09-30", "expense")
        assert d.now.saved == d.now.money_in - d.now.money_out
        assert sum(v for _, v in d.by_method) == d.now.money_out  # breakdowns add up to the total
        assert len(d.monthly) == 12 and d.monthly[-1]["month"] == "2026-10"
        for m in d.monthly:
            start, end = m["month"] + "-01", m["month"] + "-31"
            assert m["out"] == truth_sum(demo, start, end, "expense"), m["month"]
        assert len(d.daily) == 31 and sum(v for _, v in d.daily) == d.now.money_out
        assert d.net_worth == sum(b.balance for b in d.balances)
        assert d.available + -d.card_owed == d.net_worth  # demo: no other account types
        assert data_months(c)[0] == "2026-10"


def test_listing_matches_totals_and_pages(demo: DemoResult) -> None:
    with demo.ledger.engine.connect() as c:
        full = list_transactions(c, OCT.start, OCT.end, limit=None)
        assert full.total == len(full.rows)
        assert full.money_out == truth_sum(demo, "2026-10-01", "2026-10-31", "expense")
        assert full.money_in == truth_sum(demo, "2026-10-01", "2026-10-31", "income")
        dates = [r["date"] for r in full.rows]
        assert dates == sorted(dates, reverse=True)
        page = list_transactions(c, OCT.start, OCT.end, offset=10, limit=10)
        assert [r["id"] for r in page.rows] == [r["id"] for r in full.rows[10:20]]
        outs = list_transactions(c, OCT.start, OCT.end, direction="out", limit=None)
        assert all(r["amount"] < 0 for r in outs.rows) and outs.money_in == 0


def test_chart_geometry_follows_the_mark_specs() -> None:
    assert charts.nice_ticks(15_995_491) == [0, 5_000_000, 10_000_000, 15_000_000, 20_000_000]
    days = [(date(2026, 10, d), d * 10_000) for d in range(1, 32)]
    col = charts.columns(days, "s2", "t")
    widths = []
    for c in col.data["cols"]:
        xs = [float(x) for x in re.findall(r"[ML](-?\d+\.\d+),", c["d"])]
        widths.append(max(xs) - min(xs))
    assert max(widths) <= 24.0  # bars never thicker than 24px
    few = charts.columns(days[:3], "s2", "t")
    xs = [float(x) for x in re.findall(r"[ML](-?\d+\.\d+),", few.data["cols"][0]["d"])]
    assert max(xs) - min(xs) == 24.0  # capped even when there is room
    seg = charts.segments([("A", 300), ("B", 100), ("C", -50)], ["s1", "s2", "s3"])
    assert [b["name"] for b in seg["blocks"]] == ["A", "B"] and seg["blocks"][0]["label"] == "75%"
    line = charts.line_chart(
        [{"month": f"2026-{m:02d}", "in": 100, "out": 50} for m in range(1, 13)],
        [("in", "Money in", "s1"), ("out", "Money out", "s2")],
    )
    assert len(line.data["hits"]) == 12 and "Money in\t₹1.00" in line.data["hits"][0]["rows"]
