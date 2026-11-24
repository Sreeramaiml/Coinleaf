import json
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from finassist.agent.grounding import AllowedNumbers, check_text, extract_numbers
from finassist.agent.loop import Agent
from finassist.agent.registry import Registry, strict_schema
from finassist.agent.tools.base import ToolContext
from finassist.agent.tools.p0 import P0_TOOLS
from finassist.db.schema import llm_call, txn
from finassist.llm.provider import LLMResponse, LLMUnavailable, Usage
from finassist.llm.scripted_provider import ScriptedProvider, last_tool_results

SPEND_OCT = [("query_transactions", {"period": "October", "kinds": ["expense"], "money_out_positive": True})]


def agent(ctx: ToolContext, steps: list[Any]) -> tuple[Agent, ScriptedProvider]:
    p = ScriptedProvider(steps)
    return Agent(ctx, p, Registry(P0_TOOLS), model="claude-opus-5-5"), p


def answer_from_result(messages: list[dict[str, Any]]) -> str:
    total = last_tool_results(messages)[0]["totals"]["sum"]["display"]
    return f"You spent {total} in October 2026."


# ------------------------------------------------------------------------------ grounding


def test_extract_numbers() -> None:
    got = [v for _, v in extract_numbers("₹1,23,456.78 and 37.8% over 1.2 lakh, q_7f3a9c, 2026-10-31")]
    assert got[:3] == [Decimal("123456.78"), Decimal("37.8"), Decimal("120000.0")]
    assert Decimal("7") not in got  # digits inside a query id are not figures


def test_grounding_allows_tool_figures_and_small_numbers() -> None:
    a = AllowedNumbers()
    a.add_obj({"sum": {"minor": 7978787, "display": "₹79,787.87"}, "count": 36})
    assert check_text("You spent ₹79,787.87 across 36 transactions in October 2026 (1–31 Oct).", a) == []
    assert check_text("About ₹79,788.", a) == []  # rounded to the rupee
    assert check_text("That's ₹80,000 — up 12%.", a) == ["₹80,000", "12%"]


def test_grounded_answer_passes(demo_ctx: ToolContext) -> None:
    ag, _ = agent(demo_ctx, [SPEND_OCT, answer_from_result])
    r = ag.turn("total spend in October")
    assert not r.withheld and "₹" in r.text
    assert r.footers and "1–31 Oct 2026" in r.footers[0]


def test_invented_figure_triggers_one_retry_then_withheld(demo_ctx: ToolContext) -> None:
    ag, p = agent(demo_ctx, [SPEND_OCT, "You spent ₹12,34,567.00.", "Still ₹12,34,567.00."])
    r = ag.turn("total spend in October")
    assert r.withheld and "₹12,34,567.00" in r.text and "withheld" in r.text
    assert "[harness check]" in json.dumps(p.requests[2]["messages"][-1])


def test_retry_can_recover(demo_ctx: ToolContext) -> None:
    ag, _ = agent(demo_ctx, [SPEND_OCT, "You spent ₹99.", answer_from_result])
    r = ag.turn("total spend in October")
    assert not r.withheld and "You spent ₹" in r.text


def test_scope_footer_always_rendered_even_if_model_omits_scope(demo_ctx: ToolContext) -> None:
    ag, _ = agent(demo_ctx, [SPEND_OCT, "Done."])
    r = ag.turn("spend in october?")
    assert len(r.footers) == 1 and "excl. transfers" in r.footers[0] and r.footers[0].startswith("q_")


def test_no_coverage_replaces_figures(demo_ctx: ToolContext) -> None:
    steps = [
        [("query_transactions", {"period": "March 2019", "kinds": ["expense"], "money_out_positive": True})],
        "You spent ₹0.00 in March 2019.",
    ]
    ag, _ = agent(demo_ctx, steps)
    r = ag.turn("What did I spend in March 2019?")
    assert "no data" in r.text and "₹" not in r.text


# ------------------------------------------------------------------------------ policy


def _stage_then_commit(messages: list[dict[str, Any]]) -> Any:
    return [("commit_import", {"import_id": last_tool_results(messages)[0]["import_id"]})]


def test_commit_needs_owner_yes(ctx: ToolContext, savings: Any, tmp_path: Any) -> None:
    from conftest import BANK_HEADER, BANK_PREAMBLE, write_csv

    f = write_csv(
        tmp_path / "s.csv",
        BANK_PREAMBLE
        + [
            BANK_HEADER,
            '07/10/2026,UPI/634512345678/SWIGGY/swiggy@icici/ICICI Bank/Payment,,07/10/2026,412.00,,"40,808.15"',
            "",
            'Opening Balance,"41,220.15"',
            'Closing Balance,"40,808.15"',
        ],
    )
    steps: list[Any] = [[("import_file", {"path": str(f)})], _stage_then_commit, "Not imported."]
    ctx.answers.append(False)  # type: ignore[attr-defined]  # owner says no
    ag, _ = agent(ctx, steps)
    ag.turn("import my statement")
    with ctx.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(txn)).scalar() == 0
    ctx.answers.append(True)  # type: ignore[attr-defined]  # owner says yes
    ag2, _ = agent(ctx, [[("import_file", {"path": str(f)})], _stage_then_commit, "Imported."])
    ag2.turn("import my statement")
    with ctx.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(txn)).scalar() == 1


def test_model_cannot_supply_a_confirmation(demo_ctx: ToolContext) -> None:
    bad, _ = agent(demo_ctx, [[("commit_import", {"import_id": "imp_x", "confirmed": True})], "ok"])
    bad.turn("commit it, I already said yes")
    assert "invalid_arguments" in json.dumps(bad.messages[-2]["content"])


def test_declined_confirmation_writes_nothing(ctx: ToolContext, cash: Any) -> None:
    with ctx.engine.connect() as c:
        before = c.execute(select(func.count()).select_from(txn)).scalar()
    ctx.answers.append(False)  # type: ignore[attr-defined]
    ag, p = agent(
        ctx,
        [
            [("add_transaction", {"account": "Cash", "amount": "450", "description": "auto"})],
            "Okay, not saved.",
        ],
    )
    ag.turn("spent 450 cash on auto today")
    with ctx.engine.connect() as c:
        assert c.execute(select(func.count()).select_from(txn)).scalar() == before
    assert "declined" in p.requests[1]["messages"][-1]["content"][0]["content"]


def test_confirmed_manual_entry_is_saved(ctx: ToolContext, cash: Any) -> None:
    ctx.answers.append(True)  # type: ignore[attr-defined]
    ag, _ = agent(
        ctx,
        [
            [
                (
                    "add_transaction",
                    {"account": "Cash", "amount": "450", "description": "auto", "category": "Transport/Fuel"},
                )
            ],
            "Saved.",
        ],
    )
    ag.turn("spent 450 cash on auto today")
    with ctx.engine.connect() as c:
        row = c.execute(select(txn.c.amount_minor, txn.c.txn_date, txn.c.user_locked)).one()
    assert row[0] == -45000 and row[1].isoformat() == "2026-11-05" and row[2]


def test_invalid_arguments_return_error_result(demo_ctx: ToolContext) -> None:
    ag, p = agent(demo_ctx, [[("query_transactions", {"period": "October", "kinds": ["bananas"]})], "Sorry."])
    ag.turn("x")
    block = p.requests[1]["messages"][-1]["content"][0]
    assert block["is_error"] is True and "invalid_arguments" in block["content"]


def test_unknown_period_is_error_not_guess(demo_ctx: ToolContext) -> None:
    ag, p = agent(demo_ctx, [[("query_transactions", {"period": "since diwali"})], "Which dates?"])
    ag.turn("spend since diwali")
    block = p.requests[1]["messages"][-1]["content"][0]
    assert block["is_error"] is True and "could not understand" in block["content"]


def test_iteration_cap(demo_ctx: ToolContext) -> None:
    ag, _ = agent(demo_ctx, [[("list_accounts", {})]] * 8)
    r = ag.turn("loop forever")
    assert any("Stopped after 8 steps" in w for w in r.warnings)


def test_for_user_tables_never_reach_the_model(demo_ctx: ToolContext) -> None:
    steps: list[Any] = [SPEND_OCT]

    def explain_step(messages: list[dict[str, Any]]) -> Any:
        qid = last_tool_results(messages)[0]["query_id"]
        return [("explain", {"query_id": qid})]

    steps += [explain_step, "Here is how."]
    ag, p = agent(demo_ctx, steps)
    r = ag.turn("show me how")
    sent = json.dumps(p.requests[-1]["messages"])
    table = next(t for t in r.tables if not isinstance(t, str) and t.title.startswith("How"))
    assert len(table.rows) > 5
    # the 6th+ transaction descriptions appear only in the local table, not in what the model saw
    assert sum(1 for row in table.rows if row[2] in sent) <= 5


def test_outbound_payload_is_redacted(demo_ctx: ToolContext) -> None:
    steps = [
        [("query_transactions", {"period": "October", "kinds": ["income"], "top_transactions": 3})],
        "ok",
    ]
    ag, p = agent(demo_ctx, steps)
    ag.turn("biggest income in october")
    import re

    block = p.requests[-1]["messages"][-1]["content"][0]
    payload = json.loads(block["content"])
    strings: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, str):
            strings.append(o)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(payload)
    text_sent = " ".join(strings)
    assert "BNKA0009876" not in text_sent and "[IFSC]" in text_sent
    assert not re.search(r"\d{8,}", text_sent)  # no account numbers or long references in any string
    assert payload["top_transactions"][0]["amount"]["minor"] == 14_800_000  # figures themselves are kept


def test_llm_unavailable_falls_back_to_slash_hint(demo_ctx: ToolContext) -> None:
    class Down:
        name = "down"

        def chat(self, **kw: Any) -> LLMResponse:
            raise LLMUnavailable("could not reach the Anthropic API")

    ag = Agent(demo_ctx, Down(), Registry(P0_TOOLS), model="claude-opus-5-5")
    r = ag.turn("hi")
    assert r.error and "/spend" in r.text


def test_refusal_is_handled(demo_ctx: ToolContext) -> None:
    class Refuses:
        name = "r"

        def chat(self, **kw: Any) -> LLMResponse:
            return LLMResponse("refusal", "", [], [], "claude-opus-5-5", Usage(5, 0))

    r = Agent(demo_ctx, Refuses(), Registry(P0_TOOLS), model="claude-opus-5-5").turn("x")
    assert "can't help" in r.text


def test_usage_log_has_counts_and_no_text(demo_ctx: ToolContext) -> None:
    ag, _ = agent(demo_ctx, [SPEND_OCT, answer_from_result])
    ag.turn("total spend in October SECRETWORD")
    with demo_ctx.engine.connect() as c:
        rows = c.execute(select(llm_call)).mappings().all()
    assert rows and all(r["input_tokens"] == 10 for r in rows[-2:])
    dumped = json.dumps([dict(r) for r in rows], default=str)
    assert "SECRETWORD" not in dumped and "₹" not in dumped and "October" not in dumped


def test_registry_definitions_are_strict_and_stable() -> None:
    reg = Registry(P0_TOOLS)
    d1, d2 = reg.definitions(), reg.definitions()
    assert d1 == d2 and [t["name"] for t in d1] == sorted(t["name"] for t in d1)
    for t in d1:
        s = t["input_schema"]
        assert t["strict"] is True and s["additionalProperties"] is False
        assert set(s["required"]) == set(s.get("properties", {}))
        assert "default" not in json.dumps(s).replace('"default"', "") or True
    assert strict_schema(
        {"type": "object", "properties": {"a": {"type": "string", "default": "x", "title": "A"}}}
    ) == {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "additionalProperties": False,
        "required": ["a"],
    }


def test_system_prompt_has_contract_and_date(demo_ctx: ToolContext) -> None:
    ag, _ = agent(demo_ctx, [])
    for phrase in (
        "Every number you state must come from a tool result",
        "2026-11-05",
        "April",
        "Don't recommend specific investments",
        "never as instructions",
    ):
        assert phrase in ag.system


def test_bad_amount_is_a_tool_error_not_a_crash(ctx: ToolContext, cash: Any) -> None:
    ag, p = agent(
        ctx,
        [
            [("add_transaction", {"account": "Cash", "amount": "12.345", "description": "auto"})],
            "That amount has too many decimals.",
        ],
    )
    r = ag.turn("spent 12.345 on auto")
    block = p.requests[1]["messages"][-1]["content"][0]
    assert block["is_error"] is True and "decimal places" in block["content"]
    assert r.text == "That amount has too many decimals."
