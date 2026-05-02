from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


def find_project_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        if (parent / "AGENTS.md").exists() and (parent / ".pi").exists():
            return parent
    return Path.cwd().resolve()


def default_cache_root(project_root: Path) -> Path:
    """Return ``<project_root>/.cache/book-ingest``.

    Project-local cache, gitignored. Matches the convention used by
    ``.pytest_cache``, ``.mypy_cache``, etc., and keeps the cache
    project-scoped so multiple checkouts don't share state.
    """
    return (project_root / ".cache" / "book-ingest").resolve()


_DOTENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_dotenv(text: str) -> dict[str, str]:
    """Minimal dotenv parser. Supports KEY=value, KEY="value", KEY='value', export KEY=value, # comments."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0]
        if not line.strip():
            continue
        m = _DOTENV_LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in {'"', "'"}:
            val = val[1:-1]
        out[key] = val
    return out


def load_dotenv_into(env: dict[str, str], dotenv_path: Path) -> None:
    """Load dotenv values into ``env`` without overwriting existing keys."""
    if not dotenv_path.is_file():
        return
    parsed = parse_dotenv(dotenv_path.read_text(encoding="utf-8", errors="replace"))
    for k, v in parsed.items():
        env.setdefault(k, v)


_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off", ""}
LLM_MODES = {"no", "all", "images-only", "text-only"}


def parse_bool_env(value: str | None) -> bool | None:
    """Parse env-style boolean. Returns None when value is unset/unparseable so callers can fall through."""
    if value is None:
        return None
    s = value.strip().lower()
    if s in _TRUTHY:
        return True
    if s in _FALSY:
        return False
    return None


def parse_positive_int_env(value: str | None, *, default: int) -> int:
    """Parse a positive integer env var; fall back to default when unset/invalid."""
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except ValueError:
        return default
    return parsed if parsed > 0 else default


@dataclass(frozen=True)
class LLMConfig:
    enabled: bool
    enabled_source: str  # "cli" | "env" | "default" | legacy/implied variants
    api_key: str | None
    model: str
    base_url: str
    mode: str = "all"  # no | all | images-only | text-only
    max_concurrency: int = 2

    def redacted(self) -> dict:
        return {
            "mode": self.mode,
            "use_llm": self.enabled,
            "use_llm_source": self.enabled_source,
            "openai_model": self.model,
            "openai_base_url": self.base_url,
            "openai_api_key_present": bool(self.api_key),
            "max_concurrency": self.max_concurrency,
        }


def resolve_tristate(
    cli_value: bool | None, env_value: str | None, default: bool = False
) -> tuple[bool, str]:
    """Resolve a CLI > env > default boolean. Returns (value, source)."""
    if cli_value is not None:
        return cli_value, "cli"
    parsed = parse_bool_env(env_value)
    if parsed is not None:
        return parsed, "env"
    return default, "default"


def parse_llm_mode(value: str | None) -> str | None:
    """Parse the LLM mode enum, accepting boolean-ish aliases for env ergonomics."""
    if value is None:
        return None
    s = value.strip().lower().replace("_", "-")
    aliases = {
        "off": "no",
        "false": "no",
        "0": "no",
        "none": "no",
        "on": "all",
        "true": "all",
        "1": "all",
        "yes": "all",
        "image-only": "images-only",
        "images": "images-only",
        "image": "images-only",
        "text": "text-only",
    }
    s = aliases.get(s, s)
    if s in LLM_MODES:
        return s
    return None


def resolve_llm_mode(
    *,
    cli_llm_mode: str | None,
    cli_use_llm: bool | None,
    cli_describe_images: bool | None,
    env: dict[str, str],
) -> tuple[str, str]:
    """Resolve LLM behavior.

    New style is ``--llm`` / ``TTRPG_MARKER_LLM_MODE`` with modes:
    ``no``, ``all``, ``images-only``, ``text-only``.

    Legacy ``--use-llm`` and ``--describe-images`` are still accepted.  They
    map to the closest explicit mode while preserving CLI-over-env precedence.
    """
    parsed_cli_mode = parse_llm_mode(cli_llm_mode)
    if cli_llm_mode is not None:
        if parsed_cli_mode is None:
            raise ValueError(f"invalid llm mode: {cli_llm_mode}")
        return parsed_cli_mode, "cli"

    # Legacy CLI flags win over environment defaults.
    if cli_use_llm is not None or cli_describe_images is not None:
        if cli_use_llm is False and cli_describe_images is True:
            raise ValueError("--no-use-llm conflicts with --describe-images")
        if cli_use_llm is False:
            return "no", "cli-legacy"

        if cli_describe_images is not None:
            describe = cli_describe_images
        else:
            describe = parse_bool_env(env.get("TTRPG_MARKER_DESCRIBE_IMAGES")) or False

        if cli_use_llm is True:
            return ("all" if describe else "text-only"), "cli-legacy"
        if describe:
            return "images-only", "cli-legacy"
        # --no-describe-images alone: keep env use_llm if present, but force no image captions.
        env_use = parse_bool_env(env.get("TTRPG_MARKER_USE_LLM"))
        return ("text-only" if env_use else "no"), "cli-legacy"

    env_mode = parse_llm_mode(env.get("TTRPG_MARKER_LLM_MODE"))
    if env.get("TTRPG_MARKER_LLM_MODE") is not None:
        if env_mode is None:
            raise ValueError(f"invalid TTRPG_MARKER_LLM_MODE: {env.get('TTRPG_MARKER_LLM_MODE')}")
        return env_mode, "env"

    env_use = parse_bool_env(env.get("TTRPG_MARKER_USE_LLM"))
    env_describe = parse_bool_env(env.get("TTRPG_MARKER_DESCRIBE_IMAGES"))
    if env_use is True:
        return ("all" if env_describe else "text-only"), "env-legacy"
    if env_describe is True:
        return "images-only", "env-legacy"
    if env_use is False or env_describe is False:
        return "no", "env-legacy"
    return "no", "default"


def resolve_llm_config(
    *,
    cli_use_llm: bool | None,
    cli_model: str | None,
    cli_base_url: str | None,
    env: dict[str, str],
    llm_mode: str | None = None,
    llm_mode_source: str | None = None,
) -> LLMConfig:
    """Resolve LLM config with precedence: CLI > env > default.

    ``.env`` values are expected to have been merged into ``env`` already.
    New callers should pass ``llm_mode`` from :func:`resolve_llm_mode`.
    Legacy callers can still pass ``cli_use_llm``.
    """
    if llm_mode is not None:
        mode = parse_llm_mode(llm_mode)
        if mode is None:
            raise ValueError(f"invalid llm mode: {llm_mode}")
        enabled = mode != "no"
        enabled_source = llm_mode_source or "cli"
    else:
        enabled, enabled_source = resolve_tristate(
            cli_use_llm, env.get("TTRPG_MARKER_USE_LLM"), default=False
        )
        mode = "all" if enabled else "no"

    api_key = env.get("OPENAI_API_KEY") or None
    model = cli_model or env.get("TTRPG_MARKER_OPENAI_MODEL") or "gpt-4o-mini"
    base_url = (
        cli_base_url or env.get("TTRPG_MARKER_OPENAI_BASE_URL") or "https://api.openai.com/v1"
    )
    max_concurrency = parse_positive_int_env(
        env.get("TTRPG_MARKER_LLM_MAX_CONCURRENCY"), default=2
    )
    return LLMConfig(
        enabled=enabled,
        enabled_source=enabled_source,
        api_key=api_key,
        model=model,
        base_url=base_url,
        mode=mode,
        max_concurrency=max_concurrency,
    )


def build_env(project_root: Path) -> dict[str, str]:
    """Process env merged with project ``.env`` (process env wins)."""
    env: dict[str, str] = dict(os.environ)
    load_dotenv_into(env, project_root / ".env")
    return env
