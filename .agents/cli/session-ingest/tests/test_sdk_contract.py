"""The consumer contract test (PLAN step 4).

Every field of ``craig-stt-dataset`` that session-ingest reads is named here and
asserted against a dataset built with the SDK's own models. An SDK pin bump that
renames, removes or retypes one of them fails in this file — with the field name
in the failure — rather than three weeks later in production.

If a field genuinely has to change, the fix is a change in craig-stt plus a bump
here, in one change set. Working around it in consumer code is what makes a
format unchangeable, which is exactly what CONTRACT.md exists to prevent.
"""

from __future__ import annotations

from datetime import datetime
from typing import get_args

from craig_stt_dataset import (
    Counts,
    Dataset,
    DatasetManifest,
    RecordingMeta,
    Segment,
    SkipCategory,
    SpeakerInfo,
    Turn,
    open_dataset,
)

from .conftest import RECORDING_ID, Workspace, write_dataset

#: Named explicitly so a rename shows up as a test failure, not a silent None.
SEGMENT_FIELDS = (
    "i",
    "t0",
    "t1",
    "track",
    "user_id",
    "username",
    "text",
    "avg_logprob",
    "compression_ratio",
    "overlap",
    "bleed",
    "words",
)
COUNTS_FIELDS = (
    "tracks",
    "tracks_transcribed",
    "segments",
    "words",
    "speech_s",
    "audio_s",
    "overlapping_segments",
    "bleed_suspect_segments",
)
TRACK_FIELDS = (
    "track",
    "user_id",
    "username",
    "speech_s",
    "segments",
    "skipped",
    "skip_category",
    "skip_reason",
)
RECORDING_FIELDS = ("id", "start_time", "duration_s")
PROVENANCE_FIELDS = ("craig_stt_version", "model", "language", "ignored_tracks")


def test_dataset_identity_surface(workspace: Workspace) -> None:
    dataset = open_dataset(workspace.dataset_dir, verify=True)
    assert isinstance(dataset, Dataset)
    assert dataset.recording_id == RECORDING_ID
    assert dataset.status == "complete"
    assert isinstance(dataset.digest, str) and len(dataset.digest) == 64
    assert isinstance(dataset.manifest, DatasetManifest)
    assert dataset.manifest.produced_by["craig-stt-dataset"] == "1.2.0"
    assert dataset.manifest.file("segments") is not None
    assert dataset.manifest.file("meta") is not None
    assert dataset.manifest.file("speech") is not None


def test_segment_fields(workspace: Workspace) -> None:
    dataset = open_dataset(workspace.dataset_dir)
    segments = list(dataset.iter_segments())
    assert len(segments) == 30
    for field in SEGMENT_FIELDS:
        assert hasattr(segments[0], field), f"Segment.{field} disappeared"

    first = segments[0]
    assert isinstance(first, Segment)
    assert first.i == 0
    assert first.track == 1
    assert first.user_id == "u-alice"
    assert first.text.startswith("Реплика 0")
    assert first.words is not None
    assert first.words[0].p == 0.10
    assert first.words[0].w == "w0"

    bleeding = next(s for s in segments if s.bleed is not None)
    assert bleeding.bleed is not None
    assert bleeding.bleed.suspect is True
    assert bleeding.bleed.delta_db == -6.0

    overlapping = next(s for s in segments if s.overlap)
    assert overlapping.overlap == [2]


def test_counts_and_track_fields(workspace: Workspace) -> None:
    meta = open_dataset(workspace.dataset_dir).meta
    for field in COUNTS_FIELDS:
        assert hasattr(meta.counts, field), f"Counts.{field} disappeared"
    assert isinstance(meta.counts, Counts)
    assert meta.counts.tracks == 3
    assert meta.counts.tracks_transcribed == 2

    for field in TRACK_FIELDS:
        assert hasattr(meta.tracks[0], field), f"TrackStats.{field} disappeared"
    skipped = [track for track in meta.tracks if track.skipped]
    assert [track.skip_reason for track in skipped] == ["ignored: bot"]
    assert [track.skip_category for track in skipped] == ["ignored"]
    # Not skipped ⇒ no category. adopt's gate reads exactly this distinction.
    assert all(track.skip_category is None for track in meta.tracks if not track.skipped)


def test_skip_category_is_a_closed_set_and_may_be_absent(workspace: Workspace) -> None:
    """``skip_category`` (SDK 1.2.0) is what tells a deliberate omission from a loss.

    The three states adopt branches on are all asserted against a dataset the SDK
    itself serialised: ``"ignored"``, ``"failed"``, and absent — the last being
    every dataset written before the field existed, where the two are the same
    bytes and only the operator can say which happened.
    """
    assert get_args(SkipCategory) == ("ignored", "failed")

    directory = write_dataset(
        workspace.roots.datasets / "LOSTTRACKS", recording_id="LOSTTRACKS", with_lost_tracks=True
    )
    meta = open_dataset(directory).meta
    by_track = {track.track: track for track in meta.tracks}
    assert by_track[3].skip_category == "ignored"
    assert by_track[4].skip_category == "failed"
    assert by_track[5].skipped is True and by_track[5].skip_category is None
    # A skipped track counts towards `tracks` and never towards `tracks_transcribed`.
    assert meta.counts.tracks == 5
    assert meta.counts.tracks_transcribed == 2


def test_recording_meta_and_provenance(workspace: Workspace) -> None:
    meta = open_dataset(workspace.dataset_dir).meta
    for field in RECORDING_FIELDS:
        assert hasattr(meta.recording, field), f"RecordingMeta.{field} disappeared"
    assert isinstance(meta.recording, RecordingMeta)
    assert isinstance(meta.recording.start_time, datetime)
    assert meta.recording.duration_s == 300.0
    # Provenance carries what adopt/doctor/record read back.
    for field in PROVENANCE_FIELDS:
        assert hasattr(meta.provenance, field), f"Provenance.{field} disappeared"
    assert meta.provenance.craig_stt_version == "0.9.0"
    assert meta.provenance.model == "large-v3"
    assert meta.provenance.language == "ru"
    assert meta.provenance.ignored_tracks == [3]


def test_speakers_and_segments_between(workspace: Workspace) -> None:
    dataset = open_dataset(workspace.dataset_dir)
    speakers = dataset.speakers()
    assert all(isinstance(entry, SpeakerInfo) for entry in speakers)
    assert {s.user_id for s in speakers} == {"u-alice", "u-bob", "u-bot"}
    assert next(s for s in speakers if s.user_id == "u-bot").skipped is True

    window = list(dataset.segments_between(0.0, 25.0))
    # Half-open by overlap, not containment: segment 2 starts at 20s and straddles.
    assert [segment.i for segment in window] == [0, 1, 2]


def test_turns_surface(workspace: Workspace) -> None:
    """``turns()`` is the SDK's one mechanical helper, and the source of turn ids."""
    dataset = open_dataset(workspace.dataset_dir)
    turns = list(dataset.turns(merge_gap_s=1.5))
    assert turns
    first = turns[0]
    assert isinstance(first, Turn)
    for field in (
        "id",
        "t0",
        "t1",
        "track",
        "user_id",
        "username",
        "text",
        "segment_indices",
        "overlap",
        "bleed_suspect",
    ):
        assert hasattr(first, field), f"Turn.{field} disappeared"
    assert first.id == "t0-1"

    # drop_bleed removes exactly the bleed-suspect segments, never more.
    kept = list(dataset.turns(merge_gap_s=1.5, drop_bleed=True))
    dropped_indices = {
        index for turn in turns if turn.bleed_suspect for index in turn.segment_indices
    }
    assert dropped_indices == {10, 11, 20, 21}
    kept_indices = {index for turn in kept for index in turn.segment_indices}
    assert kept_indices.isdisjoint(dropped_indices)


def test_manifest_roles_and_prunable_flags(workspace: Workspace) -> None:
    manifest = open_dataset(workspace.dataset_dir).manifest
    roles = {entry.role: entry for entry in manifest.files}
    assert set(roles) >= {"meta", "segments", "speech"}
    assert roles["segments"].sha256 is not None
    assert roles["segments"].records == 30
    assert roles["segments"].prunable is False
    assert manifest.segments_digest == roles["segments"].sha256
