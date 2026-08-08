"""``grep`` — exact slicing over the rendered transcript index.

Contract (DESIGN §4):

* Query ``index.sqlite`` (written by ``render``), never the dataset and never by
  walking the Markdown. The index is disposable and rebuilt by ``render``; this
  verb only reads it.
* Filters compose: ``--speaker``, ``--from``/``--to`` (``hh:mm[:ss]`` or
  seconds), ``--regex``, ``--context N`` surrounding segments, one session or
  ``--all``.
* Every hit carries the turn id and chunk so the caller can cite it — a grep hit
  that cannot be linked is not usable as evidence, so each row ships the same
  :class:`~session_ingest.models.Evidence` shape ``record.json`` uses.
* ``--regex`` runs against the *rendered* text, which is what the chunk shows. A
  row whose lexicon substitution changed it also carries ``text_raw`` (verbatim,
  as transcribed) and ``substituted: true``, so "is this what they actually
  said?" is answerable without opening the dataset.

This exists so an agent never ``cat``s a transcript chunk: bulk table speech in
context is exactly what the design keeps out of prep retrieval.

Two implementation notes.

**Speaker matching is an explicit lookup, never a similarity.** The value is
compared, case-folded and exact, against the rendered display name, the Discord
username, the raw ``user_id``, and — through ``_speakers.yaml`` — the character
and player names that map to a ``user_id``. Nothing here guesses that "Морви"
means "Морвика".

**Case folding happens in Python, not SQL.** SQLite's ``lower()`` is ASCII-only,
so ``LOWER('Морвика')`` is a no-op and a Cyrillic speaker filter would silently
never match. ``CASEFOLD`` and ``REGEXP`` are registered as Python functions on
the read connection instead.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import SessionConfig
from .errors import SessionIngestError
from .models import Evidence
from .nextsteps import CLI, step
from .paths import SESSION_ID_RE, Roots, SessionTree
from .vaultfiles import Speakers, load_speakers

#: Columns every row is selected with, in order.
_COLUMNS = "i, t0, t1, track, user_id, username, speaker, text, text_raw, turn_id, chunk"


def hhmmss(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def parse_time(value: str | None, *, flag: str) -> float | None:
    """``hh:mm``, ``hh:mm:ss`` or plain seconds. Two colon-parts mean hours:minutes."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if ":" in text:
        parts = text.split(":")
        if len(parts) > 3:
            raise SessionIngestError(
                f"{flag} {value!r} is not a time: expected hh:mm, hh:mm:ss or seconds",
                code="invalid_time",
                detail={"flag": flag, "value": value},
            )
        try:
            numbers = [float(part) for part in parts]
        except ValueError as exc:
            raise SessionIngestError(
                f"{flag} {value!r} is not a time: expected hh:mm, hh:mm:ss or seconds",
                code="invalid_time",
                detail={"flag": flag, "value": value},
            ) from exc
        while len(numbers) < 3:
            numbers.append(0.0)
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    try:
        return float(text)
    except ValueError as exc:
        raise SessionIngestError(
            f"{flag} {value!r} is not a time: expected hh:mm, hh:mm:ss or seconds",
            code="invalid_time",
            detail={"flag": flag, "value": value},
        ) from exc


def compile_regex(pattern: str) -> re.Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise SessionIngestError(
            f"--regex {pattern!r} is not a valid regular expression: {exc}",
            code="invalid_regex",
            detail={"pattern": pattern},
        ) from exc


def speaker_user_ids(value: str, speakers: Speakers) -> list[str]:
    """Discord ids whose ``_speakers.yaml`` entry names this person. Exact, case-folded."""
    needle = value.strip().casefold()
    matched: list[str] = []
    for speaker in speakers.by_user_id.values():
        names = {
            (speaker.character or "").casefold(),
            (speaker.player or "").casefold(),
            speaker.user_id.casefold(),
        }
        names.discard("")
        if needle in names:
            matched.append(speaker.user_id)
    return matched


def _index_missing(path: Path, session_id: str) -> SessionIngestError:
    return SessionIngestError(
        f"{path} does not exist. The transcript index is written by `render`; grep only reads it.",
        code="index_missing",
        detail={"session": session_id, "index": str(path)},
        next_steps=[
            step(
                "render",
                "Render the transcript, which writes index.sqlite beside it.",
                command=f"{CLI} render --session {session_id}",
            )
        ],
    )


def open_index(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.create_function("CASEFOLD", 1, _casefold, deterministic=True)
    return connection


def _casefold(value: Any) -> Any:
    return value.casefold() if isinstance(value, str) else value


def _register_regexp(connection: sqlite3.Connection, pattern: re.Pattern[str]) -> None:
    def _matches(_: Any, value: Any) -> bool:
        return bool(isinstance(value, str) and pattern.search(value))

    connection.create_function("REGEXP", 2, _matches, deterministic=True)


@dataclass(frozen=True, slots=True)
class Query:
    """The resolved filter set, shared by every searched session."""

    speaker: str | None
    speaker_ids: tuple[str, ...]
    time_from: float | None
    time_to: float | None
    regex: re.Pattern[str] | None
    context: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "speaker": self.speaker,
            "speaker_user_ids": list(self.speaker_ids),
            "from": self.time_from,
            "to": self.time_to,
            "regex": self.regex.pattern if self.regex is not None else None,
            "context": self.context,
        }


def _where(query: Query) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if query.speaker is not None:
        needle = query.speaker.strip().casefold()
        options = ["CASEFOLD(speaker) = ?", "CASEFOLD(username) = ?", "CASEFOLD(user_id) = ?"]
        params.extend([needle, needle, needle])
        if query.speaker_ids:
            placeholders = ", ".join("?" for _ in query.speaker_ids)
            options.append(f"user_id IN ({placeholders})")
            params.extend(query.speaker_ids)
        clauses.append("(" + " OR ".join(options) + ")")
    if query.time_from is not None:
        clauses.append("t1 > ?")
        params.append(query.time_from)
    if query.time_to is not None:
        clauses.append("t0 < ?")
        params.append(query.time_to)
    if query.regex is not None:
        clauses.append("text REGEXP ?")
        params.append(query.regex.pattern)
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def _row_payload(row: sqlite3.Row, *, session_id: str, match: bool) -> dict[str, Any]:
    turn_id = row["turn_id"]
    chunk = row["chunk"]
    payload: dict[str, Any] = {
        "session": session_id,
        "i": row["i"],
        "t0": row["t0"],
        "t1": row["t1"],
        "time": hhmmss(row["t0"]),
        "track": row["track"],
        "user_id": row["user_id"],
        "username": row["username"],
        "speaker": row["speaker"],
        "text": row["text"],
        "turn_id": turn_id,
        "chunk": chunk,
        "match": match,
    }
    # `render` stores the verbatim segment beside the substituted one precisely so a
    # correction can be audited. Emitted only when the lexicon actually changed the
    # line: on an unchanged row it would double the payload to say nothing, and its
    # presence is then itself the signal that a substitution happened here.
    #
    # Compared on collapsed whitespace because `render` normalises `text` to one
    # line — otherwise a segment that merely wrapped would be reported as corrected.
    raw = row["text_raw"]
    if isinstance(raw, str) and raw and " ".join(raw.split()) != row["text"]:
        payload["text_raw"] = raw
        payload["substituted"] = True
    if turn_id and chunk:
        payload["evidence"] = Evidence.build(
            turn_id=turn_id,
            segment_i=int(row["i"]),
            t0=float(row["t0"]),
            speaker=row["speaker"],
            chunk=chunk,
            session_id=session_id,
        ).to_dict()
    return payload


def search_session(tree: SessionTree, query: Query) -> list[dict[str, Any]]:
    """Rows for one session, matches plus their ``--context`` neighbours, ordered by ``i``."""
    path = tree.index_sqlite
    if not path.is_file():
        raise _index_missing(path, tree.id)
    connection = open_index(path)
    try:
        if query.regex is not None:
            _register_regexp(connection, query.regex)
        where, params = _where(query)
        matches = [
            int(row["i"])
            for row in connection.execute(f"SELECT i FROM segments{where} ORDER BY i", params)
        ]
        if not matches:
            return []
        matched = set(matches)
        wanted = set(matched)
        if query.context > 0:
            for index in matches:
                wanted.update(range(index - query.context, index + query.context + 1))
        placeholders = ", ".join("?" for _ in wanted)
        rows = connection.execute(
            f"SELECT {_COLUMNS} FROM segments WHERE i IN ({placeholders}) ORDER BY i",
            sorted(wanted),
        ).fetchall()
    finally:
        connection.close()
    return [_row_payload(row, session_id=tree.id, match=int(row["i"]) in matched) for row in rows]


def indexed_sessions(roots: Roots) -> list[str]:
    """Session ids that carry an ``index.sqlite``, oldest id first."""
    if not roots.sessions.is_dir():
        return []
    return sorted(
        child.name
        for child in roots.sessions.iterdir()
        if child.is_dir() and SESSION_ID_RE.match(child.name) and (child / "index.sqlite").is_file()
    )


def format_lines(rows: Iterable[dict[str, Any]], *, with_session: bool) -> list[str]:
    lines: list[str] = []
    for row in rows:
        prefix = f"{row['session']} " if with_session else ""
        marker = "" if row["match"] else "  "
        lines.append(f"{prefix}{marker}[{row['time']}] {row['speaker']}: {row['text']}")
    return lines


def run(
    *,
    roots: Roots,
    config: SessionConfig,
    session_id: str | None = None,
    all_sessions: bool = False,
    speaker: str | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
    regex: str | None = None,
    context: int = 0,
) -> dict[str, Any]:
    """Returns the ``--json`` payload: matched turns with chunk, turn_id, t0, text."""
    if context < 0:
        raise SessionIngestError("--context must be >= 0", code="invalid_context")

    speakers = load_speakers(roots.speakers_file)
    query = Query(
        speaker=speaker,
        speaker_ids=tuple(speaker_user_ids(speaker, speakers)) if speaker else (),
        time_from=parse_time(time_from, flag="--from"),
        time_to=parse_time(time_to, flag="--to"),
        regex=compile_regex(regex) if regex else None,
        context=context,
    )
    if (
        query.time_from is not None
        and query.time_to is not None
        and query.time_to <= query.time_from
    ):
        raise SessionIngestError(
            f"--to ({query.time_to}s) must be after --from ({query.time_from}s)",
            code="invalid_time_range",
        )

    if all_sessions:
        session_ids = indexed_sessions(roots)
        if not session_ids:
            raise SessionIngestError(
                f"no rendered session under {roots.sessions} has an index.sqlite yet.",
                code="index_missing",
                detail={"sessions": str(roots.sessions)},
                next_steps=[
                    step(
                        "render",
                        "Render a session; index.sqlite is written beside the transcript.",
                        command=f"{CLI} render --session <session-id>",
                    )
                ],
            )
    elif session_id is None:
        raise SessionIngestError("grep needs either --session or --all", code="no_session")
    else:
        session_ids = [session_id]

    rows: list[dict[str, Any]] = []
    for candidate in session_ids:
        rows.extend(search_session(roots.session(candidate), query))

    matches = sum(1 for row in rows if row["match"])
    lines = format_lines(rows, with_session=all_sessions)
    if not rows:
        lines = ["no matches"]

    return {
        "status": "ok",
        "sessions": session_ids,
        "scope": "all" if all_sessions else "session",
        "filters": query.to_dict(),
        "matches": matches,
        "rows": rows,
        "row_count": len(rows),
        "lines": lines,
        "warnings": _warnings(speakers, query),
        "next_steps": [],
    }


def _warnings(speakers: Speakers, query: Query) -> list[str]:
    warnings: list[str] = []
    if query.speaker and not speakers.present:
        warnings.append(
            f"{speakers.path} is absent, so --speaker only matched the rendered display name, "
            f"the Discord username and the raw user_id"
        )
    elif query.speaker and not query.speaker_ids:
        warnings.append(
            f"--speaker {query.speaker!r} matched no _speakers.yaml entry; falling back to the "
            f"rendered display name, the Discord username and the raw user_id"
        )
    return warnings
