"""``prune`` — the only verb that deletes, and the one that must never over-delete."""

from __future__ import annotations

from pathlib import Path

import pytest
from craig_stt_dataset import build_manifest, prunable_roles, write_manifest

from session_ingest import prune
from session_ingest.adopt import run_adopt
from session_ingest.errors import SessionIngestError

from .conftest import RECORDING_ID, SESSION_ID, Workspace, write_dataset

PCM_BYTES = 4096
STT_BYTES = 2048
EXTRACTED_BYTES = 1024
ARCHIVE_BYTES = 512


def make_prunable(dataset_dir: Path, *, recording_id: str = RECORDING_ID) -> Path:
    """Give the synthetic dataset the regenerable roles a real one carries."""
    (dataset_dir / "pcm").mkdir(exist_ok=True)
    (dataset_dir / "pcm" / "1.wav").write_bytes(b"p" * PCM_BYTES)
    (dataset_dir / "stt").mkdir(exist_ok=True)
    (dataset_dir / "stt" / "1.json").write_bytes(b"s" * STT_BYTES)
    (dataset_dir / "source").mkdir(exist_ok=True)
    (dataset_dir / "source" / "extracted").mkdir(exist_ok=True)
    (dataset_dir / "source" / "extracted" / "1.flac").write_bytes(b"e" * EXTRACTED_BYTES)
    archive = dataset_dir / "source" / f"{recording_id}.flac.zip"
    archive.write_bytes(b"z" * ARCHIVE_BYTES)

    write_manifest(
        dataset_dir,
        build_manifest(
            dataset_dir,
            recording_id=recording_id,
            produced_by={"craig-stt": "0.9.0", "craig-stt-dataset": "1.2.0"},
            archive=archive,
        ),
    )
    return archive


@pytest.fixture
def prunable(workspace: Workspace) -> Workspace:
    make_prunable(workspace.dataset_dir)
    workspace.adopt()
    return workspace


def _prune(workspace: Workspace, **kwargs):
    return prune.run(
        roots=workspace.roots,
        config=workspace.config(),
        session_id=SESSION_ID,
        project_root=workspace.project_root,
        **kwargs,
    )


# -------------------------------------------------------------------- gating


def test_refuses_while_the_chronicle_is_unwritten(prunable: Workspace) -> None:
    with pytest.raises(SessionIngestError) as excinfo:
        _prune(prunable)
    assert excinfo.value.code == "chronicle_missing"
    assert [step["id"] for step in excinfo.value.next_steps] == ["record", "prune_forced"]
    assert (prunable.dataset_dir / "pcm").exists()


def test_a_dry_run_inventories_without_a_chronicle_and_says_so(prunable: Workspace) -> None:
    """The gate protects deletion; 'what would this reclaim?' is asked before the chronicle."""
    result = _prune(prunable, dry_run=True)
    assert result["status"] == "ok"
    assert result["dry_run"] is True
    assert result["bytes_freed"] > 0
    assert {row["role"] for row in result["inventory"]} >= {"pcm"}
    assert any("nothing was deleted" in warning for warning in result["warnings"])
    assert (prunable.dataset_dir / "pcm").exists(), "a dry run must delete nothing"


def test_force_overrides_the_gate_loudly(prunable: Workspace) -> None:
    result = _prune(prunable, dry_run=True, force=True)
    assert result["status"] == "ok"
    assert any(warning.startswith("WARNING:") for warning in result["warnings"])
    assert any("WARNING:" in line for line in result["lines"])


def test_a_scratch_dataset_is_refused_as_plain_deletable(workspace: Workspace) -> None:
    workspace.write_chronicle()
    workspace.adopt()
    scratch_dir = workspace.roots.scratch / f"{SESSION_ID}-r2" / "SCRATCH01"
    write_dataset(scratch_dir, recording_id="SCRATCH01")
    make_prunable(scratch_dir, recording_id="SCRATCH01")
    run_adopt(
        target=str(scratch_dir),
        roots=workspace.roots,
        project_root=workspace.project_root,
        config=workspace.config(),
        run=2,
        promote=True,
        allow_skipped_tracks=True,
        force_relink=True,
    )

    with pytest.raises(SessionIngestError) as excinfo:
        _prune(workspace, dry_run=True)
    assert excinfo.value.code == "scratch_dataset"
    assert "disposable" in excinfo.value.message
    assert (scratch_dir / "pcm").exists()


# ----------------------------------------------------------------- inventory


def test_dry_run_inventory_matches_the_manifest_prunable_roles(prunable: Workspace) -> None:
    prunable.write_chronicle()
    result = _prune(prunable, dry_run=True)

    assert result["dry_run"] is True
    assert set(result["roles_available"]) == set(prunable_roles(prunable.dataset_dir))
    assert set(result["roles_available"]) == {"pcm", "stt_cache", "audio_extracted"}
    assert result["roles"] == ["pcm", "audio_extracted"]
    assert result["bytes_freed"] == PCM_BYTES + EXTRACTED_BYTES
    assert result["bytes_by_role"] == {"pcm": PCM_BYTES, "audio_extracted": EXTRACTED_BYTES}
    # nothing moved
    assert (prunable.dataset_dir / "pcm" / "1.wav").exists()
    assert (prunable.dataset_dir / "source" / "extracted" / "1.flac").exists()


def test_the_stt_cache_is_reported_but_never_pruned_by_default(prunable: Workspace) -> None:
    prunable.write_chronicle()
    result = _prune(prunable, dry_run=True)
    deferred = {row["role"]: row for row in result["inventory"] if not row["pruned"]}
    assert "stt_cache" in deferred
    assert "GPU pass" in deferred["stt_cache"]["reason"]
    assert result["roles_deferred"] == ["stt_cache"]


def test_the_wsl_note_is_in_the_human_output(prunable: Workspace) -> None:
    prunable.write_chronicle()
    result = _prune(prunable, dry_run=True)
    assert "VHDX" in result["note"]
    assert any("VHDX" in line for line in result["lines"])
    assert any("would free" in line for line in result["lines"])


# ------------------------------------------------------------------ deleting


def test_pruning_removes_pcm_and_extracted_and_never_the_archive(
    prunable: Workspace,
) -> None:
    prunable.write_chronicle()
    archive = prunable.dataset_dir / "source" / f"{RECORDING_ID}.flac.zip"
    result = _prune(prunable)

    assert result["dry_run"] is False
    assert result["bytes_freed"] == PCM_BYTES + EXTRACTED_BYTES
    assert not (prunable.dataset_dir / "pcm").exists()
    assert not (prunable.dataset_dir / "source" / "extracted").exists()
    # the irreplaceable half of source/ survives, and so does the STT cache
    assert archive.exists() and archive.read_bytes() == b"z" * ARCHIVE_BYTES
    assert (prunable.dataset_dir / "stt" / "1.json").exists()
    # the dataset is still readable and still verifies
    assert (prunable.dataset_dir / "segments.jsonl").exists()
    assert prunable.adopt(force=True).status == "ok"


def test_pruning_twice_is_a_no_op(prunable: Workspace) -> None:
    prunable.write_chronicle()
    _prune(prunable)
    again = _prune(prunable)
    assert again["roles"] == []
    assert again["bytes_freed"] == 0


def test_a_dataset_with_nothing_prunable_reports_zero(workspace: Workspace) -> None:
    workspace.write_chronicle()
    workspace.adopt()
    result = _prune(workspace, dry_run=True)
    assert result["roles"] == []
    assert result["roles_available"] == []
    assert result["bytes_freed"] == 0


def test_prune_is_terminal(prunable: Workspace) -> None:
    prunable.write_chronicle()
    assert _prune(prunable, dry_run=True)["next_steps"] == []
