"""The record schema validator, and the session-link model beneath it.

The rule under test is DESIGN §6's second one: *a claim without evidence is a
bug.* Every assertion about evidence here is an assertion that the validator
rejects, rather than warns about, a claim nobody can check.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from session_ingest.models import (
    RECORD_SCHEMA,
    Evidence,
    RecordingLink,
    SessionLink,
    combined_digest,
    empty_record,
    validate_record,
)


def _evidence() -> dict[str, Any]:
    return dict(
        Evidence.build(
            turn_id="t451234-3",
            segment_i=812,
            t0=4512.3,
            speaker="Морвика",
            chunk="03-030-045",
            session_id="2025-01-01",
        ).to_dict()
    )


def _record() -> dict[str, Any]:
    """A fully populated record, as a plain dict — the validator's real input shape."""
    record: dict[str, Any] = dict(
        empty_record(
            session_id="2025-01-01",
            recording_id="xK9mQrTnZp2",
            dataset_digest="sha256:abc",
            transcript_root="vault/transcripts/2025-01-01",
            duration_s=10224.0,
            language="ru",
            participants=[
                {
                    "user_id": "u1",
                    "display": "Морвика",
                    "role": "pc",
                    "speech_s": 1811.2,
                    "speech_share": 0.19,
                }
            ],
        )
    )
    record["scenes"] = [
        {
            "id": "s1",
            "title": "Старая часовня",
            "location": "Кроненфельд",
            "t0": 0.0,
            "t1": 900.0,
            "summary": "…",
            "participants": ["Морвика"],
            "evidence": [_evidence()],
        }
    ]
    record["events"] = [
        {
            "id": "e1",
            "scene": "s1",
            "kind": "social",
            "summary": "…",
            "outcome": "…",
            "world_impact": "local",
            "needs_owner": False,
            "confidence": 0.82,
            "evidence": [_evidence()],
        }
    ]
    record["entities"] = [
        {
            "id": "n1",
            "kind": "npc",
            "name_as_heard": "Вагзар",
            "canonical": "Вазгар",
            "lexicon_term_id": "vagzar",
            "first_mention_t": 12.5,
            "vault_note": None,
            "evidence": [_evidence()],
        }
    ]
    record["quests"] = [
        {"id": "q1", "name": "…", "status_change": "advanced", "evidence": [_evidence()]}
    ]
    record["loot"] = [{"item": "…", "recipient": "…", "evidence": [_evidence()]}]
    record["commitments"] = [{"who": "…", "promise": "…", "evidence": [_evidence()]}]
    record["threads"] = [{"question": "…", "status": "open", "evidence": [_evidence()]}]
    return record


def test_a_complete_record_validates() -> None:
    assert validate_record(_record()) == []


def test_empty_record_is_schema_valid() -> None:
    record = empty_record(
        session_id="2025-01-01",
        recording_id="r",
        dataset_digest="abc",
        transcript_root="vault/transcripts/2025-01-01",
    )
    assert record["schema"] == RECORD_SCHEMA
    assert record["session"]["dataset_digest"] == "sha256:abc", "bare hex is normalised"
    assert validate_record(record) == []


@pytest.mark.parametrize(
    "collection",
    ["scenes", "events", "entities", "quests", "loot", "commitments", "threads"],
)
def test_an_element_without_evidence_is_rejected(collection: str) -> None:
    record = _record()
    record[collection][0]["evidence"] = []
    errors = validate_record(record)
    assert any("evidence" in error and "must not be empty" in error for error in errors), errors


def test_a_missing_evidence_key_is_rejected() -> None:
    record = _record()
    del record["events"][0]["evidence"][0]["link"]
    assert "events[0].evidence[0].link: missing" in validate_record(record)


def test_evidence_types_are_checked() -> None:
    record = _record()
    record["events"][0]["evidence"][0]["segment_i"] = "812"
    assert "events[0].evidence[0].segment_i: must be an integer" in validate_record(record)


def test_events_require_confidence_and_needs_owner() -> None:
    record = _record()
    del record["events"][0]["confidence"]
    del record["events"][0]["needs_owner"]
    errors = validate_record(record)
    assert "events[0].confidence: missing" in errors
    assert "events[0].needs_owner: must be a boolean" in errors


def test_confidence_out_of_range_is_rejected() -> None:
    record = _record()
    record["events"][0]["confidence"] = 1.4
    assert any("within 0..1" in error for error in validate_record(record))


def test_enums_are_enforced() -> None:
    record = _record()
    record["events"][0]["kind"] = "vibes"
    record["events"][0]["world_impact"] = "catastrophic"
    record["entities"][0]["kind"] = "spaceship"
    record["quests"][0]["status_change"] = "vibed"
    errors = "\n".join(validate_record(record))
    assert "events[0].kind" in errors
    assert "events[0].world_impact" in errors
    assert "entities[0].kind" in errors
    assert "quests[0].status_change" in errors


def test_duplicate_ids_are_rejected() -> None:
    record = _record()
    record["events"].append(copy.deepcopy(record["events"][0]))
    assert any("duplicate id 'e1'" in error for error in validate_record(record))


def test_session_block_is_checked() -> None:
    record = _record()
    del record["session"]["play_time_s"]
    record["session"]["dataset_digest"] = "abc"
    record["session"]["participants"][0]["role"] = "goblin"
    errors = validate_record(record)
    assert "session.play_time_s: missing" in errors
    assert any("sha256:" in error for error in errors)
    assert any("participants[0].role" in error for error in errors)


def test_a_null_is_valid_only_when_the_record_declares_it_missing() -> None:
    """The one mechanism that replaced record.py's waiver list."""
    record = _record()
    record["session"]["play_time_s"] = None
    record["session"]["table_talk_share"] = None

    undeclared = validate_record(record)
    assert "session.play_time_s: must be a number" in undeclared
    assert "session.table_talk_share: must be a number" in undeclared

    record["session"]["missing"] = ["play_time_s", "table_talk_share"]
    assert validate_record(record) == []

    # Declaring a key missing licenses `null`, not garbage.
    record["session"]["play_time_s"] = "later"
    assert any("must be null or a number" in error for error in validate_record(record))


def test_wrong_schema_and_missing_collections_are_reported() -> None:
    errors = validate_record({"schema": "something/else"})
    assert any("schema:" in error for error in errors)
    for collection in ("scenes", "events", "entities", "quests", "loot", "commitments", "threads"):
        assert f"{collection}: missing" in errors
    assert validate_record([]) == ["record: must be a JSON object"]


def test_evidence_builds_its_own_wikilink() -> None:
    evidence = Evidence.build(
        turn_id="t451234-3",
        segment_i=812,
        t0=4512.3,
        speaker="Морвика",
        chunk="03-030-045",
        session_id="2025-01-01",
    )
    assert evidence.link == "[[transcripts/2025-01-01/03-030-045#^t451234-3]]"


# ------------------------------------------------------------------- session


def test_session_link_round_trip_and_active_run() -> None:
    link = SessionLink(id="2025-01-01")
    link.upsert(RecordingLink("r1", "/d/r1", 1, "sha256:one"))
    link.upsert(RecordingLink("r1", "/d/r1", 2, "sha256:two"))
    assert link.runs() == [1, 2]
    assert link.dataset_digest == "sha256:one"
    link.active_run = 2
    assert link.dataset_digest == "sha256:two"

    revived = SessionLink.from_dict(link.to_dict())
    assert revived.active_run == 2
    assert [r.recording_id for r in revived.recordings] == ["r1", "r1"]


def test_upsert_replaces_the_same_recording_and_run() -> None:
    link = SessionLink(id="2025-01-01")
    link.upsert(RecordingLink("r1", "/d/r1", 1, "sha256:one"))
    link.upsert(RecordingLink("r1", "/d/r1", 1, "sha256:redone"))
    assert len(link.recordings) == 1
    assert link.dataset_digest == "sha256:redone"


def test_combined_digest_is_order_independent() -> None:
    assert combined_digest(["a"]) == "a"
    assert combined_digest([]) is None
    assert combined_digest(["a", "b"]) == combined_digest(["b", "a"])
    assert combined_digest(["a", "b"]) != combined_digest(["a", "c"])
