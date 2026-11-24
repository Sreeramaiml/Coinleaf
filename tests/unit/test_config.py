from pathlib import Path

import pytest

from finassist.config.settings import ConfigError, load_settings


def test_defaults_without_file(tmp_path: Path) -> None:
    s = load_settings(tmp_path / "missing.toml")
    assert s.general.timezone == "Asia/Kolkata"
    assert s.periods.quarter_convention == "fy"
    assert s.privacy.llm_mode == "cloud"
    assert s.llm.provider == "gemini" and s.llm.chat_model == "gemini-3.8-flash"


@pytest.mark.parametrize(
    "toml,key",
    [
        ('[general]\ntimezone = "Mars/Olympus"\n', "general.timezone"),
        ("[general]\nfy_start_month = 13\n", "general.fy_start_month"),
        ('[privacy]\nllm_mode = "maybe"\n', "privacy.llm_mode"),
        ('[llm]\neffort = "turbo"\n', "llm.effort"),
        ("[llm]\nmax_iterations = 0\n", "llm.max_iterations"),
        ("[nonsense]\nx = 1\n", "nonsense"),
        ("[general]\nbogus = 1\n", "general.bogus"),
    ],
)
def test_invalid_values_name_the_key(tmp_path: Path, toml: str, key: str) -> None:
    p = tmp_path / "config.toml"
    p.write_text(toml)
    with pytest.raises(ConfigError) as e:
        load_settings(p)
    assert key in str(e.value)


def test_env_override_of_llm_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINASSIST_LLM_MODE", "off")
    assert load_settings(tmp_path / "none.toml").privacy.llm_mode == "off"
    monkeypatch.setenv("FINASSIST_LLM_MODE", "bad")
    with pytest.raises(ConfigError, match="FINASSIST_LLM_MODE"):
        load_settings(tmp_path / "none.toml")


def test_example_config_is_valid() -> None:
    example = Path(__file__).parents[2] / "config.example.toml"
    s = load_settings(example)
    assert s.general.base_currency == "INR"


def test_finassist_config_env_overrides_default_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from finassist.cli.env import make_env

    cfg = tmp_path / "alt.toml"
    cfg.write_text('[llm]\nchat_model = "gemini-3.5-flash-lite"\n')
    monkeypatch.setenv("FINASSIST_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FINASSIST_CONFIG", str(cfg))
    assert make_env(demo=True).settings.llm.chat_model == "gemini-3.5-flash-lite"
