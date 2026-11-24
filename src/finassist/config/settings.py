"""Configuration (ARCH §15): TOML, validated by pydantic. Unknown or invalid keys stop
start-up with a message naming the key."""

from __future__ import annotations

import os
import tomllib
from datetime import date
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

__all__ = ["ConfigError", "Settings", "load_settings"]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeneralConfig(_Section):
    timezone: str = "Asia/Kolkata"
    base_currency: str = "INR"
    fy_start_month: int = Field(default=4, ge=1, le=12)

    @field_validator("timezone")
    @classmethod
    def _zone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown time zone {v!r}") from None
        return v


class PeriodsConfig(_Section):
    quarter_convention: Literal["fy", "calendar"] = "fy"


class PrivacyConfig(_Section):
    llm_mode: Literal["cloud", "local", "off"] = "cloud"


Provider = Literal["gemini", "anthropic"]

# Per-provider defaults (ARCH §11). Gemini is the owner's choice (2026-11-23); Anthropic
# stays available behind the same provider interface.
PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "gemini": {
        "chat_model": "gemini-3.8-flash",
        "classify_model": "gemini-3.5-flash-lite",
        "api_host": "generativelanguage.googleapis.com",
    },
    "anthropic": {
        "chat_model": "claude-opus-5-5",
        "classify_model": "claude-haiku-5-5",
        "api_host": "api.anthropic.com",
    },
}


class LLMConfig(_Section):
    provider: Provider = "gemini"
    chat_model: str = ""  # empty = the provider's default
    classify_model: str = ""
    # Reasoning depth. Gemini: thinking_level (max is sent as xhigh); Anthropic: output_config.effort.
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    max_tokens: int = Field(default=16000, ge=1024)
    max_iterations: int = Field(default=8, ge=1, le=20)
    retries: int = Field(default=3, ge=0, le=6)  # retries of transient server errors (5xx)
    api_host: str = ""  # empty = the provider's API host; the only host chat may contact
    # Gemini: the free tier lets Google use and human-review what you send. Set true only
    # once your key belongs to a billing-enabled Google Cloud project (paid tier). Until
    # then the real ledger never sends data to Gemini; the demo (synthetic data) still works.
    gemini_paid_tier: bool = False
    # Gemini: per-request logging (`store`). false asks Google not to log the request.
    gemini_store: bool = False
    # Anthropic only.
    strict_tools: bool = True
    refusal_fallbacks: bool = True

    @model_validator(mode="after")
    def _provider_defaults(self) -> LLMConfig:
        d = PROVIDER_DEFAULTS[self.provider]
        self.chat_model = self.chat_model or d["chat_model"]
        self.classify_model = self.classify_model or d["classify_model"]
        self.api_host = self.api_host or d["api_host"]
        return self


class ImportConfig(_Section):
    pass


class DemoConfig(_Section):
    today: date | None = None  # pins the clock for demos and tests


class Settings(_Section):
    general: GeneralConfig = GeneralConfig()
    periods: PeriodsConfig = PeriodsConfig()
    privacy: PrivacyConfig = PrivacyConfig()
    llm: LLMConfig = LLMConfig()
    import_: ImportConfig = Field(default=ImportConfig(), alias="import")
    demo: DemoConfig = DemoConfig()

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ConfigError(ValueError):
    pass


def _format_error(err: ValidationError, source: str) -> str:
    lines = [f"invalid configuration in {source}:"]
    for e in err.errors():
        key = ".".join(str(p) for p in e["loc"])
        msg = e["msg"]
        if e["type"] == "extra_forbidden":
            msg = "unknown key"
        lines.append(f"  {key}: {msg}")
    return "\n".join(lines)


def load_settings(path: Path | None = None) -> Settings:
    """Load config from `path` (or $FINASSIST_CONFIG). Missing file → all defaults."""
    if path is None and os.environ.get("FINASSIST_CONFIG"):
        path = Path(os.environ["FINASSIST_CONFIG"])
    data: dict[str, object] = {}
    source = "defaults"
    if path is not None and path.exists():
        source = str(path)
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"invalid TOML in {path}: {e}") from None
    try:
        settings = Settings.model_validate(data)
    except ValidationError as e:
        raise ConfigError(_format_error(e, source)) from None
    mode = os.environ.get("FINASSIST_LLM_MODE")
    if mode:
        if mode not in ("cloud", "local", "off"):
            raise ConfigError(f"FINASSIST_LLM_MODE: must be cloud, local or off, got {mode!r}")
        settings = settings.model_copy(
            update={"privacy": settings.privacy.model_copy(update={"llm_mode": mode})}
        )
    return settings
