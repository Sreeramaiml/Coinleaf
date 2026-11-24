"""Gemini provider: translation to and from google-genai types, with a fake client (no network)."""

from __future__ import annotations

from typing import Any

import pytest
from google.genai import errors, types

from finassist.agent.loop import Agent
from finassist.agent.registry import Registry
from finassist.agent.tools.base import ToolContext
from finassist.agent.tools.p0 import P0_TOOLS
from finassist.config.settings import LLMConfig, Settings
from finassist.llm.gemini_provider import GeminiProvider
from finassist.llm.provider import LLMUnavailable


class FakeModels:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, *, model: str, contents: list[Any], config: Any) -> Any:
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.models = FakeModels(responses)


def response(
    parts: list[types.Part],
    reason: str = "STOP",
    prompt: int = 1000,
    cached: int = 200,
    out: int = 50,
    thoughts: int = 30,
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=parts), finish_reason=types.FinishReason(reason)
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt,
            cached_content_token_count=cached,
            candidates_token_count=out,
            thoughts_token_count=thoughts,
        ),
        model_version="gemini-3.8-flash",
    )


def fc_part(
    name: str, args: dict[str, Any], call_id: str | None = None, sig: bytes | None = b"sig-1"
) -> types.Part:
    return types.Part(
        function_call=types.FunctionCall(id=call_id, name=name, args=args), thought_signature=sig
    )


def provider(responses: list[Any], **cfg: Any) -> tuple[GeminiProvider, FakeModels]:
    client = FakeClient(responses)
    return GeminiProvider(LLMConfig(**cfg), client=client), client.models


def test_settings_defaults_follow_provider() -> None:
    g = LLMConfig()
    assert (g.provider, g.chat_model, g.classify_model, g.api_host) == (
        "gemini",
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "generativelanguage.googleapis.com",
    )
    assert g.gemini_paid_tier is False and g.gemini_store is False
    a = LLMConfig(provider="anthropic")
    assert (a.chat_model, a.api_host) == ("claude-opus-5-5", "api.anthropic.com")
    assert LLMConfig(chat_model="gemini-4-carbon").chat_model == "gemini-4-carbon"
    assert Settings().llm.provider == "gemini"


def test_full_turn_round_trips_signatures_and_function_responses(demo_ctx: ToolContext) -> None:
    first_call = fc_part(
        "query_transactions", {"period": "October", "kinds": ["expense"], "money_out_positive": True}
    )
    thought = types.Part(text="planning…", thought=True)

    def answer() -> types.GenerateContentResponse:
        return response([types.Part(text="placeholder")])

    p, models = provider([response([thought, first_call]), answer()])
    ag = Agent(demo_ctx, p, Registry(P0_TOOLS, strict=False), model="gemini-3.8-flash")

    # make the second response quote the real figure from the tool result
    def respond_with_figure(**kw: Any) -> Any:
        contents = kw["contents"]
        fr = contents[-1].parts[0].function_response
        total = fr.response["result"]["totals"]["sum"]["display"]
        models.calls.append({"model": kw["model"], "contents": list(contents), "config": kw["config"]})
        return response([types.Part(text=f"You spent {total} in October 2026.")])

    original = models.generate_content
    n = {"i": 0}

    def dispatch(**kw: Any) -> Any:
        n["i"] += 1
        return original(**kw) if n["i"] == 1 else respond_with_figure(**kw)

    models.generate_content = dispatch  # type: ignore[method-assign]
    r = ag.turn("total spend in October")
    assert not r.withheld and r.text.startswith("You spent ₹") and r.footers

    second = models.calls[1]
    contents = second["contents"]
    # the model's own turn is sent back unchanged: same object, thought signature intact
    model_turn = contents[1]
    assert model_turn.role == "model" and model_turn.parts[1].thought_signature == b"sig-1"
    assert model_turn is models.calls[0]["contents"][0] or model_turn.parts[1] is first_call
    fr = contents[2].parts[0].function_response
    assert contents[2].role == "user" and fr.name == "query_transactions" and fr.id is None
    assert "query_id" in fr.response["result"]

    cfg = second["config"]
    assert cfg.automatic_function_calling.disable is True
    assert cfg.store is False
    assert cfg.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM
    assert "Every number you state must come from a tool result" in cfg.system_instruction
    decls = cfg.tools[0].function_declarations
    assert {d.name for d in decls} == {t.name for t in P0_TOOLS}
    assert all("title" not in str(d.parameters_json_schema) for d in decls)


def test_function_call_ids_are_echoed() -> None:
    p, models = provider(
        [response([fc_part("list_accounts", {}, call_id="call-xyz")]), response([types.Part(text="ok")])]
    )
    r1 = p.chat(
        system="s",
        tools=[],
        messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        model="m",
    )
    assert r1.stop_reason == "tool_use" and r1.tool_calls[0].name == "list_accounts"
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": r1.assistant_content},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": r1.tool_calls[0].id,
                    "content": '{"error": {"code": "x"}}',
                    "is_error": True,
                }
            ],
        },
    ]
    p.chat(system="s", tools=[], messages=msgs, model="m")
    fr = models.calls[1]["contents"][2].parts[0].function_response
    assert fr.id == "call-xyz" and fr.name == "list_accounts" and "error" in fr.response


@pytest.mark.parametrize(
    "reason,stop",
    [
        ("SAFETY", "refusal"),
        ("PROHIBITED_CONTENT", "refusal"),
        ("MAX_TOKENS", "max_tokens"),
        ("STOP", "end_turn"),
    ],
)
def test_finish_reasons(reason: str, stop: str) -> None:
    p, _ = provider([response([types.Part(text="x")], reason=reason)])
    assert p.chat(system="s", tools=[], messages=[], model="m").stop_reason == stop


def test_blocked_prompt_is_refusal_and_thoughts_are_not_text() -> None:
    p, _ = provider([types.GenerateContentResponse(candidates=[])])
    assert p.chat(system="s", tools=[], messages=[], model="m").stop_reason == "refusal"
    p, _ = provider([response([types.Part(text="secret plan", thought=True), types.Part(text="Answer.")])])
    assert p.chat(system="s", tools=[], messages=[], model="m").text == "Answer."


def test_usage_mapping() -> None:
    p, _ = provider([response([types.Part(text="x")], prompt=1000, cached=200, out=50, thoughts=30)])
    u = p.chat(system="s", tools=[], messages=[], model="m").usage
    assert (u.input_tokens, u.cache_read_tokens, u.output_tokens) == (800, 200, 80)


@pytest.mark.parametrize(
    "exc,msg",
    [
        (
            errors.ClientError(
                400,
                {
                    "error": {
                        "code": 400,
                        "message": "API key not valid. Please pass a valid API key.",
                        "status": "INVALID_ARGUMENT",
                    }
                },
            ),
            "rejected the API key",
        ),
        (
            errors.ClientError(
                403, {"error": {"code": 403, "message": "denied", "status": "PERMISSION_DENIED"}}
            ),
            "rejected the API key",
        ),
        (
            errors.ClientError(
                429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}}
            ),
            "quota",
        ),
        (
            errors.ServerError(500, {"error": {"code": 500, "message": "internal", "status": "INTERNAL"}}),
            r"error \(500\)",
        ),
        (PermissionError("network access blocked by finassist (strict mode): lookup of x"), "blocked"),
        (ConnectionError("reset"), "could not reach"),
    ],
)
def test_errors_become_llm_unavailable(exc: Exception, msg: str) -> None:
    p, _ = provider([exc])
    with pytest.raises(LLMUnavailable, match=msg):
        p.chat(system="s", tools=[], messages=[], model="m")


def test_effort_maps_to_thinking_level_and_store_flag() -> None:
    p, models = provider([response([types.Part(text="x")])], effort="max", gemini_store=True)
    p.chat(system="s", tools=[], messages=[], model="m")
    cfg = models.calls[0]["config"]
    assert cfg.thinking_config.thinking_level == types.ThinkingLevel.XHIGH and cfg.store is True


def test_empty_response_gets_one_nudge(demo_ctx: ToolContext) -> None:
    from finassist.llm.scripted_provider import ScriptedProvider

    sp = ScriptedProvider(["", "Nothing to report."])
    r = Agent(demo_ctx, sp, Registry(P0_TOOLS), model="m").turn("hello")
    assert r.text == "Nothing to report."
    assert "No answer or tool call was received" in str(sp.requests[1]["messages"][-1])


def test_real_ledger_needs_paid_tier_confirmation(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from finassist.cli.chat import make_provider
    from finassist.cli.env import Env
    from finassist.config.paths import Paths
    from finassist.core.periods import FixedClock
    from finassist.db.keys import MemoryKeyStore

    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test-key")
    paths = Paths(tmp_path, tmp_path / "logs", tmp_path / "reports")

    def env(demo: bool, paid: bool) -> Env:
        s = Settings(llm=LLMConfig(gemini_paid_tier=paid))
        return Env(
            demo, paths, paths, s, MemoryKeyStore(), FixedClock(__import__("datetime").date(2026, 11, 5))
        )

    prov, why = make_provider(env(demo=False, paid=False))
    assert prov is None and "gemini_paid_tier = true" in why and "human reviewers" in why
    prov, why = make_provider(env(demo=False, paid=True))
    assert isinstance(prov, GeminiProvider) and "paid tier confirmed" in why
    prov, why = make_provider(env(demo=True, paid=False))
    assert isinstance(prov, GeminiProvider) and "demo data only" in why
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-other")
    assert make_provider(env(demo=True, paid=False))[0] is not None


# ---------------------------------------------------------------- found while running locally


@pytest.mark.parametrize(
    "raw,clean",
    [
        ("AIzaSyExample-key_123\x1b[C", "AIzaSyExample-key_123"),  # right-arrow pressed while pasting
        ("\x1b[200~AIzaSyExample\x1b[201~", "AIzaSyExample"),  # bracketed paste markers
        ("  AIzaSyExample\n", "AIzaSyExample"),
        ("'AQ.Ab8RN6Example'", "AQ.Ab8RN6Example"),
    ],
)
def test_clean_api_key(raw: str, clean: str) -> None:
    from finassist.db.keys import clean_api_key

    assert clean_api_key(raw) == clean


@pytest.mark.parametrize("raw", ["", "\x1b[C", "two words", "key;rm -rf"])
def test_clean_api_key_rejects(raw: str) -> None:
    from finassist.db.keys import ApiKeyError, clean_api_key

    with pytest.raises(ApiKeyError):
        clean_api_key(raw)


def test_resolve_api_key_cleans_and_says_so(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from finassist.cli.env import Env, resolve_api_key
    from finassist.config.paths import Paths
    from finassist.core.periods import FixedClock
    from finassist.db.keys import MemoryKeyStore

    paths = Paths(tmp_path, tmp_path, tmp_path)
    env = Env(
        True, paths, paths, Settings(), MemoryKeyStore(), FixedClock(__import__("datetime").date(2026, 11, 5))
    )
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyExample\x1b[C")
    key, source = resolve_api_key(env)
    assert key == "AIzaSyExample" and "stray terminal characters removed" in source
    monkeypatch.setenv("GEMINI_API_KEY", "not a key")
    key, source = resolve_api_key(env)
    assert key is None and source.startswith("invalid key in environment")


@pytest.mark.parametrize(
    "exc,msg",
    [
        (errors.ClientError(400, "<html><title>Error 400 (Bad Request)!!1</title></html>"), "malformed"),
        (
            errors.ClientError(404, {"error": {"code": 404, "message": "gone", "status": "NOT_FOUND"}}),
            "not available",
        ),
        (
            errors.ServerError(
                503,
                {
                    "error": {
                        "code": 503,
                        "message": "This model is currently experiencing high demand.",
                        "status": "UNAVAILABLE",
                    }
                },
            ),
            "busy right now",
        ),
    ],
)
def test_clear_messages_for_errors_seen_live(exc: Exception, msg: str) -> None:
    p, _ = provider([exc])
    with pytest.raises(LLMUnavailable, match=msg):
        p.chat(system="s", tools=[], messages=[], model="m")


def test_client_retries_transient_server_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    import google.genai

    seen: dict[str, Any] = {}

    class Capture:
        def __init__(self, **kw: Any) -> None:
            seen.update(kw)

    monkeypatch.setattr(google.genai, "Client", Capture)
    GeminiProvider(LLMConfig(retries=3), api_key="AIzaSyExample")
    ro = seen["http_options"].retry_options
    assert (
        ro.attempts == 4
        and ro.http_status_codes == [500, 502, 503, 504]
        and seen["api_key"] == "AIzaSyExample"
    )
