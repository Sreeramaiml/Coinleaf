"""Per-call usage log (ARCH §14.5): time, role, model, token counts, estimated cost and
redaction count. Never the payload."""

from __future__ import annotations

from sqlalchemy import Engine, func, select

from finassist.db.schema import llm_call
from finassist.llm.provider import Usage

__all__ = ["PRICES_PER_MTOK", "estimate_micro_usd", "monthly_cost", "record_call"]

# (input, output, cache read, cache write) in USD per million tokens, paid tier.
# Anthropic: cache writes estimated at 1.25× input (5-minute TTL); ARCH §11.2.
# Gemini: output includes thinking tokens; implicit caching has no write charge. Prices from
# Google's pricing page, read 2026-11-23 (ARCH §11.4). gemini-3.8-flash rises to $1.50 / $7.50
# / $0.15 from 1 Jan 2027; update this table then.
PRICES_PER_MTOK: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
    "gemini-3.8-flash": (0.75, 3.75, 0.075, 0.0),
    "gemini-3.5-flash-lite": (0.30, 2.50, 0.03, 0.0),
}


def estimate_micro_usd(model: str, u: Usage) -> int:
    p = PRICES_PER_MTOK.get(model)
    if p is None:
        return 0
    # micro-USD = tokens × USD per MTok (tokens/1e6 × price × 1e6)
    return round(
        u.input_tokens * p[0]
        + u.output_tokens * p[1]
        + u.cache_read_tokens * p[2]
        + u.cache_write_tokens * p[3]
    )


def record_call(
    engine: Engine,
    *,
    role: str,
    provider: str,
    model: str,
    usage: Usage,
    redactions: int,
    stop_reason: str | None,
    ok: bool,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            llm_call.insert().values(
                role=role,
                provider=provider,
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
                cost_micro_usd=estimate_micro_usd(model, usage),
                redactions=redactions,
                stop_reason=stop_reason,
                ok=ok,
            )
        )


def monthly_cost(engine: Engine, month: str) -> tuple[int, int]:
    """(calls, micro-USD) for a 'YYYY-MM' month."""
    with engine.connect() as conn:
        r = conn.execute(
            select(func.count(), func.coalesce(func.sum(llm_call.c.cost_micro_usd), 0)).where(
                func.substr(llm_call.c.at, 1, 7) == month
            )
        ).one()
    return int(r[0]), int(r[1])
