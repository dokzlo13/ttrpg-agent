"""``chronicle --check`` — does the agent-authored note actually resolve?"""

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


def test_a_clean_check_unblocks_prune(workspace: Workspace) -> None:
    _prepared(workspace)
    _note(workspace, f"\n- утверждение {GOOD_LINK}\n")
    payload = _check(workspace)
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
