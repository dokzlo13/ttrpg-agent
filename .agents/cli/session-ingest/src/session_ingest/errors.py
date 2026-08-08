"""Error taxonomy.

Every failure a verb can produce is one of these, so ``__main__`` can map it to
an exit code and a ``--json`` envelope in one place rather than each verb
inventing its own reporting.
"""

from __future__ import annotations

from typing import Any


class SessionIngestError(Exception):
    """Base class. Carries optional agent-facing repair steps.

    ``next_steps`` is the whole point: a failure an agent can fix (a missing
    manifest, an unsourced environment) should say what to run, in the same
    shape as a successful verb's ordered steps.
    """

    exit_code = 1

    def __init__(
        self,
        message: str,
        *,
        code: str = "error",
        next_steps: list[dict[str, Any]] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.next_steps = next_steps or []
        self.detail = detail or {}


class EnvironmentContractError(SessionIngestError):
    """The environment contract was not sourced, so the roots are unknown.

    Never guessed: inventing ``.cache/sessions`` from the cwd is how an
    irreplaceable recording ends up written outside the protected tree.
    """

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(
            message,
            code="environment_contract",
            detail=detail,
            next_steps=[
                {
                    "id": "use_launcher",
                    "required": True,
                    "summary": (
                        "Run the CLI through its hermetic entrypoint, which sources the "
                        "environment contract."
                    ),
                    "command": ".agents/bin/session-ingest doctor --json",
                }
            ],
        )


class DatasetAdoptError(SessionIngestError):
    """A dataset failed one of adopt's gates."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        next_steps: list[dict[str, Any]] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, next_steps=next_steps, detail=detail)


class VaultFileError(SessionIngestError):
    """``_lexicon.yaml`` / ``_speakers.yaml`` is present but unreadable."""

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="vault_file", detail=detail)


class LexiconExpansionError(SessionIngestError):
    """Morphological expansion produced an ambiguous table.

    Only raised for a genuine contradiction — one generated surface form that
    two different lexicon terms both claim — because silently picking a winner
    would substitute one character's name for another's in the transcript. The
    owner resolves it in ``_lexicon.yaml``; the tool refuses to guess.
    """

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="morph_collision", detail=detail)


class LlmError(SessionIngestError):
    """A metered call could not be made or could not be trusted.

    Lives here rather than in ``llm`` so the taxonomy is complete in one file:
    ``__main__`` maps *every* failure to an exit code and a ``--json`` envelope,
    and a reader checking what a verb can raise should not have to know that
    three of the classes hid in the OpenAI plumbing module.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "llm_error",
        detail: dict[str, Any] | None = None,
        next_steps: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message, code=code, detail=detail, next_steps=next_steps)


class SchemaViolation(LlmError):
    """The model's answer did not satisfy the schema it was given, twice."""

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="schema_violation", detail=detail)


class PromptMissing(LlmError):
    """A versioned prompt file is absent — the stage cannot run reproducibly."""

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="prompt_missing", detail=detail)


class NotImplementedStage(SessionIngestError):
    """A wave-2 stage that is registered but not built yet.

    Deliberately a clean, typed failure rather than ``NotImplementedError``: the
    CLI turns it into ``{"status": "not_implemented", "verb": …}`` and exit 2, so
    an agent walking ``next_steps`` can tell "not built" apart from "broke".
    """

    exit_code = 2

    def __init__(self, verb: str, *, summary: str = "") -> None:
        super().__init__(
            f"`session-ingest {verb}` is not implemented yet",
            code="not_implemented",
            detail={"verb": verb, "summary": summary},
        )
        self.verb = verb
        self.summary = summary
