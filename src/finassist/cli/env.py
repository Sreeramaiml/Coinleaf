"""Builds what every command needs: paths, settings, keys, ledger, clock, provider."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

from finassist.config.paths import Paths, default_paths
from finassist.config.settings import Settings, load_settings
from finassist.config.system import is_private, make_private_hint, secret_store_name
from finassist.core.periods import Clock, FixedClock, SystemClock
from finassist.db.keys import API_KEYS, ApiKeyError, FileKeyStore, KeychainStore, KeyStore, clean_api_key
from finassist.db.ledger import Ledger, open_ledger
from finassist.demo.generator import DEMO_TODAY
from finassist.demo.setup import demo_keys

__all__ = ["Env", "make_env", "resolve_api_key"]


@dataclass
class Env:
    demo: bool
    paths: Paths  # the active data folder (demo folder in --demo mode)
    main_paths: Paths
    settings: Settings
    keys: KeyStore
    clock: Clock

    def ledger(self) -> Ledger:
        return open_ledger(self.paths.db_file, self.keys)


def make_env(demo: bool) -> Env:
    main = default_paths()
    # $FINASSIST_CONFIG overrides the default config file (README, config.example.toml).
    config = Path(os.environ["FINASSIST_CONFIG"]) if os.environ.get("FINASSIST_CONFIG") else main.config_file
    settings = load_settings(config)
    if demo:
        today = settings.demo.today or DEMO_TODAY
        return Env(True, main.demo, main, settings, demo_keys(main), FixedClock(today))
    clock: Clock = (
        FixedClock(settings.demo.today) if settings.demo.today else SystemClock(settings.general.timezone)
    )
    # FINASSIST_KEYSTORE=file keeps keys in owner-only files under the data folder instead of
    # the OS secret store. For tests, CI, and Linux machines without a Secret Service keyring;
    # the default is the OS secret store (NFR-4).
    keys: KeyStore = (
        FileKeyStore(main.home / "keys")
        if os.environ.get("FINASSIST_KEYSTORE") == "file"
        else KeychainStore(main.home)
    )
    return Env(False, main, main, settings, keys, clock)


# Environment variable names per provider (Gemini's SDK also reads GOOGLE_API_KEY).
ENV_KEYS = {"gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "anthropic": ("ANTHROPIC_API_KEY",)}


def _dotenv_key(path: Path, names: tuple[str, ...]) -> str | None:
    if not path.is_file():
        return None
    if not is_private(path):
        raise PermissionError(f"{path} is readable by other users; {make_private_hint(path)}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in names:
            value = value.strip().strip('"').strip("'")
            if value:
                return value
    return None


def resolve_api_key(env: Env, provider: str | None = None) -> tuple[str | None, str]:
    """(key, where it came from) for the configured provider.
    Order: environment, the OS secret store, ./.env (owner-only). Stray terminal characters (e.g. an
    arrow key pressed while pasting) are removed, and the source notes that they were."""
    provider = provider or env.settings.llm.provider
    names = ENV_KEYS[provider]
    raw: str | None = None
    source = "none"
    for n in names:
        if os.environ.get(n):
            raw, source = os.environ[n], f"environment ({n})"
            break
    if raw is None:
        # Shared API-key entry first; then the per-folder entry that versions before
        # 2026-11-23 wrote for the default data folder.
        legacy_home = Path(user_data_dir("finassist", appauthor=False))
        for store in (KeychainStore.for_api_keys(), KeychainStore(legacy_home)):
            try:
                k = store.get(API_KEYS[provider])
            except Exception:  # noqa: BLE001 - no usable keyring backend
                break
            if k:
                raw, source = k, secret_store_name()
                break
    if raw is None:
        k = _dotenv_key(Path.cwd() / ".env", names)
        if k:
            raw, source = k, ".env"
    if raw is None:
        return None, "none"
    try:
        key = clean_api_key(raw)
    except ApiKeyError as e:
        return None, f"invalid key in {source}: {e}"
    if key != raw:
        source += "; stray terminal characters removed — re-save it with `coinleaf config set-api-key`"
    return key, source
