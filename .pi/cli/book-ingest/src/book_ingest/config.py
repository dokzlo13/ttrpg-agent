from __future__ import annotations

import os
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

from dotenv import dotenv_values


def find_project_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        if (parent / "AGENTS.md").exists() and (parent / ".pi").exists():
            return parent
    return Path.cwd().resolve()


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse dotenv text using python-dotenv without mutating process env."""
    parsed = dotenv_values(stream=StringIO(text), interpolate=False)
    return {str(key): value for key, value in parsed.items() if value is not None}


def load_dotenv_into(env: dict[str, str], dotenv_path: Path) -> None:
    """Load dotenv values into ``env`` without overwriting existing keys."""
    if not dotenv_path.is_file():
        return
    parsed = dotenv_values(dotenv_path=dotenv_path, interpolate=False, encoding="utf-8")
    for k, v in parsed.items():
        if v is not None:
            env.setdefault(str(k), v)


LLM_MODES = {"no", "all", "images-only", "text-only"}


def parse_positive_int_env(value: str | None, *, default: int) -> int:
    """Parse a positive integer env var; fall back to default when unset/invalid."""
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def parse_non_negative_float_env(value: str | None, *, default: float) -> float:
    """Parse a non-negative float env var; fall back to default when unset/invalid."""
    if value is None:
        return default
    try:
        parsed = float(value.strip())
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool
    enabled_source: str  # "cli" | "env" | "default"
    api_key: str | None
    model: str
    base_url: str
    mode: str = "all"  # no | all | images-only | text-only
    max_concurrency: int = 2
    min_interval_seconds: float = 2.0
    model_source: str = "default"
    base_url_source: str = "default"
    max_concurrency_source: str = "default"
    min_interval_source: str = "default"

    def redacted(self) -> dict:
        return {
            "mode": self.mode,
            "use_llm": self.enabled,
            "use_llm_source": self.enabled_source,
            "openai_model": self.model,
            "openai_model_source": self.model_source,
            "openai_base_url": self.base_url,
            "openai_base_url_source": self.base_url_source,
            "openai_api_key_present": bool(self.api_key),
            "max_concurrency": self.max_concurrency,
            "max_concurrency_source": self.max_concurrency_source,
            "min_interval_seconds": self.min_interval_seconds,
            "min_interval_source": self.min_interval_source,
        }


def parse_llm_mode(value: str | None) -> str | None:
    """Parse the LLM mode enum."""
    if value is None:
        return None
    s = value.strip().lower().replace("_", "-")
    if s in LLM_MODES:
        return s
    return None


def resolve_llm_mode(*, cli_llm_mode: str | None, env: dict[str, str]) -> tuple[str, str]:
    """Resolve LLM behavior from ``--llm`` / ``TTRPG_MARKER_LLM_MODE`` only."""
    parsed_cli_mode = parse_llm_mode(cli_llm_mode)
    if cli_llm_mode is not None:
        if parsed_cli_mode is None:
            raise ValueError(f"invalid llm mode: {cli_llm_mode}")
        return parsed_cli_mode, "cli"

    env_mode = parse_llm_mode(env.get("TTRPG_MARKER_LLM_MODE"))
    if env.get("TTRPG_MARKER_LLM_MODE") is not None:
        if env_mode is None:
            raise ValueError(f"invalid TTRPG_MARKER_LLM_MODE: {env.get('TTRPG_MARKER_LLM_MODE')}")
        return env_mode, "env"

    return "no", "default"


def resolve_llm_config(
    *,
    cli_model: str | None,
    cli_base_url: str | None,
    env: dict[str, str],
    llm_mode: str | None = None,
    llm_mode_source: str | None = None,
) -> LLMConfig:
    """Resolve LLM config with precedence: CLI > env > default.

    ``.env`` values are expected to have been merged into ``env`` already.
    Callers should pass ``llm_mode`` from :func:`resolve_llm_mode`.
    """
    mode = parse_llm_mode(llm_mode)
    if mode is None:
        raise ValueError(f"invalid llm mode: {llm_mode}")
    enabled = mode != "no"
    enabled_source = llm_mode_source or "cli"

    api_key = env.get("OPENAI_API_KEY") or None
    if cli_model:
        model = cli_model
        model_source = "cli"
    elif env.get("TTRPG_MARKER_OPENAI_MODEL"):
        model = env["TTRPG_MARKER_OPENAI_MODEL"]
        model_source = "env"
    else:
        model = "gpt-4o-mini"
        model_source = "default"

    if cli_base_url:
        base_url = cli_base_url
        base_url_source = "cli"
    elif env.get("TTRPG_MARKER_OPENAI_BASE_URL"):
        base_url = env["TTRPG_MARKER_OPENAI_BASE_URL"]
        base_url_source = "env"
    else:
        base_url = "https://api.openai.com/v1"
        base_url_source = "default"

    max_concurrency_env = env.get("TTRPG_MARKER_LLM_MAX_CONCURRENCY")
    max_concurrency = parse_positive_int_env(max_concurrency_env, default=2)
    max_concurrency_source = "env" if max_concurrency_env else "default"
    min_interval_env = env.get("TTRPG_MARKER_LLM_MIN_INTERVAL_SECONDS")
    min_interval_seconds = parse_non_negative_float_env(min_interval_env, default=2.0)
    min_interval_source = "env" if min_interval_env else "default"
    return LLMConfig(
        enabled=enabled,
        enabled_source=enabled_source,
        api_key=api_key,
        model=model,
        base_url=base_url,
        mode=mode,
        max_concurrency=max_concurrency,
        min_interval_seconds=min_interval_seconds,
        model_source=model_source,
        base_url_source=base_url_source,
        max_concurrency_source=max_concurrency_source,
        min_interval_source=min_interval_source,
    )


def build_env(project_root: Path) -> dict[str, str]:
    """Process env merged with project ``.env`` (process env wins)."""
    env: dict[str, str] = dict(os.environ)
    load_dotenv_into(env, project_root / ".env")
    return env
