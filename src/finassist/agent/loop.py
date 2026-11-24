"""The agent turn loop (ARCH §10.1). Tools run locally against the ledger; the model only
chooses tools and phrases answers from their results.

One turn:
  user text → model → tool calls (validated) → policy gate → run → redact for_model →
  results back to the model … → final text → grounding check (one retry) → coverage
  override → TurnResult with scope footers and locally rendered tables.
"""

from __future__ import annotations

import calendar
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from finassist.agent import policy
from finassist.agent.grounding import AllowedNumbers, check_text
from finassist.agent.registry import Registry
from finassist.agent.tools.base import ToolContext, UserTable
from finassist.analytics.query import QueryResult
from finassist.core.errors import FinError
from finassist.llm.provider import LLMProvider, LLMUnavailable
from finassist.llm.redact import redact_obj
from finassist.llm.usage import record_call

__all__ = ["Agent", "TurnResult", "build_system_prompt"]

PROMPT_FILE = Path(__file__).parent / "prompts" / "system.md"


def build_system_prompt(ctx: ToolContext) -> str:
    s = ctx.settings
    quarter = (
        "Indian financial-year quarters (Q1 = Apr–Jun)"
        if s.periods.quarter_convention == "fy"
        else "calendar quarters (Q1 = Jan–Mar)"
    )
    return PROMPT_FILE.read_text(encoding="utf-8").format(
        today=ctx.clock.today().isoformat(),
        timezone=s.general.timezone,
        currency=ctx.currency,
        fy_start_month_name=calendar.month_name[s.general.fy_start_month],
        quarter_meaning=quarter,
    )


@dataclass
class TurnResult:
    text: str
    tables: list[UserTable | str] = field(default_factory=list)
    footers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    withheld: bool = False
    error: str | None = None
    tool_calls: list[str] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        ctx: ToolContext,
        provider: LLMProvider,
        registry: Registry,
        *,
        model: str,
        max_iterations: int = 8,
        role: str = "chat",
    ) -> None:
        self.ctx = ctx
        self.provider = provider
        self.registry = registry
        self.model = model
        self.max_iterations = max_iterations
        self.role = role
        self.messages: list[dict[str, Any]] = []
        self.allowed = AllowedNumbers()
        self.system = build_system_prompt(ctx)
        self.tool_defs = registry.definitions()

    # ------------------------------------------------------------------------------------------
    def _call_model(self, redactions: int) -> Any:
        try:
            resp = self.provider.chat(
                system=self.system, tools=self.tool_defs, messages=self.messages, model=self.model
            )
        except LLMUnavailable:
            record_call(
                self.ctx.engine,
                role=self.role,
                provider=self.provider.name,
                model=self.model,
                usage=_zero_usage(),
                redactions=redactions,
                stop_reason=None,
                ok=False,
            )
            raise
        record_call(
            self.ctx.engine,
            role=self.role,
            provider=self.provider.name,
            model=self.model,
            usage=resp.usage,
            redactions=redactions,
            stop_reason=resp.stop_reason,
            ok=True,
        )
        return resp

    def _run_tool(self, name: str, raw_input: dict[str, Any]) -> tuple[dict[str, Any], bool, Any]:
        tool = self.registry.get(name)
        if tool is None:
            return {"error": {"code": "unknown_tool", "message": f"no tool named {name}"}}, True, None
        try:
            args = tool.Input.model_validate(raw_input)
        except ValidationError as e:
            errs = [{"field": ".".join(str(p) for p in er["loc"]), "message": er["msg"]} for er in e.errors()]
            return {"error": {"code": "invalid_arguments", "details": errs}}, True, None
        try:
            out = policy.execute(tool, self.ctx, args)
        except FinError as e:
            return {"error": e.as_dict()}, True, None
        except ValueError as e:  # e.g. an amount with too many decimals, an unreadable period
            return {"error": {"code": "invalid", "message": str(e)}}, True, None
        return out.for_model, False, out

    def turn(self, user_text: str) -> TurnResult:
        self.messages.append({"role": "user", "content": [{"type": "text", "text": user_text}]})
        self.allowed.add_text(user_text)
        result = TurnResult(text="")
        queries: list[QueryResult] = []
        retried = False
        nudged = False
        redactions = 0
        for _ in range(self.max_iterations):
            try:
                resp = self._call_model(redactions)
            except LLMUnavailable as e:
                result.error = str(e)
                result.text = (
                    "The language model is unavailable (" + str(e) + "). Slash commands still work: "
                    "/spend, /income, /accounts, /coverage, /how — type /help."
                )
                self._finish(result, queries)
                return result
            redactions = 0
            if resp.assistant_content:
                self.messages.append({"role": "assistant", "content": resp.assistant_content})
            if resp.stop_reason == "refusal":
                result.text = "I can't help with that request. I can answer questions about your own ledger."
                self._finish(result, queries)
                return result
            if resp.tool_calls:
                blocks = []
                for call in resp.tool_calls:
                    result.tool_calls.append(call.name)
                    payload, is_error, out = self._run_tool(call.name, call.input)
                    if out is not None:
                        result.tables.extend(out.for_user)
                        queries.extend(out.queries)
                    payload, n = redact_obj(payload)
                    redactions += n
                    self.allowed.add_obj(payload)
                    blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": call.id,
                            "content": json.dumps(payload, ensure_ascii=False, default=str),
                            **({"is_error": True} if is_error else {}),
                        }
                    )
                self.messages.append({"role": "user", "content": blocks})
                continue
            if resp.stop_reason == "max_tokens":
                result.warnings.append("The answer was cut off (output limit reached).")
            if not resp.text.strip() and not nudged:
                # e.g. Gemini's MALFORMED_FUNCTION_CALL: neither text nor a usable call came back
                nudged = True
                self.messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "[harness check] No answer or tool call was received. Call a tool with valid "
                                    "arguments, or answer in plain text."
                                ),
                            }
                        ],
                    }
                )
                continue
            text = resp.text
            problems = check_text(text, self.allowed)
            if problems and not retried:
                retried = True
                self.messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "[harness check] These figures are not in any tool result: "
                                    + ", ".join(problems)
                                    + ". Call a tool to get them, or answer without them. Quote display strings exactly."
                                ),
                            }
                        ],
                    }
                )
                continue
            if problems:
                result.withheld = True
                result.text = (
                    "(The assistant's summary was withheld because it contained figures no tool "
                    "returned: " + ", ".join(problems) + ". The tool results are shown below.)"
                )
            else:
                result.text = text
            self._finish(result, queries)
            return result
        result.warnings.append(f"Stopped after {self.max_iterations} steps without a final answer.")
        result.text = result.text or "I couldn't finish that. The tool results so far are shown below."
        self._finish(result, queries)
        return result

    def _finish(self, result: TurnResult, queries: list[QueryResult]) -> None:
        if queries and all(q.coverage.status == "none" for q in queries):
            gaps = "; ".join(dict.fromkeys(g for q in queries for g in q.coverage.gaps))
            if not result.withheld and result.text and not result.error:
                result.warnings.append("No figures shown: the ledger has no data for that period.")
            result.text = (
                "The ledger has no data for "
                + ", ".join(dict.fromkeys(q.period_label for q in queries))
                + f", so I can't give a figure ({gaps}). Use /coverage to see what has been imported."
            )
            result.tables = [
                t for t in result.tables if not isinstance(t, UserTable) or "Result" not in t.title
            ]
        else:
            for q in queries:
                for note in q.notes:
                    if note not in result.warnings:
                        result.warnings.append(note)
        seen = set()
        for q in queries:
            if q.query_id in seen:
                continue
            seen.add(q.query_id)
            tx = q.quality.get("transactions", 0)
            un = q.quality.get("uncategorised", {})
            line = f"{q.query_id}: {q.scope_text} · {tx} transaction{'' if tx == 1 else 's'}"
            if un.get("count"):
                line += f" · {un['count']} uncategorised ({un['amount']['display']})"
            result.footers.append(line)


def _zero_usage() -> Any:
    from finassist.llm.provider import Usage

    return Usage()
