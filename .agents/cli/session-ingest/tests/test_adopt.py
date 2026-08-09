"""adopt's five gates, and the session.json it writes when they all pass."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from session_ingest.adopt import derive_session_date, find_dataset, run_adopt
from session_ingest.config import resolve_config
from session_ingest.errors import DatasetAdoptError
from session_ingest.models import SessionLink, SkippedTrack
from session_ingest.provenance import Provenance

from .conftest import RECORDING_ID, SESSION_ID, Workspace, write_dataset


def _config(**env: str):
    return resolve_config(env=env)


def _adopt(workspace: Workspace, **kwargs):
    defaults: dict = {
        "target": str(workspace.dataset_dir),
        "roots": workspace.roots,
        "config": _config(),
        "allow_skipped_tracks": True,
    }
    defaults.update(kwargs)
    return run_adopt(**defaults)


def _rerun_dataset(directory: Path, marker: str) -> Path:
    """A dataset of the same recording whose bytes — and so whose digest — differ.

    Written manifest-last, because the digest has to describe the edited file.
    """
    from craig_stt_dataset import build_manifest, write_manifest

    write_dataset(directory, with_manifest=False)
    segments = directory / "segments.jsonl"
    segments.write_text(
        segments.read_text(encoding="utf-8").replace("Реплика 0.", marker), encoding="utf-8"
    )
    write_manifest(
        directory,
        build_manifest(directory, recording_id=RECORDING_ID, produced_by={"craig-stt": "0.9.0"}),
    )
    return directory


# ------------------------------------------------------------------ resolution


def test_find_dataset_by_recording_id(workspace: Workspace) -> None:
    assert find_dataset(RECORDING_ID, workspace.roots) == workspace.dataset_dir.resolve()


def test_find_dataset_refuses_two_candidates(workspace: Workspace) -> None:
    """Two datasets for one id is an ambiguity the tool must not resolve itself."""
    scratch = workspace.roots.scratch / "rerun"
    write_dataset(scratch / RECORDING_ID)
    with pytest.raises(DatasetAdoptError) as excinfo:
        find_dataset(RECORDING_ID, workspace.roots)
    assert excinfo.value.code == "ambiguous_dataset"
    assert len(excinfo.value.detail["candidates"]) == 2


def test_missing_manifest_emits_the_repair_command(workspace: Workspace, tmp_path: Path) -> None:
    bare = workspace.roots.datasets / "NOMANIFEST"
    write_dataset(bare, recording_id="NOMANIFEST", with_manifest=False)
    with pytest.raises(DatasetAdoptError) as excinfo:
        find_dataset("NOMANIFEST", workspace.roots)
    error = excinfo.value
    assert error.code == "missing_manifest"
    commands = [entry.get("command") for entry in error.next_steps]
    assert any("craig-stt manifest" in (command or "") for command in commands)


# ----------------------------------------------------------------------- gates


def test_date_is_derived_from_start_time() -> None:
    assert derive_session_date(datetime(2026, 8, 8, 23, 30, tzinfo=UTC)) == "2026-08-08"
    assert derive_session_date(None) is None


def test_date_mismatch_is_reported_not_chosen(workspace: Workspace) -> None:
    with pytest.raises(DatasetAdoptError) as excinfo:
        _adopt(workspace, session_id="2026-08-09")
    error = excinfo.value
    assert error.code == "date_mismatch"
    assert error.detail["session"] == "2026-08-09"
    assert error.detail["derived_from_start_time"] == "2026-08-08"
    assert not workspace.roots.session("2026-08-09").session_json.exists()


def test_same_day_suffix_is_accepted(workspace: Workspace) -> None:
    result = _adopt(workspace, session_id="2026-08-08-b")
    assert result.status == "ok"
    assert result.session == "2026-08-08-b"


def test_partial_capture_refused_without_flag(workspace: Workspace) -> None:
    partial = workspace.roots.datasets / "PARTIAL01"
    write_dataset(partial, recording_id="PARTIAL01", status="partial")
    with pytest.raises(DatasetAdoptError) as excinfo:
        _adopt(workspace, target=str(partial))
    assert excinfo.value.code == "partial_dataset"
    assert [entry["id"] for entry in excinfo.value.next_steps] == ["adopt_allow_partial"]
    assert "--allow-partial" in excinfo.value.next_steps[0]["command"]

    allowed = _adopt(workspace, target=str(partial), allow_partial=True)
    assert allowed.status == "ok"
    assert allowed.dataset_status == "partial"
    assert any("partial" in warning for warning in allowed.warnings)


def test_ignored_tracks_need_no_flag(workspace: Workspace) -> None:
    """A `skip_category: ignored` track is a decision craig-stt already made.

    The bot was excluded because the run was configured to exclude it. Demanding
    `--allow-skipped-tracks` for that is asking the operator to re-consent to a
    choice they already recorded, so it is adopted — with the category kept.
    """
    result = run_adopt(
        target=str(workspace.dataset_dir),
        roots=workspace.roots,
        config=_config(),
    )
    assert result.status == "ok"
    assert [row["track"] for row in result.skipped_tracks] == [3]
    assert result.skipped_tracks[0]["skip_category"] == "ignored"
    assert result.skipped_tracks[0]["deliberately_ignored"] is True
    check = next(c for c in result.checks if c["check"] == "tracks_transcribed")
    assert check["ok"] is True
    assert check["blocking"] == []
    assert any("deliberately ignored" in warning for warning in result.warnings)


def test_failed_tracks_are_gated_and_the_flag_accepts_them(workspace: Workspace) -> None:
    """`failed` is a speaker this dataset lost: adopt refuses, the operator may override."""
    from .conftest import failed_track_stats

    lost = workspace.roots.datasets / "FAILED01"
    write_dataset(lost, recording_id="FAILED01", lost_tracks=[failed_track_stats()])
    with pytest.raises(DatasetAdoptError) as excinfo:
        _adopt(workspace, target=str(lost), allow_skipped_tracks=False)
    error = excinfo.value
    assert error.code == "tracks_not_transcribed"
    assert error.detail["tracks"] == 4
    assert error.detail["tracks_transcribed"] == 2
    # The deliberately-ignored bot is still reported, but it is not what blocks.
    assert [row["track"] for row in error.detail["skipped"]] == [3, 4]
    assert [row["track"] for row in error.detail["blocking"]] == [4]
    assert error.detail["uncategorised"] == []
    assert error.detail["unaccounted"] == 0
    # The blocking track is named with username, category and reason.
    assert "carol" in error.message and "failed" in error.message
    assert "decode failed: unexpected end of stream" in error.message
    assert "--allow-skipped-tracks" in error.message
    # An agent-fixable gate names the exact re-run, it does not leave next_steps empty.
    assert [entry["id"] for entry in error.next_steps] == ["adopt_allow_skipped_tracks"]
    assert "--allow-skipped-tracks" in error.next_steps[0]["command"]

    allowed = _adopt(workspace, target=str(lost), allow_skipped_tracks=True)
    assert allowed.status == "ok"
    assert [row["skip_category"] for row in allowed.skipped_tracks] == ["ignored", "failed"]
    assert any("--allow-skipped-tracks" in warning for warning in allowed.warnings)


def test_a_skip_with_no_category_is_refused_and_no_flag_lifts_it(workspace: Workspace) -> None:
    """A missing category is a missing *fact*, so there is nothing to consent to.

    The dataset is a regenerable artifact written by an older craig-stt; the only
    way to learn whether the speaker was left out or lost is to transcribe the
    local archive again with a craig-stt that records it. `--allow-skipped-tracks`
    would be the operator asserting a fact nobody has.
    """
    lost = workspace.roots.datasets / "LOST01"
    write_dataset(lost, recording_id="LOST01", with_lost_tracks=True)
    archive = lost / "source" / "LOST01.flac.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"not really a zip")

    for allow_skipped_tracks in (False, True):
        with pytest.raises(DatasetAdoptError) as excinfo:
            _adopt(workspace, target=str(lost), allow_skipped_tracks=allow_skipped_tracks)
        error = excinfo.value
        assert error.code == "tracks_missing_skip_category"
        # Reported in full, but only the categoryless track is why adopt refused.
        assert [row["track"] for row in error.detail["skipped"]] == [3, 4, 5]
        assert [row["track"] for row in error.detail["uncategorised"]] == [5]
        assert "dave" in error.message and "no skip_category" in error.message
        assert "--allow-skipped-tracks does not help" in error.message
        # The repair is a re-transcription of the archive already on disk.
        assert [entry["id"] for entry in error.next_steps] == [
            "craig_transcribe_current",
            "retry_adopt",
        ]
        command = error.next_steps[0]["command"]
        assert command == f".agents/bin/craig-stt transcribe {archive} --json"
        assert error.detail["archive"] == str(archive)

    assert not workspace.roots.session(SESSION_ID).session_json.exists()


def test_a_shortfall_with_no_track_row_still_gates(workspace: Workspace) -> None:
    """counts, not just the track list: a vanished track has no category to report."""
    from craig_stt_dataset import build_manifest, write_manifest

    ghost = workspace.roots.datasets / "GHOST01"
    write_dataset(ghost, recording_id="GHOST01", all_tracks_transcribed=True, with_manifest=False)
    meta = ghost / "meta.json"
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload["counts"]["tracks"] += 1
    meta.write_text(json.dumps(payload), encoding="utf-8")
    write_manifest(
        ghost,
        build_manifest(ghost, recording_id="GHOST01", produced_by={"craig-stt": "0.9.0"}),
    )

    with pytest.raises(DatasetAdoptError) as excinfo:
        _adopt(workspace, target=str(ghost), allow_skipped_tracks=False)
    assert excinfo.value.detail["unaccounted"] == 1
    assert "no entry in meta.tracks" in excinfo.value.message


def test_all_tracks_transcribed_needs_no_flag(workspace: Workspace) -> None:
    clean = workspace.roots.datasets / "CLEAN01"
    write_dataset(clean, recording_id="CLEAN01", all_tracks_transcribed=True)
    result = run_adopt(
        target=str(clean),
        roots=workspace.roots,
        config=_config(),
    )
    assert result.status == "ok"
    assert result.skipped_tracks == []


# ------------------------------------------------------------------- the write


def test_session_json_records_the_link(workspace: Workspace) -> None:
    result = _adopt(workspace)
    assert result.status == "ok"
    assert result.session == SESSION_ID
    assert result.dataset_digest.startswith("sha256:")

    payload = json.loads(Path(result.session_json).read_text(encoding="utf-8"))
    link = SessionLink.from_dict(payload)
    assert link.id == SESSION_ID
    assert link.active_run == 1
    assert [r.recording_id for r in link.recordings] == [RECORDING_ID]
    assert link.dataset_digest == result.dataset_digest
    # The skip is kept with the category that says it was deliberate, not as a
    # bare track number a later reader would have to re-derive from the dataset.
    assert link.recordings[0].skipped_tracks == (
        SkippedTrack(
            track=3,
            user_id="u-bot",
            username="craig-bot",
            skip_category="ignored",
            skip_reason="ignored: bot",
        ),
    )
    assert payload["recordings"][0]["skipped_tracks"][0]["skip_category"] == "ignored"
    assert payload["schema"] == "ttrpg.session-link/1"


def test_adopt_is_skip_if_done_on_the_digest(workspace: Workspace) -> None:
    first = _adopt(workspace)
    assert first.status == "ok"
    again = _adopt(workspace)
    assert again.status == "skipped"
    forced = _adopt(workspace, force=True)
    assert forced.status == "ok"


def test_second_run_does_not_move_the_active_run(workspace: Workspace) -> None:
    _adopt(workspace)
    # A re-transcription shifts the text, and with it the digest.
    rerun_dir = _rerun_dataset(workspace.roots.scratch / "run2" / RECORDING_ID, "Реплика 0!")

    second = _adopt(workspace, target=str(rerun_dir), run=2)
    assert second.run == 2
    assert second.active_run == 1

    promoted = _adopt(workspace, target=str(rerun_dir), run=2, promote=True)
    assert promoted.active_run == 2


def test_promote_refuses_when_a_chronicle_exists(workspace: Workspace) -> None:
    _adopt(workspace)
    workspace.write_chronicle()
    with pytest.raises(DatasetAdoptError) as excinfo:
        _adopt(workspace, promote=True, force=True)
    assert excinfo.value.code == "chronicle_exists"

    forced = _adopt(workspace, promote=True, force=True, force_relink=True)
    assert forced.status == "ok"
    assert any("re-linking" in warning for warning in forced.warnings)


def test_readopting_the_active_run_over_a_chronicle_refuses(workspace: Workspace) -> None:
    """One session = one transcription adoption — enforced, not just documented.

    Replacing the active dataset regenerates every turn ID at the next render,
    so the chronicle's approved evidence links would silently point at the wrong
    words. `--force` does not lift this: it is the provenance rewrite flag, not
    consent to break the note.
    """
    _adopt(workspace)
    workspace.write_chronicle()
    rerun_dir = _rerun_dataset(workspace.roots.scratch / "redo" / RECORDING_ID, "Реплика 0…")
    with pytest.raises(DatasetAdoptError) as excinfo:
        _adopt(workspace, target=str(rerun_dir), force=True)
    error = excinfo.value
    assert error.code == "chronicle_exists"
    # Both escape hatches are spelled out: a comparison run, or a deliberate relink.
    assert [entry["id"] for entry in error.next_steps] == ["adopt_new_run", "relink_chronicle"]

    relinked = _adopt(workspace, target=str(rerun_dir), force_relink=True)
    assert relinked.status == "ok"
    assert any("re-linking" in warning for warning in relinked.warnings)


def test_readopting_a_comparison_run_beside_a_chronicle_stays_free(
    workspace: Workspace,
) -> None:
    """An A/B run does not touch the active dataset, so the chronicle is safe."""
    _adopt(workspace)
    workspace.write_chronicle()
    rerun_dir = _rerun_dataset(workspace.roots.scratch / "run2" / RECORDING_ID, "Реплика 0!")
    second = _adopt(workspace, target=str(rerun_dir), run=2)
    assert second.status == "ok"
    assert second.active_run == 1


def test_readopting_the_same_dataset_still_skips_if_done(workspace: Workspace) -> None:
    """The gate watches the digest, so an identical re-adopt stays a no-op skip."""
    _adopt(workspace)
    workspace.write_chronicle()
    again = _adopt(workspace)
    assert again.status == "skipped"


def test_promotion_invalidates_downstream_provenance(workspace: Workspace) -> None:
    _adopt(workspace)
    tree = workspace.roots.session(SESSION_ID)
    provenance = Provenance.load(tree.provenance_json)
    from session_ingest.provenance import CompositeKey

    provenance.mark_done("render", CompositeKey(dataset_digest="sha256:old"), outputs=[])
    assert provenance.record("render") is not None

    rerun_dir = _rerun_dataset(workspace.roots.scratch / "run2" / RECORDING_ID, "Реплика 0?")
    result = _adopt(workspace, target=str(rerun_dir), run=2, promote=True)
    assert "render" in result.invalidated_stages
    assert Provenance.load(tree.provenance_json).record("render") is None


def test_next_steps_point_at_qa(workspace: Workspace) -> None:
    result = _adopt(workspace)
    assert [entry["id"] for entry in result.next_steps] == ["qa"]
    assert f"--session {SESSION_ID}" in result.next_steps[0]["command"]
