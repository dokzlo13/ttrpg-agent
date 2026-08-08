"""CLI wiring for the deterministic verbs: envelope, exit code, human lines.

``test_cli.py`` owns the shared surface (status line, ``--json`` placement, the
unbuilt-stage envelope). This file only asserts that ``plan``, ``render``,
``grep`` and ``prune`` are wired to it correctly — that the payload an agent
parses and the report a human reads come out of the same call.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from session_ingest.__main__ import cli

from .conftest import SESSION_ID, Workspace
from .test_prune import make_prunable


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _invoke(runner: CliRunner, workspace: Workspace, args: list[str], **env_overrides):
    return runner.invoke(cli, args, env=workspace.cli_env(**env_overrides), catch_exceptions=False)


# --------------------------------------------------------------------- plan


def test_plan_json_envelope(runner: CliRunner, workspace: Workspace) -> None:
    workspace.write_lexicon()
    workspace.adopt()
    result = _invoke(runner, workspace, ["--json", "plan", "--session", SESSION_ID])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verb"] == "plan"
    assert payload["status"] == "ok"
    assert [entry["id"] for entry in payload["next_steps"]] == ["craig_transcribe", "adopt"]
    # Biasing is opt-in since it measured as prompt echo; the files are still written.
    assert payload["with_biasing"] is False
    assert "--hotwords-file" not in payload["next_steps"][0]["command"]


def test_plan_with_biasing_flag_reaches_the_emitted_command(
    runner: CliRunner, workspace: Workspace
) -> None:
    workspace.write_lexicon()
    workspace.adopt()
    result = _invoke(
        runner, workspace, ["--json", "plan", "--session", SESSION_ID, "--with-biasing"]
    )
    payload = json.loads(result.stdout)
    assert payload["with_biasing"] is True
    assert "--hotwords-file" in payload["next_steps"][0]["command"]
    assert "biasing=on[cli]" in result.stderr


def test_plan_human_output_reports_both_files_and_the_command(
    runner: CliRunner, workspace: Workspace
) -> None:
    workspace.write_lexicon()
    workspace.adopt()
    result = _invoke(runner, workspace, ["plan", "--session", SESSION_ID])
    assert result.exit_code == 0, result.output
    assert "hotwords:" in result.stdout
    assert "initial_prompt:" in result.stdout
    assert "biasing:" in result.stdout
    assert "Next:" in result.stdout
    assert ".agents/bin/craig-stt transcribe" in result.stdout


def test_plan_budget_flag_reaches_the_verb(runner: CliRunner, workspace: Workspace) -> None:
    workspace.write_lexicon()
    workspace.adopt()
    result = _invoke(
        runner, workspace, ["--json", "plan", "--session", SESSION_ID, "--budget-chars", "15"]
    )
    assert json.loads(result.stdout)["budget_chars"] == 15


# ------------------------------------------------------------------- render


def test_render_json_envelope_and_window_flag(runner: CliRunner, workspace: Workspace) -> None:
    workspace.write_lexicon()
    workspace.write_speakers()
    workspace.adopt()
    result = _invoke(
        runner, workspace, ["--json", "render", "--session", SESSION_ID, "--window-minutes", "1"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verb"] == "render"
    assert payload["status"] == "ok"
    assert payload["window_minutes"] == 1
    assert payload["chunk_count"] == 5
    assert "window=1m[cli]" in result.stderr


def test_render_human_output_names_the_artifacts(runner: CliRunner, workspace: Workspace) -> None:
    workspace.adopt()
    result = _invoke(runner, workspace, ["render", "--session", SESSION_ID])
    assert result.exit_code == 0, result.output
    assert "Transcript:" in result.stdout
    assert "Anchors:" in result.stdout
    assert "Next:" in result.stdout


def test_render_without_an_adopted_session_is_a_json_failure(
    runner: CliRunner, workspace: Workspace
) -> None:
    result = _invoke(runner, workspace, ["--json", "render", "--session", SESSION_ID])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["code"] == "not_adopted"


# --------------------------------------------------------------------- grep


def test_grep_json_and_human_output_agree(runner: CliRunner, workspace: Workspace) -> None:
    workspace.write_lexicon()
    workspace.write_speakers()
    workspace.adopt()
    _invoke(runner, workspace, ["--json", "render", "--session", SESSION_ID])

    as_json = _invoke(
        runner, workspace, ["--json", "grep", "--speaker", "Морвика", "--regex", "Вазгар"]
    )
    assert as_json.exit_code == 0, as_json.output
    payload = json.loads(as_json.stdout)
    assert payload["verb"] == "grep"
    assert payload["matches"] == 5
    assert payload["rows"][0]["evidence"]["link"].startswith(f"[[transcripts/{SESSION_ID}/")

    human = _invoke(runner, workspace, ["grep", "--speaker", "Морвика", "--regex", "Вазгар"])
    assert human.stdout.splitlines() == payload["lines"]


def test_grep_all_reports_the_scope_on_stderr(runner: CliRunner, workspace: Workspace) -> None:
    workspace.adopt()
    _invoke(runner, workspace, ["--json", "render", "--session", SESSION_ID])
    result = _invoke(runner, workspace, ["--json", "grep", "--all", "--regex", "Реплика 1\\."])
    assert result.exit_code == 0, result.output
    assert "scope=all[cli]" in result.stderr
    assert json.loads(result.stdout)["sessions"] == [SESSION_ID]


def test_grep_before_render_names_render(runner: CliRunner, workspace: Workspace) -> None:
    workspace.adopt()
    result = _invoke(runner, workspace, ["--json", "grep", "--regex", "что-нибудь"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["code"] == "index_missing"
    assert payload["next_steps"][0]["id"] == "render"


# -------------------------------------------------------------------- prune


def test_prune_dry_run_json_envelope(runner: CliRunner, workspace: Workspace) -> None:
    make_prunable(workspace.dataset_dir)
    workspace.adopt()
    workspace.write_chronicle()
    result = _invoke(runner, workspace, ["--json", "prune", "--dry-run"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verb"] == "prune"
    assert payload["dry_run"] is True
    assert payload["roles"] == ["pcm", "audio_extracted"]
    assert "dry_run=true[cli]" in result.stderr


def test_prune_human_output_carries_the_inventory_and_the_wsl_note(
    runner: CliRunner, workspace: Workspace
) -> None:
    make_prunable(workspace.dataset_dir)
    workspace.adopt()
    workspace.write_chronicle()
    result = _invoke(runner, workspace, ["prune", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "prune pcm" in result.stdout
    assert "keep  stt_cache" in result.stdout
    assert "VHDX" in result.stdout


def test_prune_refuses_without_a_chronicle(runner: CliRunner, workspace: Workspace) -> None:
    make_prunable(workspace.dataset_dir)
    workspace.adopt()
    result = _invoke(runner, workspace, ["--json", "prune"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["code"] == "chronicle_missing"
    assert (workspace.dataset_dir / "pcm").exists()


def test_prune_dry_run_inventories_without_a_chronicle(
    runner: CliRunner, workspace: Workspace
) -> None:
    make_prunable(workspace.dataset_dir)
    workspace.adopt()
    result = _invoke(runner, workspace, ["--json", "prune", "--dry-run"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["dry_run"] is True
    assert (workspace.dataset_dir / "pcm").exists()
