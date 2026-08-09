"""``view`` — the agent's read path into ``record.json``. Deterministic, read-only.

``record.json`` is the layer-2 interface, but it is not a document an agent
should page into context: for one 2.8-hour session it is ~280 KB, and roughly
60 % of that is the same evidence block repeated under every element (each row
carries the full ``{turn_id, segment_i, t0, speaker, chunk, link}`` for each of
its citations).

This verb renders the same record as compact Markdown — one line per element,
one evidence link by default, times as ``hh:mm:ss`` — which is what the agent
actually needs to draft a chronicle. Nothing is summarised or rephrased: every
line is a projection of fields already in the record, so a quote taken from
here is still a quote from the record. When more citations are wanted,
``--links N`` widens it; ``--json`` returns the same selection structurally.

Filters exist so the two common questions are one command each:

* "what happened, in order?" — the default.
* "what must the owner decide?" — ``--needs-owner``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .errors import SessionIngestError
from .nextsteps import CLI, next_steps_for, step
from .paths import Roots, SessionTree
from .writer import read_json

#: Element families in DESIGN §6 order — the order a chronicle is drafted in.
SECTIONS: tuple[str, ...] = (
    "scenes",
    "events",
    "entities",
    "quests",
    "loot",
    "commitments",
    "threads",
)

_HEADINGS = {
    "scenes": "Сцены",
    "events": "События",
    "entities": "Сущности",
    "quests": "Квесты",
    "loot": "Добыча",
    "commitments": "Обязательства",
    "threads": "Открытые нити",
}


def hhmmss(seconds: float | int | None) -> str:
    """``3661.4`` -> ``01:01:01``. ``None`` -> ``--:--:--``, never a fabricated zero."""
    if seconds is None:
        return "--:--:--"
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def record_missing_error(tree: SessionTree) -> SessionIngestError:
    return SessionIngestError(
        f"no record.json for {tree.id}. `view` reads the assembled record; it never "
        f"re-derives one from the dataset.",
        code="record_missing",
        detail={"expected": str(tree.record_json)},
        next_steps=[
            step(
                "record",
                "Assemble record.json first (deterministic, no API key needed).",
                command=f"{CLI} record --session {tree.id}",
            )
        ],
    )


def links_of(row: Mapping[str, Any], limit: int) -> list[str]:
    """The first ``limit`` ready wikilinks. Rows without evidence return nothing."""
    if limit <= 0:
        return []
    out: list[str] = []
    for item in row.get("evidence") or []:
        if not isinstance(item, Mapping):
            continue
        link = item.get("link")
        if isinstance(link, str) and link:
            out.append(link)
        if len(out) >= limit:
            break
    return out


def _suffix(row: Mapping[str, Any], limit: int) -> str:
    links = links_of(row, limit)
    return (" " + " ".join(links)) if links else ""


def _confidence(row: Mapping[str, Any], floor: float | None) -> bool:
    if floor is None:
        return True
    value = row.get("confidence")
    if not isinstance(value, (int, float)):
        return True
    return float(value) >= floor


def owner_flagged(row: Mapping[str, Any]) -> bool:
    """DESIGN §6 rule 5: the owner sees anything that touches the world or is flagged."""
    return bool(row.get("needs_owner")) or row.get("world_impact") not in (None, "none")


def select(
    record: Mapping[str, Any],
    *,
    sections: Sequence[str],
    scene: str | None,
    kind: str | None,
    needs_owner: bool,
    min_confidence: float | None,
) -> dict[str, list[Mapping[str, Any]]]:
    """Apply the filters. Every filter is an exact field comparison, never a search."""
    chosen: dict[str, list[Mapping[str, Any]]] = {}
    for family in sections:
        rows = [row for row in (record.get(family) or []) if isinstance(row, Mapping)]
        if scene is not None:
            if family == "scenes":
                rows = [row for row in rows if row.get("id") == scene]
            elif family == "events":
                rows = [row for row in rows if row.get("scene") == scene]
            else:
                # Only scenes and events carry a scene id; asking to scope loot by
                # scene would silently return everything, which reads as a lie.
                rows = []
        if kind is not None:
            rows = [row for row in rows if row.get("kind") == kind]
        if needs_owner:
            rows = [row for row in rows if owner_flagged(row)] if family == "events" else []
        rows = [row for row in rows if _confidence(row, min_confidence)]
        chosen[family] = rows
    return chosen


def _session_lines(session: Mapping[str, Any], record: Mapping[str, Any]) -> list[str]:
    lines = [f"# Сессия {session.get('id')} — выжимка record.json", ""]
    duration = session.get("duration_s")
    play = session.get("play_time_s")
    share = session.get("table_talk_share")
    lines.append(
        f"- Запись {hhmmss(duration)}"
        + (f", игра {hhmmss(play)}" if play is not None else ", игра неизвестна")
        + (f", застольный трёп {share:.0%}" if isinstance(share, (int, float)) else "")
    )
    participants = session.get("participants") or []
    if participants:
        who = ", ".join(
            f"{p.get('display')} ({p.get('role')}, {p.get('speech_share', 0):.0%})"
            for p in participants
            if isinstance(p, Mapping)
        )
        lines.append(f"- Участники: {who}")
    provenance = session.get("provenance")
    if isinstance(provenance, Mapping):
        lines.append(
            f"- Провенанс: {provenance.get('stt_model')} · {provenance.get('llm_model')} · "
            f"промпт {provenance.get('prompt_version')}"
        )
    missing = session.get("missing") or []
    if missing:
        lines.append(f"- **Отсутствует в записи:** {', '.join(str(m) for m in missing)}")
    unmapped = session.get("unmapped_participants") or []
    if unmapped:
        lines.append(f"- **Неопознанные говорящие:** {', '.join(str(u) for u in unmapped)}")
    flagged = sum(1 for row in (record.get("events") or []) if owner_flagged(row))
    lines.append(f"- Событий к решению владельца: {flagged}")
    lines.append("")
    return lines


def _render_rows(family: str, rows: Iterable[Mapping[str, Any]], links: int) -> list[str]:
    out: list[str] = []
    for row in rows:
        if family == "scenes":
            head = f"- **{row.get('id')}** [{hhmmss(row.get('t0'))}] {row.get('title')}"
            where = row.get("location")
            if where:
                head += f" — {where}"
            out.append(head + _suffix(row, links))
            summary = row.get("summary")
            if summary:
                out.append(f"  - {summary}")
        elif family == "events":
            flag = ""
            if row.get("needs_owner"):
                flag = " **[решение владельца]**"
            elif row.get("world_impact") not in (None, "none"):
                flag = f" [world_impact: {row.get('world_impact')}]"
            out.append(
                f"- **{row.get('id')}** ({row.get('scene')}, {row.get('kind')}) "
                f"{row.get('summary')}{flag}" + _suffix(row, links)
            )
            outcome = row.get("outcome")
            if outcome:
                out.append(f"  - итог: {outcome}")
        elif family == "entities":
            slug = row.get("registry_slug")
            note = row.get("vault_note")
            if slug:
                mark = f"`{slug}`" + (f" → `{note}`" if note else " (ещё в ростере)")
            else:
                mark = "**не в реестре**"
            out.append(
                f"- [{hhmmss(row.get('first_mention_t'))}] {row.get('canonical')} "
                f"({row.get('kind')}) — {mark}" + _suffix(row, links)
            )
        elif family == "quests":
            out.append(
                f"- **{row.get('status_change')}** — {row.get('name')}" + _suffix(row, links)
            )
            detail = row.get("detail")
            if detail:
                out.append(f"  - {detail}")
        elif family == "loot":
            who = row.get("recipient")
            qty = row.get("quantity")
            out.append(
                f"- {row.get('item')}"
                + (f" ×{qty}" if qty else "")
                + (f" → {who}" if who else "")
                + _suffix(row, links)
            )
        elif family == "commitments":
            out.append(f"- **{row.get('who')}** → {row.get('promise')}" + _suffix(row, links))
            deadline = row.get("deadline")
            if deadline:
                out.append(f"  - срок: {deadline}")
        elif family == "threads":
            out.append(f"- [{row.get('status')}] {row.get('question')}" + _suffix(row, links))
    return out


def _empty_reason(family: str, *, needs_owner: bool, scene: str | None) -> str:
    """Why a section is empty. "Nothing selected" reads as "nothing exists"."""
    if needs_owner and family != "events":
        return "_не отбирается: --needs-owner относится только к событиям_"
    if scene is not None and family not in ("scenes", "events"):
        return "_не отбирается: только у сцен и событий есть привязка к сцене_"
    return "_ничего не отобрано_"


def render(
    record: Mapping[str, Any],
    chosen: Mapping[str, list[Mapping[str, Any]]],
    *,
    links: int,
    header: bool,
    needs_owner: bool = False,
    scene: str | None = None,
) -> list[str]:
    session = record.get("session")
    lines: list[str] = []
    if header and isinstance(session, Mapping):
        lines.extend(_session_lines(session, record))
    for family in SECTIONS:
        rows = chosen.get(family)
        if rows is None:
            continue
        lines.append(f"## {_HEADINGS[family]} ({len(rows)})")
        if not rows:
            lines.append(_empty_reason(family, needs_owner=needs_owner, scene=scene))
        else:
            lines.extend(_render_rows(family, rows, links))
        lines.append("")
    return lines


def run(
    *,
    roots: Roots,
    session_id: str,
    sections: Sequence[str] | None = None,
    scene: str | None = None,
    kind: str | None = None,
    needs_owner: bool = False,
    min_confidence: float | None = None,
    links: int = 1,
    header: bool = True,
    include_lines: bool = True,
) -> dict[str, Any]:
    """Render a filtered view of ``record.json``. Returns the ``--json`` payload.

    ``include_lines=False`` drops the rendered Markdown from the payload. The
    JSON envelope otherwise carried the same content twice — ``selected`` as
    rows and ``lines`` as prose — which made ``view --json`` 84 % the size of
    the ``record.json`` it exists to shrink. Structured callers want the rows.
    """
    tree: SessionTree = roots.session(session_id)
    if not tree.record_json.is_file():
        raise record_missing_error(tree)

    payload = read_json(tree.record_json)
    if not isinstance(payload, Mapping):
        raise SessionIngestError(
            f"{tree.record_json} is not a JSON object.",
            code="record_invalid",
            detail={"path": str(tree.record_json)},
        )

    # `--needs-owner` is a question about events, so on its own it narrows to them.
    # Printing five "nothing selected" headings underneath would bury the answer.
    # An explicit --section still wins: the caller said what they wanted.
    if sections:
        wanted = tuple(sections)
    elif needs_owner:
        wanted = ("events",)
    else:
        wanted = SECTIONS
    unknown = [s for s in wanted if s not in SECTIONS]
    if unknown:
        raise SessionIngestError(
            f"unknown section(s): {', '.join(unknown)}. Known: {', '.join(SECTIONS)}.",
            code="unknown_section",
            detail={"unknown": unknown, "known": list(SECTIONS)},
        )

    chosen = select(
        payload,
        sections=wanted,
        scene=scene,
        kind=kind,
        needs_owner=needs_owner,
        min_confidence=min_confidence,
    )
    lines = (
        render(payload, chosen, links=links, header=header, needs_owner=needs_owner, scene=scene)
        if include_lines
        else []
    )
    session = payload.get("session")
    out = {
        "status": "ok",
        "session": session_id,
        "path": str(tree.record_json),
        "filters": {
            "sections": list(wanted),
            "scene": scene,
            "kind": kind,
            "needs_owner": needs_owner,
            "min_confidence": min_confidence,
            "links": links,
        },
        "counts": {family: len(rows) for family, rows in chosen.items()},
        "selected": {
            family: [
                {**{k: v for k, v in row.items() if k != "evidence"}, "links": links_of(row, links)}
                for row in rows
            ]
            for family, rows in chosen.items()
        },
        "transcript_root": (
            session.get("transcript_root") if isinstance(session, Mapping) else None
        ),
        "next_steps": next_steps_for("view", session_id=session_id),
    }
    if include_lines:
        out["lines"] = lines
    return out
