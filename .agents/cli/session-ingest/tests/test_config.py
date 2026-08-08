"""Config resolution: CLI > env > default, and the source tag that proves it."""

from __future__ import annotations

from pathlib import Path

from session_ingest.config import (
    DEFAULT_GLOSSARY_BUDGET_CHARS,
    DEFAULT_MERGE_GAP_S,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_QA_ECHO_TERM_FACTOR,
    DEFAULT_QA_MAX_BLEED_RATE,
    DEFAULT_WINDOW_MINUTES,
    build_env,
    find_project_root,
    parse_dotenv,
    resolve_config,
)


def test_defaults_and_their_sources() -> None:
    config = resolve_config(env={})
    assert config.window_minutes == DEFAULT_WINDOW_MINUTES
    assert config.merge_gap_s == DEFAULT_MERGE_GAP_S
    assert config.glossary_budget_chars == DEFAULT_GLOSSARY_BUDGET_CHARS
    assert config.openai_model == DEFAULT_OPENAI_MODEL
    assert config.qa_max_bleed_rate == DEFAULT_QA_MAX_BLEED_RATE
    assert config.api_key is None and config.api_key_present is False
    for key in ("window_minutes", "merge_gap_s", "openai_model", "qa_max_bleed_rate"):
        assert config.source(key) == "default"


def test_env_wins_over_default() -> None:
    config = resolve_config(
        env={
            "TTRPG_SESSION_WINDOW_MINUTES": "20",
            "TTRPG_SESSION_MERGE_GAP_S": "2.5",
            "TTRPG_SESSION_OPENAI_MODEL": "gpt-test",
            "TTRPG_SESSION_QA_MIN_WORD_P10": "0.75",
            "OPENAI_API_KEY": "sk-secret",
        }
    )
    assert (config.window_minutes, config.source("window_minutes")) == (20, "env")
    assert (config.merge_gap_s, config.source("merge_gap_s")) == (2.5, "env")
    assert (config.openai_model, config.source("openai_model")) == ("gpt-test", "env")
    assert (config.qa_min_word_p10, config.source("qa_min_word_p10")) == (0.75, "env")
    assert config.api_key_present is True


def test_cli_wins_over_env() -> None:
    config = resolve_config(
        env={"TTRPG_SESSION_WINDOW_MINUTES": "20", "TTRPG_SESSION_OPENAI_MODEL": "gpt-env"},
        cli_window_minutes=5,
        cli_openai_model="gpt-cli",
    )
    assert (config.window_minutes, config.source("window_minutes")) == (5, "cli")
    assert (config.openai_model, config.source("openai_model")) == ("gpt-cli", "cli")


def test_unparseable_env_falls_back_and_says_default() -> None:
    """A discarded env value must not be reported as the source of the result."""
    config = resolve_config(
        env={
            "TTRPG_SESSION_WINDOW_MINUTES": "not-a-number",
            "TTRPG_SESSION_MERGE_GAP_S": "-1",
            "TTRPG_SESSION_QA_MAX_BLEED_RATE": "1.5",
        }
    )
    assert config.window_minutes == DEFAULT_WINDOW_MINUTES
    assert config.source("window_minutes") == "default"
    assert config.merge_gap_s == DEFAULT_MERGE_GAP_S
    assert config.source("merge_gap_s") == "default"
    assert config.qa_max_bleed_rate == DEFAULT_QA_MAX_BLEED_RATE
    assert config.source("qa_max_bleed_rate") == "default", "a share above 1.0 is not a share"


def test_redacted_reports_presence_not_value() -> None:
    payload = resolve_config(env={"OPENAI_API_KEY": "sk-secret"}).redacted()
    assert payload["openai_api_key_present"] is True
    assert "sk-secret" not in repr(payload)
    assert payload["window_minutes_source"] == "default"


def test_knobs_and_thresholds_are_the_cache_key_material() -> None:
    config = resolve_config(env={})
    assert set(config.knobs()) == {
        "window_minutes",
        "window_overlap_pct",
        "merge_gap_s",
        "glossary_budget_chars",
    }
    assert set(config.thresholds()) == {
        "min_word_p10",
        "max_bleed_rate",
        "max_lexicon_miss_rate",
    }, "the echo factor is a cross-run reading aid, not a per-run gate"


def test_the_echo_term_factor_is_a_ratio_not_a_share() -> None:
    """It is legitimately above 1.0, so share parsing would discard every real value."""
    config = resolve_config(env={})
    assert config.qa_echo_term_factor == DEFAULT_QA_ECHO_TERM_FACTOR
    assert config.source("qa_echo_term_factor") == "default"

    tuned = resolve_config(env={"TTRPG_SESSION_QA_ECHO_TERM_FACTOR": "5"})
    assert (tuned.qa_echo_term_factor, tuned.source("qa_echo_term_factor")) == (5.0, "env")

    broken = resolve_config(env={"TTRPG_SESSION_QA_ECHO_TERM_FACTOR": "-2"})
    assert broken.qa_echo_term_factor == DEFAULT_QA_ECHO_TERM_FACTOR
    assert broken.source("qa_echo_term_factor") == "default"


def test_dotenv_is_a_fallback_never_an_override(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "TTRPG_SESSION_WINDOW_MINUTES=99\nTTRPG_SESSION_OPENAI_MODEL=from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TTRPG_SESSION_WINDOW_MINUTES", "7")
    monkeypatch.delenv("TTRPG_SESSION_OPENAI_MODEL", raising=False)
    env = build_env(tmp_path)
    assert env["TTRPG_SESSION_WINDOW_MINUTES"] == "7", "the process environment wins"
    assert env["TTRPG_SESSION_OPENAI_MODEL"] == "from-dotenv"


def test_parse_dotenv_does_not_touch_the_process_env(monkeypatch) -> None:
    monkeypatch.delenv("SOME_UNUSED_KEY", raising=False)
    parsed = parse_dotenv("SOME_UNUSED_KEY=value\n")
    assert parsed == {"SOME_UNUSED_KEY": "value"}
    import os

    assert "SOME_UNUSED_KEY" not in os.environ


def test_project_root_honours_ttrpg_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TTRPG_ROOT", str(tmp_path))
    assert find_project_root() == tmp_path.resolve()
