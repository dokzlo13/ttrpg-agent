from book_ingest.config import (
    LLMConfig,
    parse_dotenv,
    parse_llm_mode,
    parse_non_negative_float_env,
    parse_positive_int_env,
    resolve_llm_config,
    resolve_llm_mode,
)


def test_parse_dotenv_basic():
    text = """\
KEY=value
QUOTED="quoted value"
SINGLE='single value'
EXPORTED=exported
# comment line
TRAILING=trail # comment
HASHED="value # not a comment inside quotes"
EXPANDED=${KEY}
"""
    out = parse_dotenv(text)
    assert out["KEY"] == "value"
    assert out["QUOTED"] == "quoted value"
    assert out["SINGLE"] == "single value"
    assert out["EXPORTED"] == "exported"
    assert out["TRAILING"] == "trail"
    assert out["HASHED"] == "value # not a comment inside quotes"
    assert out["EXPANDED"] == "${KEY}"


def test_parse_dotenv_export_prefix():
    out = parse_dotenv("export FOO=bar\nexport BAZ='qux'\n")
    assert out["FOO"] == "bar"
    assert out["BAZ"] == "qux"


def test_parse_dotenv_skips_invalid_lines():
    out = parse_dotenv("not a kv line\n=novalue\nKEY=value\n")
    assert out == {"KEY": "value"}


def test_resolve_llm_config_cli_mode_controls_enabled():
    cfg = resolve_llm_config(
        cli_model="cli-model",
        cli_base_url="http://cli/v1",
        env={"OPENAI_API_KEY": "secret"},
        llm_mode="no",
        llm_mode_source="cli",
    )
    assert cfg.enabled is False
    assert cfg.enabled_source == "cli"


def test_resolve_llm_config_env_mode_controls_enabled():
    cfg = resolve_llm_config(
        cli_model=None,
        cli_base_url=None,
        env={"OPENAI_API_KEY": "k"},
        llm_mode="all",
        llm_mode_source="env",
    )
    assert cfg.enabled is True
    assert cfg.enabled_source == "env"
    assert cfg.api_key == "k"


def test_resolve_llm_config_default_off():
    cfg = resolve_llm_config(
        cli_model=None,
        cli_base_url=None,
        env={},
        llm_mode="no",
        llm_mode_source="default",
    )
    assert cfg.enabled is False
    assert cfg.enabled_source == "default"
    assert cfg.mode == "no"
    assert cfg.api_key is None
    assert cfg.max_concurrency == 2
    assert cfg.min_interval_seconds == 2.0


def test_parse_positive_int_env():
    assert parse_positive_int_env("4", default=2) == 4
    assert parse_positive_int_env("0", default=2) == 2
    assert parse_positive_int_env("nope", default=2) == 2
    assert parse_positive_int_env(None, default=2) == 2


def test_parse_non_negative_float_env():
    assert parse_non_negative_float_env("1.5", default=2.0) == 1.5
    assert parse_non_negative_float_env("0", default=2.0) == 0.0
    assert parse_non_negative_float_env("-1", default=2.0) == 2.0
    assert parse_non_negative_float_env("nope", default=2.0) == 2.0
    assert parse_non_negative_float_env(None, default=2.0) == 2.0


def test_parse_llm_mode_accepts_only_current_modes():
    assert parse_llm_mode("images-only") == "images-only"
    assert parse_llm_mode("text-only") == "text-only"
    assert parse_llm_mode("no") == "no"
    assert parse_llm_mode("all") == "all"
    assert parse_llm_mode("images") is None
    assert parse_llm_mode("true") is None
    assert parse_llm_mode("garbage") is None


def test_resolve_llm_mode_cli_wins():
    mode, source = resolve_llm_mode(
        cli_llm_mode="images-only",
        env={"TTRPG_MARKER_LLM_MODE": "all"},
    )
    assert (mode, source) == ("images-only", "cli")


def test_resolve_llm_mode_env():
    mode, source = resolve_llm_mode(
        cli_llm_mode=None,
        env={"TTRPG_MARKER_LLM_MODE": "text-only"},
    )
    assert (mode, source) == ("text-only", "env")


def test_resolve_llm_config_uses_cli_model_overrides():
    cfg = resolve_llm_config(
        cli_model="cli-model",
        cli_base_url="http://cli/v1",
        env={
            "OPENAI_API_KEY": "secret",
            "TTRPG_MARKER_OPENAI_MODEL": "env-model",
            "TTRPG_MARKER_OPENAI_BASE_URL": "http://env/v1",
        },
        llm_mode="all",
    )
    assert cfg.model == "cli-model"
    assert cfg.base_url == "http://cli/v1"


def test_resolve_llm_config_falls_back_to_env_model_when_no_cli():
    cfg = resolve_llm_config(
        cli_model=None,
        cli_base_url=None,
        env={"TTRPG_MARKER_OPENAI_MODEL": "env-model"},
        llm_mode="no",
    )
    assert cfg.model == "env-model"


def test_resolve_llm_config_uses_env_max_concurrency():
    cfg = resolve_llm_config(
        cli_model=None,
        cli_base_url=None,
        env={"TTRPG_MARKER_LLM_MAX_CONCURRENCY": "4"},
        llm_mode="no",
    )
    assert cfg.max_concurrency == 4


def test_resolve_llm_config_uses_env_min_interval():
    cfg = resolve_llm_config(
        cli_model=None,
        cli_base_url=None,
        env={"TTRPG_MARKER_LLM_MIN_INTERVAL_SECONDS": "1.25"},
        llm_mode="images-only",
    )
    assert cfg.min_interval_seconds == 1.25
    assert cfg.min_interval_source == "env"


def test_llm_config_redacted_omits_secret():
    cfg = LLMConfig(
        enabled=True, enabled_source="cli", api_key="sekrit", model="m", base_url="https://x/v1"
    )
    redacted = cfg.redacted()
    assert redacted["openai_api_key_present"] is True
    assert redacted["use_llm_source"] == "cli"
    assert "sekrit" not in str(redacted)
