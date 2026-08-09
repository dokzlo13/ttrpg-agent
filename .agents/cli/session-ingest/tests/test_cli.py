"""The agent-facing surface: the JSON envelope, the stderr status line, exit codes."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from session_ingest import render as render_mod
from session_ingest.__main__ import cli
from session_ingest.errors import NotImplementedStage

from .conftest import MORPH_LEXICON_YAML, RECORDING_ID, SESSION_ID, Workspace


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _invoke(runner: CliRunner, workspace: Workspace, args: list[str], **env_overrides):
    return runner.invoke(cli, args, env=workspace.cli_env(**env_overrides), catch_exceptions=False)


def _adopt(runner: CliRunner, workspace: Workspace, *extra: str):
    return _invoke(
        runner,
        workspace,
        ["--json", "adopt", str(workspace.dataset_dir), "--allow-skipped-tracks", *extra],
    )


# ------------------------------------------------------------------- doctor


def test_doctor_json_envelope(runner: CliRunner, workspace: Workspace) -> None:
    result = _invoke(runner, workspace, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["tool"] == "session-ingest"
    assert payload["verb"] == "doctor"
    assert payload["status"] in {"ok", "review", "failed"}
    assert payload["sdk"]["package"] == "craig-stt-dataset"
    assert payload["llm"]["openai_api_key_present"] is False
    assert payload["datasets"][0]["recording_id"] == RECORDING_ID
    assert set(payload["roots"]) == {"sessions", "datasets", "transcripts", "notes", "scratch"}
    assert "session_derived_bytes" in payload["disk"]


def test_doctor_never_prints_the_key(runner: CliRunner, workspace: Workspace) -> None:
    result = _invoke(runner, workspace, ["doctor", "--json"], OPENAI_API_KEY="sk-do-not-leak")
    assert "sk-do-not-leak" not in result.output
    assert json.loads(result.stdout)["llm"]["openai_api_key_present"] is True


def test_doctor_reports_producer_version_skew(runner: CliRunner, workspace: Workspace) -> None:
    payload = json.loads(_invoke(runner, workspace, ["doctor", "--json"]).stdout)
    assert payload["sdk"]["newest_producer_version"] == "1.2.0"
    assert payload["vault_files"]["speakers"]["present"] is False


def test_doctor_human_output(runner: CliRunner, workspace: Workspace) -> None:
    result = _invoke(runner, workspace, ["doctor"])
    assert result.exit_code == 0
    assert "Status:" in result.stdout
    assert "craig-stt-dataset" in result.stdout


def test_doctor_reports_the_resolved_work_dir_and_how_to_redirect_it(
    runner: CliRunner, workspace: Workspace
) -> None:
    """The work dir is contract-owned, and the obvious override fails silently.

    `.agents/env.sh` exports CRAIG_STT_WORK_DIR unconditionally and the launcher
    sources it *after* the caller's environment, so an env prefix never reaches
    craig-stt. Doctor has to say which directory a transcription will actually
    land in, and that `--work-dir` is the flag that moves it.
    """
    payload = json.loads(_invoke(runner, workspace, ["doctor", "--json"]).stdout)
    tools = payload["tools"]
    assert tools["craig_stt_work_dir"] == str(workspace.roots.datasets)
    assert tools["craig_stt_work_dir_source"] == "TTRPG_SESSION_DATASETS_DIR"
    note = tools["craig_stt_work_dir_note"]
    assert "--work-dir" in note
    assert "clobbered" in note
    assert ".agents/env.sh" in note

    human = _invoke(runner, workspace, ["doctor"]).stdout
    assert str(workspace.roots.datasets) in human


def test_doctor_warns_when_the_environment_carries_a_different_work_dir(
    runner: CliRunner, workspace: Workspace, tmp_path
) -> None:
    stray = tmp_path / "elsewhere"
    payload = json.loads(
        _invoke(runner, workspace, ["doctor", "--json"], CRAIG_STT_WORK_DIR=str(stray)).stdout
    )
    finding = next(f for f in payload["findings"] if f["code"] == "craig_stt_work_dir_overridden")
    assert finding["severity"] == "warning"
    assert finding["detail"]["env_value"] == str(stray)
    assert finding["detail"]["datasets_root"] == str(workspace.roots.datasets)
    assert payload["tools"]["craig_stt_work_dir_in_env"] == str(stray)
    # The launcher wins, so the reported work dir is still the contract's.
    assert payload["tools"]["craig_stt_work_dir"] == str(workspace.roots.datasets)


def test_doctor_is_quiet_when_the_work_dir_matches_the_contract(
    runner: CliRunner, workspace: Workspace
) -> None:
    payload = json.loads(
        _invoke(
            runner,
            workspace,
            ["doctor", "--json"],
            CRAIG_STT_WORK_DIR=str(workspace.roots.datasets),
        ).stdout
    )
    codes = {f["code"] for f in payload["findings"]}
    assert "craig_stt_work_dir_overridden" not in codes


# ------------------------------------------------------------------- status


def test_status_line_goes_to_stderr_with_sources(runner: CliRunner, workspace: Workspace) -> None:
    result = runner.invoke(
        cli,
        ["render", "--session", SESSION_ID, "--window-minutes", "20", "--json"],
        env=workspace.cli_env(TTRPG_SESSION_MERGE_GAP_S="2.0"),
        catch_exceptions=False,
    )
    assert result.stderr.startswith("session-ingest: ")
    assert f"session={SESSION_ID}[cli]" in result.stderr
    assert "window=20m[cli]" in result.stderr
    assert "merge_gap=2.0s[env]" in result.stderr
    assert "session-ingest:" not in result.stdout


def test_adopt_status_line_carries_the_manifest_digest(
    runner: CliRunner, workspace: Workspace
) -> None:
    result = runner.invoke(
        cli,
        ["adopt", str(workspace.dataset_dir), "--allow-skipped-tracks", "--json"],
        env=workspace.cli_env(),
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.stderr
    assert "dataset=sha256:" in result.stderr
    assert "[manifest]" in result.stderr
    assert f"session={SESSION_ID}[manifest]" in result.stderr


# -------------------------------------------------------------------- adopt


def test_adopt_json_envelope(runner: CliRunner, workspace: Workspace) -> None:
    result = _adopt(runner, workspace)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verb"] == "adopt"
    assert payload["status"] == "ok"
    assert payload["session"] == SESSION_ID
    assert payload["recording_id"] == RECORDING_ID
    assert payload["dataset_digest"].startswith("sha256:")
    assert [step["id"] for step in payload["next_steps"]] == ["qa"]


def test_adopt_failure_is_a_json_envelope_with_the_repair(
    runner: CliRunner, workspace: Workspace
) -> None:
    from .conftest import failed_track_stats, write_dataset

    # A `skip_category: ignored` track adopts without a flag, so the gate has to be
    # tripped by a skip that genuinely lost a speaker.
    lost = write_dataset(
        workspace.roots.datasets / "FAILED01",
        recording_id="FAILED01",
        lost_tracks=[failed_track_stats()],
    )
    result = _invoke(runner, workspace, ["--json", "adopt", str(lost)])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["code"] == "tracks_not_transcribed"
    assert payload["detail"]["tracks"] == 4
    assert [row["track"] for row in payload["detail"]["blocking"]] == [4]
    assert "--allow-skipped-tracks" in payload["next_steps"][0]["command"]


def test_adopt_missing_manifest_emits_the_craig_repair(
    runner: CliRunner, workspace: Workspace
) -> None:
    from .conftest import write_dataset

    bare = workspace.roots.datasets / "NOMANIFEST"
    write_dataset(bare, recording_id="NOMANIFEST", with_manifest=False)
    result = _invoke(runner, workspace, ["--json", "adopt", str(bare)])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["code"] == "missing_manifest"
    assert payload["next_steps"][0]["command"].startswith(".agents/bin/craig-stt manifest")


# ----------------------------------------------------------------------- qa


def test_qa_json_envelope_after_adopt(runner: CliRunner, workspace: Workspace) -> None:
    workspace.write_lexicon()
    workspace.write_speakers()
    assert _adopt(runner, workspace).exit_code == 0

    result = _invoke(runner, workspace, ["qa", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verb"] == "qa"
    assert payload["status"] == "ok"
    assert payload["session"] == SESSION_ID
    metrics = payload["report"]["metrics"]
    assert metrics["word_p10"] == 0.55
    assert metrics["lexicon_miss_rate"] == pytest.approx(0.6)
    assert {entry["metric"] for entry in payload["thresholds_crossed"]} == {
        "word_p10",
        "bleed_rate",
        "lexicon_miss_rate",
    }


def test_qa_human_output_lists_the_metrics(runner: CliRunner, workspace: Workspace) -> None:
    assert _adopt(runner, workspace).exit_code == 0
    result = _invoke(runner, workspace, ["qa"])
    assert result.exit_code == 0
    for metric in ("word_p10", "bleed_rate", "overlap_rate", "tracks_missing"):
        assert metric in result.stdout


def test_qa_human_output_spells_out_the_echo_signals(capsys: pytest.CaptureFixture[str]) -> None:
    """A reader of the terminal must not have to know what a signal name means."""
    from session_ingest.__main__ import _print_qa
    from session_ingest.qa import compare_runs

    comparison = compare_runs(
        {
            "run": 1,
            "metrics": {
                "words_with_probability": 30_000,
                "compression_outlier_count": 6,
                "lexicon_miss_rate": 0.40,
                "lexicon_terms": [{"term_id": "vagzar", "canonical": "Вазгар", "total_hits": 100}],
            },
        },
        {
            "run": 2,
            "metrics": {
                "words_with_probability": 30_130,
                "compression_outlier_count": 85,
                "lexicon_miss_rate": 0.10,
                "lexicon_terms": [{"term_id": "vagzar", "canonical": "Вазгар", "total_hits": 512}],
            },
        },
    )
    _print_qa(
        {
            "status": "ok",
            "session": SESSION_ID,
            "run": 2,
            "qa_path": "/tmp/qa.json",
            "report": {"metrics": {}},
            "comparison": comparison,
        }
    )
    out = capsys.readouterr().out
    assert "Echo check:" in out
    assert "lexicon_term_hits" in out and "delta=412" in out
    assert "term_hits_outran_words: Lexicon-term hits grew far faster" in out
    assert "compression_outliers_jumped: Segments above whisper" in out
    assert "prompt echo" in out


def test_qa_human_output_says_so_when_no_signal_was_raised(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from session_ingest.__main__ import _print_qa
    from session_ingest.qa import compare_runs

    quiet = compare_runs(
        {"run": 1, "metrics": {"words_with_probability": 100, "compression_outlier_count": 1}},
        {"run": 2, "metrics": {"words_with_probability": 120, "compression_outlier_count": 1}},
    )
    _print_qa(
        {
            "status": "ok",
            "session": SESSION_ID,
            "run": 2,
            "qa_path": "/tmp/qa.json",
            "report": {"metrics": {}},
            "comparison": quiet,
        }
    )
    assert "signals: none" in capsys.readouterr().out


def test_qa_without_an_adopted_session_names_adopt(runner: CliRunner, workspace: Workspace) -> None:
    result = _invoke(runner, workspace, ["--json", "qa"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["code"] == "no_session"
    assert payload["next_steps"][0]["id"] == "adopt"


def test_session_id_comes_from_the_environment_when_not_given(
    runner: CliRunner, workspace: Workspace
) -> None:
    assert _adopt(runner, workspace).exit_code == 0
    result = _invoke(runner, workspace, ["--json", "qa"], TTRPG_SESSION_ID=SESSION_ID)
    assert json.loads(result.stdout)["session"] == SESSION_ID


# --------------------------------------------------------- unbuilt stages


def _stub_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make one verb behave like an unbuilt stage.

    The exit-2 envelope is a property of ``execute``, not of whichever verbs
    happen to be stubs this week, so it is tested through the mechanism. A
    hand-maintained list of stub verbs would go stale on the day each one lands.
    """

    def _unbuilt(**_: object) -> dict:
        raise NotImplementedStage("render", summary="transcript chunk rendering")

    monkeypatch.setattr(render_mod, "run", _unbuilt)


def test_an_unbuilt_stage_exits_2_with_not_implemented(
    runner: CliRunner, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_render(monkeypatch)
    result = _invoke(runner, workspace, ["--json", "render", "--session", SESSION_ID])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "not_implemented"
    assert payload["verb"] == "render"
    assert payload["next_steps"] == []


def test_an_unbuilt_stage_still_emits_its_status_line(
    runner: CliRunner, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_render(monkeypatch)
    result = runner.invoke(
        cli, ["render", "--session", SESSION_ID], env=workspace.cli_env(), catch_exceptions=False
    )
    assert result.exit_code == 2
    assert result.stderr.startswith("session-ingest: ")


def test_metered_stub_status_line_reports_key_absence(
    runner: CliRunner, workspace: Workspace
) -> None:
    result = runner.invoke(
        cli, ["extract", "--session", SESSION_ID], env=workspace.cli_env(), catch_exceptions=False
    )
    assert "key=absent" in result.stderr
    assert "llm.model=" in result.stderr


# ---------------------------------------------------------------- surface


def test_every_designed_verb_is_registered(runner: CliRunner) -> None:
    expected = {
        "doctor",
        "plan",
        "adopt",
        "qa",
        "render",
        "grep",
        "segment",
        "extract",
        "recap",
        "record",
        "glossary",
        "prune",
    }
    assert expected <= set(cli.commands)


def test_unsourced_environment_fails_with_the_launcher_hint(
    runner: CliRunner, workspace: Workspace
) -> None:
    result = runner.invoke(
        cli,
        ["--json", "qa", "--session", SESSION_ID],
        env={"TTRPG_ROOT": str(workspace.project_root), "TTRPG_SESSIONS_DIR": None},
        catch_exceptions=False,
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["code"] == "environment_contract"
    assert payload["detail"]["missing"] == "TTRPG_SESSIONS_DIR"
    assert payload["next_steps"][0]["id"] == "use_launcher"


def test_json_flag_is_accepted_on_either_side(runner: CliRunner, workspace: Workspace) -> None:
    before = _invoke(runner, workspace, ["--json", "doctor"])
    after = _invoke(runner, workspace, ["doctor", "--json"])
    assert json.loads(before.stdout)["verb"] == json.loads(after.stdout)["verb"] == "doctor"


# ------------------------------------------------------------------ lexicon


def test_lexicon_lists_terms_without_touching_anything(
    runner: CliRunner, workspace: Workspace
) -> None:
    workspace.write_lexicon(MORPH_LEXICON_YAML)
    result = _invoke(runner, workspace, ["lexicon", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verb"] == "lexicon"
    assert payload["status"] == "ok"
    assert payload["term_count"] == 2
    assert payload["morph_term_count"] == 1
    assert payload["expanded"] is False
    assert "expansion" not in payload
    assert [term["id"] for term in payload["terms"]] == ["morvika", "morvika-gen"]
    assert payload["terms"][0]["morph"] is True


def test_lexicon_expand_returns_every_generated_pair_and_reason(
    runner: CliRunner, workspace: Workspace
) -> None:
    workspace.write_lexicon(MORPH_LEXICON_YAML)
    payload = json.loads(_invoke(runner, workspace, ["lexicon", "--expand", "--json"]).stdout)

    assert payload["expanded"] is True
    table = payload["expansion"]
    assert table["schema"] == "ttrpg.session-morph-expansion/1"
    assert table["digest"].startswith("sha256:")
    assert table["versions"]["pymorphy3"] and table["versions"]["pymorphy3_dicts_ru"]

    rows = {row["term_id"]: row for row in payload["expansion_by_term"]}
    assert set(rows) == {"morvika"}, "only opted-in terms appear"
    pairs = {(p["case"], p["variant"], p["display"]) for p in rows["morvika"]["pairs"]}
    assert ("datv", "Марвике", "Морвике") in pairs
    assert ("ablt", "Марвикой", "Морвикой") in pairs
    assert "Марвики" not in {variant for _, variant, _ in pairs}
    assert any(
        note["kind"] == "excluded" and note["subject"] == "Марвики"
        for note in rows["morvika"]["notes"]
    )


def test_lexicon_is_readable_without_a_session(runner: CliRunner, workspace: Workspace) -> None:
    """No dataset, no adopted session, no writes — pure inspection."""
    result = _invoke(runner, workspace, ["lexicon"])
    assert result.exit_code == 0, result.output
    assert "absent" in result.stdout
    assert not (workspace.roots.sessions / SESSION_ID).exists()
