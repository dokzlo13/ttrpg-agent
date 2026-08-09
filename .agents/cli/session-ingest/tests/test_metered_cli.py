"""The agent-facing surface of the metered verbs plus ``record``.

DESIGN principle 7 in its observable form: on a machine with no
``OPENAI_API_KEY`` the four metered verbs exit **0** with
``{"status": "skipped"}`` and ``record`` still produces a valid handoff. An
agent walking ``next_steps`` must be able to run the whole chain keyless.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from session_ingest.__main__ import cli
from session_ingest.models import validate_record

from .conftest import SESSION_ID, Workspace
from .fakes import write_anchors, write_extraction

METERED = ["segment", "extract", "recap", "glossary"]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _invoke(runner: CliRunner, workspace: Workspace, args: list[str], **env):
    return runner.invoke(cli, args, env=workspace.cli_env(**env), catch_exceptions=False)


def _adopt(runner: CliRunner, workspace: Workspace):
    result = _invoke(
        runner, workspace, ["--json", "adopt", str(workspace.dataset_dir), "--allow-skipped-tracks"]
    )
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("verb", METERED)
def test_metered_verbs_skip_cleanly_without_a_key(
    runner: CliRunner, workspace: Workspace, verb: str
) -> None:
    _adopt(runner, workspace)
    result = _invoke(runner, workspace, ["--json", verb, "--session", SESSION_ID])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verb"] == verb
    assert payload["status"] == "skipped"
    assert payload["code"] == "missing_api_key"
    assert payload["metered"] is True
    assert payload["next_steps"] == []


@pytest.mark.parametrize("verb", METERED)
def test_metered_verbs_report_key_absence_on_stderr(
    runner: CliRunner, workspace: Workspace, verb: str
) -> None:
    _adopt(runner, workspace)
    result = _invoke(runner, workspace, [verb, "--session", SESSION_ID])
    assert "key=absent" in result.stderr
    assert "llm.model=" in result.stderr
    assert "session-ingest:" not in result.stdout


def test_record_runs_keyless_end_to_end(runner: CliRunner, workspace: Workspace) -> None:
    workspace.write_lexicon()
    workspace.write_speakers()
    _adopt(runner, workspace)
    write_anchors(workspace)
    write_extraction(workspace)

    result = _invoke(runner, workspace, ["--json", "record", "--session", SESSION_ID])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verb"] == "record"
    assert payload["status"] == "ok"
    assert payload["schema"] == "ttrpg.session-record/1"
    assert payload["counts"]["events"] == 2
    assert [step["id"] for step in payload["next_steps"]] == [
        "view",
        "view_owner_queue",
        "ingest_chronicle",
        "chronicle_check",
    ]

    record = json.loads(workspace.roots.session(SESSION_ID).record_json.read_text("utf-8"))
    # Without `segment` nothing can know the table-talk share, so the record says so
    # in `session.missing` — and that declaration is itself valid. No waivers.
    assert validate_record(record) == []
    assert record["session"]["missing"] == ["play_time_s", "table_talk_share"]
    assert record["session"]["play_time_s"] is None


def test_record_failure_is_a_json_envelope_naming_render(
    runner: CliRunner, workspace: Workspace
) -> None:
    _adopt(runner, workspace)
    result = _invoke(runner, workspace, ["--json", "record", "--session", SESSION_ID])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["code"] == "anchors_missing"
    assert payload["next_steps"][0]["id"] == "render"


def test_recap_accepts_an_audience(runner: CliRunner, workspace: Workspace) -> None:
    _adopt(runner, workspace)
    result = _invoke(runner, workspace, ["recap", "--session", SESSION_ID, "--audience", "players"])
    assert result.exit_code == 0
    assert "audience=players[cli]" in result.stderr
