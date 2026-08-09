"""The data shapes the CLI writes to disk, and the validator for the one that matters.

Three groups:

``session.json``
    :class:`SessionLink` — date ↔ recording id(s), the active run, the digest.
    Multi-recording capable because Craig restarting mid-session must be
    representable rather than an error.

``qa.json``
    :class:`QaMetrics` / :class:`QaReport` — DESIGN §5's facts plus which
    configured thresholds they cross. Facts, never verdicts.

``record.json``
    ``ttrpg.session-record/1``, the *only* thing layer 2 reads. Its rules are
    enforced by :func:`validate_record`, and the load-bearing one is DESIGN §6
    rule 2: **a claim without evidence is a bug**, so an element with an empty
    ``evidence`` list is a validation error, not a warning.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, NotRequired, TypedDict

from .provenance import as_sha256, sha256_text, utc_now

RECORD_SCHEMA = "ttrpg.session-record/1"
SESSION_SCHEMA = "ttrpg.session-link/1"
QA_SCHEMA = "ttrpg.session-qa/1"

PARTICIPANT_ROLES = frozenset({"pc", "dm", "guest"})
EVENT_KINDS = frozenset({"combat", "social", "discovery", "travel", "downtime", "rules", "meta"})
WORLD_IMPACTS = frozenset({"none", "local", "world_breaking"})
ENTITY_KINDS = frozenset({"npc", "location", "faction", "item", "creature", "quest"})
QUEST_STATUS_CHANGES = frozenset({"introduced", "advanced", "completed", "failed", "abandoned"})
TURN_CLASSES = frozenset({"in_character", "table_talk", "mechanics", "ambiguous"})


# ============================================================ session.json


@dataclass(frozen=True, slots=True)
class SkippedTrack:
    """One track the dataset did not transcribe, as ``adopt`` recorded it.

    A track number alone cannot answer the question this row exists for — *was
    the speaker left out or lost?* — so the SDK's ``skip_category`` is carried
    with it: ``"ignored"`` for a deliberate omission (a configured exclusion, or
    less speech than the VAD floor), ``"failed"`` for a track that would not
    decode. ``skip_reason`` is the SDK's free text: display it, never parse it.

    The category stays optional so a row without one still deserialises — a
    session.json is read long after it was written, and refusing to load is a
    worse failure than reporting the gap. ``adopt`` never writes such a row: a
    dataset whose skips carry no category is refused rather than adopted.
    """

    track: int
    user_id: str | None = None
    username: str | None = None
    skip_category: str | None = None
    skip_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "track": self.track,
            "user_id": self.user_id,
            "username": self.username,
            "skip_category": self.skip_category,
            "skip_reason": self.skip_reason,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SkippedTrack:
        return cls(
            track=int(payload.get("track", 0)),
            user_id=payload.get("user_id"),
            username=payload.get("username"),
            skip_category=payload.get("skip_category"),
            skip_reason=payload.get("skip_reason"),
        )


@dataclass(frozen=True, slots=True)
class RecordingLink:
    """One adopted dataset, pinned to the run it belongs to."""

    recording_id: str
    dataset_path: str
    run: int
    dataset_digest: str
    status: str = "complete"
    started_at: str | None = None
    duration_s: float | None = None
    adopted_at: str = ""
    skipped_tracks: tuple[SkippedTrack, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "recording_id": self.recording_id,
            "dataset_path": self.dataset_path,
            "run": self.run,
            "dataset_digest": self.dataset_digest,
            "status": self.status,
            "started_at": self.started_at,
            "duration_s": self.duration_s,
            "adopted_at": self.adopted_at,
            "skipped_tracks": [track.to_dict() for track in self.skipped_tracks],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RecordingLink:
        skipped = payload.get("skipped_tracks")
        return cls(
            recording_id=str(payload.get("recording_id", "")),
            dataset_path=str(payload.get("dataset_path", "")),
            run=int(payload.get("run", 1)),
            dataset_digest=str(payload.get("dataset_digest", "")),
            status=str(payload.get("status", "complete")),
            started_at=payload.get("started_at"),
            duration_s=payload.get("duration_s"),
            adopted_at=str(payload.get("adopted_at", "")),
            skipped_tracks=(
                tuple(SkippedTrack.from_dict(row) for row in skipped if isinstance(row, Mapping))
                if isinstance(skipped, list)
                else ()
            ),
        )


def combined_digest(digests: Sequence[str]) -> str | None:
    """One digest for a session that may span several recordings.

    A single recording keeps its own digest verbatim so it stays comparable with
    the manifest. Several are folded in sorted order, because the identity of a
    two-recording session must not depend on adoption order.
    """
    cleaned = [d for d in digests if d]
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return cleaned[0]
    return sha256_text("|".join(sorted(cleaned)))


@dataclass
class SessionLink:
    """``session.json`` — the date ↔ recording binding and the active run."""

    id: str
    recordings: list[RecordingLink] = field(default_factory=list)
    active_run: int = 1
    updated_at: str = ""
    schema: str = SESSION_SCHEMA

    def runs(self) -> list[int]:
        return sorted({r.run for r in self.recordings})

    def for_run(self, run: int) -> list[RecordingLink]:
        return [r for r in self.recordings if r.run == run]

    def active_recordings(self) -> list[RecordingLink]:
        return self.for_run(self.active_run)

    @property
    def dataset_digest(self) -> str | None:
        """The active run's digest — what every downstream cache key is built on."""
        return combined_digest([r.dataset_digest for r in self.active_recordings()])

    def upsert(self, link: RecordingLink) -> None:
        """Replace the entry for this (recording_id, run) pair, or append it."""
        for index, existing in enumerate(self.recordings):
            if existing.recording_id == link.recording_id and existing.run == link.run:
                self.recordings[index] = link
                return
        self.recordings.append(link)
        self.recordings.sort(key=lambda r: (r.run, r.started_at or "", r.recording_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "id": self.id,
            "active_run": self.active_run,
            "dataset_digest": self.dataset_digest,
            "recordings": [r.to_dict() for r in self.recordings],
            "updated_at": self.updated_at or utc_now(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SessionLink:
        raw = payload.get("recordings")
        recordings = (
            [RecordingLink.from_dict(entry) for entry in raw if isinstance(entry, Mapping)]
            if isinstance(raw, list)
            else []
        )
        return cls(
            id=str(payload.get("id", "")),
            recordings=recordings,
            active_run=int(payload.get("active_run", 1)),
            updated_at=str(payload.get("updated_at", "")),
            schema=str(payload.get("schema", SESSION_SCHEMA)),
        )


# ================================================================= qa.json


@dataclass(frozen=True, slots=True)
class TrackSpeech:
    """Per-track speech accounting, including tracks that produced nothing."""

    track: int
    user_id: str | None
    username: str | None
    speech_s: float
    speech_share: float
    segments: int
    skipped: bool
    skip_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "track": self.track,
            "user_id": self.user_id,
            "username": self.username,
            "speech_s": self.speech_s,
            "speech_share": self.speech_share,
            "segments": self.segments,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


@dataclass(frozen=True, slots=True)
class CompressionOutlier:
    """A repetition-loop suspect, with the timestamp needed to go listen."""

    segment_i: int
    t0: float
    compression_ratio: float
    track: int
    username: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_i": self.segment_i,
            "t0": self.t0,
            "compression_ratio": self.compression_ratio,
            "track": self.track,
            "username": self.username,
        }


@dataclass(frozen=True, slots=True)
class LexiconTermCounts:
    """Exact counts of enumerated strings — no similarity, no fuzz."""

    term_id: str
    canonical: str
    canonical_hits: int
    variant_hits: int
    per_variant: Mapping[str, int] = field(default_factory=dict)

    @property
    def total_hits(self) -> int:
        return self.canonical_hits + self.variant_hits

    @property
    def miss_rate(self) -> float | None:
        return self.variant_hits / self.total_hits if self.total_hits else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "term_id": self.term_id,
            "canonical": self.canonical,
            "canonical_hits": self.canonical_hits,
            "variant_hits": self.variant_hits,
            "total_hits": self.total_hits,
            "miss_rate": self.miss_rate,
            "per_variant": dict(self.per_variant),
        }


@dataclass(frozen=True, slots=True)
class ThresholdCrossing:
    metric: str
    value: float
    threshold: float
    direction: Literal["min", "max"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": self.value,
            "threshold": self.threshold,
            "direction": self.direction,
        }

    def describe(self) -> str:
        comparison = "<" if self.direction == "min" else ">"
        return f"{self.metric}={self.value:.4g} {comparison} {self.threshold:.4g}"


@dataclass(frozen=True, slots=True)
class QaMetrics:
    """DESIGN §5's eight metrics, plus the per-track accounting they are read with."""

    word_p10: float | None
    low_logprob_share: float | None
    compression_outliers: tuple[CompressionOutlier, ...]
    bleed_rate: float
    overlap_rate: float
    lexicon_miss_rate: float | None
    unmapped_speakers: tuple[dict[str, Any], ...]
    tracks_missing: int
    segments: int = 0
    words_with_probability: int = 0
    segments_with_logprob: int = 0
    per_track: tuple[TrackSpeech, ...] = ()
    skipped_tracks: tuple[TrackSpeech, ...] = ()
    lexicon_terms: tuple[LexiconTermCounts, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "word_p10": self.word_p10,
            "low_logprob_share": self.low_logprob_share,
            "compression_outliers": [o.to_dict() for o in self.compression_outliers],
            "compression_outlier_count": len(self.compression_outliers),
            "bleed_rate": self.bleed_rate,
            "overlap_rate": self.overlap_rate,
            "lexicon_miss_rate": self.lexicon_miss_rate,
            "unmapped_speakers": [dict(s) for s in self.unmapped_speakers],
            "tracks_missing": self.tracks_missing,
            "segments": self.segments,
            "words_with_probability": self.words_with_probability,
            "segments_with_logprob": self.segments_with_logprob,
            "per_track": [t.to_dict() for t in self.per_track],
            "skipped_tracks": [t.to_dict() for t in self.skipped_tracks],
            "lexicon_terms": [t.to_dict() for t in self.lexicon_terms],
        }


@dataclass(frozen=True, slots=True)
class QaReport:
    """One run's QA facts. Written to ``runs/<N>/qa.json``."""

    session: str
    run: int
    dataset_digest: str | None
    recording_id: str | None
    metrics: QaMetrics
    thresholds: Mapping[str, float]
    thresholds_crossed: tuple[ThresholdCrossing, ...]
    lexicon: Mapping[str, Any] = field(default_factory=dict)
    speakers: Mapping[str, Any] = field(default_factory=dict)
    generated_at: str = ""
    schema: str = QA_SCHEMA

    @property
    def crossed(self) -> bool:
        return bool(self.thresholds_crossed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "session": self.session,
            "run": self.run,
            "dataset_digest": self.dataset_digest,
            "recording_id": self.recording_id,
            "generated_at": self.generated_at or utc_now(),
            "metrics": self.metrics.to_dict(),
            "thresholds": dict(self.thresholds),
            "thresholds_crossed": [c.to_dict() for c in self.thresholds_crossed],
            "lexicon": dict(self.lexicon),
            "speakers": dict(self.speakers),
        }


# ============================================================== record.json


class EvidenceDict(TypedDict):
    """The uniform evidence item (DESIGN §6), wikilink included."""

    turn_id: str
    segment_i: int
    t0: float
    speaker: str
    chunk: str
    link: str


@dataclass(frozen=True, slots=True)
class Evidence:
    """One pointer from a generated claim back to the words that support it."""

    turn_id: str
    segment_i: int
    t0: float
    speaker: str
    chunk: str
    link: str

    @classmethod
    def build(
        cls,
        *,
        turn_id: str,
        segment_i: int,
        t0: float,
        speaker: str,
        chunk: str,
        session_id: str,
    ) -> Evidence:
        """Construct with the Obsidian wikilink derived, never hand-assembled twice."""
        return cls(
            turn_id=turn_id,
            segment_i=segment_i,
            t0=t0,
            speaker=speaker,
            chunk=chunk,
            link=f"[[transcripts/{session_id}/{chunk}#^{turn_id}]]",
        )

    def to_dict(self) -> EvidenceDict:
        return {
            "turn_id": self.turn_id,
            "segment_i": self.segment_i,
            "t0": self.t0,
            "speaker": self.speaker,
            "chunk": self.chunk,
            "link": self.link,
        }


class ParticipantDict(TypedDict):
    user_id: str
    display: str
    role: str
    speech_s: float
    speech_share: float


class RecordProvenanceDict(TypedDict):
    stt_model: str
    merge_gap_s: float
    lexicon_digest: str | None
    prompt_version: str
    llm_model: str | None


class SessionBlockDict(TypedDict):
    """``play_time_s`` / ``table_talk_share`` are nullable on purpose.

    Both are derived from ``turns.class.jsonl``, which only the metered
    ``segment`` stage produces. On a keyless machine they are genuinely unknown,
    and ``null`` + a listing in ``session.missing`` says so; inventing ``0.0``
    would be a fabricated number layer 2 could not tell from a measured one.
    """

    id: str
    recording_id: str
    dataset_digest: str
    started_at: str | None
    duration_s: float
    language: str | None
    participants: list[ParticipantDict]
    play_time_s: float | None
    table_talk_share: float | None
    transcript_root: str
    provenance: RecordProvenanceDict


class SceneDict(TypedDict):
    id: str
    title: str
    location: NotRequired[str | None]
    t0: float
    t1: float
    summary: str
    participants: list[str]
    evidence: list[EvidenceDict]


class EventDict(TypedDict):
    id: str
    scene: NotRequired[str | None]
    kind: str
    summary: str
    outcome: NotRequired[str | None]
    world_impact: str
    needs_owner: bool
    confidence: float
    evidence: list[EvidenceDict]


class EntityDict(TypedDict):
    id: str
    kind: str
    name_as_heard: str
    canonical: str
    lexicon_term_id: NotRequired[str | None]
    first_mention_t: float
    #: Slug from ``state/entity-registry.md``; ``None`` when the name matched nothing.
    registry_slug: NotRequired[str | None]
    #: Vault path the slug points at, once the entity has been promoted to its own note.
    vault_note: NotRequired[str | None]
    evidence: list[EvidenceDict]


class QuestDict(TypedDict):
    id: str
    name: str
    status_change: str
    detail: NotRequired[str | None]
    evidence: list[EvidenceDict]


class LootDict(TypedDict):
    item: str
    recipient: NotRequired[str | None]
    quantity: NotRequired[str | None]
    evidence: list[EvidenceDict]


class CommitmentDict(TypedDict):
    who: str
    promise: str
    deadline: NotRequired[str | None]
    evidence: list[EvidenceDict]


class ThreadDict(TypedDict):
    question: str
    status: str
    evidence: list[EvidenceDict]


class RecordDict(TypedDict):
    schema: str
    session: SessionBlockDict
    scenes: list[SceneDict]
    events: list[EventDict]
    entities: list[EntityDict]
    quests: list[QuestDict]
    loot: list[LootDict]
    commitments: list[CommitmentDict]
    threads: list[ThreadDict]


#: collection -> (required string keys, id key or None, enum checks)
_COLLECTIONS: dict[str, dict[str, Any]] = {
    "scenes": {
        "id_key": "id",
        "required": ("title", "summary"),
        "numbers": ("t0", "t1"),
        "enums": {},
        "confidence_required": False,
    },
    "events": {
        "id_key": "id",
        "required": ("kind", "summary", "world_impact"),
        "numbers": (),
        "enums": {"kind": EVENT_KINDS, "world_impact": WORLD_IMPACTS},
        "confidence_required": True,
    },
    "entities": {
        "id_key": "id",
        "required": ("kind", "name_as_heard", "canonical"),
        "numbers": ("first_mention_t",),
        "enums": {"kind": ENTITY_KINDS},
        "confidence_required": False,
    },
    "quests": {
        "id_key": "id",
        "required": ("name", "status_change"),
        "numbers": (),
        "enums": {"status_change": QUEST_STATUS_CHANGES},
        "confidence_required": False,
    },
    "loot": {
        "id_key": None,
        "required": ("item",),
        "numbers": (),
        "enums": {},
        "confidence_required": False,
    },
    "commitments": {
        "id_key": None,
        "required": ("who", "promise"),
        "numbers": (),
        "enums": {},
        "confidence_required": False,
    },
    "threads": {
        "id_key": None,
        "required": ("question", "status"),
        "numbers": (),
        "enums": {},
        "confidence_required": False,
    },
}

_SESSION_REQUIRED = (
    "id",
    "recording_id",
    "dataset_digest",
    "duration_s",
    "participants",
    "play_time_s",
    "table_talk_share",
    "transcript_root",
    "provenance",
)
_PROVENANCE_REQUIRED = ("stt_model", "merge_gap_s", "prompt_version")
_EVIDENCE_KEYS: dict[str, type | tuple[type, ...]] = {
    "turn_id": str,
    "segment_i": int,
    "t0": (int, float),
    "speaker": str,
    "chunk": str,
    "link": str,
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_evidence(where: str, value: Any, errors: list[str]) -> None:
    """A claim without evidence is a bug (DESIGN §6 rule 2) — so it is an error here."""
    if not isinstance(value, list):
        errors.append(f"{where}.evidence: must be a list")
        return
    if not value:
        errors.append(f"{where}.evidence: must not be empty — a claim without evidence is a bug")
        return
    for index, item in enumerate(value):
        at = f"{where}.evidence[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{at}: must be an object")
            continue
        for key, expected in _EVIDENCE_KEYS.items():
            if key not in item:
                errors.append(f"{at}.{key}: missing")
                continue
            candidate = item[key]
            if expected is int:
                if not isinstance(candidate, int) or isinstance(candidate, bool):
                    errors.append(f"{at}.{key}: must be an integer")
            elif expected is str:
                if not isinstance(candidate, str) or not candidate:
                    errors.append(f"{at}.{key}: must be a non-empty string")
            elif not _is_number(candidate):
                errors.append(f"{at}.{key}: must be a number")


def _validate_confidence(
    where: str, element: Mapping[str, Any], required: bool, errors: list[str]
) -> None:
    if "confidence" not in element:
        if required:
            errors.append(f"{where}.confidence: missing")
        return
    value = element["confidence"]
    if not _is_number(value):
        errors.append(f"{where}.confidence: must be a number")
        return
    if not 0.0 <= float(value) <= 1.0:
        errors.append(f"{where}.confidence: must be within 0..1, got {value}")


def _validate_session_block(block: Any, errors: list[str]) -> None:
    if not isinstance(block, dict):
        errors.append("session: must be an object")
        return
    for key in _SESSION_REQUIRED:
        if key not in block:
            errors.append(f"session.{key}: missing")
    # A key the record openly lists in `session.missing` is *declared* unknown, so
    # the number check does not apply to it. That declaration is what keeps the
    # validator strict everywhere else: `record` no longer needs a waiver list to
    # get an honest null past validation, and an *undeclared* null still fails.
    declared_missing = block.get("missing")
    unknown = set(declared_missing) if isinstance(declared_missing, list) else set()
    for key in ("duration_s", "play_time_s", "table_talk_share"):
        if key in unknown:
            if block.get(key) is not None and not _is_number(block[key]):
                errors.append(f"session.{key}: must be null or a number when listed in `missing`")
            continue
        if key in block and not _is_number(block[key]):
            errors.append(f"session.{key}: must be a number")
    digest = block.get("dataset_digest")
    if isinstance(digest, str) and not digest.startswith("sha256:"):
        errors.append("session.dataset_digest: must be a `sha256:` prefixed digest")

    participants = block.get("participants")
    if participants is not None:
        if not isinstance(participants, list):
            errors.append("session.participants: must be a list")
        else:
            for index, participant in enumerate(participants):
                at = f"session.participants[{index}]"
                if not isinstance(participant, dict):
                    errors.append(f"{at}: must be an object")
                    continue
                for key in ("user_id", "display", "role"):
                    if not isinstance(participant.get(key), str) or not participant.get(key):
                        errors.append(f"{at}.{key}: must be a non-empty string")
                role = participant.get("role")
                if isinstance(role, str) and role not in PARTICIPANT_ROLES:
                    errors.append(
                        f"{at}.role: must be one of {sorted(PARTICIPANT_ROLES)}, got {role!r}"
                    )
                for key in ("speech_s", "speech_share"):
                    if not _is_number(participant.get(key)):
                        errors.append(f"{at}.{key}: must be a number")

    provenance = block.get("provenance")
    if provenance is not None:
        if not isinstance(provenance, dict):
            errors.append("session.provenance: must be an object")
        else:
            for key in _PROVENANCE_REQUIRED:
                if key not in provenance:
                    errors.append(f"session.provenance.{key}: missing")
            if "merge_gap_s" in provenance and not _is_number(provenance["merge_gap_s"]):
                errors.append("session.provenance.merge_gap_s: must be a number")


def validate_record(record: Any) -> list[str]:
    """Structurally validate a ``ttrpg.session-record/1`` payload.

    Returns a list of dotted-path problems; an empty list means valid. This is a
    *structural* validator on purpose — it checks shape, enums, id uniqueness and
    the evidence rule. It does not judge whether a summary is any good, and it
    never resolves links (that is ``record``'s job, against anchors.json).
    """
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["record: must be a JSON object"]

    schema = record.get("schema")
    if schema != RECORD_SCHEMA:
        errors.append(f"schema: must be {RECORD_SCHEMA!r}, got {schema!r}")

    if "session" not in record:
        errors.append("session: missing")
    else:
        _validate_session_block(record["session"], errors)

    for name, rules in _COLLECTIONS.items():
        if name not in record:
            errors.append(f"{name}: missing")
            continue
        collection = record[name]
        if not isinstance(collection, list):
            errors.append(f"{name}: must be a list")
            continue
        seen_ids: set[str] = set()
        for index, element in enumerate(collection):
            where = f"{name}[{index}]"
            if not isinstance(element, dict):
                errors.append(f"{where}: must be an object")
                continue

            id_key = rules["id_key"]
            if id_key:
                element_id = element.get(id_key)
                if not isinstance(element_id, str) or not element_id:
                    errors.append(f"{where}.{id_key}: must be a non-empty string")
                elif element_id in seen_ids:
                    errors.append(f"{where}.{id_key}: duplicate id {element_id!r}")
                else:
                    seen_ids.add(element_id)

            for key in rules["required"]:
                value = element.get(key)
                if not isinstance(value, str) or not value:
                    errors.append(f"{where}.{key}: must be a non-empty string")
            for key in rules["numbers"]:
                if not _is_number(element.get(key)):
                    errors.append(f"{where}.{key}: must be a number")
            for key, allowed in rules["enums"].items():
                value = element.get(key)
                if isinstance(value, str) and value not in allowed:
                    errors.append(f"{where}.{key}: must be one of {sorted(allowed)}, got {value!r}")

            if name == "events" and not isinstance(element.get("needs_owner"), bool):
                errors.append(f"{where}.needs_owner: must be a boolean")

            _validate_confidence(where, element, bool(rules["confidence_required"]), errors)
            _validate_evidence(where, element.get("evidence"), errors)

    return errors


def empty_record(
    *,
    session_id: str,
    recording_id: str,
    dataset_digest: str,
    transcript_root: str,
    duration_s: float = 0.0,
    started_at: str | None = None,
    language: str | None = None,
    participants: Iterable[ParticipantDict] = (),
    play_time_s: float | None = 0.0,
    table_talk_share: float | None = 0.0,
    stt_model: str = "unknown",
    merge_gap_s: float = 1.5,
    lexicon_digest: str | None = None,
    prompt_version: str = "extract/1",
    llm_model: str | None = None,
) -> RecordDict:
    """A schema-valid record with every collection empty.

    Used by ``record`` as the assembly base and by tests as the fixture floor,
    so the required-key list lives in exactly one place.
    """
    return {
        "schema": RECORD_SCHEMA,
        "session": {
            "id": session_id,
            "recording_id": recording_id,
            "dataset_digest": as_sha256(dataset_digest) or dataset_digest,
            "started_at": started_at,
            "duration_s": duration_s,
            "language": language,
            "participants": list(participants),
            "play_time_s": play_time_s,
            "table_talk_share": table_talk_share,
            "transcript_root": transcript_root,
            "provenance": {
                "stt_model": stt_model,
                "merge_gap_s": merge_gap_s,
                "lexicon_digest": lexicon_digest,
                "prompt_version": prompt_version,
                "llm_model": llm_model,
            },
        },
        "scenes": [],
        "events": [],
        "entities": [],
        "quests": [],
        "loot": [],
        "commitments": [],
        "threads": [],
    }
