"""``record`` — assemble the layer-2 handoff. Deterministic, no LLM call.

DESIGN §4 and §6:

* Pure assembly. ``world_impact`` and ``needs_owner`` come out of ``extract``,
  which is precisely what lets ``record`` run without an API key: on a keyless
  machine it produces a valid, empty-collection record rather than fabricating
  the LLM-derived fields.
* Every ``evidence`` pointer is resolved through ``anchors.json`` into a full
  :class:`session_ingest.models.Evidence` — turn id, segment index, ``t0``,
  speaker, chunk and the ready Obsidian wikilink — and the result is checked
  with :func:`session_ingest.models.validate_record`. An element that lost its
  evidence fails validation instead of shipping.
* ``session.provenance`` carries the STT model, ``merge_gap_s``, the lexicon
  digest, the extract prompt version and the LLM model; ``dataset_digest`` is
  stamped because evidence links are valid only for the dataset they were
  produced against (DESIGN §6 rule 4).

This module also owns :class:`AnchorIndex`, the ``segment_i ↔ turn_id ↔ chunk``
bridge ``render`` writes and both ``record`` and ``recap`` read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from craig_stt_dataset import Dataset

from .adopt import load_session_link, resolve_active_dataset
from .config import SessionConfig
from .errors import SessionIngestError
from .models import (
    Evidence,
    ParticipantDict,
    RecordDict,
    empty_record,
    validate_record,
)
from .nextsteps import CLI, next_steps_for, step
from .paths import Roots, SessionTree
from .provenance import CompositeKey, Provenance, sha256_file, utc_now
from .segment import load_classifications
from .vaultfiles import Lexicon, Speakers, load_lexicon, load_speakers
from .writer import read_json, write_json

PROMPT_VERSION = "extract/1"

#: The seven element families of ``ttrpg.session-record/1``, in DESIGN §6 order.
COLLECTIONS: tuple[str, ...] = (
    "scenes",
    "events",
    "entities",
    "quests",
    "loot",
    "commitments",
    "threads",
)

UNKNOWN_SPEAKER = "unknown"


# ------------------------------------------------------------------ anchors


@dataclass(frozen=True, slots=True)
class Anchor:
    """One rendered segment: where it landed and which turn block it belongs to."""

    segment_i: int
    turn_id: str
    chunk: str
    t0: float


@dataclass(frozen=True, slots=True)
class AnchorIndex:
    """``anchors.json``, indexed both ways.

    ``by_turn`` keeps the *lowest* segment index for each turn — a turn is many
    segments, and its block id sits at its first one, so that is the segment an
    evidence pointer should name.
    """

    path: Path
    digest: str | None
    by_segment: Mapping[int, Anchor]
    by_turn: Mapping[str, Anchor]

    def evidence_for(self, *, turn_id: str, speaker: str, session_id: str) -> Evidence | None:
        anchor = self.by_turn.get(turn_id)
        if anchor is None:
            return None
        return Evidence.build(
            turn_id=anchor.turn_id,
            segment_i=anchor.segment_i,
            t0=anchor.t0,
            speaker=speaker or UNKNOWN_SPEAKER,
            chunk=anchor.chunk,
            session_id=session_id,
        )


def anchors_missing_error(path: Path, session_id: str) -> SessionIngestError:
    return SessionIngestError(
        f"{path} is missing: evidence pointers cannot be resolved into wikilinks without the "
        f"render step's ID bridge.",
        code="anchors_missing",
        detail={"path": str(path), "session": session_id},
        next_steps=[
            step(
                "render",
                "Render the transcript; it writes anchors.json alongside the chunks.",
                command=f"{CLI} render --session {session_id}",
            )
        ],
    )


def load_anchors(path: Path, *, session_id: str) -> AnchorIndex:
    """Read the ID bridge. Absence is a hard failure with the repair named."""
    if not path.is_file():
        raise anchors_missing_error(path, session_id)
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise SessionIngestError(f"{path} does not contain a JSON object", code="anchors_invalid")
    by_segment: dict[int, Anchor] = {}
    by_turn: dict[str, Anchor] = {}
    for raw_index, raw in payload.items():
        if not isinstance(raw, Mapping):
            continue
        try:
            segment_i = int(raw_index)
        except (TypeError, ValueError):
            continue
        turn_id = raw.get("turn_id")
        chunk = raw.get("chunk")
        t0 = raw.get("t0")
        if not isinstance(turn_id, str) or not isinstance(chunk, str):
            continue
        anchor = Anchor(
            segment_i=segment_i,
            turn_id=turn_id,
            chunk=chunk,
            t0=float(t0) if isinstance(t0, (int, float)) and not isinstance(t0, bool) else 0.0,
        )
        by_segment[segment_i] = anchor
        existing = by_turn.get(turn_id)
        if existing is None or segment_i < existing.segment_i:
            by_turn[turn_id] = anchor
    return AnchorIndex(path=path, digest=sha256_file(path), by_segment=by_segment, by_turn=by_turn)


# --------------------------------------------------------------- extraction


def load_extraction(path: Path) -> dict[str, Any] | None:
    """``extraction.json`` if it exists. Absent is legal — that is the keyless path."""
    if not path.is_file():
        return None
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise SessionIngestError(
            f"{path} does not contain a JSON object", code="extraction_invalid"
        )
    return payload


def _elements(extraction: Mapping[str, Any] | None, family: str) -> list[Mapping[str, Any]]:
    if extraction is None:
        return []
    elements = extraction.get("elements")
    if not isinstance(elements, Mapping):
        return []
    rows = elements.get(family)
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def resolve_evidence(
    raw: Any, anchors: AnchorIndex, *, session_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Turn extraction evidence rows into full evidence items. Unresolvable ids are reported."""
    resolved: list[dict[str, Any]] = []
    unresolved: list[str] = []
    if not isinstance(raw, list):
        return resolved, unresolved
    for entry in raw:
        if not isinstance(entry, Mapping):
            continue
        turn_id = entry.get("turn_id")
        if not isinstance(turn_id, str):
            continue
        speaker = entry.get("speaker")
        evidence = anchors.evidence_for(
            turn_id=turn_id,
            speaker=speaker if isinstance(speaker, str) else UNKNOWN_SPEAKER,
            session_id=session_id,
        )
        if evidence is None:
            unresolved.append(turn_id)
            continue
        resolved.append(dict(evidence.to_dict()))
    return resolved, unresolved


# ------------------------------------------------------------- participants


def _track_rows(qa: Mapping[str, Any] | None, dataset: Dataset) -> list[dict[str, Any]]:
    """Per-track speech from ``qa.json`` when it exists, else recomputed from the SDK."""
    if qa is not None:
        metrics = qa.get("metrics")
        if isinstance(metrics, Mapping):
            rows = metrics.get("per_track")
            if isinstance(rows, list) and rows:
                return [dict(row) for row in rows if isinstance(row, Mapping)]
    tracks = dataset.meta.tracks
    total = sum(track.speech_s for track in tracks)
    return [
        {
            "track": track.track,
            "user_id": track.user_id,
            "username": track.username,
            "speech_s": track.speech_s,
            "speech_share": (track.speech_s / total) if total else 0.0,
            "skipped": track.skipped,
        }
        for track in tracks
    ]


def build_participants(
    qa: Mapping[str, Any] | None, dataset: Dataset, speakers: Speakers
) -> tuple[list[ParticipantDict], list[str], list[str]]:
    """Participants with speech shares, plus the ids nobody has mapped yet.

    An unmapped id is *not* dropped — it spoke, so it is a participant — but its
    ``role`` falls back to ``guest`` and the id is listed in
    ``session.unmapped_participants`` so the guess is visible and correctable in
    ``_speakers.yaml`` rather than silently baked into the record.
    """
    participants: list[ParticipantDict] = []
    unmapped: list[str] = []
    warnings: list[str] = []
    for row in _track_rows(qa, dataset):
        if row.get("skipped"):
            continue
        user_id = row.get("user_id")
        if not isinstance(user_id, str) or not user_id:
            warnings.append(
                f"track {row.get('track')} has no discord user_id and is omitted from participants"
            )
            continue
        mapped = speakers.get(user_id)
        display = mapped.display() if mapped is not None else (row.get("username") or user_id)
        role = (mapped.role if mapped is not None else None) or "guest"
        if mapped is None or not mapped.role:
            unmapped.append(user_id)
        speech_s = row.get("speech_s")
        share = row.get("speech_share")
        participants.append(
            {
                "user_id": user_id,
                "display": str(display),
                "role": role,
                "speech_s": float(speech_s) if isinstance(speech_s, (int, float)) else 0.0,
                "speech_share": float(share) if isinstance(share, (int, float)) else 0.0,
            }
        )
    if unmapped:
        warnings.append(
            f"{len(unmapped)} speaking user id(s) have no role in _speakers.yaml and were "
            f"recorded as `guest`: {', '.join(unmapped)}"
        )
    return participants, unmapped, warnings


# ------------------------------------------------------------- play time


@dataclass(frozen=True, slots=True)
class PlayTime:
    """Play time and table-talk share, or the honest admission that they are unknown."""

    play_time_s: float | None
    table_talk_share: float | None
    speech_by_class: dict[str, float]
    classified_turns: int

    @property
    def known(self) -> bool:
        return self.play_time_s is not None and self.table_talk_share is not None


def compute_play_time(
    dataset: Dataset, *, merge_gap_s: float, classes: Mapping[str, str], duration_s: float
) -> PlayTime:
    """``table_talk_share`` over classified speech; ``play_time_s`` over wall clock.

    The share is speech-based (table talk ÷ all classified speech) because that
    is what the classification measures. Play time is then the wall-clock
    complement of it, which is the number DESIGN §6's example carries.
    """
    if not classes:
        return PlayTime(None, None, {}, 0)
    totals: dict[str, float] = {}
    classified = 0
    for turn in dataset.turns(merge_gap_s=merge_gap_s, drop_bleed=False):
        label = classes.get(turn.id)
        if label is None:
            continue
        classified += 1
        totals[label] = totals.get(label, 0.0) + max(0.0, turn.t1 - turn.t0)
    total_speech = sum(totals.values())
    if total_speech <= 0:
        return PlayTime(None, None, totals, classified)
    share = totals.get("table_talk", 0.0) / total_speech
    return PlayTime(
        play_time_s=max(0.0, duration_s * (1.0 - share)),
        table_talk_share=share,
        speech_by_class=totals,
        classified_turns=classified,
    )


# ----------------------------------------------------------------- lexicon


def lexicon_lookup(lexicon: Lexicon) -> dict[str, str]:
    """``casefolded canonical/display -> term id``. Exact equality only, never fuzzy."""
    mapping: dict[str, str] = {}
    for term in lexicon.terms:
        for form in term.canonical_forms():
            mapping.setdefault(form.strip().casefold(), term.id)
    return mapping


# --------------------------------------------------------------------- verb


def composite_key(
    *,
    dataset_digest: str | None,
    lexicon_digest: str | None,
    config: SessionConfig,
    extraction_digest: str | None,
    anchors_digest: str | None,
    qa_digest: str | None,
    class_digest: str | None,
    prompt_version: str,
) -> CompositeKey:
    return CompositeKey(
        dataset_digest=dataset_digest,
        lexicon_digest=lexicon_digest,
        prompt_version=prompt_version,
        knobs={
            "merge_gap_s": config.merge_gap_s,
            "extraction_digest": extraction_digest,
            "anchors_digest": anchors_digest,
            "qa_digest": qa_digest,
            "class_digest": class_digest,
        },
    )


def _digest_or_none(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


def _qa_one_liner(qa: Mapping[str, Any] | None) -> str | None:
    if qa is None:
        return None
    crossed = qa.get("thresholds_crossed")
    if not isinstance(crossed, list) or not crossed:
        return "QA: no configured threshold crossed"
    described = ", ".join(
        f"{entry.get('metric')}={entry.get('value')}"
        for entry in crossed
        if isinstance(entry, Mapping)
    )
    return f"QA: thresholds crossed — {described}"


def _copy(row: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    return {key: row.get(key) for key in keys}


def run(
    *,
    roots: Roots,
    config: SessionConfig,
    session_id: str,
    run: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Assemble and validate ``record.json``. Returns the ``--json`` payload."""
    tree: SessionTree = roots.session(session_id)
    recording, dataset = resolve_active_dataset(tree, run)
    link = load_session_link(tree)
    effective_run = run if run is not None else link.active_run

    anchors = load_anchors(tree.anchors_json, session_id=session_id)
    extraction = load_extraction(tree.extraction_json)
    qa_payload = read_json(tree.qa_json) if tree.qa_json.is_file() else None
    qa = qa_payload if isinstance(qa_payload, Mapping) else None
    lexicon = load_lexicon(roots.lexicon_file)
    speakers = load_speakers(roots.speakers_file)
    classes = load_classifications(tree.turns_class_jsonl)

    prompt_version = str((extraction or {}).get("prompt_version") or PROMPT_VERSION)
    key = composite_key(
        dataset_digest=recording.dataset_digest,
        lexicon_digest=lexicon.digest,
        config=config,
        extraction_digest=_digest_or_none(tree.extraction_json),
        anchors_digest=anchors.digest,
        qa_digest=_digest_or_none(tree.qa_json),
        class_digest=_digest_or_none(tree.turns_class_jsonl),
        prompt_version=prompt_version,
    )
    provenance = Provenance.load(tree.provenance_json)

    if provenance.should_skip("record", key, force=force, root=tree.root):
        existing = read_json(tree.record_json) if tree.record_json.is_file() else {}
        counts = {family: len(existing.get(family) or []) for family in COLLECTIONS}
        return {
            "status": "skipped",
            "session": session_id,
            "run": effective_run,
            "path": str(tree.record_json),
            "counts": counts,
            "warnings": ["record already assembled for this key; use --force to re-run"],
            "next_steps": next_steps_for(
                "record",
                session_id=session_id,
                api_key_present=config.api_key_present,
                run=effective_run,
            ),
        }

    warnings: list[str] = []
    if extraction is None:
        warnings.append(
            f"{tree.extraction_json.name} is absent — the record is structurally valid but has "
            f"no elements. Run `extract` (metered) to populate it."
        )

    meta = dataset.meta
    duration_s = float(meta.recording.duration_s or 0.0)
    play = compute_play_time(
        dataset, merge_gap_s=config.merge_gap_s, classes=classes, duration_s=duration_s
    )
    # Declared unknowns, not waived errors: `validate_record` reads this list and
    # accepts a null for exactly the keys named in it (see models._validate_session_block).
    missing: list[str] = [] if play.known else ["play_time_s", "table_talk_share"]
    if not play.known:
        warnings.append(
            f"{tree.turns_class_jsonl.name} is absent or empty — play_time_s and "
            f"table_talk_share are reported as null and listed in session.missing"
        )

    participants, unmapped, participant_warnings = build_participants(qa, dataset, speakers)
    warnings.extend(participant_warnings)

    base: RecordDict = empty_record(
        session_id=session_id,
        recording_id=recording.recording_id,
        dataset_digest=recording.dataset_digest or "",
        transcript_root=tree.transcript_root_rel,
        duration_s=duration_s,
        started_at=meta.recording.start_time.isoformat() if meta.recording.start_time else None,
        language=meta.provenance.language,
        participants=participants,
        play_time_s=play.play_time_s,
        table_talk_share=play.table_talk_share,
        stt_model=meta.provenance.model or "unknown",
        merge_gap_s=config.merge_gap_s,
        lexicon_digest=lexicon.digest,
        prompt_version=prompt_version,
        llm_model=(extraction or {}).get("model"),
    )
    # Assembled as a plain dict from here: the session block gains three reporting
    # keys the TypedDict does not declare.
    record: dict[str, Any] = dict(base)
    session_block: dict[str, Any] = dict(base["session"])
    session_block["missing"] = missing
    session_block["unmapped_participants"] = unmapped
    session_block["speech_by_class"] = play.speech_by_class
    record["session"] = session_block

    unresolved: list[dict[str, Any]] = []
    lexicon_ids = lexicon_lookup(lexicon)

    def evidence_of(row: Mapping[str, Any], family: str, element_id: Any) -> list[dict[str, Any]]:
        resolved, missing_ids = resolve_evidence(
            row.get("evidence"), anchors, session_id=session_id
        )
        if missing_ids:
            unresolved.append({"family": family, "id": element_id, "turn_ids": missing_ids})
        return resolved

    for row in _elements(extraction, "scenes"):
        record["scenes"].append(
            {
                **_copy(row, ("id", "title", "location", "t0", "t1", "summary", "participants")),
                "confidence": row.get("confidence"),
                "evidence": evidence_of(row, "scenes", row.get("id")),
            }
        )
    for row in _elements(extraction, "events"):
        record["events"].append(
            {
                **_copy(
                    row,
                    (
                        "id",
                        "scene",
                        "kind",
                        "summary",
                        "outcome",
                        "world_impact",
                        "needs_owner",
                        "confidence",
                    ),
                ),
                "evidence": evidence_of(row, "events", row.get("id")),
            }
        )
    for row in _elements(extraction, "entities"):
        canonical = row.get("canonical")
        term_id = (
            lexicon_ids.get(canonical.strip().casefold()) if isinstance(canonical, str) else None
        )
        record["entities"].append(
            {
                **_copy(row, ("id", "kind", "name_as_heard", "canonical", "first_mention_t")),
                "lexicon_term_id": term_id,
                # M1: resolving a canonical name to a vault note is the tracker's job.
                "vault_note": None,
                "confidence": row.get("confidence"),
                "evidence": evidence_of(row, "entities", row.get("id")),
            }
        )
    for row in _elements(extraction, "quests"):
        record["quests"].append(
            {
                **_copy(row, ("id", "name", "status_change", "detail")),
                "confidence": row.get("confidence"),
                "evidence": evidence_of(row, "quests", row.get("id")),
            }
        )
    for row in _elements(extraction, "loot"):
        record["loot"].append(
            {
                **_copy(row, ("item", "recipient", "quantity")),
                "confidence": row.get("confidence"),
                "evidence": evidence_of(row, "loot", row.get("item")),
            }
        )
    for row in _elements(extraction, "commitments"):
        record["commitments"].append(
            {
                **_copy(row, ("who", "promise", "deadline")),
                "confidence": row.get("confidence"),
                "evidence": evidence_of(row, "commitments", row.get("who")),
            }
        )
    for row in _elements(extraction, "threads"):
        record["threads"].append(
            {
                **_copy(row, ("question", "status")),
                "confidence": row.get("confidence"),
                "evidence": evidence_of(row, "threads", row.get("question")),
            }
        )

    if unresolved:
        warnings.append(
            f"{len(unresolved)} element(s) cite a turn id that anchors.json does not know; "
            f"those pointers were dropped. Re-render if the transcript is stale."
        )

    errors = validate_record(record)
    if errors:
        raise SessionIngestError(
            f"record.json for {session_id} does not satisfy ttrpg.session-record/1: "
            + "; ".join(errors[:10]),
            code="record_invalid",
            detail={"errors": errors, "unresolved_evidence": unresolved[:20]},
        )

    write_json(tree.record_json, record)
    counts = {family: len(record[family]) for family in COLLECTIONS}
    provenance.mark_done(
        "record",
        key,
        outputs=[tree.record_json],
        extra={"run": effective_run, "counts": counts, "generated_at": utc_now()},
    )

    owner_questions = [
        row
        for row in record["events"]
        if row.get("world_impact") != "none" or row.get("needs_owner")
    ]
    return {
        "status": "ok",
        "session": session_id,
        "run": effective_run,
        "path": str(tree.record_json),
        "schema": record["schema"],
        "counts": counts,
        "evidence_items": sum(
            len(row.get("evidence") or []) for family in COLLECTIONS for row in record[family]
        ),
        "unresolved_evidence": unresolved[:20],
        "needs_owner": len(owner_questions),
        "play_time_s": play.play_time_s,
        "table_talk_share": play.table_talk_share,
        "missing": missing,
        "qa": _qa_one_liner(qa),
        "warnings": warnings,
        "next_steps": next_steps_for(
            "record",
            session_id=session_id,
            api_key_present=config.api_key_present,
            run=effective_run,
        ),
    }
