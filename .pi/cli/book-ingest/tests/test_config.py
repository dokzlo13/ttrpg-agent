import pytest

from book_ingest.config import (
    LLMConfig,
    parse_bool_env,
    parse_dotenv,
    parse_llm_mode,
    resolve_llm_config,
    resolve_llm_mode,
    resolve_tristate,
)


def test_parse_dotenv_basic():
    text = """\
KEY=value
QUOTED="quoted value"
SINGLE='single value'
EXPORTED=exported
# comment line
TRAILING=trail # not a comment marker inside value
"""
    out = parse_dotenv(text)
    assert out["KEY"] == "value"
    assert out["QUOTED"] == "quoted value"
    assert out["SINGLE"] == "single value"
    assert out["EXPORTED"] == "exported"
    assert out["TRAILING"] == "trail"


def test_parse_dotenv_export_prefix():
    out = parse_dotenv("export FOO=bar\nexport BAZ='qux'\n")
    assert out["FOO"] == "bar"
    assert out["BAZ"] == "qux"


def test_parse_dotenv_skips_invalid_lines():
    out = parse_dotenv("not a kv line\n=novalue\nKEY=value\n")
    assert out == {"KEY": "value"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("", False),
        (None, None),
        ("maybe", None),
    ],
)
def test_parse_bool_env(value, expected):
    assert parse_bool_env(value) is expected


def test_resolve_llm_config_cli_wins_over_env_true():
    cfg = resolve_llm_config(
        cli_use_llm=False,
        cli_model="cli-model",
        cli_base_url="http://cli/v1",
        env={"OPENAI_API_KEY": "secret", "TTRPG_MARKER_USE_LLM": "true"},
    )
    assert cfg.enabled is False
    assert cfg.enabled_source == "cli"


def test_resolve_llm_config_env_when_cli_unset():
    cfg = resolve_llm_config(
        cli_use_llm=None,
        cli_model=None,
        cli_base_url=None,
        env={"TTRPG_MARKER_USE_LLM": "true", "OPENAI_API_KEY": "k"},
    )
    assert cfg.enabled is True
    assert cfg.enabled_source == "env"
    assert cfg.api_key == "k"


def test_resolve_llm_config_default_off():
    cfg = resolve_llm_config(
        cli_use_llm=None,
        cli_model=None,
        cli_base_url=None,
        env={},
    )
    assert cfg.enabled is False
    assert cfg.enabled_source == "default"
    assert cfg.mode == "no"
    assert cfg.api_key is None


def test_parse_llm_mode_aliases():
    assert parse_llm_mode("images") == "images-only"
    assert parse_llm_mode("text") == "text-only"
    assert parse_llm_mode("off") == "no"
    assert parse_llm_mode("true") == "all"
    assert parse_llm_mode("garbage") is None


def test_resolve_llm_mode_new_cli_wins():
    mode, source = resolve_llm_mode(
        cli_llm_mode="images-only",
        cli_use_llm=True,
        cli_describe_images=True,
        env={"TTRPG_MARKER_LLM_MODE": "all"},
    )
    assert (mode, source) == ("images-only", "cli")


def test_resolve_llm_mode_env_mode_wins_over_legacy_env():
    mode, source = resolve_llm_mode(
        cli_llm_mode=None,
        cli_use_llm=None,
        cli_describe_images=None,
        env={"TTRPG_MARKER_LLM_MODE": "text-only", "TTRPG_MARKER_DESCRIBE_IMAGES": "true"},
    )
    assert (mode, source) == ("text-only", "env")


def test_resolve_llm_mode_legacy_describe_only_maps_to_images_only():
    mode, source = resolve_llm_mode(
        cli_llm_mode=None,
        cli_use_llm=None,
        cli_describe_images=None,
        env={"TTRPG_MARKER_DESCRIBE_IMAGES": "true"},
    )
    assert (mode, source) == ("images-only", "env-legacy")


def test_resolve_llm_mode_legacy_use_and_describe_maps_to_all():
    mode, source = resolve_llm_mode(
        cli_llm_mode=None,
        cli_use_llm=None,
        cli_describe_images=None,
        env={"TTRPG_MARKER_USE_LLM": "true", "TTRPG_MARKER_DESCRIBE_IMAGES": "true"},
    )
    assert (mode, source) == ("all", "env-legacy")


def test_resolve_llm_config_uses_cli_model_overrides():
    cfg = resolve_llm_config(
        cli_use_llm=True,
        cli_model="cli-model",
        cli_base_url="http://cli/v1",
        env={
            "OPENAI_API_KEY": "secret",
            "TTRPG_MARKER_OPENAI_MODEL": "env-model",
            "TTRPG_MARKER_OPENAI_BASE_URL": "http://env/v1",
        },
    )
    assert cfg.model == "cli-model"
    assert cfg.base_url == "http://cli/v1"


def test_resolve_llm_config_falls_back_to_env_model_when_no_cli():
    cfg = resolve_llm_config(
        cli_use_llm=None,
        cli_model=None,
        cli_base_url=None,
        env={"TTRPG_MARKER_OPENAI_MODEL": "env-model"},
    )
    assert cfg.model == "env-model"


def test_resolve_tristate_cli_wins():
    assert resolve_tristate(True, "false", default=False) == (True, "cli")
    assert resolve_tristate(False, "true", default=True) == (False, "cli")


def test_resolve_tristate_falls_to_env():
    assert resolve_tristate(None, "yes", default=False) == (True, "env")
    assert resolve_tristate(None, "0", default=True) == (False, "env")


def test_resolve_tristate_falls_to_default():
    assert resolve_tristate(None, None, default=True) == (True, "default")
    assert resolve_tristate(None, "garbage", default=False) == (False, "default")


def test_llm_config_redacted_omits_secret():
    cfg = LLMConfig(
        enabled=True, enabled_source="cli", api_key="sekrit", model="m", base_url="https://x/v1"
    )
    redacted = cfg.redacted()
    assert redacted["openai_api_key_present"] is True
    assert redacted["use_llm_source"] == "cli"
    assert "sekrit" not in str(redacted)
