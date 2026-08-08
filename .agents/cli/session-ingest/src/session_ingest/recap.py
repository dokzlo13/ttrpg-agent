"""``recap`` — the Russian recap draft. Metered.

DESIGN §4 and PLAN step 6:

* Reads ``extraction.json`` and ``anchors.json`` **only**. ``tests/test_recap.py``
  asserts this module never opens the dataset: the recap is a distillation of the
  extraction, and re-reading verbatim speech here is how unevidenced claims creep
  in. Everything the footer needs — dataset digest, ``merge_gap_s``, model — is
  recorded in ``extraction.json`` by the stage that did read the dataset.
* Russian body. Every bullet ends in an evidence wikilink resolved through
  ``anchors.json``; a bullet whose citations resolve to nothing is dropped rather
  than shipped unevidenced.
* ``## Вопросы к владельцу`` is **not** written by the model. It is assembled
  deterministically from events with ``world_impact != "none"`` or
  ``needs_owner`` — DESIGN §6 rule 5 is a property of the data, not a judgement
  call the recap prompt gets to make.
* ``--audience players`` is M3 and must stay a projection of the same store, not
  a second generation pass.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .config import SessionConfig
from .errors import SessionIngestError
from .llm import (
    Usage,
    client_for,
    lexicon_reference,
    load_prompt,
    metered_skip,
    object_schema,
    structured_call,
)
from .nextsteps import CLI, next_steps_for, step
from .paths import Roots, SessionTree
from .provenance import CompositeKey, Provenance, sha256_file, short_digest, utc_now
from .record import AnchorIndex, load_anchors, load_extraction
from .vaultfiles import load_lexicon
from .writer import read_json, write_text

PROMPT_VERSION = "recap/1"

#: The fixed section order (the old design's headings). The model fills them; it
#: does not get to invent, rename or reorder them.
SECTIONS: tuple[str, ...] = (
    "Сцены",
    "Бои",
    "Социальные сцены",
    "Добыча",
    "Продвижение квестов",
    "Новые лица",
    "Открытые нити",
    "Точка остановки",
)

OWNER_SECTION = "Вопросы к владельцу"

RECAP_SCHEMA = object_schema(
    {
        "sections": {
            "type": "array",
            "items": object_schema(
                {
                    "heading": {"type": "string", "enum": list(SECTIONS)},
                    "bullets": {
                        "type": "array",
                        "items": object_schema(
                            {
                                "text": {"type": "string"},
                                "evidence": {"type": "array", "items": {"type": "string"}},
                            }
                        ),
                    },
                }
            ),
        }
    }
)

#: Script bucketing for `search_terms` — mechanical, not semantic: RESEARCH §5
#: rejects a full English mirror and prescribes mixed RU+EN terms per chunk.
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


# ------------------------------------------------------------------- helpers


def evidence_pool(extraction: Mapping[str, Any]) -> dict[str, str]:
    """``turn_id -> speaker`` over every evidence row in the extraction.

    The recap may only cite turns the extraction already cited: it never saw the
    transcript, so a turn id it produced from anywhere else is an invention.
    """
    pool: dict[str, str] = {}
    elements = extraction.get("elements")
    if not isinstance(elements, Mapping):
        return pool
    for rows in elements.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            for entry in row.get("evidence") or []:
                if not isinstance(entry, Mapping):
                    continue
                turn_id = entry.get("turn_id")
                speaker = entry.get("speaker")
                if isinstance(turn_id, str):
                    pool.setdefault(turn_id, speaker if isinstance(speaker, str) else "unknown")
    return pool


def search_terms(extraction: Mapping[str, Any], *, limit: int = 24) -> list[str]:
    """Mixed RU+EN terms for retrieval (RESEARCH §5: no English mirror, mixed terms instead)."""
    russian: list[str] = []
    latin: list[str] = []
    elements = extraction.get("elements")
    if not isinstance(elements, Mapping):
        return []
    for row in elements.get("entities") or []:
        if not isinstance(row, Mapping):
            continue
        for key in ("canonical", "name_as_heard"):
            value = row.get(key)
            if not isinstance(value, str):
                continue
            term = value.strip()
            if not term:
                continue
            bucket = russian if _CYRILLIC_RE.search(term) else latin
            if term not in bucket:
                bucket.append(term)
    return (russian + latin)[:limit]


@dataclass(frozen=True, slots=True)
class Bullet:
    text: str
    links: tuple[str, ...]

    def render(self) -> str:
        return f"- {self.text} {' '.join(self.links)}".rstrip()


def build_bullets(
    raw: Any, *, pool: Mapping[str, str], anchors: AnchorIndex, session_id: str
) -> tuple[list[Bullet], int]:
    """Attach the resolved wikilink(s); drop anything that cannot be evidenced."""
    bullets: list[Bullet] = []
    dropped = 0
    if not isinstance(raw, list):
        return bullets, dropped
    for row in raw:
        if not isinstance(row, Mapping):
            dropped += 1
            continue
        text = row.get("text")
        if not isinstance(text, str) or not text.strip():
            dropped += 1
            continue
        links: list[str] = []
        seen: set[str] = set()
        for turn_id in row.get("evidence") or []:
            if not isinstance(turn_id, str) or turn_id not in pool or turn_id in seen:
                continue
            evidence = anchors.evidence_for(
                turn_id=turn_id, speaker=pool[turn_id], session_id=session_id
            )
            if evidence is None:
                continue
            seen.add(turn_id)
            links.append(evidence.link)
        if not links:
            dropped += 1
            continue
        bullets.append(Bullet(text=text.strip().rstrip("."), links=tuple(links)))
    return bullets, dropped


def owner_questions(
    extraction: Mapping[str, Any], *, anchors: AnchorIndex, session_id: str
) -> list[Bullet]:
    """Assembled from the data, never from the model: DESIGN §6 rule 5."""
    bullets: list[Bullet] = []
    elements = extraction.get("elements")
    if not isinstance(elements, Mapping):
        return bullets
    for row in elements.get("events") or []:
        if not isinstance(row, Mapping):
            continue
        impact = row.get("world_impact")
        if impact == "none" and not row.get("needs_owner"):
            continue
        links: list[str] = []
        for entry in row.get("evidence") or []:
            if not isinstance(entry, Mapping):
                continue
            turn_id = entry.get("turn_id")
            speaker = entry.get("speaker")
            if not isinstance(turn_id, str):
                continue
            evidence = anchors.evidence_for(
                turn_id=turn_id,
                speaker=speaker if isinstance(speaker, str) else "unknown",
                session_id=session_id,
            )
            if evidence is not None:
                links.append(evidence.link)
        if not links:
            continue
        flag = "требует решения" if row.get("needs_owner") else f"world_impact: {impact}"
        summary = str(row.get("summary") or "").strip().rstrip(".")
        bullets.append(Bullet(text=f"{summary} — {flag}", links=tuple(links)))
    return bullets


def frontmatter(
    *, session_id: str, audience: str, terms: Sequence[str], entities: Sequence[str]
) -> str:
    def yaml_list(values: Sequence[str]) -> str:
        if not values:
            return "[]"
        return "[" + ", ".join(f'"{value}"' for value in values) + "]"

    return "\n".join(
        [
            "---",
            "type: session",
            "status: draft",
            "language: ru",
            "source: pipeline",
            f"transcript: {session_id}",
            f"audience: {audience}",
            f"created: {utc_now()[:10]}",
            "tags: [campaign, session, draft]",
            f"search_terms: {yaml_list(terms)}",
            f"entities: {yaml_list(entities)}",
            "---",
        ]
    )


def provenance_footer(
    extraction: Mapping[str, Any], *, config: SessionConfig, qa_line: str | None
) -> str:
    lines = [
        "---",
        "",
        "> [!info] Провенанс",
        f"> Датасет: `{short_digest(str(extraction.get('dataset_digest') or ''))}`  ",
        f"> merge_gap_s: {extraction.get('merge_gap_s')}  ",
        f"> Модель: {config.openai_model}  ",
        f"> Промпты: {extraction.get('prompt_version')} → {PROMPT_VERSION}  ",
    ]
    if qa_line:
        lines.append(f"> {qa_line}  ")
    lines.append("> Черновик машинной генерации: каждый пункт ссылается на реплику стенограммы.")
    return "\n".join(lines)


def qa_one_liner(tree: SessionTree) -> str | None:
    if not tree.qa_json.is_file():
        return None
    payload = read_json(tree.qa_json)
    if not isinstance(payload, Mapping):
        return None
    crossed = payload.get("thresholds_crossed")
    if not isinstance(crossed, list) or not crossed:
        return "QA: ни один порог не пересечён"
    described = ", ".join(
        str(entry.get("metric")) for entry in crossed if isinstance(entry, Mapping)
    )
    return f"QA: пересечены пороги — {described}"


# --------------------------------------------------------------------- verb


def extraction_missing_error(tree: SessionTree) -> SessionIngestError:
    return SessionIngestError(
        f"{tree.extraction_json} is missing; recap is written from the extraction only.",
        code="extraction_missing",
        detail={"path": str(tree.extraction_json)},
        next_steps=[
            step(
                "extract",
                "Run the map-reduce extraction first (metered).",
                command=f"{CLI} extract --session {tree.id}",
                metered=True,
            )
        ],
    )


def composite_key(
    *,
    extraction_digest: str | None,
    anchors_digest: str | None,
    config: SessionConfig,
    prompt_digest: str,
    audience: str,
) -> CompositeKey:
    return CompositeKey(
        prompt_version=PROMPT_VERSION,
        model=config.openai_model,
        knobs={
            "extraction_digest": extraction_digest,
            "anchors_digest": anchors_digest,
            "prompt_digest": prompt_digest,
            "audience": audience,
        },
    )


def run(
    *,
    roots: Roots,
    config: SessionConfig,
    session_id: str,
    audience: str = "dm",
    force: bool = False,
) -> dict[str, Any]:
    """Draft ``recap.draft.md`` from the extraction. Returns the ``--json`` payload."""
    skipped = metered_skip(config, "recap")
    if skipped is not None:
        return skipped

    tree: SessionTree = roots.session(session_id)
    extraction = load_extraction(tree.extraction_json)
    if extraction is None:
        raise extraction_missing_error(tree)
    anchors = load_anchors(tree.anchors_json, session_id=session_id)
    prompt = load_prompt(PROMPT_VERSION)
    lexicon = load_lexicon(roots.lexicon_file)

    key = composite_key(
        extraction_digest=sha256_file(tree.extraction_json),
        anchors_digest=anchors.digest,
        config=config,
        prompt_digest=prompt.digest,
        audience=audience,
    )
    provenance = Provenance.load(tree.provenance_json)

    if provenance.should_skip("recap", key, force=force, root=tree.root):
        return {
            "status": "skipped",
            "session": session_id,
            "audience": audience,
            "path": str(tree.recap_draft),
            "usage": Usage().to_dict(),
            "warnings": ["recap already drafted for this key; use --force to re-run"],
            "next_steps": next_steps_for("recap", session_id=session_id, api_key_present=True),
        }

    system_prompt = prompt.render(
        lexicon_terms=lexicon_reference(lexicon),
        audience=audience,
        session_id=session_id,
    )
    data, usage, _attempts = structured_call(
        config=config,
        prompt_version=PROMPT_VERSION,
        system_prompt=system_prompt,
        user_content=_user_content(extraction),
        schema=RECAP_SCHEMA,
        client=client_for(config),
        schema_name="session_recap",
    )

    pool = evidence_pool(extraction)
    by_heading: dict[str, list[Bullet]] = {}
    dropped = 0
    for section in data.get("sections") or []:
        if not isinstance(section, Mapping):
            continue
        heading = section.get("heading")
        if heading not in SECTIONS:
            continue
        bullets, lost = build_bullets(
            section.get("bullets"), pool=pool, anchors=anchors, session_id=session_id
        )
        dropped += lost
        by_heading.setdefault(str(heading), []).extend(bullets)

    questions = owner_questions(extraction, anchors=anchors, session_id=session_id)
    entities = [
        str(row.get("canonical"))
        for row in (extraction.get("elements") or {}).get("entities") or []
        if isinstance(row, Mapping) and row.get("canonical")
    ]

    body: list[str] = [
        frontmatter(
            session_id=session_id,
            audience=audience,
            terms=search_terms(extraction),
            entities=entities[:24],
        ),
        "",
        f"# Сессия {session_id} — черновик пересказа",
        "",
    ]
    for heading in SECTIONS:
        body.append(f"## {heading}")
        bullets = by_heading.get(heading) or []
        body.extend(bullet.render() for bullet in bullets)
        if not bullets:
            body.append("_нет данных в выжимке_")
        body.append("")
    body.append(f"## {OWNER_SECTION}")
    if questions:
        body.extend(bullet.render() for bullet in questions)
    else:
        body.append("_ничего не требует решения владельца_")
    body.append("")
    body.append(provenance_footer(extraction, config=config, qa_line=qa_one_liner(tree)))
    body.append("")

    write_text(tree.recap_draft, "\n".join(body))

    warnings: list[str] = []
    if dropped:
        warnings.append(
            f"{dropped} bullet(s) were dropped because their citations did not resolve through "
            f"anchors.json; an unevidenced bullet is not written"
        )

    total_bullets = sum(len(rows) for rows in by_heading.values())
    provenance.mark_done(
        "recap",
        key,
        outputs=[tree.recap_draft],
        extra={
            "audience": audience,
            "bullets": total_bullets,
            "owner_questions": len(questions),
            "usage": usage.to_dict(),
        },
    )

    return {
        "status": "ok",
        "session": session_id,
        "audience": audience,
        "path": str(tree.recap_draft),
        "model": config.openai_model,
        "prompt_version": PROMPT_VERSION,
        "bullets": total_bullets,
        "bullets_dropped": dropped,
        "owner_questions": len(questions),
        "sections": {heading: len(by_heading.get(heading) or []) for heading in SECTIONS},
        "usage": usage.to_dict(),
        "warnings": warnings,
        "next_steps": next_steps_for("recap", session_id=session_id, api_key_present=True),
    }


def _user_content(extraction: Mapping[str, Any]) -> str:
    """The extraction, trimmed to what the recap may cite."""
    elements = extraction.get("elements")
    payload = {
        "session": extraction.get("session"),
        "elements": elements if isinstance(elements, Mapping) else {},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
