"""Shared OpenAI plumbing for the metered stages.

Four things live here, and each exists so the contract cannot drift between four
verb implementations:

:func:`metered_skip`
    DESIGN principle 7 — *metered stages skip cleanly (exit 0, ``status:
    skipped``) without ``OPENAI_API_KEY``*. Every metered verb calls it first.

:func:`load_prompt`
    Prompts are versioned files under ``prompts/<stage>.v<N>.<lang>.md``. The
    version string **and** the file digest go into the stage's composite key, so
    editing a prompt invalidates every artifact it produced even when the author
    forgot to bump the version.

:func:`structured_call`
    One schema-first completion. Schema-first because every metered stage in
    this pipeline returns *records*, not prose: a free-text answer that has to be
    parsed back is where evidence links get lost. A response that does not
    satisfy the schema is retried once with the validation error appended, then
    raised as a typed failure.

:func:`map_reduce`
    Bounded-parallel map over windows. A window that keeps failing is returned
    in ``failed_windows`` with its error — **never silently dropped**, because a
    missing window is a hole in the session record nobody would notice. Token
    usage is accumulated across every call so each verb can report its cost.

The JSON-schema checking here is deliberately *mechanical verification of an
LLM's proposal* (RESEARCH §5): the model decides what a turn means, this module
only checks that the answer has the shape it promised.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from .config import SessionConfig
from .errors import LlmError, PromptMissing, SchemaViolation, SessionIngestError
from .provenance import sha256_text
from .vaultfiles import Lexicon

#: Re-exported from :mod:`session_ingest.errors`, where the whole taxonomy lives.
#: Kept importable from here because every metered stage raises them through this
#: module and `from .llm import SchemaViolation` reads better at those call sites.
__all__ = [
    "LlmError",
    "MapReduceResult",
    "Prompt",
    "PromptMissing",
    "SchemaViolation",
    "StructuredResult",
    "Usage",
    "WindowOutcome",
    "client_for",
    "lexicon_reference",
    "load_prompt",
    "map_reduce",
    "metered_skip",
    "nullable",
    "object_schema",
    "prompt_filename",
    "schema_errors",
    "structured_call",
]

#: ``src/session_ingest/prompts`` — versioned prompt files, one per stage.
PROMPTS_DIR = Path(__file__).parent / "prompts"

#: Attempts per window inside :func:`map_reduce` (one call plus two retries).
WINDOW_ATTEMPTS = 3

#: Attempts inside :func:`structured_call` (one call plus one schema repair).
SCHEMA_ATTEMPTS = 2

_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_LEADING_COMMENT_RE = re.compile(r"\A\s*<!--.*?-->\s*", re.DOTALL)
_VERSION_RE = re.compile(r"^(?P<stage>[a-z][a-z0-9_-]*)/(?P<number>\d+)$")


# ---------------------------------------------------------------------- skip


def metered_skip(config: SessionConfig, verb: str) -> dict[str, Any] | None:
    """The keyless-skip envelope, or ``None`` when the stage may run.

    Exit code stays 0: an agent walking ``next_steps`` must be able to run the
    whole chain on a machine without a key and get a transcript plus a
    deterministic ``record.json`` out of it.
    """
    if config.api_key_present:
        return None
    return {
        "status": "skipped",
        "verb": verb,
        "code": "missing_api_key",
        "reason": "OPENAI_API_KEY is not configured; metered stages are skipped, not failed.",
        "metered": True,
        "next_steps": [],
    }


# -------------------------------------------------------------------- usage


@dataclass(frozen=True, slots=True)
class Usage:
    """Token accounting, summable across calls.

    Reported by every metered verb's ``--json`` payload: a stage that quietly
    spent four times what the operator expected should say so in the same object
    that says what it produced.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            calls=self.calls + other.calls,
        )

    @classmethod
    def from_response(cls, response: Any) -> Usage:
        """Read ``response.usage`` tolerantly — a provider that omits it is not an error."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return cls(calls=1)

        def field(name: str) -> int:
            value = getattr(usage, name, None)
            if value is None and isinstance(usage, Mapping):
                value = usage.get(name)
            return value if isinstance(value, int) and not isinstance(value, bool) else 0

        prompt = field("prompt_tokens")
        completion = field("completion_tokens")
        total = field("total_tokens") or (prompt + completion)
        return cls(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total, calls=1)

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "calls": self.calls,
        }


# ------------------------------------------------------------------- prompts


@dataclass(frozen=True, slots=True)
class Prompt:
    """One versioned prompt file, with the digest that keys the cache."""

    version: str
    path: Path
    text: str
    digest: str

    def placeholders(self) -> set[str]:
        """``{name}`` tokens present in the body. Used to check a render is complete."""
        return set(_PLACEHOLDER_RE.findall(self.text))

    def render(self, **values: str) -> str:
        """Substitute ``{name}`` tokens literally.

        Deliberately not ``str.format``: prompt bodies contain JSON braces, and a
        format call would either explode on them or need them doubled, which is
        exactly the kind of invisible edit that breaks a prompt months later.
        """
        rendered = self.text
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", value)
        return rendered


def lexicon_reference(lexicon: Lexicon, *, limit: int = 200) -> str:
    """The canonical-name block every Russian prompt injects as ``{lexicon_terms}``.

    Lives beside the prompt loader rather than in one stage: four prompts declare
    the same placeholder, and four renderings of the campaign's names would drift.
    """
    terms = lexicon.active_terms()[:limit]
    if not terms:
        return "(словарь кампании пуст)"
    lines = []
    for term in terms:
        display = term.display_ru or term.canonical
        kind = f" — {term.kind}" if term.kind else ""
        lines.append(f"- {term.canonical} ({display}){kind}")
    return "\n".join(lines)


def prompt_filename(version: str, *, lang: str = "ru") -> str:
    """``"segment/1"`` -> ``"segment.v1.ru.md"``."""
    match = _VERSION_RE.match(version)
    if match is None:
        raise PromptMissing(
            f"invalid prompt version {version!r}: expected `<stage>/<number>`, e.g. `extract/1`",
            detail={"version": version},
        )
    return f"{match.group('stage')}.v{match.group('number')}.{lang}.md"


def load_prompt(version: str, *, lang: str = "ru", directory: Path | None = None) -> Prompt:
    """Read a versioned prompt file, stripping its leading placeholder comment."""
    root = directory if directory is not None else PROMPTS_DIR
    path = root / prompt_filename(version, lang=lang)
    if not path.is_file():
        raise PromptMissing(
            f"prompt file {path} is missing; `{version}` cannot be run reproducibly",
            detail={"version": version, "path": str(path)},
        )
    raw = path.read_text(encoding="utf-8")
    body = _LEADING_COMMENT_RE.sub("", raw).strip()
    return Prompt(version=version, path=path, text=body, digest=sha256_text(raw))


# -------------------------------------------------------------- json schemas


def object_schema(
    properties: Mapping[str, Any], *, required: Sequence[str] | None = None
) -> dict[str, Any]:
    """An object schema shaped for OpenAI ``strict`` structured outputs.

    Strict mode demands ``additionalProperties: false`` and *every* property in
    ``required`` — optionality is expressed as a nullable type, not as an absent
    key. Building the schema through one helper is what keeps that true across
    four stages.
    """
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required) if required is not None else list(properties),
        "additionalProperties": False,
    }


def nullable(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Make a leaf schema accept ``null`` as well as its declared type."""
    payload = dict(schema)
    declared = payload.get("type")
    if isinstance(declared, str):
        payload["type"] = [declared, "null"]
    elif isinstance(declared, list) and "null" not in declared:
        payload["type"] = [*declared, "null"]
    return payload


_TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


def schema_errors(schema: Mapping[str, Any], value: Any, *, where: str = "$") -> list[str]:
    """Check ``value`` against the subset of JSON Schema this module emits.

    Extra keys are tolerated on purpose: an unexpected field is harmless (the
    caller reads what it asked for), while a spurious retry costs money.
    """
    problems: list[str] = []
    declared = schema.get("type")
    types = [declared] if isinstance(declared, str) else list(declared or [])
    if types and not any(_TYPE_CHECKS.get(name, lambda _v: True)(value) for name in types):
        problems.append(f"{where}: expected {'|'.join(types)}, got {type(value).__name__}")
        return problems

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        problems.append(f"{where}: {value!r} is not one of {enum}")

    if isinstance(value, dict):
        properties = schema.get("properties")
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    problems.append(f"{where}.{key}: missing")
        if isinstance(properties, Mapping):
            for key, sub in properties.items():
                if key in value and isinstance(sub, Mapping):
                    problems.extend(schema_errors(sub, value[key], where=f"{where}.{key}"))

    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                problems.extend(schema_errors(items, item, where=f"{where}[{index}]"))

    return problems


# --------------------------------------------------------------------- calls


def client_for(config: SessionConfig) -> Any:
    """Build the OpenAI client this run's calls go through.

    Synchronous on purpose: :func:`map_reduce` bounds parallelism with a thread
    pool, so one blocking client is simpler than an event loop threaded through
    four verbs. Never construct one without a key — call :func:`metered_skip`
    first; the raise here is a programming-error backstop, not a user path.
    """
    if not config.api_key:
        raise LlmError(
            "OPENAI_API_KEY is not configured; metered stages must call metered_skip() "
            "before building a client.",
            code="missing_api_key",
        )
    from openai import OpenAI

    base_url = (os.environ.get("OPENAI_BASE_URL") or "").strip() or None
    return OpenAI(api_key=config.api_key, base_url=base_url)


class StructuredResult(NamedTuple):
    """``data, usage = structured_call(...)`` — the parsed object and what it cost."""

    data: dict[str, Any]
    usage: Usage
    attempts: int = 1


def _message_content(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise SchemaViolation("the model returned no choices", detail={"choices": 0})
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else ""


def structured_call(
    *,
    config: SessionConfig,
    prompt_version: str,
    system_prompt: str,
    user_content: str,
    schema: Mapping[str, Any],
    client: Any | None = None,
    schema_name: str = "result",
    max_attempts: int = SCHEMA_ATTEMPTS,
) -> StructuredResult:
    """One schema-first completion, with a single self-repair retry.

    The repair turn appends the validation error to the conversation rather than
    starting over: the model that produced a nearly-right object usually fixes
    the one field it got wrong, and a blank retry throws that away.
    """
    active = client if client is not None else client_for(config)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    usage = Usage()
    last_problems: list[str] = []

    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            response = active.chat.completions.create(
                model=config.openai_model,
                messages=messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": dict(schema),
                    },
                },
            )
        except SessionIngestError:
            raise
        except Exception as exc:  # transport / provider failure, not a schema problem
            raise LlmError(
                f"{prompt_version}: the OpenAI call failed ({type(exc).__name__}: {exc})",
                code="openai_call_failed",
                detail={"prompt_version": prompt_version, "error": type(exc).__name__},
            ) from exc

        usage = usage + Usage.from_response(response)
        content = _message_content(response)
        try:
            parsed = json.loads(content) if content.strip() else None
        except json.JSONDecodeError as exc:
            parsed = None
            last_problems = [f"$: response is not valid JSON ({exc})"]
        else:
            last_problems = schema_errors(schema, parsed)

        if isinstance(parsed, dict) and not last_problems:
            return StructuredResult(data=parsed, usage=usage, attempts=attempt)

        if not last_problems:
            last_problems = ["$: expected a JSON object at the top level"]
        messages = [
            *messages,
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": (
                    "Предыдущий ответ не прошёл проверку схемы:\n- "
                    + "\n- ".join(last_problems[:20])
                    + "\nВерни ТОЛЬКО валидный JSON, точно соответствующий схеме."
                ),
            },
        ]

    raise SchemaViolation(
        f"{prompt_version}: the model's answer did not satisfy the schema after "
        f"{max(1, max_attempts)} attempts",
        detail={"prompt_version": prompt_version, "problems": last_problems[:20]},
    )


# ----------------------------------------------------------------- map/reduce


@dataclass(frozen=True, slots=True)
class WindowOutcome:
    """What happened to exactly one window. ``data is None`` means it failed."""

    index: int
    data: dict[str, Any] | None
    error: str | None
    error_code: str | None
    attempts: int
    usage: Usage

    @property
    def ok(self) -> bool:
        return self.data is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.index,
            "error": self.error,
            "code": self.error_code,
            "attempts": self.attempts,
            "usage": self.usage.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class MapReduceResult:
    """Per-window outcomes in input order, plus the run's total token usage."""

    outcomes: tuple[WindowOutcome, ...]
    usage: Usage

    @property
    def windows(self) -> int:
        return len(self.outcomes)

    def succeeded(self) -> list[tuple[int, dict[str, Any]]]:
        """``(window_index, payload)`` for every window that produced an answer."""
        return [(o.index, o.data) for o in self.outcomes if o.data is not None]

    def failed_windows(self) -> list[dict[str, Any]]:
        """Reported, never dropped: a hole in the extraction must be visible."""
        return [o.to_dict() for o in self.outcomes if o.data is None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "windows": self.windows,
            "succeeded": len(self.succeeded()),
            "failed_windows": self.failed_windows(),
            "usage": self.usage.to_dict(),
        }


def map_reduce(
    *,
    config: SessionConfig,
    prompt_version: str,
    windows: Sequence[Any],
    system_prompt: str | Callable[[Any], str],
    schema: Mapping[str, Any],
    render: Callable[[Any], str],
    client: Any | None = None,
    schema_name: str = "result",
    max_attempts: int = WINDOW_ATTEMPTS,
) -> MapReduceResult:
    """Map ``render(window)`` over ``windows`` with bounded concurrency.

    ``system_prompt`` may be a callable when the prompt carries per-window
    context (window number, time span) — stages whose prompt file declares those
    placeholders pass a closure rather than pre-rendering one shared string.

    Concurrency is ``TTRPG_SESSION_OPENAI_MAX_CONCURRENCY``. Each window gets up
    to ``max_attempts`` :func:`structured_call` invocations (each of which may
    itself repair one schema violation); a window that still fails is returned
    as a failed outcome carrying its error. Results are ordered by window index,
    never by completion order, so two runs reduce identically.
    """
    if not windows:
        return MapReduceResult(outcomes=(), usage=Usage())

    active = client if client is not None else client_for(config)

    def system_for(window: Any) -> str:
        return system_prompt(window) if callable(system_prompt) else system_prompt

    def one(index: int, window: Any) -> WindowOutcome:
        usage = Usage()
        error: str | None = None
        code: str | None = None
        attempt = 0
        for attempt in range(1, max(1, max_attempts) + 1):
            try:
                result = structured_call(
                    config=config,
                    prompt_version=prompt_version,
                    system_prompt=system_for(window),
                    user_content=render(window),
                    schema=schema,
                    client=active,
                    schema_name=schema_name,
                )
            except LlmError as exc:
                error, code = exc.message, exc.code
                continue
            return WindowOutcome(
                index=index,
                data=result.data,
                error=None,
                error_code=None,
                attempts=attempt,
                usage=usage + result.usage,
            )
        return WindowOutcome(
            index=index,
            data=None,
            error=error or "unknown failure",
            error_code=code or "llm_error",
            attempts=attempt,
            usage=usage,
        )

    workers = max(1, min(config.openai_max_concurrency, len(windows)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(lambda pair: one(*pair), list(enumerate(windows))))

    outcomes.sort(key=lambda outcome: outcome.index)
    total = Usage()
    for outcome in outcomes:
        total = total + outcome.usage
    return MapReduceResult(outcomes=tuple(outcomes), usage=total)
