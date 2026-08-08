"""Configuration resolution: CLI > env > default, with the source of every key kept.

Same shape as ``book_ingest.config``: the resolved value is useless to a reader
of a provenance file without knowing *where it came from*, so every knob carries
a ``cli`` / ``env`` / ``default`` tag that both the stderr status line and the
recorded provenance print back.

``.env`` is normally already in the process environment (``.agents/env.sh``
parses it). :func:`build_env` re-reads ``$TTRPG_ROOT/.env`` as a fallback only,
with ``setdefault`` semantics — the process environment always wins, exactly as
book-ingest does it, so a launcher-set value is never overridden by a stale file.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

CLI = "cli"
ENV = "env"
DEFAULT = "default"

# --- defaults (DESIGN §3) ---------------------------------------------------
DEFAULT_WINDOW_MINUTES = 15
DEFAULT_WINDOW_OVERLAP_PCT = 10
DEFAULT_MERGE_GAP_S = 1.5
DEFAULT_GLOSSARY_BUDGET_CHARS = 450
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_OPENAI_MAX_CONCURRENCY = 4
DEFAULT_QA_MIN_WORD_P10 = 0.60
DEFAULT_QA_MAX_BLEED_RATE = 0.10
DEFAULT_QA_MAX_LEXICON_MISS_RATE = 0.35
#: How much faster lexicon-term hits may grow than the transcript itself before
#: ``qa --compare`` raises the ``term_hits_outran_words`` echo signal. Not a
#: threshold in the ``thresholds()`` sense: it crosses nothing, gates nothing, and
#: never appears in a composite key — it only decides whether a *fact* is worth
#: pointing at. Default 3.0; the run that motivated it grew term hits by 412 on
#: 130 extra words, a factor above 3 by any reading.
DEFAULT_QA_ECHO_TERM_FACTOR = 3.0

ENV_WINDOW_MINUTES = "TTRPG_SESSION_WINDOW_MINUTES"
ENV_WINDOW_OVERLAP_PCT = "TTRPG_SESSION_WINDOW_OVERLAP_PCT"
ENV_MERGE_GAP_S = "TTRPG_SESSION_MERGE_GAP_S"
ENV_GLOSSARY_BUDGET_CHARS = "TTRPG_SESSION_GLOSSARY_BUDGET_CHARS"
ENV_OPENAI_MODEL = "TTRPG_SESSION_OPENAI_MODEL"
ENV_OPENAI_MAX_CONCURRENCY = "TTRPG_SESSION_OPENAI_MAX_CONCURRENCY"
ENV_QA_MIN_WORD_P10 = "TTRPG_SESSION_QA_MIN_WORD_P10"
ENV_QA_MAX_BLEED_RATE = "TTRPG_SESSION_QA_MAX_BLEED_RATE"
ENV_QA_MAX_LEXICON_MISS_RATE = "TTRPG_SESSION_QA_MAX_LEXICON_MISS_RATE"
ENV_QA_ECHO_TERM_FACTOR = "TTRPG_SESSION_QA_ECHO_TERM_FACTOR"


def find_project_root(start: Path | None = None) -> Path:
    """Locate the repository root.

    ``.agents/`` is the canonical toolchain and ``AGENTS.md`` the contract; a
    harness adapter directory is not a sentinel because it is absent in a
    single-harness clone. Every ``.agents/bin/*`` entrypoint exports
    ``TTRPG_ROOT``, which is authoritative when no explicit ``start`` is given.
    """
    if start is None:
        env_root = os.environ.get("TTRPG_ROOT")
        if env_root:
            candidate = Path(env_root).resolve()
            if candidate.is_dir():
                return candidate
    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        if (parent / "AGENTS.md").exists() and (parent / ".agents").exists():
            return parent
    return Path.cwd().resolve()


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse dotenv text without mutating the process environment."""
    parsed = dotenv_values(stream=StringIO(text), interpolate=False)
    return {str(key): value for key, value in parsed.items() if value is not None}


def load_dotenv_into(env: dict[str, str], dotenv_path: Path) -> None:
    """Fallback-load ``.env`` values into ``env`` without overwriting existing keys."""
    if not dotenv_path.is_file():
        return
    parsed = dotenv_values(dotenv_path=dotenv_path, interpolate=False, encoding="utf-8")
    for key, value in parsed.items():
        if value is not None:
            env.setdefault(str(key), value)


def build_env(project_root: Path) -> dict[str, str]:
    """Process env merged with the project ``.env`` (process env wins)."""
    env: dict[str, str] = dict(os.environ)
    load_dotenv_into(env, project_root / ".env")
    return env


# --- tolerant scalar parsing ------------------------------------------------
# An unparseable env value falls back to the documented default and reports its
# source as `default`, so the status line never claims a value came from the
# environment when the environment's value was discarded.


def parse_positive_int(value: str | None, *, default: int) -> tuple[int, bool]:
    if value is None or not value.strip():
        return default, False
    try:
        parsed = int(value.strip())
    except ValueError:
        return default, False
    if parsed <= 0:
        return default, False
    return parsed, True


def parse_non_negative_float(value: str | None, *, default: float) -> tuple[float, bool]:
    if value is None or not value.strip():
        return default, False
    try:
        parsed = float(value.strip())
    except ValueError:
        return default, False
    if parsed < 0:
        return default, False
    return parsed, True


def parse_share(value: str | None, *, default: float) -> tuple[float, bool]:
    """A 0..1 share. Out-of-range values are discarded, not clamped."""
    parsed, ok = parse_non_negative_float(value, default=default)
    if not ok:
        return default, False
    if parsed > 1.0:
        return default, False
    return parsed, True


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """Every tunable knob, resolved, with the source of each recorded."""

    window_minutes: int
    window_overlap_pct: int
    merge_gap_s: float
    glossary_budget_chars: int
    openai_model: str
    openai_max_concurrency: int
    qa_min_word_p10: float
    qa_max_bleed_rate: float
    qa_max_lexicon_miss_rate: float
    qa_echo_term_factor: float
    api_key: str | None
    sources: Mapping[str, str] = field(default_factory=dict)

    @property
    def api_key_present(self) -> bool:
        return bool(self.api_key)

    def source(self, key: str) -> str:
        return self.sources.get(key, DEFAULT)

    def thresholds(self) -> dict[str, float]:
        """The three gates a single run's facts are measured against.

        ``qa_echo_term_factor`` is deliberately not here: it is a cross-run
        reading aid, and putting it in would both change every stored QA cache
        key and imply a fourth thing a single run can "cross".
        """
        return {
            "min_word_p10": self.qa_min_word_p10,
            "max_bleed_rate": self.qa_max_bleed_rate,
            "max_lexicon_miss_rate": self.qa_max_lexicon_miss_rate,
        }

    def knobs(self) -> dict[str, Any]:
        """The knob subset that belongs in a stage's composite cache key."""
        return {
            "window_minutes": self.window_minutes,
            "window_overlap_pct": self.window_overlap_pct,
            "merge_gap_s": self.merge_gap_s,
            "glossary_budget_chars": self.glossary_budget_chars,
        }

    def redacted(self) -> dict[str, Any]:
        """Provenance-safe view. The API key is reported as presence, never value."""
        return {
            "window_minutes": self.window_minutes,
            "window_minutes_source": self.source("window_minutes"),
            "window_overlap_pct": self.window_overlap_pct,
            "window_overlap_pct_source": self.source("window_overlap_pct"),
            "merge_gap_s": self.merge_gap_s,
            "merge_gap_s_source": self.source("merge_gap_s"),
            "glossary_budget_chars": self.glossary_budget_chars,
            "glossary_budget_chars_source": self.source("glossary_budget_chars"),
            "openai_model": self.openai_model,
            "openai_model_source": self.source("openai_model"),
            "openai_max_concurrency": self.openai_max_concurrency,
            "openai_max_concurrency_source": self.source("openai_max_concurrency"),
            "qa_min_word_p10": self.qa_min_word_p10,
            "qa_min_word_p10_source": self.source("qa_min_word_p10"),
            "qa_max_bleed_rate": self.qa_max_bleed_rate,
            "qa_max_bleed_rate_source": self.source("qa_max_bleed_rate"),
            "qa_max_lexicon_miss_rate": self.qa_max_lexicon_miss_rate,
            "qa_max_lexicon_miss_rate_source": self.source("qa_max_lexicon_miss_rate"),
            "qa_echo_term_factor": self.qa_echo_term_factor,
            "qa_echo_term_factor_source": self.source("qa_echo_term_factor"),
            "openai_api_key_present": self.api_key_present,
        }


def resolve_config(
    *,
    env: Mapping[str, str],
    cli_window_minutes: int | None = None,
    cli_window_overlap_pct: int | None = None,
    cli_merge_gap_s: float | None = None,
    cli_glossary_budget_chars: int | None = None,
    cli_openai_model: str | None = None,
    cli_openai_max_concurrency: int | None = None,
) -> SessionConfig:
    """Resolve every knob with CLI > env > default precedence."""
    sources: dict[str, str] = {}

    def pick_int(name: str, cli_value: int | None, env_key: str, default: int) -> int:
        if cli_value is not None:
            sources[name] = CLI
            return cli_value
        value, from_env = parse_positive_int(env.get(env_key), default=default)
        sources[name] = ENV if from_env else DEFAULT
        return value

    def pick_float(name: str, cli_value: float | None, env_key: str, default: float) -> float:
        if cli_value is not None:
            sources[name] = CLI
            return cli_value
        value, from_env = parse_non_negative_float(env.get(env_key), default=default)
        sources[name] = ENV if from_env else DEFAULT
        return value

    def pick_share(name: str, env_key: str, default: float) -> float:
        value, from_env = parse_share(env.get(env_key), default=default)
        sources[name] = ENV if from_env else DEFAULT
        return value

    window_minutes = pick_int(
        "window_minutes", cli_window_minutes, ENV_WINDOW_MINUTES, DEFAULT_WINDOW_MINUTES
    )
    window_overlap_pct = pick_int(
        "window_overlap_pct",
        cli_window_overlap_pct,
        ENV_WINDOW_OVERLAP_PCT,
        DEFAULT_WINDOW_OVERLAP_PCT,
    )
    merge_gap_s = pick_float("merge_gap_s", cli_merge_gap_s, ENV_MERGE_GAP_S, DEFAULT_MERGE_GAP_S)
    glossary_budget_chars = pick_int(
        "glossary_budget_chars",
        cli_glossary_budget_chars,
        ENV_GLOSSARY_BUDGET_CHARS,
        DEFAULT_GLOSSARY_BUDGET_CHARS,
    )

    if cli_openai_model:
        openai_model = cli_openai_model
        sources["openai_model"] = CLI
    elif env.get(ENV_OPENAI_MODEL, "").strip():
        openai_model = env[ENV_OPENAI_MODEL].strip()
        sources["openai_model"] = ENV
    else:
        openai_model = DEFAULT_OPENAI_MODEL
        sources["openai_model"] = DEFAULT

    openai_max_concurrency = pick_int(
        "openai_max_concurrency",
        cli_openai_max_concurrency,
        ENV_OPENAI_MAX_CONCURRENCY,
        DEFAULT_OPENAI_MAX_CONCURRENCY,
    )

    return SessionConfig(
        window_minutes=window_minutes,
        window_overlap_pct=window_overlap_pct,
        merge_gap_s=merge_gap_s,
        glossary_budget_chars=glossary_budget_chars,
        openai_model=openai_model,
        openai_max_concurrency=openai_max_concurrency,
        qa_min_word_p10=pick_share("qa_min_word_p10", ENV_QA_MIN_WORD_P10, DEFAULT_QA_MIN_WORD_P10),
        qa_max_bleed_rate=pick_share(
            "qa_max_bleed_rate", ENV_QA_MAX_BLEED_RATE, DEFAULT_QA_MAX_BLEED_RATE
        ),
        qa_max_lexicon_miss_rate=pick_share(
            "qa_max_lexicon_miss_rate",
            ENV_QA_MAX_LEXICON_MISS_RATE,
            DEFAULT_QA_MAX_LEXICON_MISS_RATE,
        ),
        # A ratio, not a share: it is legitimately above 1.0, so pick_share would
        # discard every useful value it was ever given.
        qa_echo_term_factor=pick_float(
            "qa_echo_term_factor", None, ENV_QA_ECHO_TERM_FACTOR, DEFAULT_QA_ECHO_TERM_FACTOR
        ),
        api_key=(env.get("OPENAI_API_KEY") or "").strip() or None,
        sources=sources,
    )
