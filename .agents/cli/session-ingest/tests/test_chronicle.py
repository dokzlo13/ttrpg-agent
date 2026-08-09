"""``chronicle`` — check, freeze, and status over the agent-authored notes."""

from __future__ import annotations

import pytest

from session_ingest import chronicle as chronicle_mod
from session_ingest.errors import SessionIngestError

from .conftest import SESSION_ID, Workspace
from .fakes import adopt, chunk_for, turn_id_for, write_anchors

GOOD_LINK = f"[[transcripts/{SESSION_ID}/{chunk_for(3)}#^{turn_id_for(3)}]]"

FRONTMATTER = f"""\
---
type: session
session: 1
date_real: {SESSION_ID}
transcript: {SESSION_ID}
status: draft
world_days_elapsed: 1
world_time_confidence: estimated
arc: padduck
participants: [morvika]
---
"""

CANON_FRONTMATTER = FRONTMATTER.replace("status: draft", "status: canon")

QUESTIONS = """
## Вопросы владельцу

### P1 — Сколько суток прошло

- принять 0
- принять 1

### P2 — Кто была девочка

- Кейла
- другое
"""


def _prepared(workspace: Workspace) -> None:
    adopt(workspace)
    write_anchors(workspace)


def _note(workspace: Workspace, body: str, *, frontmatter: str = FRONTMATTER):
    path = workspace.roots.chronicles_dir
    path.mkdir(parents=True, exist_ok=True)
    note = path / f"s001-{SESSION_ID}-chapel.md"
    note.write_text(frontmatter + body, encoding="utf-8")
    return note


def _check(workspace: Workspace):
    return chronicle_mod.run(roots=workspace.roots, session_id=SESSION_ID)


def _freeze(workspace: Workspace):
    return chronicle_mod.run_freeze(roots=workspace.roots, session_id=SESSION_ID)


def _status(workspace: Workspace):
    return chronicle_mod.run_status(roots=workspace.roots)


# ------------------------------------------------------------------ discovery


def test_no_chronicle_is_a_typed_refusal_naming_the_directory(workspace: Workspace) -> None:
    _prepared(workspace)
    with pytest.raises(SessionIngestError) as excinfo:
        _check(workspace)
    assert excinfo.value.code == "chronicle_missing"
    assert str(workspace.roots.chronicles_dir) in excinfo.value.detail["expected_dir"]


def test_a_note_in_the_right_place_is_found(workspace: Workspace) -> None:
    _prepared(workspace)
    note = _note(workspace, f"\n# Сессия 1\n\n- Партия обыскала часовню {GOOD_LINK}\n")
    payload = _check(workspace)
    assert payload["chronicles"] == [str(note)]
    assert payload["clean"] is True
    assert payload["links_resolved"] == 1
    assert payload["links_unresolved"] == 0


# ------------------------------------------------------------------ links


def test_an_unknown_turn_id_is_reported_not_silently_passed(workspace: Workspace) -> None:
    _prepared(workspace)
    bad = f"[[transcripts/{SESSION_ID}/{chunk_for(3)}#^t99999-9]]"
    _note(workspace, f"\n- утверждение {bad}\n")
    payload = _check(workspace)
    assert payload["clean"] is False
    assert payload["links_unresolved"] == 1
    assert payload["notes"][0]["links_unresolved"][0]["reason"] == "no such turn id in anchors.json"


def test_a_turn_cited_from_the_wrong_chunk_is_reported(workspace: Workspace) -> None:
    """Obsidian renders this as plain text; it is found at the table, not here."""
    _prepared(workspace)
    wrong = f"[[transcripts/{SESSION_ID}/99-999-999#^{turn_id_for(3)}]]"
    _note(workspace, f"\n- утверждение {wrong}\n")
    payload = _check(workspace)
    assert payload["links_unresolved"] == 1
    assert "not 99-999-999" in payload["notes"][0]["links_unresolved"][0]["reason"]


def test_a_link_into_another_session_is_reported(workspace: Workspace) -> None:
    _prepared(workspace)
    other = f"[[transcripts/2020-01-01/{chunk_for(3)}#^{turn_id_for(3)}]]"
    _note(workspace, f"\n- утверждение {other}\n")
    payload = _check(workspace)
    assert payload["links_unresolved"] == 1
    assert "not " + SESSION_ID in payload["notes"][0]["links_unresolved"][0]["reason"]


def test_repeated_links_are_counted_once(workspace: Workspace) -> None:
    _prepared(workspace)
    _note(workspace, f"\n- один {GOOD_LINK}\n- два {GOOD_LINK}\n")
    assert _check(workspace)["links_resolved"] == 1


def test_a_chronicle_with_no_citations_is_flagged(workspace: Workspace) -> None:
    _prepared(workspace)
    _note(workspace, "\n# Сессия 1\n\n- ничем не подтверждённое утверждение\n")
    payload = _check(workspace)
    assert payload["clean"] is False
    assert "nothing in it is evidenced" in " ".join(payload["notes"][0]["problems"])


# ------------------------------------------------------------------ frontmatter


def test_missing_required_frontmatter_is_named(workspace: Workspace) -> None:
    _prepared(workspace)
    _note(workspace, f"\n- утверждение {GOOD_LINK}\n", frontmatter="---\ntype: session\n---\n")
    payload = _check(workspace)
    assert payload["clean"] is False
    assert set(payload["notes"][0]["missing_frontmatter"]) == {
        "session",
        "date_real",
        "transcript",
        "status",
    }


def test_a_transcript_key_pointing_elsewhere_is_a_problem(workspace: Workspace) -> None:
    _prepared(workspace)
    frontmatter = FRONTMATTER.replace(f"transcript: {SESSION_ID}", "transcript: 2020-01-01")
    _note(workspace, f"\n- утверждение {GOOD_LINK}\n", frontmatter=frontmatter)
    problems = " ".join(_check(workspace)["notes"][0]["problems"])
    assert "does not match" in problems


def test_a_prep_note_filed_as_a_chronicle_is_a_problem(workspace: Workspace) -> None:
    """`type: session` is reserved for play records — that wall is the whole point."""
    _prepared(workspace)
    frontmatter = FRONTMATTER.replace("type: session", "type: prep")
    _note(workspace, f"\n- утверждение {GOOD_LINK}\n", frontmatter=frontmatter)
    problems = " ".join(_check(workspace)["notes"][0]["problems"])
    assert "a play record is `type: session`" in problems


def test_a_note_without_frontmatter_is_reported(workspace: Workspace) -> None:
    _prepared(workspace)
    _note(workspace, f"# Сессия 1\n\n- утверждение {GOOD_LINK}\n", frontmatter="")
    problems = " ".join(_check(workspace)["notes"][0]["problems"])
    assert "does not start with a YAML frontmatter block" in problems


# ------------------------------------------------------------------ next steps


def test_a_clean_draft_points_at_the_owner_review(workspace: Workspace) -> None:
    """The review is an in-chat step: a clean draft waits for the owner, not a tool."""
    _prepared(workspace)
    _note(workspace, f"\n- утверждение {GOOD_LINK}\n" + QUESTIONS)
    payload = _check(workspace)
    assert payload["clean"] is True
    assert [s["id"] for s in payload["next_steps"]] == ["owner_review", "recheck"]


def test_a_clean_canon_note_points_at_freeze(workspace: Workspace) -> None:
    _prepared(workspace)
    _note(workspace, f"\n- утверждение {GOOD_LINK}\n", frontmatter=CANON_FRONTMATTER)
    payload = _check(workspace)
    assert payload["clean"] is True
    assert payload["canon"] is True
    assert payload["frozen"] is False
    assert [s["id"] for s in payload["next_steps"]] == ["freeze"]


def test_a_frozen_canon_note_unblocks_prune(workspace: Workspace) -> None:
    _prepared(workspace)
    _note(workspace, f"\n- утверждение {GOOD_LINK}\n", frontmatter=CANON_FRONTMATTER)
    _freeze(workspace)
    payload = _check(workspace)
    assert payload["frozen"] is True
    assert [s["id"] for s in payload["next_steps"]] == ["prune"]


def test_a_dirty_check_asks_for_a_repair_instead(workspace: Workspace) -> None:
    _prepared(workspace)
    _note(workspace, "\n- ничем не подтверждённое утверждение\n")
    payload = _check(workspace)
    assert [s["id"] for s in payload["next_steps"]] == ["fix_chronicle", "recheck"]


# ------------------------------------------------------- the --json envelope


def test_json_envelope_survives_a_yaml_date(workspace: Workspace) -> None:
    """An unquoted `date_real: 2026-08-08` parses to `datetime.date`.

    Every chronicle carries one, and it is not JSON-serialisable — so the verb
    used to do all its work correctly and then crash inside `json.dumps`, which
    an agent reads as "the check failed". Exercised through the CLI on purpose:
    calling `run()` directly never touches the serialisation path.
    """
    import json

    from click.testing import CliRunner

    from session_ingest.__main__ import cli

    _prepared(workspace)
    _note(workspace, f"\n- утверждение {GOOD_LINK}\n")

    result = CliRunner().invoke(
        cli, ["chronicle", "--session", SESSION_ID, "--json"], env=workspace.cli_env()
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verb"] == "chronicle"
    assert payload["clean"] is True
    assert payload["links_resolved"] == 1
    # stringified rather than dropped — the value is diagnostic
    assert payload["notes"][0]["frontmatter"]["date_real"] == SESSION_ID


def test_json_safe_stringifies_only_what_it_must() -> None:
    import datetime

    assert chronicle_mod.json_safe("x") == "x"
    assert chronicle_mod.json_safe(3) == 3
    assert chronicle_mod.json_safe(None) is None
    assert chronicle_mod.json_safe(datetime.date(2026, 8, 8)) == "2026-08-08"
    assert chronicle_mod.json_safe([datetime.date(2026, 8, 8), 1]) == ["2026-08-08", 1]
    assert chronicle_mod.json_safe({"d": datetime.date(2026, 8, 8)}) == {"d": "2026-08-08"}


# ------------------------------------------------- aliased links (the HIGH bug)


def test_an_aliased_link_is_checked_not_ignored(workspace: Workspace) -> None:
    """`[[…#^turn|подпись]]` is standard Obsidian and routine in authored prose.

    The first LINK_RE excluded `|` from the block group without accepting an
    alias tail, so an aliased citation matched nothing — a note whose every
    citation was aliased and BROKEN came back `clean: true`.
    """
    _prepared(workspace)
    aliased = f"[[transcripts/{SESSION_ID}/{chunk_for(3)}#^{turn_id_for(3)}|в тот вечер]]"
    _note(workspace, f"\n- утверждение {aliased}\n")
    payload = _check(workspace)
    assert payload["links_resolved"] == 1
    assert payload["clean"] is True


def test_a_broken_aliased_link_is_reported(workspace: Workspace) -> None:
    _prepared(workspace)
    broken = f"[[transcripts/{SESSION_ID}/{chunk_for(3)}#^t99999-9|подпись]]"
    _note(workspace, f"\n- утверждение {broken}\n")
    payload = _check(workspace)
    assert payload["clean"] is False
    assert payload["links_unresolved"] == 1
    assert payload["notes"][0]["links_unresolved"][0]["block"] == "t99999-9"


def test_a_note_of_only_aliased_links_is_not_called_unevidenced(workspace: Workspace) -> None:
    _prepared(workspace)
    aliased = f"[[transcripts/{SESSION_ID}/{chunk_for(3)}#^{turn_id_for(3)}|там]]"
    _note(workspace, f"\n- один {aliased}\n")
    problems = " ".join(_check(workspace)["notes"][0]["problems"])
    assert "nothing in it is evidenced" not in problems


# ------------------------------------------------------------- owner questions


def test_a_draft_reports_its_open_question_count(workspace: Workspace) -> None:
    _prepared(workspace)
    _note(workspace, f"\n- утверждение {GOOD_LINK}\n" + QUESTIONS)
    payload = _check(workspace)
    assert payload["clean"] is True
    assert payload["open_questions"] == 2
    assert payload["notes"][0]["questions_section"] is True


def test_a_canon_note_with_open_questions_is_a_contradiction(workspace: Workspace) -> None:
    """`status: canon` is the freeze; freezing with unanswered questions is not a state."""
    _prepared(workspace)
    _note(workspace, f"\n- утверждение {GOOD_LINK}\n" + QUESTIONS, frontmatter=CANON_FRONTMATTER)
    payload = _check(workspace)
    assert payload["clean"] is False
    assert "Вопросы владельцу" in " ".join(payload["notes"][0]["problems"])


def test_a_questions_heading_inside_a_code_fence_is_not_a_section(workspace: Workspace) -> None:
    """Slicing is structural and fence-aware — a quoted heading is content, not a queue."""
    _prepared(workspace)
    body = f"\n- утверждение {GOOD_LINK}\n\n```\n## Вопросы владельцу\n### P1 — пример\n```\n"
    _note(workspace, body, frontmatter=CANON_FRONTMATTER)
    payload = _check(workspace)
    assert payload["clean"] is True
    assert payload["open_questions"] == 0


# ---------------------------------------------------------------------- freeze


CANON_BODY = f"""
# Сессия 1

- факт первый {GOOD_LINK} ^s1-f01

## Реконсиляция

- решение P1: принято ^s1-d01
"""


def test_freeze_writes_the_record_and_check_reports_frozen(workspace: Workspace) -> None:
    _prepared(workspace)
    _note(workspace, CANON_BODY, frontmatter=CANON_FRONTMATTER)
    payload = _freeze(workspace)
    assert payload["frozen"] is True
    assert payload["refreeze"] is False
    tree = workspace.roots.session(SESSION_ID)
    assert tree.chronicle_freeze_json.is_file()
    checked = _check(workspace)
    assert checked["frozen"] is True
    assert checked["notes"][0]["freeze"] == "ok"


def test_freeze_refuses_a_draft(workspace: Workspace) -> None:
    """Freezing is the LAST step of the review, not a shortcut past it."""
    _prepared(workspace)
    _note(workspace, CANON_BODY)
    with pytest.raises(SessionIngestError) as excinfo:
        _freeze(workspace)
    assert excinfo.value.code == "chronicle_not_canon"


def test_freeze_refuses_an_unclean_note(workspace: Workspace) -> None:
    """Attesting a broken note would enforce exactly the wrong thing."""
    _prepared(workspace)
    bad = f"[[transcripts/{SESSION_ID}/{chunk_for(3)}#^t99999-9]]"
    _note(workspace, f"\n- утверждение {bad}\n", frontmatter=CANON_FRONTMATTER)
    with pytest.raises(SessionIngestError) as excinfo:
        _freeze(workspace)
    assert excinfo.value.code == "chronicle_unclean"


def test_an_edit_outside_reconciliation_is_drift(workspace: Workspace) -> None:
    _prepared(workspace)
    note = _note(workspace, CANON_BODY, frontmatter=CANON_FRONTMATTER)
    _freeze(workspace)
    note.write_text(
        note.read_text(encoding="utf-8").replace("факт первый", "факт ПОДМЕНЁННЫЙ"),
        encoding="utf-8",
    )
    payload = _check(workspace)
    assert payload["clean"] is False
    assert payload["notes"][0]["freeze"] == "drift"
    assert "changed after the freeze" in " ".join(payload["notes"][0]["problems"])


def test_an_append_to_reconciliation_is_not_drift(workspace: Workspace) -> None:
    """`## Реконсиляция` is the one append-allowed section of a frozen note."""
    _prepared(workspace)
    note = _note(workspace, CANON_BODY, frontmatter=CANON_FRONTMATTER)
    _freeze(workspace)
    note.write_text(
        note.read_text(encoding="utf-8") + "- добавлено после заморозки: реткон ^r-20260810-01\n",
        encoding="utf-8",
    )
    payload = _check(workspace)
    assert payload["clean"] is True
    assert payload["notes"][0]["freeze"] == "ok"


def test_a_refreeze_acknowledges_a_recorded_retcon(workspace: Workspace) -> None:
    """Drift is not "never edit" — it is "never edit silently". Re-freezing closes it."""
    _prepared(workspace)
    note = _note(workspace, CANON_BODY, frontmatter=CANON_FRONTMATTER)
    _freeze(workspace)
    note.write_text(
        note.read_text(encoding="utf-8").replace("факт первый", "~~факт первый~~ реткон"),
        encoding="utf-8",
    )
    assert _check(workspace)["notes"][0]["freeze"] == "drift"
    payload = _freeze(workspace)
    assert payload["refreeze"] is True
    assert _check(workspace)["notes"][0]["freeze"] == "ok"


def test_a_frozen_note_demoted_to_draft_is_drift(workspace: Workspace) -> None:
    _prepared(workspace)
    note = _note(workspace, CANON_BODY, frontmatter=CANON_FRONTMATTER)
    _freeze(workspace)
    note.write_text(
        note.read_text(encoding="utf-8").replace("status: canon", "status: draft"),
        encoding="utf-8",
    )
    payload = _check(workspace)
    assert payload["clean"] is False
    assert "un-freezing" in " ".join(payload["notes"][0]["problems"])


def test_a_deleted_freeze_record_degrades_to_convention(workspace: Workspace) -> None:
    """The record lives in the prunable cache: losing it must not read as corruption."""
    _prepared(workspace)
    _note(workspace, CANON_BODY, frontmatter=CANON_FRONTMATTER)
    _freeze(workspace)
    workspace.roots.session(SESSION_ID).chronicle_freeze_json.unlink()
    payload = _check(workspace)
    assert payload["clean"] is True
    assert payload["frozen"] is False
    assert [s["id"] for s in payload["next_steps"]] == ["freeze"]


# ---------------------------------------------------------------------- status


def test_status_reports_pending_and_caught_up(workspace: Workspace) -> None:
    _prepared(workspace)
    _note(workspace, f"\n- утверждение {GOOD_LINK}\n" + QUESTIONS)
    payload = _status(workspace)
    assert payload["caught_up"] is False
    assert payload["pending"] == [f"s001-{SESSION_ID}-chapel.md"]
    assert payload["next_steps"][0]["id"] == "owner_review"


def test_status_is_caught_up_when_everything_is_canon(workspace: Workspace) -> None:
    _prepared(workspace)
    _note(workspace, CANON_BODY, frontmatter=CANON_FRONTMATTER)
    _freeze(workspace)
    # A meta note beside the ledger (a decision protocol, a stray file) is listed
    # but never blocks: only `type: session` notes gate the verdict.
    meta = workspace.roots.chronicles_dir / "s001-proposals.md"
    meta.write_text("---\ntype: meta\nstatus: resolved\n---\n\n# Протокол\n", encoding="utf-8")
    payload = _status(workspace)
    assert payload["caught_up"] is True
    assert payload["pending"] == []
    assert {row["name"] for row in payload["notes"]} == {
        f"s001-{SESSION_ID}-chapel.md",
        "s001-proposals.md",
    }


def test_status_flags_post_freeze_drift(workspace: Workspace) -> None:
    _prepared(workspace)
    note = _note(workspace, CANON_BODY, frontmatter=CANON_FRONTMATTER)
    _freeze(workspace)
    note.write_text(
        note.read_text(encoding="utf-8").replace("факт первый", "факт изменённый"),
        encoding="utf-8",
    )
    payload = _status(workspace)
    assert payload["caught_up"] is False
    assert payload["notes"][0]["freeze"] == "drift"


def test_status_needs_no_session_and_survives_an_empty_ledger(workspace: Workspace) -> None:
    payload = _status(workspace)
    assert payload["caught_up"] is True
    assert payload["notes"] == []


# ------------------------------------------------------- the CLI surface


def test_freeze_and_status_via_the_cli(workspace: Workspace) -> None:
    """Exercised through CliRunner on purpose: `run()` never touches serialisation."""
    import json

    from click.testing import CliRunner

    from session_ingest.__main__ import cli

    _prepared(workspace)
    _note(workspace, CANON_BODY, frontmatter=CANON_FRONTMATTER)
    runner = CliRunner()

    frozen = runner.invoke(
        cli, ["chronicle", "--session", SESSION_ID, "--freeze", "--json"], env=workspace.cli_env()
    )
    assert frozen.exit_code == 0, frozen.output
    payload = json.loads(frozen.stdout)
    assert payload["verb"] == "chronicle"
    assert payload["frozen"] is True

    status = runner.invoke(cli, ["chronicle", "--status", "--json"], env=workspace.cli_env())
    assert status.exit_code == 0, status.output
    payload = json.loads(status.stdout)
    assert payload["caught_up"] is True
    assert "mode=status[cli]" in status.stderr


def test_freeze_refusal_is_a_json_failure_through_the_cli(workspace: Workspace) -> None:
    import json

    from click.testing import CliRunner

    from session_ingest.__main__ import cli

    _prepared(workspace)
    _note(workspace, CANON_BODY)  # still a draft
    result = CliRunner().invoke(
        cli, ["chronicle", "--session", SESSION_ID, "--freeze", "--json"], env=workspace.cli_env()
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["code"] == "chronicle_not_canon"


def test_freeze_and_status_together_are_refused(workspace: Workspace) -> None:
    from click.testing import CliRunner

    from session_ingest.__main__ import cli

    result = CliRunner().invoke(cli, ["chronicle", "--freeze", "--status"], env=workspace.cli_env())
    assert result.exit_code == 2
    assert "different modes" in result.output


# --------------------------------------------------------- frontmatter parsing


def test_a_four_dash_line_does_not_close_the_frontmatter() -> None:
    fm, reason = chronicle_mod.read_frontmatter("---\ntype: session\n----\n# body\n")
    assert fm == {}
    assert reason == "frontmatter block is opened but never closed"


def test_a_leading_horizontal_rule_gets_an_actionable_diagnostic() -> None:
    """Obsidian reads a leading `---` as frontmatter, so "not a mapping" is the
    honest classification — but the message has to name the likely cause."""
    fm, reason = chronicle_mod.read_frontmatter("---\n\ntext\n\n---\n")
    assert fm == {}
    assert reason is not None and "horizontal rule" in reason


def test_a_body_delimiter_does_not_truncate_the_block() -> None:
    fm, reason = chronicle_mod.read_frontmatter(
        "---\ntype: session\nsession: 1\n---\n\n# Body\n\n---\n\nmore\n"
    )
    assert reason is None
    assert fm == {"type": "session", "session": 1}
