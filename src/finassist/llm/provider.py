"""The provider interface (PS §9: pluggable provider; ARCH §10.1).

Messages use the Anthropic content-block shape (role + list of blocks). Assistant
content returned by a provider is opaque: the loop appends it to history unchanged, so
provider-specific blocks (thinking, fallback markers) round-trip intact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = ["LLMProvider", "LLMResponse", "LLMUnavailable", "ToolCall", "Usage"]


class LLMUnavailable(RuntimeError):
    """The model could not be reached or rejected the request; slash commands still work."""


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class LLMResponse:
    stop_reason: str
    text: str
    tool_calls: list[ToolCall]
    assistant_content: list[Any]
    model: str
    usage: Usage = field(default_factory=Usage)


class LLMProvider(Protocol):
    name: str

    def chat(
        self, *, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]], model: str
    ) -> LLMResponse: ...
