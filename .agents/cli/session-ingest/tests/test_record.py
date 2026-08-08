"""``record`` — deterministic assembly, evidence resolution, schema validation."""

from __future__ import annotations

import pytest

from session_ingest import record as record_mod
from session_ingest.errors import SessionIngestError
from session_ingest.models import validate_record
from session_ingest.writer import read_json

from .conftest import SESSION_ID, Workspace
from .fakes import (
    adopt,
    default_elements,
    evidence,
    make_config,
    turn_id_for,
    write_anchors,
    write_classes,
    write_extraction,
)


def validation_errors(record) -> list[str]:
    """No waivers: a record that declares a key in ``session.missing`` is simply valid."""
    return validate_record(record)


def _run(workspace: Workspace, **kwargs):
    return record_mod.run(
        roots=workspace.roots,
        config=make_config(api_key=None),
        session_id=SESSION_ID,
        **kwargs,
    )


def _prepared(workspace: Workspace) -> None:
    adopt(workspace)
    workspace.write_lexicon()
    workspace.write_speakers()
    write_anchors(workspace)


# ------------------------------------------------------------------ anchors


def test_anchors_are_indexed_both_ways(workspace: Workspace) -> None:
    write_anchors(workspace)
    tree = workspace.roots.session(SESSION_ID)
    anchors = record_mod.load_anchors(tree.anchors_json, session_id=SESSION_ID)
    assert anchors.by_segment[3].turn_id == turn_id_for(3)
    assert anchors.by_turn[turn_id_for(3)].segment_i == 3
    assert anchors.digest is not None

    built = anchors.evidence_for(turn_id=turn_id_for(3), speaker="Морвика", session_id=SESSION_ID)
    assert built is not None
    assert built.link == f"[[transcripts/{SESSION_ID}/01-000-002#^{turn_id_for(3)}]]"


def test_a_missing_anchors_file_names_render_as_the_repair(workspace: Workspace) -> None:
    adopt(workspace)
    with pytest.raises(SessionIngestError) as excinfo:
        _run(workspace)
    assert excinfo.value.code == "anchors_missing"
    assert excinfo.value.next_steps[0]["id"] == "render"


# ------------------------------------------------------------ the assembly


def test_record_validates_and_every_link_resolves(workspace: Workspace) -> None:
    _prepared(workspace)
    write_extraction(workspace)

    payload = _run(workspace)
    assert payload["status"] == "ok"
    assert payload["counts"] == {
        "scenes": 1,
        "events": 2,
        "entities": 1,
        "quests": 1,
        "loot": 1,
        "commitments": 1,
        "threads": 1,
    }
    assert payload["unresolved_evidence"] == []

    record = read_json(workspace.roots.session(SESSION_ID).record_json)
    assert validation_errors(record) == []
    assert record["schema"] == "ttrpg.session-record/1"

    links = [
        item["link"]
        for family in payload["counts"]
        for row in record[family]
        for item in row["evidence"]
    ]
    assert links, "a claim without evidence is a bug"
    assert all(link.startswith(f"[[transcripts/{SESSION_ID}/") for link in links)
    assert all("#^t" in link for link in links)
    assert payload["evidence_items"] == len(links)


def test_the_session_block_carries_provenance_and_participants(workspace: Workspace) -> None:
    _prepared(workspace)
    write_extraction(workspace)
    _run(workspace)

    block = read_json(workspace.roots.session(SESSION_ID).record_json)["session"]
    assert block["id"] == SESSION_ID
    assert block["dataset_digest"].startswith("sha256:")
    assert block["language"] == "ru"
    assert block["transcript_root"] == f"vault/transcripts/{SESSION_ID}"
    assert block["provenance"] == {
        "stt_model": "large-v3",
        "merge_gap_s": 1.5,
        "lexicon_digest": block["provenance"]["lexicon_digest"],
        "prompt_version": "extract/1",
        "llm_model": "gpt-test",
    }
    assert block["provenance"]["lexicon_digest"].startswith("sha256:")

    by_id = {row["user_id"]: row for row in block["participants"]}
    assert set(by_id) == {"u-alice", "u-bob"}, "the skipped bot track is not a participant"
    assert by_id["u-alice"]["display"] == "Морвика"
    assert by_id["u-alice"]["role"] == "pc"
    assert by_id["u-bob"]["role"] == "guest", "unmapped speakers fall back, visibly"
    assert block["unmapped_participants"] == ["u-bob"]
    assert by_id["u-alice"]["speech_share"] == pytest.approx(0.6)


def test_entities_are_cross_referenced_against_the_lexicon(workspace: Workspace) -> None:
    _prepared(workspace)
    elements = default_elements()
    elements["entities"].append(
        {
            "id": "n2",
            "kind": "location",
            "name_as_heard": "Неизвестное место",
            "canonical": "Неизвестное место",
            "first_mention_t": 0.0,
            "confidence": 0.3,
            "evidence": evidence(0),
        }
    )
    write_extraction(workspace, elements=elements)
    _run(workspace)

    entities = read_json(workspace.roots.session(SESSION_ID).record_json)["entities"]
    assert entities[0]["canonical"] == "Вазгар"
    assert entities[0]["lexicon_term_id"] == "vagzar"
    assert entities[0]["vault_note"] is None, "note resolution is the tracker's job in M1"
    assert entities[1]["lexicon_term_id"] is None


# ---------------------------------------------------------------- play time


def test_play_time_is_null_and_listed_when_nothing_classified_the_turns(
    workspace: Workspace,
) -> None:
    _prepared(workspace)
    write_extraction(workspace)
    payload = _run(workspace)

    assert payload["play_time_s"] is None
    assert payload["table_talk_share"] is None
    assert payload["missing"] == ["play_time_s", "table_talk_share"]

    written = read_json(workspace.roots.session(SESSION_ID).record_json)
    block = written["session"]
    assert block["play_time_s"] is None
    assert block["missing"] == ["play_time_s", "table_talk_share"]
    # The declared null is valid on its own terms — no waiver list anywhere.
    assert validate_record(written) == []


def test_play_time_is_computed_from_the_class_file(workspace: Workspace) -> None:
    _prepared(workspace)
    write_extraction(workspace)
    # Six of thirty equal-length turns are table talk -> a 0.2 share of speech.
    write_classes(
        workspace,
        {
            turn_id_for(index): ("table_talk" if index < 6 else "in_character")
            for index in range(30)
        },
    )
    payload = _run(workspace)

    assert payload["missing"] == []
    assert payload["table_talk_share"] == pytest.approx(0.2)
    assert payload["play_time_s"] == pytest.approx(300.0 * 0.8)

    block = read_json(workspace.roots.session(SESSION_ID).record_json)["session"]
    assert block["speech_by_class"]["table_talk"] == pytest.approx(48.0)
    assert validate_record(read_json(workspace.roots.session(SESSION_ID).record_json)) == []


# ----------------------------------------------------------------- failures


def test_an_element_whose_evidence_cannot_resolve_fails_validation(
    workspace: Workspace,
) -> None:
    _prepared(workspace)
    write_extraction(
        workspace,
        elements={
            "scenes": [
                {
                    "id": "s1",
                    "title": "Сцена",
                    "location": None,
                    "summary": "Сводка",
                    "participants": [],
                    "t0": 0.0,
                    "t1": 1.0,
                    "confidence": 0.5,
                    "evidence": [{"turn_id": "t-not-rendered", "t0": 0.0, "speaker": "X"}],
                }
            ],
            "events": [],
            "entities": [],
            "quests": [],
            "loot": [],
            "commitments": [],
            "threads": [],
        },
    )
    with pytest.raises(SessionIngestError) as excinfo:
        _run(workspace)
    assert excinfo.value.code == "record_invalid"
    assert any("evidence" in error for error in excinfo.value.detail["errors"])
    assert excinfo.value.detail["unresolved_evidence"][0]["turn_ids"] == ["t-not-rendered"]


def test_a_keyless_machine_still_produces_a_valid_empty_record(workspace: Workspace) -> None:
    _prepared(workspace)
    payload = _run(workspace)

    assert payload["status"] == "ok"
    assert payload["counts"] == dict.fromkeys(payload["counts"], 0)
    assert any("extraction.json is absent" in warning for warning in payload["warnings"])
    assert validation_errors(read_json(workspace.roots.session(SESSION_ID).record_json)) == []


def test_record_skips_on_the_second_run_and_force_overrides(workspace: Workspace) -> None:
    _prepared(workspace)
    write_extraction(workspace)
    assert _run(workspace)["status"] == "ok"
    assert _run(workspace)["status"] == "skipped"
    assert _run(workspace, force=True)["status"] == "ok"


def test_a_new_extraction_invalidates_the_record(workspace: Workspace) -> None:
    _prepared(workspace)
    write_extraction(workspace)
    assert _run(workspace)["status"] == "ok"

    elements = default_elements()
    elements["threads"] = []
    write_extraction(workspace, elements=elements)
    payload = _run(workspace)
    assert payload["status"] == "ok"
    assert payload["counts"]["threads"] == 0


def test_owner_questions_are_counted_from_the_data(workspace: Workspace) -> None:
    _prepared(workspace)
    write_extraction(workspace)
    payload = _run(workspace)
    # e1 is world_impact=local, e2 is needs_owner: both reach the owner.
    assert payload["needs_owner"] == 2
