"""Tool registry: pydantic input models → provider tool definitions.

With strict tool use, the API guarantees arguments match the schema. Strict schemas need
`additionalProperties: false` and every property listed in `required`; optional fields
stay optional by allowing null. Arguments are validated again with pydantic regardless.
"""

from __future__ import annotations

import copy
from typing import Any

from finassist.agent.tools.base import Tool

__all__ = ["Registry", "strict_schema"]

_DROP = {"title", "default"}


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    s = copy.deepcopy(schema)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            node = {k: walk(v) for k, v in node.items() if k not in _DROP}
            if node.get("type") == "object" or "properties" in node:
                node["type"] = "object"
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}).keys())
            return node
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(s)  # type: ignore[no-any-return]


class Registry:
    def __init__(self, tools: list[Tool], strict: bool = True) -> None:
        self.tools = {t.name: t for t in tools}
        self.strict = strict
        if len(self.tools) != len(tools):
            raise ValueError("duplicate tool names")

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def definitions(self) -> list[dict[str, Any]]:
        """Deterministic order and content, so the cached prompt prefix stays stable."""
        out = []
        for name in sorted(self.tools):
            t = self.tools[name]
            schema = t.Input.model_json_schema()
            d: dict[str, Any] = {
                "name": t.name,
                "description": t.description,
                "input_schema": strict_schema(schema) if self.strict else schema,
            }
            if self.strict:
                d["strict"] = True
            out.append(d)
        return out
