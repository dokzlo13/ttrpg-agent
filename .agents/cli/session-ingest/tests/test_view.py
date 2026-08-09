"""``view`` — the compact read path over record.json: filters, links, refusals."""

from __future__ import annotations

import pytest

from session_ingest import record as record_mod
from session_ingest import view as view_mod
from session_ingest.errors import SessionIngestError

from .conftest import SESSION_ID, Workspace
from .fakes import adopt, make_config, write_anchors, write_classes, write_extraction


def _record(workspace: Workspace) -> None:
    """Everything `record` needs, then the record itself — `view` reads only its output."""
    adopt(workspace)
    workspace.write_lexicon()
    workspace.write_speakers()
    write_anchors(workspace)
    write_extraction(workspace)
    write_classes(workspace, {})
    record_mod.run(roots=workspace.roots, config=make_config(api_key=None), session_id=SESSION_ID)


def _view(workspace: Workspace, **kwargs):
    return view_mod.run(roots=workspace.roots, session_id=SESSION_ID, **kwargs)


# ------------------------------------------------------------------ formatting


def test_hhmmss_never_fabricates_a_zero() -> None:
    assert view_mod.hhmmss(3661.9) == "01:01:01"
    assert view_mod.hhmmss(0) == "00:00:00"
    assert view_mod.hhmmss(None) == "--:--:--"


# ------------------------------------------------------------------ refusals


def test_view_without_a_record_names_the_repair(workspace: Workspace) -> None:
    adopt(workspace)
    with pytest.raises(SessionIngestError) as excinfo:
        _view(workspace)
    assert excinfo.value.code == "record_missing"
    assert "record" in {s["id"] for s in excinfo.value.next_steps}


def test_an_unknown_section_is_refused_by_name(workspace: Workspace) -> None:
    _record(workspace)
    with pytest.raises(SessionIngestError) as excinfo:
        _view(workspace, sections=["scenes", "monsters"])
    assert excinfo.value.code == "unknown_section"
    assert excinfo.value.detail["unknown"] == ["monsters"]


# ------------------------------------------------------------------ selecting


def test_default_view_covers_every_family(workspace: Workspace) -> None:
    _record(workspace)
    payload = _view(workspace)
    assert payload["status"] == "ok"
    assert set(payload["counts"]) == set(view_mod.SECTIONS)
    assert payload["counts"]["events"] == 2
    assert payload["counts"]["scenes"] == 1


def test_needs_owner_keeps_only_flagged_events(workspace: Workspace) -> None:
    """`e1` is world_impact: local, `e2` is needs_owner — both qualify.

    On its own the flag also narrows to the events family: it asks a question
    about events, and five empty headings would bury the answer.
    """
    _record(workspace)
    payload = _view(workspace, needs_owner=True)
    assert set(payload["counts"]) == {"events"}
    assert payload["counts"]["events"] == 2


def test_an_explicit_section_still_wins_over_needs_owner(workspace: Workspace) -> None:
    _record(workspace)
    payload = _view(workspace, needs_owner=True, sections=["events", "entities"])
    assert set(payload["counts"]) == {"events", "entities"}
    assert payload["counts"]["entities"] == 0


def test_scene_filter_scopes_scenes_and_their_events(workspace: Workspace) -> None:
    _record(workspace)
    payload = _view(workspace, scene="s1")
    assert payload["counts"]["scenes"] == 1
    # e2 has scene: None, so it is not in s1.
    assert payload["counts"]["events"] == 1
    # Loot carries no scene id; claiming it belongs to s1 would be a lie.
    assert payload["counts"]["loot"] == 0


def test_kind_filter_is_an_exact_comparison(workspace: Workspace) -> None:
    _record(workspace)
    assert _view(workspace, kind="discovery")["counts"]["events"] == 1
    assert _view(workspace, kind="disco")["counts"]["events"] == 0


def test_min_confidence_drops_low_rows(workspace: Workspace) -> None:
    _record(workspace)
    payload = _view(workspace, min_confidence=0.65)
    assert payload["counts"]["events"] == 1  # e2 is 0.4
    assert payload["counts"]["loot"] == 0  # 0.5


# ------------------------------------------------------------------ links


def test_one_link_per_row_by_default(workspace: Workspace) -> None:
    _record(workspace)
    payload = _view(workspace, sections=["scenes"])
    scene = payload["selected"]["scenes"][0]
    assert len(scene["links"]) == 1
    assert scene["links"][0].startswith(f"[[transcripts/{SESSION_ID}/")


def test_links_flag_widens_the_citation_list(workspace: Workspace) -> None:
    _record(workspace)
    payload = _view(workspace, sections=["scenes"], links=5)
    assert len(payload["selected"]["scenes"][0]["links"]) == 2  # the scene cites 2 turns


def test_selection_never_carries_the_raw_evidence_blocks(workspace: Workspace) -> None:
    """The whole reason this verb exists: evidence blocks are ~60% of record.json."""
    _record(workspace)
    payload = _view(workspace)
    for rows in payload["selected"].values():
        for row in rows:
            assert "evidence" not in row


def test_rendered_lines_are_markdown_and_carry_the_flags(workspace: Workspace) -> None:
    _record(workspace)
    text = "\n".join(_view(workspace)["lines"])
    assert text.startswith(f"# Сессия {SESSION_ID}")
    assert "## События (2)" in text
    assert "**[решение владельца]**" in text  # e2
    assert "[world_impact: local]" in text  # e1


def test_no_header_omits_the_session_block(workspace: Workspace) -> None:
    _record(workspace)
    lines = _view(workspace, header=False)["lines"]
    assert not any(line.startswith("# Сессия") for line in lines)


def test_entities_report_registry_state(workspace: Workspace) -> None:
    """Without a registry every entity reads as unresolved, and says so."""
    _record(workspace)
    text = "\n".join(_view(workspace, sections=["entities"])["lines"])
    assert "**не в реестре**" in text


def test_entities_show_the_slug_once_the_registry_knows_them(workspace: Workspace) -> None:
    workspace.write_entity_registry()
    _record(workspace)
    text = "\n".join(_view(workspace, sections=["entities"])["lines"])
    assert "`vagzar`" in text
    assert "`npcs/vagzar.md`" in text


# ----------------------------------------------------------- the CLI surface


def _cli_view(workspace: Workspace, *args: str):
    """Through click, not through run() — the path that produced the known bug."""
    from click.testing import CliRunner

    from session_ingest.__main__ import cli

    return CliRunner().invoke(
        cli, ["view", "--session", SESSION_ID, *args], env=workspace.cli_env()
    )


def test_view_json_envelope_is_well_formed(workspace: Workspace) -> None:
    import json

    _record(workspace)
    result = _cli_view(workspace, "--json")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verb"] == "view"
    assert payload["status"] == "ok"
    assert payload["counts"]["events"] == 2


def test_view_json_does_not_also_ship_the_rendered_prose(workspace: Workspace) -> None:
    """`selected` and `lines` are the same content twice; --json carries the rows."""
    import json

    _record(workspace)
    payload = json.loads(_cli_view(workspace, "--json").stdout)
    assert "lines" not in payload
    assert payload["selected"]["events"]


def test_view_text_mode_still_renders_prose(workspace: Workspace) -> None:
    _record(workspace)
    result = _cli_view(workspace)
    assert result.exit_code == 0, result.output
    assert "## События" in result.stdout


def test_an_unknown_section_fails_through_the_cli(workspace: Workspace) -> None:
    _record(workspace)
    result = _cli_view(workspace, "--section", "monsters")
    assert result.exit_code != 0


def test_empty_sections_say_why_they_are_empty(workspace: Workspace) -> None:
    """ "Nothing selected" under an explicit --section reads as "nothing exists"."""
    _record(workspace)
    text = "\n".join(_view(workspace, needs_owner=True, sections=["events", "entities"])["lines"])
    assert "--needs-owner относится только к событиям" in text
    text = "\n".join(_view(workspace, scene="s1", sections=["scenes", "loot"])["lines"])
    assert "только у сцен и событий есть привязка к сцене" in text
