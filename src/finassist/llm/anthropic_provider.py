"""Anthropic Messages API provider (ARCH §11.1).

- Streaming with get_final_message() (long outputs never hit HTTP timeouts).
- Prompt caching: one cache breakpoint at the end of the frozen system prompt, which
  covers the tool definitions rendered before it.
- Adaptive thinking is the model default; depth is set with output_config.effort.
- tool_choice stays auto (forced tool choice is not supported on this model).
- Server-side refusal fallbacks (`fallbacks: "default"`) are on by default; the loop
  still checks stop_reason == "refusal" before reading content.
"""

from __future__ import annotations

from typing import Any

import anthropic

from finassist.config.settings import LLMConfig
from finassist.llm.provider import LLMResponse, LLMUnavailable, ToolCall, Usage

__all__ = ["AnthropicProvider"]

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, cfg: LLMConfig, api_key: str | None = None) -> None:
        self.cfg = cfg
        self.client = (
            anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=120.0)
            if api_key
            else anthropic.Anthropic(max_retries=2, timeout=120.0)
        )

    def chat(
        self, *, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]], model: str
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self.cfg.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "tools": tools,
            "messages": messages,
            "output_config": {"effort": self.cfg.effort},
        }
        msg: Any
        try:
            if self.cfg.refusal_fallbacks:
                with self.client.beta.messages.stream(
                    **kwargs, betas=[FALLBACK_BETA], fallbacks="default"
                ) as stream:
                    msg = stream.get_final_message()
            else:
                with self.client.messages.stream(**kwargs) as stream:
                    msg = stream.get_final_message()
        except anthropic.AuthenticationError:
            raise LLMUnavailable("the Anthropic API rejected the API key") from None
        except anthropic.RateLimitError:
            raise LLMUnavailable("the Anthropic API is rate-limiting requests; try again shortly") from None
        except anthropic.BadRequestError as e:
            raise LLMUnavailable(f"the Anthropic API rejected the request: {e.message}") from None
        except anthropic.APIStatusError as e:
            raise LLMUnavailable(f"the Anthropic API returned an error ({e.status_code})") from None
        except anthropic.APIConnectionError:
            raise LLMUnavailable("could not reach the Anthropic API") from None

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        content: list[Any] = list(msg.content)
        for block in content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input or {})))
        u = msg.usage
        usage = Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        return LLMResponse(
            stop_reason=msg.stop_reason or "end_turn",
            text="\n".join(text_parts).strip(),
            tool_calls=calls,
            assistant_content=content,
            model=msg.model,
            usage=usage,
        )
