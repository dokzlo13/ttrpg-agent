"""A synthetic craig-stt dataset, built with the SDK's own models and manifest writer.

Never hand-written JSONL. The whole point of the consumer contract test is that
an SDK pin bump which renames or retypes a field fails *here*, and a fixture
assembled by hand would happily keep passing while production broke.

The fixture's numbers are chosen so every QA metric has an exact, hand-checkable
expected value — see ``EXPECTED`` below, and ``test_qa.py``, which recomputes
them by hand rather than by calling the code under test.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from craig_stt_dataset import (
    Bleed,
    Counts,
    Provenance,
    RecordingMeta,
    Segment,
    SpeechRegion,
    TrackStats,
    TranscriptMeta,
    WordSpan,
    build_manifest,
    write_manifest,
)

from session_ingest.paths import Roots

RECORDING_ID = "TESTREC01"
SESSION_ID = "2026-08-08"
START_TIME = datetime(2026, 8, 8, 18, 30, 0, tzinfo=UTC)

SEGMENT_COUNT = 30
#: Six deliberately low word probabilities; everything else is 0.95.
LOW_WORD_PROBABILITIES = [0.10, 0.20, 0.30, 0.40, 0.50, 0.55]
BLEED_SEGMENTS = {10, 11, 20, 21}
OVERLAP_SEGMENTS = {4: [2], 5: [1], 6: [2], 7: [1]}
LOW_LOGPROB_SEGMENTS = {3, 13, 23}
#: 2.9 and 3.5 are outliers; 2.4 is exactly the threshold and must NOT count.
COMPRESSION_RATIOS = {7: 2.9, 8: 2.4, 17: 3.5}

#: One canonical hit spelled two ways (upper/lower) and three enumerated variants.
TERM_TEXT = {
    2: "Реплика 2. Вазгар говорит.",
    12: "Реплика 12. вазгар кивает.",
    4: "Реплика 4. Вагзар идёт.",
    14: "Реплика 14. Вагзар смотрит.",
    24: "Реплика 24. Вазагар молчит.",
    6: "Реплика 6. Освальд Стоун здесь.",
}

LEXICON_YAML = """\
terms:
  - id: vagzar
    canonical: Вазгар
    display_ru: Вазгар
    variants: [Вагзар, Вазагар]
    kind: npc
    active: true
    priority: 10
    source: session/2025-01-01
  - id: kilverin
    canonical: Кильверин
    variants: [Кильверинь]
    kind: npc
    active: true
    priority: 5
  - id: oswald
    canonical: Освальд
    variants: [Освалд]
    kind: npc
    active: false
    priority: 1
"""

#: The live vault's shape in miniature: a `morph: true` lemma with one explicit
#: oblique entry beside it, so the precedence rule has something to win against.
MORPH_LEXICON_YAML = """\
terms:
  - id: morvika
    canonical: Morvika
    display_ru: Морвика
    variants: [Марвика]
    kind: npc
    active: true
    priority: 1
    morph: true
  - id: morvika-gen
    canonical: Morvika
    display_ru: Морвики
    variants: [Марвики]
    kind: npc
    active: true
    priority: 9
"""

SPEAKERS_YAML = """\
speakers:
  "u-alice":
    player: Alice
    character: Морвика
    role: pc
"""


#: ``vault/notes/state/entity-registry.md`` — prose around one fenced yaml block.
#: The prose is deliberately present in the fixture: the loader must find the
#: block inside a real note, not assume the file is bare YAML.
ENTITY_REGISTRY_MD = """\
---
type: meta
---

# Реестр сущностей

Machine table below; everything else is for the owner.

```yaml
schema: ttrpg.entity-registry/1
entities:
  - {slug: morvika, kind: pc, ru: "Морвика", en: "Morvika", aliases: ["Марвика"],
     note: null, roster: "party-register", lexicon: null}
  - {slug: vagzar, kind: npc, ru: "Вазгар", en: null, aliases: ["Вагзар"],
     note: "npcs/vagzar.md", roster: null, lexicon: vagzar}
  - {slug: kilverin, kind: location, ru: "Кильверин", en: "Kilverin", aliases: [],
     note: null, roster: null, lexicon: kilverin}
```

## Connections
"""


class Expected:
    """Hand-computed metric values for the fixture. Kept beside the fixture on purpose."""

    segments = 30
    words_with_probability = 60
    #: nearest rank ceil(0.10 * 60) = 6 -> the 6th smallest probability
    word_p10 = 0.55
    low_logprob_share = 3 / 30
    compression_outliers = 2
    bleed_rate = 4 / 30
    overlap_rate = 4 / 30
    #: 2 canonical hits vs 3 variant hits for `vagzar`; the other terms score zero
    #: (`kilverin` never appears) or are inactive (`oswald`).
    lexicon_miss_rate = 0.6
    unmapped_speakers = 1  # u-bob; the skipped bot track is not a participant
    tracks_missing = 1

    @property
    def track_shares(self) -> dict[int, float]:
        return {1: 0.6, 2: 0.4, 3: 0.0}


EXPECTED = Expected()


def _segment_text(index: int) -> str:
    return TERM_TEXT.get(index, f"Реплика {index}.")


def _word_probabilities(index: int) -> list[float]:
    if index < 3:
        return LOW_WORD_PROBABILITIES[2 * index : 2 * index + 2]
    return [0.95, 0.95]


def build_segments() -> list[Segment]:
    """Thirty segments alternating between two speaking tracks, in global time order."""
    segments: list[Segment] = []
    for index in range(SEGMENT_COUNT):
        track = 1 if index % 2 == 0 else 2
        t0 = float(index * 10)
        t1 = t0 + 8.0
        probabilities = _word_probabilities(index)
        words = [
            WordSpan(t0=t0 + offset, t1=t0 + offset + 1.0, w=f"w{offset}", p=probability)
            for offset, probability in enumerate(probabilities)
        ]
        segments.append(
            Segment(
                i=index,
                t0=t0,
                t1=t1,
                track=track,
                user_id="u-alice" if track == 1 else "u-bob",
                username="alice" if track == 1 else "bob",
                text=_segment_text(index),
                avg_logprob=-1.5 if index in LOW_LOGPROB_SEGMENTS else -0.35,
                no_speech_prob=0.01,
                compression_ratio=COMPRESSION_RATIOS.get(index, 1.2),
                temperature=0.0,
                overlap=OVERLAP_SEGMENTS.get(index, []),
                bleed=(
                    Bleed(suspect=True, louder_track=3 - track, delta_db=-6.0)
                    if index in BLEED_SEGMENTS
                    else None
                ),
                words=words,
            )
        )
    return segments


def build_meta(segments: list[Segment]) -> TranscriptMeta:
    tracks = [
        TrackStats(
            track=1,
            filename="1-alice.flac",
            user_id="u-alice",
            username="alice",
            source_duration_s=300.0,
            pcm_duration_s=300.0,
            speech_s=120.0,
            speech_ratio=0.4,
            speech_regions=15,
            segments=15,
            words=30,
        ),
        TrackStats(
            track=2,
            filename="2-bob.flac",
            user_id="u-bob",
            username="bob",
            source_duration_s=300.0,
            pcm_duration_s=300.0,
            speech_s=80.0,
            speech_ratio=0.27,
            speech_regions=15,
            segments=15,
            words=30,
        ),
        TrackStats(
            track=3,
            filename="",
            user_id="u-bot",
            username="craig-bot",
            speech_s=0.0,
            segments=0,
            words=0,
            skipped=True,
            # craig-stt-dataset 1.2.0: a deliberate omission says so structurally,
            # so adopt never has to parse the free-text reason to know it.
            skip_category="ignored",
            skip_reason="ignored: bot",
        ),
    ]
    provenance = Provenance(
        craig_stt_version="0.9.0",
        python_version="3.13.8",
        craig_format="flac",
        ignore_bots=True,
        ignored_tracks=[3],
        sample_rate=16000,
        engine="faster-whisper",
        model="large-v3",
        device="cuda",
        compute_type="float16",
        language="ru",
        task="transcribe",
    )
    counts = Counts(
        tracks=len(tracks),
        tracks_transcribed=sum(1 for track in tracks if not track.skipped),
        segments=len(segments),
        words=sum(len(segment.words or []) for segment in segments),
        speech_s=sum(track.speech_s for track in tracks),
        audio_s=300.0,
        overlapping_segments=sum(1 for segment in segments if segment.overlap),
        bleed_suspect_segments=sum(
            1 for segment in segments if segment.bleed and segment.bleed.suspect
        ),
    )
    return TranscriptMeta(
        recording=RecordingMeta(
            id=RECORDING_ID,
            guild="Test Guild",
            channel="table",
            start_time=START_TIME,
            duration_s=300.0,
        ),
        tracks=tracks,
        counts=counts,
        provenance=provenance,
    )


def failed_track_stats() -> TrackStats:
    """craig-stt-dataset 1.2.0's shape for a track that would not decode.

    The speaker is lost, and saying so is the whole reason the row exists at all.
    Adopting it is the operator's call, so this is what ``--allow-skipped-tracks``
    exists for.
    """
    return TrackStats(
        track=4,
        filename="4-carol.flac",
        user_id="u-carol",
        username="carol",
        speech_s=0.0,
        segments=0,
        words=0,
        skipped=True,
        skip_category="failed",
        skip_reason="decode failed: unexpected end of stream",
    )


def uncategorised_track_stats() -> TrackStats:
    """What an older craig-stt wrote: skipped, free text, and no ``skip_category``.

    A deliberate omission and a lost speaker are literally the same bytes here,
    which is why no flag can accept it — the fact is absent from the dataset.
    """
    return TrackStats(
        track=5,
        filename="5-dave.flac",
        user_id="u-dave",
        username="dave",
        speech_s=0.0,
        segments=0,
        words=0,
        skipped=True,
        skip_reason="no speech detected (0.00s < 5.0s)",
    )


def lost_track_stats() -> list[TrackStats]:
    """Both skips that are *not* deliberate, one of each kind adopt refuses on."""
    return [failed_track_stats(), uncategorised_track_stats()]


def write_dataset(
    directory: Path,
    *,
    recording_id: str = RECORDING_ID,
    start_time: datetime | None = START_TIME,
    status: str = "complete",
    with_manifest: bool = True,
    all_tracks_transcribed: bool = False,
    with_lost_tracks: bool = False,
    lost_tracks: Sequence[TrackStats] | None = None,
) -> Path:
    """Serialise the synthetic dataset through the SDK's models and manifest writer.

    ``with_lost_tracks`` appends both non-deliberate skips; ``lost_tracks`` appends
    exactly the rows given, for a test that needs one kind of skip in isolation.
    """
    directory.mkdir(parents=True, exist_ok=True)
    segments = build_segments()
    meta = build_meta(segments)
    meta.recording.id = recording_id
    meta.recording.start_time = start_time
    if all_tracks_transcribed:
        # Drop the ignored bot so counts.tracks == counts.tracks_transcribed.
        meta.tracks = [track for track in meta.tracks if not track.skipped]
        meta.counts.tracks = len(meta.tracks)
        meta.counts.tracks_transcribed = len(meta.tracks)
        meta.provenance.ignored_tracks = None
    extra_skips = list(lost_tracks) if lost_tracks is not None else []
    if with_lost_tracks:
        extra_skips = [*lost_track_stats(), *extra_skips]
    if extra_skips:
        # Skipped tracks count towards `tracks`, never towards `tracks_transcribed`.
        meta.tracks = [*meta.tracks, *extra_skips]
        meta.counts.tracks = len(meta.tracks)

    (directory / "meta.json").write_text(meta.model_dump_json(indent=2), encoding="utf-8")
    (directory / "segments.jsonl").write_text(
        "".join(segment.model_dump_json() + "\n" for segment in segments), encoding="utf-8"
    )
    (directory / "speech.jsonl").write_text(
        "".join(
            SpeechRegion(track=segment.track, t0=segment.t0, t1=segment.t1).model_dump_json() + "\n"
            for segment in segments
        ),
        encoding="utf-8",
    )
    if with_manifest:
        manifest = build_manifest(
            directory,
            recording_id=recording_id,
            produced_by={"craig-stt": "0.9.0", "craig-stt-dataset": "1.2.0"},
            status="complete" if status == "complete" else "partial",
        )
        write_manifest(directory, manifest)
    return directory


@dataclass
class Workspace:
    """A throwaway project root with the four contract roots inside it."""

    project_root: Path
    roots: Roots
    dataset_dir: Path
    recording_id: str = RECORDING_ID
    session_id: str = SESSION_ID

    def cli_env(self, **overrides: str | None) -> dict[str, str | None]:
        """Environment for a CliRunner invocation; a ``None`` value removes the variable."""
        base: dict[str, str | None] = {
            "TTRPG_ROOT": str(self.project_root),
            "TTRPG_SESSIONS_DIR": str(self.roots.sessions),
            "TTRPG_SESSION_DATASETS_DIR": str(self.roots.datasets),
            "TTRPG_TRANSCRIPTS_DIR": str(self.roots.transcripts),
            "TTRPG_NOTES_DIR": str(self.roots.notes),
            "TTRPG_SESSION_ID": None,
            "OPENAI_API_KEY": None,
        }
        base.update(overrides)
        return base

    def plain_env(self, **overrides: str) -> dict[str, str]:
        """A plain env mapping for direct (non-CLI) calls into the library."""
        base = {
            "TTRPG_ROOT": str(self.project_root),
            "TTRPG_SESSIONS_DIR": str(self.roots.sessions),
            "TTRPG_SESSION_DATASETS_DIR": str(self.roots.datasets),
            "TTRPG_TRANSCRIPTS_DIR": str(self.roots.transcripts),
            "TTRPG_NOTES_DIR": str(self.roots.notes),
        }
        base.update(overrides)
        return base

    def write_lexicon(self, text: str = LEXICON_YAML) -> Path:
        path = self.roots.lexicon_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_speakers(self, text: str = SPEAKERS_YAML) -> Path:
        path = self.roots.speakers_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def config(self, **cli):
        """A resolved SessionConfig for direct library calls."""
        from session_ingest.config import resolve_config

        return resolve_config(env=self.plain_env(), **cli)

    def adopt(self, **kwargs):
        """Bind the synthetic dataset to its session, the way every later verb needs it."""
        from session_ingest.adopt import run_adopt

        return run_adopt(
            target=str(self.dataset_dir),
            roots=self.roots,
            config=self.config(),
            allow_skipped_tracks=True,
            **kwargs,
        )

    def write_chronicle(self, name: str | None = None) -> Path:
        path = self.roots.chronicles_dir
        path.mkdir(parents=True, exist_ok=True)
        note = path / (name or f"s001-{self.session_id}-test.md")
        note.write_text("# Сессия 1\n", encoding="utf-8")
        return note

    def write_entity_registry(self, text: str = ENTITY_REGISTRY_MD) -> Path:
        path = self.roots.entity_registry_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    project_root = tmp_path / "repo"
    (project_root / ".agents").mkdir(parents=True)
    (project_root / "AGENTS.md").write_text("# test\n", encoding="utf-8")

    roots = Roots(
        sessions=project_root / ".cache" / "sessions",
        datasets=project_root / ".cache" / "sessions" / "datasets",
        transcripts=project_root / "vault" / "transcripts",
        notes=project_root / "vault" / "notes",
    )
    roots.datasets.mkdir(parents=True)
    roots.transcripts.mkdir(parents=True)
    roots.notes.mkdir(parents=True)

    dataset_dir = write_dataset(roots.datasets / RECORDING_ID)
    return Workspace(project_root=project_root, roots=roots, dataset_dir=dataset_dir)
