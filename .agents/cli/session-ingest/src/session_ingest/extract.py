"""``extract`` — map-reduce the play into evidence-bearing elements. Metered.

DESIGN §4, §6 and PLAN step 6:

* Input is ``turns(drop_bleed=True)`` — a bleed-suspect turn is probably someone
  else's voice on this track, and a misattributed line poisons every ``who``
  field downstream. ``--keep-bleed`` is the escape hatch. Turns that ``segment``
  classed ``table_talk`` are removed on top of that; when ``turns.class.jsonl``
  is absent the stage proceeds and says so.
* ~``window_minutes`` windows with ``window_overlap_pct`` overlap, split on turn
  boundaries only. A window that fails after retries is **reported** in
  ``failed_windows``, never dropped: a hole in the extraction is a hole in the
  session record nobody sees.
* Every element carries ``evidence[]`` and a ``confidence``; events also carry
  ``world_impact`` and ``needs_owner``. There is no later LLM pass to add them,
  which is exactly what lets ``record`` be deterministic and keyless.
* The reduce is **pure Python**: exact, case-folded string equality on one key
  per family, applied only inside the overlap zone between two adjacent windows.
  No similarity, no distance, no fuzz — two scenes are the same scene because
  the model named them identically in the region it saw twice, and for no other
  reason.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .adopt import load_session_link, resolve_active_dataset
from .config import SessionConfig
from .llm import (
    Usage,
    client_for,
    lexicon_reference,
    load_prompt,
    map_reduce,
    metered_skip,
    nullable,
    object_schema,
)
from .models import ENTITY_KINDS, EVENT_KINDS, QUEST_STATUS_CHANGES, WORLD_IMPACTS
from .nextsteps import next_steps_for
from .paths import Roots, SessionTree
from .provenance import CompositeKey, Provenance, utc_now
from .segment import (
    TurnRow,
    TurnWindow,
    build_windows,
    class_file_digest,
    collect_turns,
    load_classifications,
    overlap_ids,
)
from .vaultfiles import load_lexicon, load_speakers
from .writer import write_json

PROMPT_VERSION = "extract/1"
EXTRACTION_SCHEMA = "ttrpg.session-extraction/1"

#: Element families in ``record.json`` order (DESIGN §6).
FAMILIES: tuple[str, ...] = (
    "scenes",
    "events",
    "entities",
    "quests",
    "loot",
    "commitments",
    "threads",
)

#: Families whose elements get a session-local id, and its prefix.
ID_PREFIXES: dict[str, str] = {"scenes": "s", "events": "e", "entities": "n", "quests": "q"}

#: Fields that must be a non-empty string for the element to be accepted.
REQUIRED_STRINGS: dict[str, tuple[str, ...]] = {
    "scenes": ("title", "summary"),
    "events": ("kind", "summary", "world_impact"),
    "entities": ("kind", "name_as_heard", "canonical"),
    "quests": ("name", "status_change"),
    "loot": ("item",),
    "commitments": ("who", "promise"),
    "threads": ("question", "status"),
}

#: Enum-constrained fields, checked again after the model answered.
ENUMS: dict[str, dict[str, frozenset[str]]] = {
    "events": {"kind": EVENT_KINDS, "world_impact": WORLD_IMPACTS},
    "entities": {"kind": ENTITY_KINDS},
    "quests": {"status_change": QUEST_STATUS_CHANGES},
    "threads": {"status": frozenset({"open"})},
}

#: Scalar fields copied verbatim from the model's answer, per family.
CARRIED: dict[str, tuple[str, ...]] = {
    "scenes": ("title", "location", "summary", "participants"),
    "events": ("kind", "summary", "outcome", "world_impact", "needs_owner"),
    "entities": ("kind", "name_as_heard", "canonical"),
    "quests": ("name", "status_change", "detail"),
    "loot": ("item", "recipient", "quantity"),
    "commitments": ("who", "promise", "deadline"),
    "threads": ("question", "status"),
}


def dedupe_key(family: str, fields: Mapping[str, Any]) -> str:
    """The one string two elements must share exactly (case-folded) to be merged."""
    if family == "scenes":
        parts = [fields.get("title")]
    elif family == "events":
        parts = [fields.get("summary")]
    elif family == "entities":
        parts = [fields.get("canonical") or fields.get("name_as_heard")]
    elif family == "quests":
        parts = [fields.get("name")]
    elif family == "loot":
        parts = [fields.get("item"), fields.get("recipient")]
    elif family == "commitments":
        parts = [fields.get("who"), fields.get("promise")]
    else:
        parts = [fields.get("question")]
    return "|".join(
        (part or "").strip().casefold() if isinstance(part, str) else "" for part in parts
    )


# ------------------------------------------------------------------- schema


def _element(properties: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(properties)
    payload["evidence"] = {"type": "array", "items": {"type": "string"}}
    payload["confidence"] = {"type": "number"}
    return object_schema(payload)


def _array(properties: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": _element(properties)}


EXTRACT_SCHEMA = object_schema(
    {
        "scenes": _array(
            {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "location": nullable({"type": "string"}),
                "summary": {"type": "string"},
                "participants": {"type": "array", "items": {"type": "string"}},
            }
        ),
        "events": _array(
            {
                "scene_ref": nullable({"type": "string"}),
                "kind": {"type": "string", "enum": sorted(EVENT_KINDS)},
                "summary": {"type": "string"},
                "outcome": nullable({"type": "string"}),
                "world_impact": {"type": "string", "enum": sorted(WORLD_IMPACTS)},
                "needs_owner": {"type": "boolean"},
            }
        ),
        "entities": _array(
            {
                "kind": {"type": "string", "enum": sorted(ENTITY_KINDS)},
                "name_as_heard": {"type": "string"},
                "canonical": {"type": "string"},
            }
        ),
        "quests": _array(
            {
                "name": {"type": "string"},
                "status_change": {"type": "string", "enum": sorted(QUEST_STATUS_CHANGES)},
                "detail": nullable({"type": "string"}),
            }
        ),
        "loot": _array(
            {
                "item": {"type": "string"},
                "recipient": nullable({"type": "string"}),
                "quantity": nullable({"type": "string"}),
            }
        ),
        "commitments": _array(
            {
                "who": {"type": "string"},
                "promise": {"type": "string"},
                "deadline": nullable({"type": "string"}),
            }
        ),
        "threads": _array(
            {
                "question": {"type": "string"},
                "status": {"type": "string", "enum": ["open"]},
            }
        ),
    }
)


# ------------------------------------------------------------------ reduce


@dataclass(slots=True)
class Element:
    """One accepted element, before ids are assigned."""

    family: str
    key: str
    fields: dict[str, Any]
    confidence: float
    evidence: list[dict[str, Any]]
    turn_ids: set[str]
    t0: float
    t1: float
    windows: set[int] = field(default_factory=set)
    local_ids: set[tuple[int, str]] = field(default_factory=set)
    merged: int = 0

    def absorb(self, other: Element) -> None:
        """Union the evidence; every scalar field stays as the first window wrote it."""
        known = {entry["turn_id"] for entry in self.evidence}
        for entry in other.evidence:
            if entry["turn_id"] not in known:
                self.evidence.append(entry)
                known.add(entry["turn_id"])
        self.evidence.sort(key=lambda entry: (entry["t0"], entry["turn_id"]))
        self.turn_ids |= other.turn_ids
        self.t0 = min(self.t0, other.t0)
        self.t1 = max(self.t1, other.t1)
        self.windows |= other.windows
        self.local_ids |= other.local_ids
        self.merged += 1


def _clamp(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _evidence_rows(
    raw: Any, by_id: Mapping[str, TurnRow]
) -> tuple[list[dict[str, Any]], set[str], list[str]]:
    """Keep only turn ids that were actually in the window. Unknown ids are dropped."""
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    unknown: list[str] = []
    if not isinstance(raw, list):
        return rows, ids, unknown
    for item in raw:
        if not isinstance(item, str):
            continue
        turn = by_id.get(item)
        if turn is None:
            unknown.append(item)
            continue
        if item in ids:
            continue
        ids.add(item)
        rows.append(
            {
                "turn_id": turn.turn_id,
                "t0": turn.t0,
                "speaker": turn.speaker,
                "segment_indices": list(turn.segment_indices),
            }
        )
    rows.sort(key=lambda entry: (entry["t0"], entry["turn_id"]))
    return rows, ids, unknown


def _accept(
    family: str, raw: Mapping[str, Any], window: TurnWindow, by_id: Mapping[str, TurnRow]
) -> tuple[Element | None, dict[str, Any] | None]:
    """Validate one raw element. Returns either the element or a rejection row."""
    for key in REQUIRED_STRINGS[family]:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            return None, {"window": window.index, "family": family, "reason": f"{key} is empty"}
    for key, allowed in ENUMS.get(family, {}).items():
        if raw.get(key) not in allowed:
            return None, {
                "window": window.index,
                "family": family,
                "reason": f"{key}={raw.get(key)!r} is not one of {sorted(allowed)}",
            }
    if family == "events" and not isinstance(raw.get("needs_owner"), bool):
        return None, {
            "window": window.index,
            "family": family,
            "reason": "needs_owner is not a boolean",
        }

    evidence, turn_ids, unknown = _evidence_rows(raw.get("evidence"), by_id)
    if not evidence:
        return None, {
            "window": window.index,
            "family": family,
            "reason": "no evidence resolvable to a turn in this window",
            "unknown_turn_ids": unknown[:10],
        }

    fields = {key: raw.get(key) for key in CARRIED[family]}
    if family == "scenes" and not isinstance(fields.get("participants"), list):
        fields["participants"] = []

    turns = [by_id[turn_id] for turn_id in turn_ids]
    element = Element(
        family=family,
        key=dedupe_key(family, fields),
        fields=fields,
        confidence=_clamp(raw.get("confidence")),
        evidence=evidence,
        turn_ids=turn_ids,
        t0=min(turn.t0 for turn in turns),
        t1=max(turn.t1 for turn in turns),
        windows={window.index},
    )
    if family == "scenes" and isinstance(raw.get("id"), str):
        element.local_ids.add((window.index, raw["id"]))
    return element, None


def reduce_windows(
    windows: Sequence[TurnWindow], answers: Iterable[tuple[int, dict[str, Any]]]
) -> tuple[dict[str, list[Element]], list[dict[str, Any]], dict[tuple[int, str], Element]]:
    """Merge per-window answers into one element set.

    Deduplication is confined to the overlap zone between two *adjacent* windows:
    an element is a merge candidate only when it cites a turn both windows saw.
    Two identically-named scenes an hour apart therefore stay two scenes, which
    is what you want — a party can revisit the same tavern twice in an evening.
    """
    by_index = {window.index: window for window in windows}
    payloads = dict(sorted(answers, key=lambda pair: pair[0]))

    emitted: dict[str, list[Element]] = {family: [] for family in FAMILIES}
    per_window: dict[int, list[Element]] = {window.index: [] for window in windows}
    rejected: list[dict[str, Any]] = []
    scene_by_local: dict[tuple[int, str], Element] = {}
    event_scene_refs: list[tuple[Element, int, str]] = []

    for index in sorted(payloads):
        window = by_index.get(index)
        if window is None:
            continue
        payload = payloads[index]
        by_id = window.by_id()
        zone: set[str] = set()
        candidates: dict[tuple[str, str], Element] = {}
        if index > 0 and (index - 1) in by_index:
            zone = overlap_ids(by_index[index - 1], window)
            if zone:
                for previous in per_window.get(index - 1, []):
                    if previous.turn_ids & zone:
                        candidates.setdefault((previous.family, previous.key), previous)

        for family in FAMILIES:
            rows = payload.get(family)
            if not isinstance(rows, list):
                continue
            for raw in rows:
                if not isinstance(raw, dict):
                    rejected.append(
                        {"window": index, "family": family, "reason": "element is not an object"}
                    )
                    continue
                element, rejection = _accept(family, raw, window, by_id)
                if element is None:
                    if rejection is not None:
                        rejected.append(rejection)
                    continue

                target = candidates.get((family, element.key))
                if target is not None and zone and element.turn_ids & zone:
                    target.absorb(element)
                    per_window[index].append(target)
                    merged_into = target
                else:
                    emitted[family].append(element)
                    per_window[index].append(element)
                    merged_into = element

                if family == "scenes":
                    for local in merged_into.local_ids | element.local_ids:
                        scene_by_local[local] = merged_into
                if family == "events" and isinstance(raw.get("scene_ref"), str):
                    event_scene_refs.append((merged_into, index, raw["scene_ref"]))

    for event, window_index, local_id in event_scene_refs:
        scene = scene_by_local.get((window_index, local_id))
        if scene is not None:
            event.fields.setdefault("_scene", scene)

    emitted["scenes"].sort(key=lambda element: (element.t0, element.key))
    return emitted, rejected, scene_by_local


def assign_ids(emitted: Mapping[str, list[Element]]) -> dict[str, list[dict[str, Any]]]:
    """Session-local ids in output order, then the final serialisable shapes."""
    ids: dict[int, str] = {}
    for family, prefix in ID_PREFIXES.items():
        for position, element in enumerate(emitted[family], start=1):
            ids[id(element)] = f"{prefix}{position}"

    out: dict[str, list[dict[str, Any]]] = {}
    for family in FAMILIES:
        rows: list[dict[str, Any]] = []
        for element in emitted[family]:
            payload: dict[str, Any] = {}
            if family in ID_PREFIXES:
                payload["id"] = ids[id(element)]
            for key in CARRIED[family]:
                payload[key] = element.fields.get(key)
            if family == "scenes":
                payload["t0"] = element.t0
                payload["t1"] = element.t1
            if family == "events":
                scene = element.fields.get("_scene")
                payload["scene"] = ids.get(id(scene)) if isinstance(scene, Element) else None
            if family == "entities":
                payload["first_mention_t"] = element.t0
            payload["confidence"] = element.confidence
            payload["merged_from_overlap"] = element.merged
            payload["evidence"] = element.evidence
            rows.append(payload)
        out[family] = rows
    return out


# --------------------------------------------------------------------- verb


def composite_key(
    *,
    dataset_digest: str | None,
    lexicon_digest: str | None,
    speakers_digest: str | None,
    config: SessionConfig,
    prompt_digest: str,
    keep_bleed: bool,
    class_digest: str | None,
) -> CompositeKey:
    return CompositeKey(
        dataset_digest=dataset_digest,
        lexicon_digest=lexicon_digest,
        speakers_digest=speakers_digest,
        prompt_version=PROMPT_VERSION,
        model=config.openai_model,
        knobs={
            "window_minutes": config.window_minutes,
            "window_overlap_pct": config.window_overlap_pct,
            "merge_gap_s": config.merge_gap_s,
            "keep_bleed": keep_bleed,
            "prompt_digest": prompt_digest,
            "class_digest": class_digest,
        },
    )


def run(
    *,
    roots: Roots,
    config: SessionConfig,
    session_id: str,
    run: int | None = None,
    keep_bleed: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Map-reduce the session into ``extraction.json``. Returns the ``--json`` payload."""
    skipped = metered_skip(config, "extract")
    if skipped is not None:
        return skipped

    tree: SessionTree = roots.session(session_id)
    recording, dataset = resolve_active_dataset(tree, run)
    link = load_session_link(tree)
    effective_run = run if run is not None else link.active_run

    lexicon = load_lexicon(roots.lexicon_file)
    speakers = load_speakers(roots.speakers_file)
    prompt = load_prompt(PROMPT_VERSION)
    class_digest = class_file_digest(tree)

    key = composite_key(
        dataset_digest=recording.dataset_digest,
        lexicon_digest=lexicon.digest,
        speakers_digest=speakers.digest,
        config=config,
        prompt_digest=prompt.digest,
        keep_bleed=keep_bleed,
        class_digest=class_digest,
    )
    provenance = Provenance.load(tree.provenance_json)
    output = tree.extraction_json

    if provenance.should_skip("extract", key, force=force, root=tree.root):
        return {
            "status": "skipped",
            "session": session_id,
            "run": effective_run,
            "path": str(output),
            "usage": Usage().to_dict(),
            "warnings": ["already extracted for this key; use --force to re-run"],
            "next_steps": next_steps_for(
                "extract", session_id=session_id, api_key_present=True, run=effective_run
            ),
        }

    warnings: list[str] = []
    turns = collect_turns(
        dataset,
        merge_gap_s=config.merge_gap_s,
        drop_bleed=not keep_bleed,
        speakers=speakers,
    )
    total_turns = len(turns)

    classifications = load_classifications(tree.turns_class_jsonl)
    if classifications:
        kept = [turn for turn in turns if classifications.get(turn.turn_id) != "table_talk"]
        excluded = total_turns - len(kept)
        turns = kept
        table_talk_filter: dict[str, Any] = {
            "applied": True,
            "path": str(tree.turns_class_jsonl),
            "excluded_turns": excluded,
        }
    else:
        table_talk_filter = {
            "applied": False,
            "reason": f"{tree.turns_class_jsonl.name} is absent; every turn was considered",
        }
        warnings.append(
            f"{tree.turns_class_jsonl.name} is absent — table talk was not filtered out. "
            f"Run `segment` first for a cleaner extraction."
        )

    windows = build_windows(
        turns, window_minutes=config.window_minutes, overlap_pct=config.window_overlap_pct
    )
    reference = lexicon_reference(lexicon)

    def system_for(window: TurnWindow) -> str:
        return prompt.render(
            lexicon_terms=reference,
            window_index=str(window.index + 1),
            window_count=str(len(windows)),
            window_span=window.span,
        )

    result = map_reduce(
        config=config,
        prompt_version=PROMPT_VERSION,
        windows=windows,
        system_prompt=system_for,
        schema=EXTRACT_SCHEMA,
        render=lambda window: window.render(),
        client=client_for(config),
        schema_name="session_extraction",
    )

    emitted, rejected, _ = reduce_windows(windows, result.succeeded())
    elements = assign_ids(emitted)

    if result.failed_windows():
        warnings.append(
            f"{len(result.failed_windows())} window(s) failed after retries and are reported "
            f"in `failed_windows`; the extraction has a hole there"
        )
    if rejected:
        warnings.append(
            f"{len(rejected)} element(s) were rejected — most often for citing a turn id that "
            f"was not in the window. A claim without evidence is a bug, so they are not kept."
        )

    payload = {
        "schema": EXTRACTION_SCHEMA,
        "session": session_id,
        "run": effective_run,
        "generated_at": utc_now(),
        "dataset_digest": recording.dataset_digest,
        "lexicon_digest": lexicon.digest,
        "speakers_digest": speakers.digest,
        "prompt_version": PROMPT_VERSION,
        "prompt_digest": prompt.digest,
        "model": config.openai_model,
        "merge_gap_s": config.merge_gap_s,
        "window_minutes": config.window_minutes,
        "window_overlap_pct": config.window_overlap_pct,
        "keep_bleed": keep_bleed,
        "table_talk_filter": table_talk_filter,
        "turns_considered": len(turns),
        "turns_total": total_turns,
        "windows": len(windows),
        "elements": elements,
        "failed_windows": result.failed_windows(),
        "rejected_elements": rejected,
        "usage": result.usage.to_dict(),
    }
    write_json(output, payload)

    provenance.mark_done(
        "extract",
        key,
        outputs=[output],
        extra={
            "run": effective_run,
            "windows": len(windows),
            "elements": {family: len(rows) for family, rows in elements.items()},
            "failed_windows": len(result.failed_windows()),
            "usage": result.usage.to_dict(),
        },
    )

    return {
        "status": "ok",
        "session": session_id,
        "run": effective_run,
        "path": str(output),
        "model": config.openai_model,
        "prompt_version": PROMPT_VERSION,
        "windows": len(windows),
        "turns_considered": len(turns),
        "turns_total": total_turns,
        "table_talk_filter": table_talk_filter,
        "elements": {family: len(rows) for family, rows in elements.items()},
        "failed_windows": result.failed_windows(),
        "rejected_elements": rejected[:50],
        "rejected_count": len(rejected),
        "usage": result.usage.to_dict(),
        "warnings": warnings,
        "next_steps": next_steps_for(
            "extract", session_id=session_id, api_key_present=True, run=effective_run
        ),
    }
