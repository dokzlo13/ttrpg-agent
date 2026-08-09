"""Where everything lives — resolved from the environment contract, never guessed.

The four roots come from ``.agents/env.sh``:

======================================  =====================================
``TTRPG_SESSIONS_DIR``                  ``.cache/sessions``
``TTRPG_SESSION_DATASETS_DIR``          ``.cache/sessions/datasets`` (== ``CRAIG_STT_WORK_DIR``)
``TTRPG_TRANSCRIPTS_DIR``               ``vault/transcripts``
``TTRPG_NOTES_DIR``                     ``vault/notes`` (layer 2; read-only to this CLI)
======================================  =====================================

An unset root is a hard failure with the launcher named, not a fallback: a
derived path invented from the cwd would put an irreplaceable recording outside
the tree the cleanup skill knows to refuse.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .errors import EnvironmentContractError, SessionIngestError

#: ``YYYY-MM-DD`` plus an optional same-day suffix (``2026-08-08-b``).
SESSION_ID_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})(?:-(?P<suffix>[a-z0-9]+))?$")

SESSION_JSON = "session.json"
PROVENANCE_JSON = "provenance.json"


def parse_session_id(session_id: str) -> tuple[str, str | None]:
    """Split a session id into its date part and optional same-day suffix."""
    match = SESSION_ID_RE.match(session_id)
    if match is None:
        raise SessionIngestError(
            f"invalid session id {session_id!r}: expected YYYY-MM-DD with an optional "
            f"same-day suffix, e.g. 2026-08-08 or 2026-08-08-b",
            code="invalid_session_id",
        )
    return match.group("date"), match.group("suffix")


def session_date_of(session_id: str) -> str:
    return parse_session_id(session_id)[0]


def _required_root(env: dict[str, str], key: str) -> Path:
    raw = env.get(key)
    if not raw or not raw.strip():
        raise EnvironmentContractError(
            f"{key} is not set. The environment contract was not sourced — run the CLI "
            f"through .agents/bin/session-ingest (which sources .agents/env.sh) rather "
            f"than a bare `uv run`.",
            detail={"missing": key},
        )
    return Path(raw.strip()).expanduser()


@dataclass(frozen=True, slots=True)
class Roots:
    """The four contract roots plus the paths derived directly from them."""

    sessions: Path
    datasets: Path
    transcripts: Path
    notes: Path

    @property
    def scratch(self) -> Path:
        """A/B re-transcription work dirs. Disposable by design."""
        return self.sessions / "scratch"

    @property
    def speakers_file(self) -> Path:
        return self.transcripts / "_speakers.yaml"

    @property
    def lexicon_file(self) -> Path:
        return self.transcripts / "_lexicon.yaml"

    # -- layer 2 (the tracker). The CLI only ever *reads* these; the agent writes.

    @property
    def chronicles_dir(self) -> Path:
        """``vault/notes/sessions/`` — the append-only session chronicle."""
        return self.notes / "sessions"

    @property
    def state_dir(self) -> Path:
        """``vault/notes/state/`` — regenerated projections."""
        return self.notes / "state"

    @property
    def inbox_dir(self) -> Path:
        """``vault/notes/inbox/`` — pending owner proposals; empty means caught up."""
        return self.notes / "inbox"

    @property
    def entity_registry_file(self) -> Path:
        """The canonical-name table that maps ``record.json`` names onto slugs."""
        return self.state_dir / "entity-registry.md"

    def session(self, session_id: str) -> SessionTree:
        parse_session_id(session_id)
        return SessionTree(root=self.sessions / session_id, id=session_id, roots=self)

    def known_session_ids(self) -> list[str]:
        """Session directories that carry a ``session.json``, oldest id first."""
        if not self.sessions.is_dir():
            return []
        found = [
            child.name
            for child in self.sessions.iterdir()
            if child.is_dir()
            and SESSION_ID_RE.match(child.name)
            and (child / SESSION_JSON).is_file()
        ]
        return sorted(found)

    def to_dict(self) -> dict[str, str]:
        return {
            "sessions": str(self.sessions),
            "datasets": str(self.datasets),
            "transcripts": str(self.transcripts),
            "notes": str(self.notes),
            "scratch": str(self.scratch),
        }


def resolve_roots(env: dict[str, str] | None = None) -> Roots:
    """Read the four roots out of the environment; fail loudly when unsourced."""
    resolved = dict(os.environ) if env is None else env
    return Roots(
        sessions=_required_root(resolved, "TTRPG_SESSIONS_DIR"),
        datasets=_required_root(resolved, "TTRPG_SESSION_DATASETS_DIR"),
        transcripts=_required_root(resolved, "TTRPG_TRANSCRIPTS_DIR"),
        notes=_required_root(resolved, "TTRPG_NOTES_DIR"),
    )


@dataclass(frozen=True, slots=True)
class SessionTree:
    """``.cache/sessions/<session-id>/`` — every derived artifact for one session.

    Layout is DESIGN §1. Nothing here is created on access; verbs create only
    what they are about to write.
    """

    root: Path
    id: str
    roots: Roots

    # ------------------------------------------------------------- session-wide

    @property
    def session_json(self) -> Path:
        return self.root / SESSION_JSON

    @property
    def provenance_json(self) -> Path:
        return self.root / PROVENANCE_JSON

    @property
    def inputs_dir(self) -> Path:
        """Snapshots of the biasing/identity inputs a run was produced with."""
        return self.root / "inputs"

    @property
    def hotwords_file(self) -> Path:
        return self.inputs_dir / "hotwords.txt"

    @property
    def initial_prompt_file(self) -> Path:
        return self.inputs_dir / "initial_prompt.txt"

    @property
    def speakers_snapshot(self) -> Path:
        return self.inputs_dir / "speakers.yaml"

    @property
    def lexicon_snapshot(self) -> Path:
        return self.inputs_dir / "lexicon.yaml"

    # ------------------------------------------------------------------- runs

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    def run_dir(self, run: int) -> Path:
        return self.runs_dir / str(run)

    def run_qa_json(self, run: int) -> Path:
        return self.run_dir(run) / "qa.json"

    @property
    def qa_json(self) -> Path:
        """Copy of the active run's QA report, for the un-numbered read path."""
        return self.root / "qa.json"

    def known_runs(self) -> list[int]:
        if not self.runs_dir.is_dir():
            return []
        return sorted(
            int(child.name)
            for child in self.runs_dir.iterdir()
            if child.is_dir() and child.name.isdigit()
        )

    # -------------------------------------------------------------- artifacts

    @property
    def turns_class_jsonl(self) -> Path:
        return self.root / "turns.class.jsonl"

    @property
    def anchors_json(self) -> Path:
        """The ID bridge: ``segment_i -> {turn_id, chunk, t0}``."""
        return self.root / "anchors.json"

    @property
    def extraction_json(self) -> Path:
        return self.root / "extraction.json"

    @property
    def record_json(self) -> Path:
        return self.root / "record.json"

    @property
    def recap_draft(self) -> Path:
        return self.root / "recap.draft.md"

    @property
    def index_sqlite(self) -> Path:
        return self.root / "index.sqlite"

    # ------------------------------------------------------------ vault-facing

    @property
    def transcript_dir(self) -> Path:
        return self.roots.transcripts / self.id

    @property
    def transcript_overview(self) -> Path:
        return self.transcript_dir / f"__{self.id}.md"

    @property
    def transcript_root_rel(self) -> str:
        """The ``transcript_root`` string recorded in ``record.json``."""
        return f"vault/transcripts/{self.id}"

    def chronicle_candidates(self) -> list[Path]:
        """Chronicle notes in ``vault/notes/sessions/`` that mention this session id.

        ``adopt --promote`` refuses when one exists: a re-transcription renumbers
        every segment and turn ID, so approved evidence links would silently
        start pointing at the wrong words. ``prune`` refuses for the mirror-image
        reason — the audio is regenerable only while the recording still exists.

        The directory comes from ``TTRPG_NOTES_DIR`` (the environment contract),
        not from the cwd: a chronicle the tool failed to find would read as "no
        chronicle written", which is the answer that deletes things.

        A same-day suffix makes the plain substring match ambiguous: the id
        ``2026-08-08`` occurs inside a filename for ``2026-08-08-b`` too. Left
        alone that lets a sibling session's chronicle unblock ``prune`` for a
        session nobody wrote up. So any candidate that also carries a *longer,
        actually adopted* session id is dropped — an exact check against real
        sessions on disk, never a guess about which slug is a suffix.
        """
        chronicles = self.roots.chronicles_dir
        if not chronicles.is_dir():
            return []
        siblings = [
            other
            for other in self.roots.known_session_ids()
            if other != self.id and other.startswith(f"{self.id}-")
        ]
        found = []
        for path in sorted(chronicles.glob(f"*{self.id}*.md")):
            if not path.is_file():
                continue
            if any(sibling in path.name for sibling in siblings):
                continue
            found.append(path)
        return found

    def to_dict(self) -> dict[str, str]:
        return {
            "session_dir": str(self.root),
            "session_json": str(self.session_json),
            "provenance_json": str(self.provenance_json),
            "inputs_dir": str(self.inputs_dir),
            "runs_dir": str(self.runs_dir),
            "anchors_json": str(self.anchors_json),
            "record_json": str(self.record_json),
            "recap_draft": str(self.recap_draft),
            "transcript_dir": str(self.transcript_dir),
        }
