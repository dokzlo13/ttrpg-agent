"""``chronicle`` — verify the note the *agent* wrote. Deterministic, read-only.

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

What it reports is facts — resolved, unresolved, missing frontmatter keys — and
never a verdict on whether the writing is any good.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

from .errors import SessionIngestError
from .nextsteps import CLI, next_steps_for, step
from .paths import Roots, SessionTree
from .record import load_anchors

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


def run(*, roots: Roots, session_id: str) -> dict[str, Any]:
    """Check the chronicle for one session. Returns the ``--json`` payload."""
    tree: SessionTree = roots.session(session_id)
    chronicles = tree.chronicle_candidates()
    if not chronicles:
        raise _no_chronicle_error(tree, roots)

    anchors = load_anchors(tree.anchors_json, session_id=session_id)

    notes: list[dict[str, Any]] = []
    lines: list[str] = []
    total_ok = 0
    total_bad = 0
    for path in chronicles:
        text = path.read_text(encoding="utf-8")
        frontmatter, fm_reason = read_frontmatter(text)
        missing = [k for k in REQUIRED_FRONTMATTER if k not in frontmatter]
        absent_recommended = [k for k in RECOMMENDED_FRONTMATTER if k not in frontmatter]
        checks = check_links(text, anchors, session_id=session_id)
        good = [c for c in checks if c.resolved]
        bad = [c for c in checks if not c.resolved]
        total_ok += len(good)
        total_bad += len(bad)

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

        notes.append(
            {
                "path": str(path),
                "frontmatter": {k: json_safe(frontmatter.get(k)) for k in REQUIRED_FRONTMATTER},
                "missing_frontmatter": missing,
                "missing_recommended": absent_recommended,
                "links_total": len(checks),
                "links_resolved": len(good),
                "links_unresolved": [c.to_dict() for c in bad],
                "problems": problems,
            }
        )

        lines.append(f"{path}")
        lines.append(f"  ссылки: {len(good)}/{len(checks)} разрешаются через anchors.json")
        if absent_recommended:
            lines.append(f"  необязательные поля отсутствуют: {', '.join(absent_recommended)}")
        for problem in problems:
            lines.append(f"  ПРОБЛЕМА: {problem}")
        for check in bad:
            lines.append(f"  БИТАЯ ССЫЛКА {check.raw} — {check.reason}")

    status_ok = total_bad == 0 and all(not n["problems"] for n in notes)
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
        "anchors_digest": anchors.digest,
        "clean": status_ok,
        "lines": lines,
        "next_steps": next_steps_for("chronicle", session_id=session_id, clean=status_ok),
    }
