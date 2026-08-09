"""The chain, and the one rule that governs it: never point an agent at a no-op."""

from __future__ import annotations

from session_ingest.nextsteps import METERED_VERBS, next_steps_for

SESSION = "2026-08-08"


def _ids(verb: str, **kwargs) -> list[str]:
    return [entry["id"] for entry in next_steps_for(verb, session_id=SESSION, **kwargs)]


def test_the_happy_chain_in_order() -> None:
    assert _ids("plan") == ["craig_transcribe", "adopt"]
    assert _ids("adopt") == ["qa"]
    assert _ids("qa", api_key_present=True) == [
        "render",
        "qmd_refresh",
        "segment",
        "extract",
        "recap",
        "record",
    ]
    assert _ids("render", api_key_present=True) == [
        "qmd_refresh",
        "segment",
        "extract",
        "recap",
        "record",
    ]
    assert _ids("extract", api_key_present=True) == ["recap", "record"]
    assert _ids("recap") == ["record"]


def test_metered_steps_vanish_without_a_key() -> None:
    keyed = next_steps_for("qa", session_id=SESSION, api_key_present=True)
    keyless = next_steps_for("qa", session_id=SESSION, api_key_present=False)
    metered = {entry["id"] for entry in keyed if entry.get("metered")}
    assert metered == {"segment", "extract", "recap"}
    assert metered.isdisjoint({entry["id"] for entry in keyless})
    # record is deterministic and must survive the keyless path.
    assert "record" in {entry["id"] for entry in keyless}


def test_every_metered_verb_is_marked_metered_somewhere() -> None:
    emitted = {
        entry["id"]
        for verb in ("qa", "render", "segment", "extract")
        for entry in next_steps_for(verb, session_id=SESSION, api_key_present=True)
        if entry.get("metered")
    }
    crossed = {
        entry["id"]
        for entry in next_steps_for(
            "qa", session_id=SESSION, api_key_present=True, thresholds_crossed=True
        )
        if entry.get("metered")
    }
    assert (emitted | crossed) == METERED_VERBS


def test_crossed_thresholds_switch_to_the_rerun_chain() -> None:
    steps = next_steps_for(
        "qa", session_id=SESSION, api_key_present=True, thresholds_crossed=True, run=1
    )
    assert [entry["id"] for entry in steps] == [
        "glossary",
        "plan_rerun",
        "craig_transcribe_rerun",
        "adopt_rerun",
        "qa_compare",
        "promote",
    ]
    commands = {entry["id"]: entry.get("command", "") for entry in steps}
    assert "--run 2" in commands["plan_rerun"]
    assert "--compare 1" in commands["qa_compare"]
    assert "--work-dir" in commands["craig_transcribe_rerun"]
    assert "--promote" in commands["promote"]


def test_the_rerun_work_dir_is_a_global_flag_not_an_env_prefix() -> None:
    """`.agents/env.sh` re-exports CRAIG_STT_WORK_DIR, so a prefix is silently lost.

    The launcher sources the environment contract *after* the caller's environment
    is in place, so `CRAIG_STT_WORK_DIR=… .agents/bin/craig-stt transcribe …` writes
    into the live corpus instead of the scratch dir — on top of the very run the
    re-transcription is supposed to be compared against. The global flag outranks
    the environment, and being global it has to precede the subcommand.
    """
    step = next(
        entry
        for entry in next_steps_for(
            "qa", session_id=SESSION, thresholds_crossed=True, scratch_dir="/scratch/s-r2"
        )
        if entry["id"] == "craig_transcribe_rerun"
    )
    command = step["command"]
    assert "CRAIG_STT_WORK_DIR=" not in command
    assert command.startswith(".agents/bin/craig-stt --work-dir /scratch/s-r2 transcribe ")
    assert command.index("--work-dir") < command.index("transcribe")
    assert "GLOBAL" in step["summary"]


def test_every_transcribe_command_asks_for_json() -> None:
    """craig-stt has a machine contract of its own; the chain must not drop it.

    Without ``--json`` the agent gets a Rich table and has to guess where the
    dataset landed, which is exactly the scrape this project's `next_steps`
    convention exists to avoid.
    """
    emitted = [
        *next_steps_for("plan", session_id=SESSION),
        *next_steps_for("qa", session_id=SESSION, thresholds_crossed=True),
    ]
    commands = [entry["command"] for entry in emitted if entry["id"].startswith("craig_transcribe")]
    assert len(commands) == 2
    assert all(command.endswith(" --json") for command in commands)


def test_the_rerun_chain_never_re_downloads() -> None:
    step = next(
        entry
        for entry in next_steps_for("qa", session_id=SESSION, thresholds_crossed=True)
        if entry["id"] == "craig_transcribe_rerun"
    )
    assert "LOCAL archive" in step["summary"]


def test_session_flag_is_threaded_into_every_command() -> None:
    for verb in ("plan", "adopt", "qa", "render", "extract", "recap"):
        for entry in next_steps_for(verb, session_id=SESSION, api_key_present=True):
            command = entry.get("command", "")
            if command.startswith(".agents/bin/session-ingest"):
                assert f"--session {SESSION}" in command, (verb, command)


def test_record_points_the_agent_at_layer_two() -> None:
    steps = next_steps_for("record", session_id=SESSION)
    assert [entry["id"] for entry in steps] == [
        "view",
        "view_owner_queue",
        "ingest_chronicle",
        "chronicle_check",
    ]
    # The compact read comes before the authoring step on purpose: record.json is
    # ~10x the size of its own digest, and an agent told to "read record.json"
    # will do exactly that.
    assert "Do NOT page record.json" in steps[0]["summary"]
    assert "no write path into vault/notes/" in steps[2]["summary"]


def test_chronicle_check_walks_repair_review_freeze_then_prune() -> None:
    """The chain is the protocol: fix → owner review in chat → freeze → prune."""
    dirty = next_steps_for("chronicle", session_id=SESSION, clean=False)
    assert [entry["id"] for entry in dirty] == ["fix_chronicle", "recheck"]
    draft = next_steps_for("chronicle", session_id=SESSION, clean=True)
    assert [entry["id"] for entry in draft] == ["owner_review", "recheck"]
    assert "IN CHAT" in draft[0]["summary"].upper()
    unfrozen = next_steps_for("chronicle", session_id=SESSION, clean=True, canon=True)
    assert [entry["id"] for entry in unfrozen] == ["freeze"]
    frozen = next_steps_for("chronicle", session_id=SESSION, clean=True, canon=True, frozen=True)
    assert [entry["id"] for entry in frozen] == ["prune"]


def test_terminal_verbs_have_no_next_step() -> None:
    assert next_steps_for("doctor") == []
    assert next_steps_for("grep", session_id=SESSION) == []
    assert next_steps_for("prune", session_id=SESSION) == []
