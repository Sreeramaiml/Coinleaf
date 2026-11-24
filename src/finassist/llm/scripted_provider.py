"""A deterministic fake provider for tests and offline demos (ARCH §16.1).

A script is a list of steps. Each step is either a list of tool calls
(`[("query_transactions", {...}), ...]`) or a final text string. A step may also be a
callable taking the request's messages and returning one of those, so a test can write
an answer from the tool results it received. Every request is recorded for inspection.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from finassist.llm.provider import LLMResponse, ToolCall, Usage

__all__ = ["ScriptedProvider", "last_tool_results"]

Step = str | list[tuple[str, dict[str, Any]]] | Callable[[list[dict[str, Any]]], Any]


def last_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parsed tool_result payloads from the most recent user message."""
    for m in reversed(messages):
        if m["role"] == "user" and isinstance(m["content"], list):
            out = []
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    out.append(json.loads(b["content"]))
            if out:
                return out
    return []


@dataclass
class ScriptedProvider:
    steps: list[Step]
    name: str = "scripted"
    requests: list[dict[str, Any]] = field(default_factory=list)
    _n: int = 0

    def chat(
        self, *, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]], model: str
    ) -> LLMResponse:
        self.requests.append(
            {
                "system": system,
                "tools": tools,
                "messages": json.loads(json.dumps(messages, default=str)),
                "model": model,
            }
        )
        if self._n >= len(self.steps):
            raise AssertionError("scripted provider ran out of steps")
        step = self.steps[self._n]
        self._n += 1
        if callable(step):
            step = step(messages)
        if isinstance(step, str):
            return LLMResponse("end_turn", step, [], [{"type": "text", "text": step}], model, Usage(10, 10))
        calls = [ToolCall(id=f"toolu_{self._n}_{i}", name=n, input=a) for i, (n, a) in enumerate(step)]
        content = [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls]
        return LLMResponse("tool_use", "", calls, content, model, Usage(10, 10))
