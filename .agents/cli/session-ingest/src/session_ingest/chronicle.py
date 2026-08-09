"""``chronicle`` — verify and freeze the note the *agent* wrote. Deterministic.

The CLI has no write path into ``vault/notes/`` and this verb does not change
that: it never creates, edits or formats a chronicle. It checks one that already
exists, because two things silently break otherwise and both are expensive.

* **Evidence links that resolve to nothing.** A chronicle cites the transcript
  through ``[[transcripts/<id>/NN-mmm-mmm#^t<turn>]]``. Those ids come from
  ``anchors.json``; a typo, a hand-edit, or a chronicle written against a
  superseded run produces a link Obsidian renders as ordinary text. Nobody
  notices until they follow it at the table.
* **A chronicle the tooling cannot see.** ``prune`` refuses to delete a
  session's audio until a note under ``vault/notes/sessions/`` mentions the
  session id, and ``adopt --promote`` refuses once one does. A chronicle saved
  one directory over satisfies neither, so ``prune`` keeps refusing and the
  owner concludes the gate is broken rather than that the file is misplaced.
* **A "canon" note that quietly kept changing.** ``status: canon`` is the
  protocol's freeze, and a freeze nobody can verify is a convention. ``--freeze``
  records a content digest (the append-allowed ``## Реконсиляция`` section
  excluded) in the session tree; ``--check`` reports any later divergence. A
  *recorded* retcon marking is re-frozen explicitly — the point is that no edit
  to frozen history is silent, not that no edit ever happens.

Three modes: ``--check`` (read-only, per session), ``--freeze`` (writes only the
freeze record under ``.cache/sessions/<id>/``), ``--status`` (read-only sweep of
every note in ``vault/notes/sessions/`` — the mechanical "am I caught up?").

What it reports is facts — resolved, unresolved, missing frontmatter keys,
digest drift — and never a verdict on whether the writing is any good.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import SessionIngestError
from .nextsteps import CLI, next_steps_for, step
from .paths import Roots, SessionTree
from .provenance import utc_now
from .record import load_anchors
from .writer import read_json, write_json

#: ``[[transcripts/<session>/<chunk>#^<block>]]``, with Obsidian's optional
#: ``|display text`` tail.
#:
#: The alias form is NOT exotic — a chronicle is agent-authored prose and
#: ``ttrpg-vault-rich-notes`` teaches aliased wikilinks, so citations arrive as
#: ``[[…#^t123-4|в тот вечер]]`` routinely. An earlier version of this pattern
#: forbade ``|`` in the block group *without* accepting an alias tail, so an
#: aliased citation matched nothing at all: it was not reported unresolved, it
#: was invisible. A note whose every citation was aliased and broken came back
#: ``clean: true`` — the failure direction that says everything is fine.
LINK_RE = re.compile(
    r"\[\[transcripts/(?P<session>[^/\]|#]+)/(?P<chunk>[^\]|#]+)"
    r"#\^(?P<block>[^\]|]+?)(?:\|[^\]]*)?\]\]"
)

#: Frontmatter keys a chronicle needs for the tracker to work. `session` and
#: `world_days_elapsed` drive the calendar; `transcript` binds evidence to a run.
REQUIRED_FRONTMATTER = ("type", "session", "date_real", "transcript", "status")
RECOMMENDED_FRONTMATTER = ("world_days_elapsed", "world_time_confidence", "arc", "participants")

#: The one section a draft chronicle carries and a frozen one must not: the
#: owner-review queue. Questions are asked in chat; this section is where the
#: unanswered ones survive an interrupted conversation. Exact H2 text — the
#: skill and this constant are the same contract.
QUESTIONS_HEADING = "## Вопросы владельцу"

#: The one section that may receive appends after the freeze (decision records,
#: retcon events, post-freeze additions). Excluded from the freeze digest so a
#: legitimate append never reads as corruption.
RECONCILIATION_HEADING = "## Реконсиляция"

FREEZE_SCHEMA = "ttrpg.chronicle-freeze/1"


def split_sections(text: str) -> list[tuple[str | None, list[str]]]:
    """Split a note into ``(h2_heading, lines)`` runs, fence-aware.

    Purely structural: an H2 is a line that starts with ``## `` outside a fenced
    code block. The leading run (frontmatter, title, preamble) has heading
    ``None``. No content is interpreted, only sliced.
    """
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.startswith("## "):
            sections.append((line.strip(), []))
            continue
        sections[-1][1].append(line)
    return sections


def section_body(text: str, heading: str) -> list[str] | None:
    """The lines of one H2 section, or ``None`` when the heading is absent."""
    for found, lines in split_sections(text):
        if found == heading:
            return lines
    return None


def open_questions(text: str) -> tuple[bool, int]:
    """Whether the owner-questions section exists, and how many ``### `` items it holds."""
    body = section_body(text, QUESTIONS_HEADING)
    if body is None:
        return False, 0
    return True, sum(1 for line in body if line.startswith("### "))


def freeze_digest(text: str) -> str:
    """Digest of the note with the ``## Реконсиляция`` body excluded.

    The heading itself stays in the digest — deleting the whole section is an
    edit, appending inside it is not. Everything else, frontmatter included, is
    covered: canon means the facts, the evidence links and the metadata hold
    still, not merely the prose.
    """
    kept: list[str] = []
    for heading, lines in split_sections(text):
        if heading == RECONCILIATION_HEADING:
            kept.append(heading)
            continue
        if heading is not None:
            kept.append(heading)
        kept.extend(lines)
    payload = "\n".join(kept) + "\n"
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_freeze_record(path: Path) -> dict[str, Any] | None:
    """The freeze record, or ``None`` when the session was never frozen."""
    if not path.is_file():
        return None
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("notes"), dict):
        raise SessionIngestError(
            f"{path} is not a chronicle freeze record; delete it and re-run "
            f"`{CLI} chronicle --freeze` if the chronicle is canon.",
            code="freeze_record_invalid",
            detail={"path": str(path)},
        )
    return payload


@dataclass(frozen=True, slots=True)
class LinkCheck:
    raw: str
    session: str
    chunk: str
    block: str
    resolved: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "link": self.raw,
            "session": self.session,
            "chunk": self.chunk,
            "block": self.block,
            "resolved": self.resolved,
            "reason": self.reason,
        }


def json_safe(value: Any) -> Any:
    """Coerce a frontmatter value into something ``json.dumps`` accepts.

    YAML turns an unquoted ``date_real: 2026-08-08`` into a ``datetime.date``,
    and every chronicle carries one. Passing that straight into the ``--json``
    envelope crashes the whole verb *after* the work is done — the check
    succeeds and the agent sees a traceback. Anything not JSON-native is
    stringified rather than dropped: the value is diagnostic, not structural.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    return str(value)


def read_frontmatter(text: str) -> tuple[dict[str, Any], str | None]:
    """Parse the leading YAML block. Returns ``({}, reason)`` when there isn't one.

    Delimiters are matched line-wise against exactly ``---``. Splitting on the
    substring ``"\\n---"`` instead accepted ``----`` as a closer, truncated the
    block at any quoted scalar whose line began with ``---``, and reported a
    leading horizontal rule as "frontmatter must be a mapping, got str" — three
    misleading diagnostics for notes that were merely unusual.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, "note does not start with a YAML frontmatter block"
    closing = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        return {}, "frontmatter block is opened but never closed"
    body = "\n".join(lines[1:closing])
    try:
        loaded = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        return {}, f"frontmatter is not valid YAML: {exc}"
    if loaded is None:
        return {}, None
    if not isinstance(loaded, dict):
        # Obsidian treats a leading `---` as frontmatter regardless, so this is the
        # honest classification rather than "no frontmatter" — but say what it
        # probably is, because a horizontal rule on line 1 is the usual cause.
        return {}, (
            f"frontmatter must be a mapping, got {type(loaded).__name__} — if the note opens "
            f"with a horizontal rule rather than frontmatter, move it below the block"
        )
    return loaded, None


def check_links(text: str, anchors: Any, *, session_id: str) -> list[LinkCheck]:
    """Resolve every transcript citation in the note against ``anchors.json``."""
    checks: list[LinkCheck] = []
    seen: set[str] = set()
    for match in LINK_RE.finditer(text):
        raw = match.group(0)
        if raw in seen:
            continue
        seen.add(raw)
        link_session = match.group("session")
        chunk = match.group("chunk")
        block = match.group("block")
        if link_session != session_id:
            checks.append(
                LinkCheck(
                    raw=raw,
                    session=link_session,
                    chunk=chunk,
                    block=block,
                    resolved=False,
                    reason=f"cites session {link_session}, not {session_id}",
                )
            )
            continue
        anchor = anchors.by_turn.get(block)
        if anchor is None:
            checks.append(
                LinkCheck(
                    raw=raw,
                    session=link_session,
                    chunk=chunk,
                    block=block,
                    resolved=False,
                    reason="no such turn id in anchors.json",
                )
            )
            continue
        if anchor.chunk != chunk:
            checks.append(
                LinkCheck(
                    raw=raw,
                    session=link_session,
                    chunk=chunk,
                    block=block,
                    resolved=False,
                    reason=f"turn lives in chunk {anchor.chunk}, not {chunk}",
                )
            )
            continue
        checks.append(
            LinkCheck(raw=raw, session=link_session, chunk=chunk, block=block, resolved=True)
        )
    return checks


def _no_chronicle_error(tree: SessionTree, roots: Roots) -> SessionIngestError:
    return SessionIngestError(
        f"no chronicle under {roots.chronicles_dir} mentions {tree.id}. The CLI never writes "
        f"one — the agent drafts it from record.json and recap.draft.md.",
        code="chronicle_missing",
        detail={"session": tree.id, "expected_dir": str(roots.chronicles_dir)},
        next_steps=[
            step(
                "view",
                "Read the record compactly, then author the chronicle.",
                command=f"{CLI} view --session {tree.id}",
            )
        ],
    )


def _check_note(
    path: Path, *, anchors: Any, session_id: str, freeze_notes: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Every fact ``--check`` can state about one note. Pure function of the file."""
    text = path.read_text(encoding="utf-8")
    frontmatter, fm_reason = read_frontmatter(text)
    missing = [k for k in REQUIRED_FRONTMATTER if k not in frontmatter]
    absent_recommended = [k for k in RECOMMENDED_FRONTMATTER if k not in frontmatter]
    checks = check_links(text, anchors, session_id=session_id)
    good = [c for c in checks if c.resolved]
    bad = [c for c in checks if not c.resolved]
    status_value = frontmatter.get("status")
    questions_present, questions_count = open_questions(text)

    problems: list[str] = []
    if fm_reason:
        problems.append(fm_reason)
    if missing:
        problems.append(f"frontmatter is missing {', '.join(missing)}")
    declared = frontmatter.get("transcript")
    if declared is not None and str(declared) != session_id:
        problems.append(f"frontmatter `transcript: {declared}` does not match {session_id}")
    if frontmatter.get("type") not in (None, "session"):
        problems.append(
            f"`type: {frontmatter.get('type')}` — a play record is `type: session`; "
            f"prep notes are `type: prep`"
        )
    if not checks:
        problems.append("no transcript citations at all — nothing in it is evidenced")
    if status_value == "canon" and questions_present:
        problems.append(
            f"`status: canon` but `{QUESTIONS_HEADING}` is still present — a freeze with open "
            f"owner questions is a contradiction. Resolve them into `{RECONCILIATION_HEADING}` "
            f"and delete the section, or set the note back to `status: draft`."
        )

    # --- the freeze side -------------------------------------------------------
    # Freeze findings are kept apart from structural problems: ``--check`` treats
    # both as dirt, but ``--freeze`` must not refuse over the very drift it exists
    # to acknowledge — re-freezing IS the documented resolution.
    freeze: str
    freeze_problems: list[str] = []
    recorded = freeze_notes.get(path.name) if freeze_notes is not None else None
    if recorded is None:
        freeze = "none"
        if freeze_notes is not None and status_value == "canon":
            freeze_problems.append(
                "the session carries a freeze record but this note is not in it — it appeared "
                "after the freeze. Re-freeze deliberately if it belongs."
            )
    elif status_value != "canon":
        freeze = "drift"
        freeze_problems.append(
            f"the note was frozen but is now `status: {status_value}` — un-freezing is a "
            f"retcon-shaped change and must be deliberate. Restore `status: canon` or "
            f"re-freeze after the owner's decision."
        )
    elif freeze_digest(text) != str(recorded.get("digest")):
        freeze = "drift"
        freeze_problems.append(
            f"content changed after the freeze (outside `{RECONCILIATION_HEADING}`). A recorded "
            f"retcon marking is legitimate — re-run `{CLI} chronicle --session {session_id} "
            f"--freeze` to acknowledge it. Anything else is a silent edit to frozen history."
        )
    else:
        freeze = "ok"

    return {
        "path": str(path),
        "frontmatter": {k: json_safe(frontmatter.get(k)) for k in REQUIRED_FRONTMATTER},
        "missing_frontmatter": missing,
        "missing_recommended": absent_recommended,
        "status": json_safe(status_value),
        "open_questions": questions_count if questions_present else 0,
        "questions_section": questions_present,
        "freeze": freeze,
        "links_total": len(checks),
        "links_resolved": len(good),
        "links_unresolved": [c.to_dict() for c in bad],
        "problems": problems + freeze_problems,
        "structural_problems": problems,
    }


def _collect(
    roots: Roots, session_id: str
) -> tuple[SessionTree, list[Path], list[dict[str, Any]], Any]:
    """Load and check every chronicle candidate for one session."""
    tree: SessionTree = roots.session(session_id)
    chronicles = tree.chronicle_candidates()
    if not chronicles:
        raise _no_chronicle_error(tree, roots)
    anchors = load_anchors(tree.anchors_json, session_id=session_id)
    record = load_freeze_record(tree.chronicle_freeze_json)
    freeze_notes = record.get("notes") if record is not None else None
    notes = [
        _check_note(path, anchors=anchors, session_id=session_id, freeze_notes=freeze_notes)
        for path in chronicles
    ]
    return tree, chronicles, notes, anchors


def _report_lines(notes: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for note in notes:
        lines.append(note["path"])
        lines.append(
            f"  ссылки: {note['links_resolved']}/{note['links_total']} "
            f"разрешаются через anchors.json"
        )
        state = f"  статус: {note['status'] or '—'}"
        if note["questions_section"]:
            state += f" · открытых вопросов: {note['open_questions']}"
        state += {
            "ok": " · заморожена, дайджест сходится",
            "drift": " · ИЗМЕНЕНА после заморозки",
            "none": " · заморозки нет",
        }[note["freeze"]]
        lines.append(state)
        if note["missing_recommended"]:
            lines.append(
                f"  необязательные поля отсутствуют: {', '.join(note['missing_recommended'])}"
            )
        for problem in note["problems"]:
            lines.append(f"  ПРОБЛЕМА: {problem}")
        for check in note["links_unresolved"]:
            lines.append(f"  БИТАЯ ССЫЛКА {check['link']} — {check['reason']}")
    return lines


def run(*, roots: Roots, session_id: str) -> dict[str, Any]:
    """``--check``: verify the chronicle for one session. Read-only."""
    _tree, chronicles, notes, anchors = _collect(roots, session_id)
    total_ok = sum(n["links_resolved"] for n in notes)
    total_bad = sum(len(n["links_unresolved"]) for n in notes)
    status_ok = total_bad == 0 and all(not n["problems"] for n in notes)
    all_canon = all(n["status"] == "canon" for n in notes)
    all_frozen = bool(notes) and all(n["freeze"] == "ok" for n in notes)

    lines = _report_lines(notes)
    lines.append("")
    lines.append(
        f"итого: {len(chronicles)} запись(ей), {total_ok} ссылок разрешено, {total_bad} битых"
    )
    if status_ok:
        lines.append("prune разблокирован: гейт хроники видит эту сессию")

    return {
        "status": "ok",
        "session": session_id,
        "chronicles": [str(p) for p in chronicles],
        "notes": notes,
        "links_resolved": total_ok,
        "links_unresolved": total_bad,
        "open_questions": sum(n["open_questions"] for n in notes),
        "anchors_digest": anchors.digest,
        "clean": status_ok,
        "canon": all_canon,
        "frozen": all_frozen,
        "lines": lines,
        "next_steps": next_steps_for(
            "chronicle",
            session_id=session_id,
            clean=status_ok,
            canon=all_canon,
            frozen=all_frozen,
        ),
    }


def run_freeze(*, roots: Roots, session_id: str) -> dict[str, Any]:
    """``--freeze``: record the canon chronicle's digests in the session tree.

    The only write this verb performs, and it is *not* into ``vault/notes/`` —
    the note itself is untouched. Refuses unless the check is clean and every
    candidate is ``status: canon``: freezing a broken or still-draft note would
    turn the enforcement into an attestation of the wrong thing.
    """
    tree, chronicles, notes, _anchors = _collect(roots, session_id)
    problems = [p for n in notes for p in n["structural_problems"]]
    total_bad = sum(len(n["links_unresolved"]) for n in notes)
    if problems or total_bad:
        raise SessionIngestError(
            f"the chronicle for {session_id} does not pass --check "
            f"({len(problems)} problem(s), {total_bad} broken link(s)); freezing would attest "
            f"a broken note. Fix it and re-check first.",
            code="chronicle_unclean",
            detail={"problems": problems, "links_unresolved": total_bad},
            next_steps=[
                step(
                    "recheck",
                    "See exactly what is broken, repair the note, then freeze.",
                    command=f"{CLI} chronicle --session {session_id} --check",
                )
            ],
        )
    not_canon = [n["path"] for n in notes if n["status"] != "canon"]
    if not_canon:
        raise SessionIngestError(
            f"not every chronicle for {session_id} is `status: canon`: "
            f"{', '.join(not_canon)}. The freeze is the last step of the review — ask the "
            f"owner the note's open questions, apply the answers, flip the status, then freeze.",
            code="chronicle_not_canon",
            detail={"not_canon": not_canon},
        )

    previous = load_freeze_record(tree.chronicle_freeze_json)
    digests = {
        path.name: {"digest": freeze_digest(path.read_text(encoding="utf-8"))}
        for path in chronicles
    }
    payload = {
        "schema": FREEZE_SCHEMA,
        "session": session_id,
        "frozen_at": utc_now(),
        "notes": digests,
        "refreeze_of": previous.get("frozen_at") if previous else None,
    }
    write_json(tree.chronicle_freeze_json, payload)

    lines = [
        f"заморожено: {len(notes)} запись(ей) для {session_id}",
        *(f"  {Path(n['path']).name}" for n in notes),
        f"дайджесты записаны в {tree.chronicle_freeze_json}",
    ]
    if previous:
        lines.append(f"перезаморозка: предыдущая от {previous.get('frozen_at')}")
    return {
        "status": "ok",
        "session": session_id,
        "frozen": True,
        "refreeze": previous is not None,
        "freeze_record": str(tree.chronicle_freeze_json),
        "notes": [Path(n["path"]).name for n in notes],
        "lines": lines,
        "next_steps": next_steps_for(
            "chronicle", session_id=session_id, clean=True, canon=True, frozen=True
        ),
    }


def run_status(*, roots: Roots) -> dict[str, Any]:
    """``--status``: the mechanical "am I caught up?" over the whole ledger.

    Sweeps every ``*.md`` under ``vault/notes/sessions/`` — no session id needed.
    Caught up means: every ``type: session`` note is ``status: canon``, carries no
    open owner questions, and no frozen note has drifted. A missing freeze record
    does not block: enforcement fails open to convention, because the record
    lives in the prunable cache.
    """
    chronicles_dir = roots.chronicles_dir
    rows: list[dict[str, Any]] = []
    if chronicles_dir.is_dir():
        for path in sorted(chronicles_dir.glob("*.md")):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            frontmatter, fm_reason = read_frontmatter(text)
            questions_present, questions_count = open_questions(text)
            note_type = frontmatter.get("type")
            status_value = frontmatter.get("status")

            freeze = "n/a"
            transcript = frontmatter.get("transcript")
            if note_type == "session" and transcript is not None:
                try:
                    tree = roots.session(str(transcript))
                    record = load_freeze_record(tree.chronicle_freeze_json)
                except SessionIngestError as exc:
                    # A malformed `transcript:` or a corrupt freeze record in ONE
                    # note must not take down the whole sweep — report it on that
                    # row and keep going.
                    fm_reason = fm_reason or exc.message
                    record = None
                recorded = record.get("notes", {}).get(path.name) if record else None
                if recorded is None:
                    freeze = "none"
                elif status_value == "canon" and freeze_digest(text) == str(recorded.get("digest")):
                    freeze = "ok"
                else:
                    freeze = "drift"

            rows.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "type": json_safe(note_type),
                    "status": json_safe(status_value),
                    "frontmatter_problem": fm_reason,
                    "open_questions": questions_count if questions_present else 0,
                    "questions_section": questions_present,
                    "freeze": freeze,
                }
            )

    sessions = [r for r in rows if r["type"] == "session"]
    pending = [
        r
        for r in sessions
        if r["status"] != "canon" or r["questions_section"] or r["freeze"] == "drift"
    ]
    caught_up = not pending

    lines: list[str] = []
    for row in rows:
        marks: list[str] = [str(row["status"] or "—")]
        if row["questions_section"]:
            marks.append(f"открытых вопросов: {row['open_questions']}")
        if row["freeze"] == "drift":
            marks.append("ИЗМЕНЕНА после заморозки")
        elif row["freeze"] == "ok":
            marks.append("заморожена")
        prefix = "  " if row["type"] == "session" else "  ("
        suffix = "" if row["type"] == "session" else f", type: {row['type'] or '—'})"
        lines.append(f"{prefix}{row['name']} — {' · '.join(marks)}{suffix}")
    lines.append("")
    if caught_up:
        lines.append("всё разобрано: каждая запись сессии — канон, открытых вопросов нет")
    else:
        lines.append(f"не разобрано: {len(pending)} запись(ей) требует владельца или заморозки")

    next_steps = []
    if pending:
        next_steps.append(
            step(
                "owner_review",
                (
                    "Agent step: for each pending note — ask the owner its open questions in "
                    "chat, apply the answers into the note and the affected canon, delete the "
                    "questions section, set `status: canon`, then `chronicle --check` and "
                    "`chronicle --freeze` for that session."
                ),
                required=False,
                pending=[r["name"] for r in pending],
            )
        )
    return {
        "status": "ok",
        "chronicles_dir": str(chronicles_dir),
        "notes": rows,
        "pending": [r["name"] for r in pending],
        "caught_up": caught_up,
        "lines": lines,
        "next_steps": next_steps,
    }
