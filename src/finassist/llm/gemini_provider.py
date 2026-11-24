"""Gemini API provider (google-genai SDK, `models.generate_content`).

The agent loop speaks one message shape (role + content blocks: text, tool_use,
tool_result). This provider translates it to Gemini `Content`/`Part` objects and back.

- Tools are declared with `parameters_json_schema` (plain JSON Schema).
- Automatic function calling is disabled: every call goes back to the agent loop, so the
  policy gate, redaction and grounding check apply (ARCH §10).
- The model's own `Content` is kept in history unchanged, so thought signatures (required
  by Gemini 3 models in multi-turn function calling) are returned exactly as received.
- Thinking depth uses `thinking_level`; `store` controls per-request logging.
- Blocked or safety-stopped responses are reported as stop_reason "refusal".
"""

from __future__ import annotations

import itertools
import json
from typing import Any

from finassist.config.settings import LLMConfig
from finassist.llm.provider import LLMResponse, LLMUnavailable, ToolCall, Usage

__all__ = ["GeminiProvider", "gemini_tool_schema"]

_REFUSAL_REASONS = {
    "SAFETY",
    "PROHIBITED_CONTENT",
    "BLOCKLIST",
    "SPII",
    "RECITATION",
    "IMAGE_SAFETY",
    "IMAGE_PROHIBITED_CONTENT",
}
_LEVEL = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH", "xhigh": "XHIGH", "max": "XHIGH"}
_DROP_KEYS = {"title"}


def gemini_tool_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The pydantic JSON Schema without cosmetic keys."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items() if k not in _DROP_KEYS}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    out: dict[str, Any] = walk(schema)
    return out


class GeminiProvider:
    name = "gemini"

    def __init__(self, cfg: LLMConfig, api_key: str | None = None, client: Any = None) -> None:
        self.cfg = cfg
        if client is None:
            from google import genai
            from google.genai import types

            # Retry transient server errors (500/502/503/504) with backoff. Quota errors (429)
            # are not retried: they rarely clear within seconds.
            http = types.HttpOptions(
                retry_options=types.HttpRetryOptions(
                    attempts=cfg.retries + 1,
                    initial_delay=2.0,
                    max_delay=20.0,
                    http_status_codes=[500, 502, 503, 504],
                )
            )
            client = (
                genai.Client(api_key=api_key, http_options=http)
                if api_key
                else genai.Client(http_options=http)
            )
        self.client = client
        self._ids = itertools.count(1)
        # tool_use id → (function name, id Gemini gave the call or None)
        self._calls: dict[str, tuple[str, str | None]] = {}

    # ---------------------------------------------------------------------------- conversion
    def _contents(self, messages: list[dict[str, Any]]) -> list[Any]:
        from google.genai import types

        out: list[Any] = []
        for m in messages:
            content = m["content"]
            if m["role"] == "assistant":
                for block in content:
                    if isinstance(block, types.Content):
                        out.append(block)  # the model's own turn, unchanged (thought signatures)
                    elif isinstance(block, dict) and block.get("type") == "text":
                        out.append(types.Content(role="model", parts=[types.Part(text=block["text"])]))
                continue
            parts = []
            for block in content:
                if block.get("type") == "text":
                    parts.append(types.Part(text=block["text"]))
                elif block.get("type") == "tool_result":
                    name, gemini_id = self._calls.get(block["tool_use_id"], ("unknown_tool", None))
                    try:
                        payload = json.loads(block["content"])
                    except (TypeError, ValueError):
                        payload = {"text": str(block["content"])}
                    key = "error" if block.get("is_error") else "result"
                    parts.append(
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=gemini_id, name=name, response={key: payload}
                            )
                        )
                    )
            if parts:
                out.append(types.Content(role="user", parts=parts))
        return out

    def _config(self, system: str, tools: list[dict[str, Any]]) -> Any:
        from google.genai import types

        decls = [
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters_json_schema=gemini_tool_schema(t["input_schema"]),
            )
            for t in tools
        ]
        return types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=decls)] if decls else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel(_LEVEL[self.cfg.effort])),
            max_output_tokens=self.cfg.max_tokens,
            store=self.cfg.gemini_store,
        )

    # --------------------------------------------------------------------------------- call
    def chat(
        self, *, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]], model: str
    ) -> LLMResponse:
        from google.genai import errors

        try:
            resp = self.client.models.generate_content(
                model=model, contents=self._contents(messages), config=self._config(system, tools)
            )
        except errors.ClientError as e:
            msg = str(e.message or e.details or "")
            if (e.code == 400 and msg.lstrip().lower().startswith("<html")) or "<title>Error 400" in str(e):
                raise LLMUnavailable(
                    "Google refused the request as malformed (HTTP 400). This usually means the API key has "
                    "stray characters; re-save it with `coinleaf config set-api-key`"
                ) from None
            if e.code == 404:
                raise LLMUnavailable(
                    f"the model {model!r} is not available to this API key; set chat_model "
                    "under [llm] in config.toml"
                ) from None
            if e.code in (401, 403) or (e.code == 400 and "API key" in str(e.message)):
                raise LLMUnavailable("the Gemini API rejected the API key or project") from None
            if e.code == 429:
                raise LLMUnavailable(
                    "the Gemini API quota or rate limit was reached; try again later"
                ) from None
            raise LLMUnavailable(f"the Gemini API rejected the request ({e.code}: {e.message})") from None
        except errors.ServerError as e:
            if e.code == 503:
                raise LLMUnavailable(
                    "Gemini is busy right now (high demand, HTTP 503, still failing after "
                    f"{self.cfg.retries} retries); try again in a few minutes"
                ) from None
            raise LLMUnavailable(f"the Gemini API returned an error ({e.code})") from None
        except PermissionError as e:  # the network guard
            raise LLMUnavailable(str(e)) from None
        except OSError as e:
            raise LLMUnavailable(f"could not reach the Gemini API ({type(e).__name__})") from None
        except Exception as e:  # noqa: BLE001 - httpx/httpx2 transport errors have no common base here
            if type(e).__module__.split(".")[0] in ("httpx", "httpx2", "httpcore", "httpcore2"):
                raise LLMUnavailable(f"could not reach the Gemini API ({type(e).__name__})") from None
            raise
        return self._parse(resp, model)

    def _parse(self, resp: Any, model: str) -> LLMResponse:
        usage = self._usage(getattr(resp, "usage_metadata", None))
        model_used = getattr(resp, "model_version", None) or model
        candidates = getattr(resp, "candidates", None) or []
        if not candidates:
            # the prompt itself was blocked (prompt_feedback.block_reason)
            return LLMResponse("refusal", "", [], [], model_used, usage)
        cand = candidates[0]
        reason = getattr(cand.finish_reason, "value", cand.finish_reason) or "STOP"
        content = cand.content
        parts = list(getattr(content, "parts", None) or [])
        texts: list[str] = []
        calls: list[ToolCall] = []
        for p in parts:
            if getattr(p, "function_call", None):
                fc = p.function_call
                local_id = f"call_{next(self._ids)}"
                self._calls[local_id] = (fc.name, fc.id)
                calls.append(ToolCall(id=local_id, name=fc.name, input=dict(fc.args or {})))
            elif getattr(p, "text", None) and not getattr(p, "thought", False):
                texts.append(p.text)
        if str(reason) in _REFUSAL_REASONS:
            stop = "refusal"
        elif calls:
            stop = "tool_use"
        elif str(reason) == "MAX_TOKENS":
            stop = "max_tokens"
        else:
            stop = "end_turn"
        assistant = [content] if content is not None and parts else []
        return LLMResponse(stop, "".join(texts).strip(), calls, assistant, model_used, usage)

    @staticmethod
    def _usage(u: Any) -> Usage:
        if u is None:
            return Usage()
        prompt = int(getattr(u, "prompt_token_count", 0) or 0)
        cached = int(getattr(u, "cached_content_token_count", 0) or 0)
        output = int(getattr(u, "candidates_token_count", 0) or 0) + int(
            getattr(u, "thoughts_token_count", 0) or 0
        )
        # Thinking tokens are billed as output on Gemini; cached tokens are a subset of the prompt.
        return Usage(input_tokens=max(prompt - cached, 0), output_tokens=output, cache_read_tokens=cached)
