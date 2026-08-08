"""``segment`` — per-turn IC/OOC classification. Metered.

DESIGN §4 and RESEARCH §5:

* Schema-first structured output, one enum per turn:
  ``in_character | table_talk | mechanics | ambiguous`` plus a confidence.
  The ``ambiguous`` bucket is kept deliberately — tone-of-voice cues are lost in
  text, and forcing a binary choice manufactures false confidence.
* Turn-boundary linear windows (~``window_minutes``) with ``window_overlap_pct``
  overlap; a turn seen in two windows keeps its **first** classification. Dumb
  turn-aware chunking on purpose: semantic segmentation was measured not to beat
  it.
* Writes ``turns.class.jsonl``, one line per classified turn.

This module also owns the turn-windowing machinery (:class:`TurnRow`,
:class:`TurnWindow`, :func:`build_windows`) that ``extract`` and ``glossary``
import. One implementation, because three stages that disagree about where a
window ends would disagree about which turns were considered at all.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from craig_stt_dataset import Dataset

from .adopt import load_session_link, resolve_active_dataset
from .config import SessionConfig
from .errors import SessionIngestError
from .llm import (
    Usage,
    client_for,
    lexicon_reference,
    load_prompt,
    map_reduce,
    metered_skip,
    object_schema,
)
from .models import TURN_CLASSES
from .nextsteps import next_steps_for
from .paths import Roots, SessionTree
from .provenance import CompositeKey, Provenance, sha256_file, utc_now
from .vaultfiles import Speakers, load_lexicon, load_speakers
from .writer import write_text

PROMPT_VERSION = "segment/1"

#: Overlap is capped so a pathological setting cannot make windows stop advancing.
MAX_OVERLAP_PCT = 90


# ------------------------------------------------------------ turns & windows


@dataclass(frozen=True, slots=True)
class TurnRow:
    """One turn, with the speaker already resolved to a human label."""

    turn_id: str
    t0: float
    t1: float
    track: int
    user_id: str | None
    speaker: str
    text: str
    segment_indices: tuple[int, ...]
    bleed_suspect: bool

    @property
    def duration_s(self) -> float:
        return max(0.0, self.t1 - self.t0)

    def render(self) -> str:
        return f"[{clock(self.t0)}] ({self.turn_id}) {self.speaker}: {self.text}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "t0": self.t0,
            "t1": self.t1,
            "speaker": self.speaker,
            "segment_indices": list(self.segment_indices),
        }


@dataclass(frozen=True, slots=True)
class TurnWindow:
    """A run of whole turns. Windows never split a turn — evidence ids must stay whole."""

    index: int
    turns: tuple[TurnRow, ...]

    @property
    def t0(self) -> float:
        return self.turns[0].t0 if self.turns else 0.0

    @property
    def t1(self) -> float:
        return max((turn.t1 for turn in self.turns), default=0.0)

    @property
    def span(self) -> str:
        return f"{clock(self.t0)}–{clock(self.t1)}"

    def turn_ids(self) -> set[str]:
        return {turn.turn_id for turn in self.turns}

    def by_id(self) -> dict[str, TurnRow]:
        return {turn.turn_id: turn for turn in self.turns}

    def render(self) -> str:
        return "\n".join(turn.render() for turn in self.turns)


def clock(seconds: float) -> str:
    """``hh:mm:ss`` — the same form the rendered transcript uses."""
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def speaker_label(
    *, user_id: str | None, username: str | None, track: int, speakers: Speakers
) -> str:
    """Best available human label, never invented.

    ``_speakers.yaml`` first, then the Discord handle, then the raw id, then the
    track number. An unmapped speaker is reported as what the recording knows,
    not guessed at from what they said.
    """
    mapped = speakers.get(user_id)
    if mapped is not None:
        return mapped.display()
    if username:
        return username
    if user_id:
        return user_id
    return f"track {track}"


def collect_turns(
    dataset: Dataset, *, merge_gap_s: float, drop_bleed: bool, speakers: Speakers
) -> list[TurnRow]:
    """Read turns through the SDK — never a local re-implementation (CONTRACT rule 1)."""
    rows: list[TurnRow] = []
    for turn in dataset.turns(merge_gap_s=merge_gap_s, drop_bleed=drop_bleed):
        rows.append(
            TurnRow(
                turn_id=turn.id,
                t0=turn.t0,
                t1=turn.t1,
                track=turn.track,
                user_id=turn.user_id,
                speaker=speaker_label(
                    user_id=turn.user_id,
                    username=turn.username,
                    track=turn.track,
                    speakers=speakers,
                ),
                text=turn.text,
                segment_indices=tuple(turn.segment_indices),
                bleed_suspect=turn.bleed_suspect,
            )
        )
    return rows


def build_windows(
    turns: Sequence[TurnRow], *, window_minutes: int, overlap_pct: int = 0
) -> list[TurnWindow]:
    """Split turns into ~``window_minutes`` windows that overlap by ``overlap_pct``.

    Boundaries land on turn boundaries by construction: a window is a slice of
    the turn list, never of the text. The next window starts at the first turn
    inside the trailing overlap zone, and always advances at least one turn, so
    a long single turn cannot stall the walk.
    """
    if not turns:
        return []
    span = max(1.0, float(window_minutes) * 60.0)
    overlap = span * max(0, min(overlap_pct, MAX_OVERLAP_PCT)) / 100.0

    windows: list[TurnWindow] = []
    start = 0
    while start < len(turns):
        window_t0 = turns[start].t0
        end = start
        while end < len(turns) and turns[end].t0 < window_t0 + span:
            end += 1
        windows.append(TurnWindow(index=len(windows), turns=tuple(turns[start:end])))
        if end >= len(turns):
            break
        threshold = window_t0 + span - overlap
        next_start = end
        for index in range(start, end):
            if turns[index].t0 >= threshold:
                next_start = index
                break
        start = max(next_start, start + 1)
    return windows


def overlap_ids(previous: TurnWindow, current: TurnWindow) -> set[str]:
    """Turn ids shared by two adjacent windows — the only zone dedup may act in."""
    return previous.turn_ids() & current.turn_ids()


# ------------------------------------------------------------------- schema

TURN_CLASS_SCHEMA = object_schema(
    {
        "turns": {
            "type": "array",
            "items": object_schema(
                {
                    "turn_id": {"type": "string"},
                    "class": {"type": "string", "enum": sorted(TURN_CLASSES)},
                    "confidence": {"type": "number"},
                }
            ),
        }
    }
)


# --------------------------------------------------------------------- verb


@dataclass(frozen=True, slots=True)
class Classification:
    turn_id: str
    turn_class: str
    confidence: float

    def to_json_line(self) -> str:
        return json.dumps(
            {"turn_id": self.turn_id, "class": self.turn_class, "confidence": self.confidence},
            ensure_ascii=False,
        )


def _clamp(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def reduce_classifications(
    windows: Sequence[TurnWindow], answers: Iterable[tuple[int, dict[str, Any]]]
) -> tuple[dict[str, Classification], list[dict[str, Any]]]:
    """First window wins; unknown turn ids are rejected, not trusted.

    The guardrail is the whole point of the no-heuristics rule read the right way
    round: the model decides what a turn *is*, and this function checks only that
    it answered about a turn that was actually in front of it.
    """
    known: dict[int, set[str]] = {window.index: window.turn_ids() for window in windows}
    accepted: dict[str, Classification] = {}
    rejected: list[dict[str, Any]] = []

    for window_index, payload in sorted(answers, key=lambda pair: pair[0]):
        rows = payload.get("turns")
        if not isinstance(rows, list):
            rejected.append({"window": window_index, "reason": "no `turns` array in the answer"})
            continue
        for row in rows:
            if not isinstance(row, dict):
                rejected.append({"window": window_index, "reason": "row is not an object"})
                continue
            turn_id = row.get("turn_id")
            turn_class = row.get("class")
            if not isinstance(turn_id, str) or turn_id not in known.get(window_index, set()):
                rejected.append(
                    {"window": window_index, "turn_id": turn_id, "reason": "turn id not in window"}
                )
                continue
            if not isinstance(turn_class, str) or turn_class not in TURN_CLASSES:
                rejected.append(
                    {"window": window_index, "turn_id": turn_id, "reason": "unknown class"}
                )
                continue
            if turn_id in accepted:
                continue  # overlap zone: the earlier window already answered
            accepted[turn_id] = Classification(
                turn_id=turn_id, turn_class=turn_class, confidence=_clamp(row.get("confidence"))
            )
    return accepted, rejected


def distribution(classifications: Iterable[Classification]) -> dict[str, int]:
    counts = dict.fromkeys(sorted(TURN_CLASSES), 0)
    for entry in classifications:
        counts[entry.turn_class] += 1
    return counts


def load_classifications(path: Path) -> dict[str, str]:
    """Read ``turns.class.jsonl`` into ``turn_id -> class``. Absent file yields ``{}``."""
    if not path.is_file():
        return {}
    mapping: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SessionIngestError(
                f"{path} is not valid JSONL: {exc}", code="turn_class_invalid"
            ) from exc
        if isinstance(row, dict):
            turn_id = row.get("turn_id")
            turn_class = row.get("class")
            if isinstance(turn_id, str) and isinstance(turn_class, str):
                mapping[turn_id] = turn_class
    return mapping


def composite_key(
    *,
    dataset_digest: str | None,
    lexicon_digest: str | None,
    speakers_digest: str | None,
    config: SessionConfig,
    prompt_digest: str,
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
            "prompt_digest": prompt_digest,
        },
    )


def run(
    *,
    roots: Roots,
    config: SessionConfig,
    session_id: str,
    run: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Classify every turn of the active run. Returns the ``--json`` payload."""
    skipped = metered_skip(config, "segment")
    if skipped is not None:
        return skipped

    tree: SessionTree = roots.session(session_id)
    recording, dataset = resolve_active_dataset(tree, run)
    link = load_session_link(tree)
    effective_run = run if run is not None else link.active_run

    lexicon = load_lexicon(roots.lexicon_file)
    speakers = load_speakers(roots.speakers_file)
    prompt = load_prompt(PROMPT_VERSION)

    key = composite_key(
        dataset_digest=recording.dataset_digest,
        lexicon_digest=lexicon.digest,
        speakers_digest=speakers.digest,
        config=config,
        prompt_digest=prompt.digest,
    )
    provenance = Provenance.load(tree.provenance_json)
    output = tree.turns_class_jsonl

    if provenance.should_skip("segment", key, force=force, root=tree.root):
        existing = load_classifications(output)
        counts = dict.fromkeys(sorted(TURN_CLASSES), 0)
        for value in existing.values():
            if value in counts:
                counts[value] += 1
        return {
            "status": "skipped",
            "session": session_id,
            "run": effective_run,
            "path": str(output),
            "classified": len(existing),
            "distribution": counts,
            "usage": Usage().to_dict(),
            "warnings": ["already classified for this key; use --force to re-run"],
            "next_steps": next_steps_for(
                "segment", session_id=session_id, api_key_present=True, run=effective_run
            ),
        }

    turns = collect_turns(
        dataset, merge_gap_s=config.merge_gap_s, drop_bleed=False, speakers=speakers
    )
    windows = build_windows(
        turns,
        window_minutes=config.window_minutes,
        overlap_pct=config.window_overlap_pct,
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
        schema=TURN_CLASS_SCHEMA,
        render=lambda window: window.render(),
        client=client_for(config),
        schema_name="turn_classes",
    )

    accepted, rejected = reduce_classifications(windows, result.succeeded())
    ordered = [accepted[turn.turn_id] for turn in turns if turn.turn_id in accepted]
    write_text(output, "".join(entry.to_json_line() + "\n" for entry in ordered))

    warnings: list[str] = []
    unclassified = len(turns) - len(ordered)
    if unclassified:
        warnings.append(
            f"{unclassified} of {len(turns)} turns were not classified and are absent from "
            f"{output.name}; downstream stages treat them as unfiltered"
        )
    if result.failed_windows():
        warnings.append(
            f"{len(result.failed_windows())} window(s) failed after retries and are reported "
            f"in `failed_windows`, not dropped silently"
        )

    provenance.mark_done(
        "segment",
        key,
        outputs=[output],
        extra={
            "run": effective_run,
            "windows": len(windows),
            "turns": len(turns),
            "classified": len(ordered),
            "usage": result.usage.to_dict(),
            "generated_at": utc_now(),
        },
    )

    return {
        "status": "ok",
        "session": session_id,
        "run": effective_run,
        "path": str(output),
        "model": config.openai_model,
        "prompt_version": PROMPT_VERSION,
        "turns": len(turns),
        "classified": len(ordered),
        "unclassified": unclassified,
        "windows": len(windows),
        "distribution": distribution(ordered),
        "rejected": rejected[:50],
        "rejected_count": len(rejected),
        "failed_windows": result.failed_windows(),
        "usage": result.usage.to_dict(),
        "warnings": warnings,
        "next_steps": next_steps_for(
            "segment", session_id=session_id, api_key_present=True, run=effective_run
        ),
    }


def class_file_digest(tree: SessionTree) -> str | None:
    """Digest of ``turns.class.jsonl`` for downstream composite keys, or ``None``."""
    path = tree.turns_class_jsonl
    return sha256_file(path) if path.is_file() else None
