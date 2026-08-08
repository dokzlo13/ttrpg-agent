"""``extract`` — bleed/table-talk filtering, the evidence guardrail, overlap dedup."""

from __future__ import annotations

from typing import Any

from session_ingest import extract as extract_mod
from session_ingest.extract import dedupe_key
from session_ingest.writer import read_json

from .conftest import SESSION_ID, Workspace
from .fakes import (
    FakeClient,
    adopt,
    constant,
    extraction_answer,
    make_config,
    turn_id_for,
    user_turn_ids,
    write_classes,
)

WINDOWED = {"TTRPG_SESSION_WINDOW_MINUTES": "1", "TTRPG_SESSION_WINDOW_OVERLAP_PCT": "50"}


def scene(scene_id: str, title: str, *segments: int, confidence: float = 0.8) -> dict[str, Any]:
    return {
        "id": scene_id,
        "title": title,
        "location": None,
        "summary": f"Сводка: {title}",
        "participants": ["Морвика"],
        "evidence": [turn_id_for(index) for index in segments],
        "confidence": confidence,
    }


def event(summary: str, *segments: int, impact: str = "none", owner: bool = False, ref=None):
    return {
        "scene_ref": ref,
        "kind": "social",
        "summary": summary,
        "outcome": None,
        "world_impact": impact,
        "needs_owner": owner,
        "evidence": [turn_id_for(index) for index in segments],
        "confidence": 0.6,
    }


def _run(workspace: Workspace, client: FakeClient, monkeypatch, **kwargs: Any):
    monkeypatch.setattr(extract_mod, "client_for", lambda _config: client)
    env = {**WINDOWED, **kwargs.pop("env", {})}
    return extract_mod.run(
        roots=workspace.roots,
        config=make_config(**env),
        session_id=SESSION_ID,
        **kwargs,
    )


def _elements(workspace: Workspace) -> dict[str, list[dict[str, Any]]]:
    payload = read_json(workspace.roots.session(SESSION_ID).extraction_json)
    return payload["elements"]


# ---------------------------------------------------------------- dedupe key


def test_dedupe_key_is_exact_equality_after_casefold() -> None:
    assert dedupe_key("scenes", {"title": " Церковь "}) == dedupe_key(
        "scenes", {"title": "церковь"}
    )
    assert dedupe_key("scenes", {"title": "Церковь"}) != dedupe_key("scenes", {"title": "Церковъ"})
    assert dedupe_key("loot", {"item": "меч", "recipient": "А"}) != dedupe_key(
        "loot", {"item": "меч", "recipient": "Б"}
    )


# --------------------------------------------------------------------- verb


def test_extract_without_a_key_skips_cleanly(workspace: Workspace) -> None:
    adopt(workspace)
    payload = extract_mod.run(
        roots=workspace.roots, config=make_config(api_key=None), session_id=SESSION_ID
    )
    assert payload["status"] == "skipped"
    assert payload["code"] == "missing_api_key"
    assert not workspace.roots.session(SESSION_ID).extraction_json.exists()


def test_bleed_turns_are_dropped_unless_kept(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)
    dropped = _run(workspace, FakeClient(constant(extraction_answer())), monkeypatch)
    assert dropped["turns_total"] == 26, "the four bleed-suspect segments are gone"
    assert dropped["turns_considered"] == 26

    kept = _run(workspace, FakeClient(constant(extraction_answer())), monkeypatch, keep_bleed=True)
    assert kept["turns_total"] == 30


def test_table_talk_is_filtered_when_the_class_file_exists(
    workspace: Workspace, monkeypatch
) -> None:
    adopt(workspace)
    write_classes(
        workspace,
        {
            turn_id_for(index): ("table_talk" if index < 6 else "in_character")
            for index in range(30)
        },
    )
    client = FakeClient(constant(extraction_answer()))
    payload = _run(workspace, client, monkeypatch)

    assert payload["table_talk_filter"]["applied"] is True
    assert payload["table_talk_filter"]["excluded_turns"] == 6
    assert payload["turns_considered"] == 20
    shown = "".join(client.user_contents())
    assert turn_id_for(0) not in shown, "table talk never reaches the extraction prompt"
    assert turn_id_for(6) in shown


def test_a_missing_class_file_is_noted_not_fatal(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)
    payload = _run(workspace, FakeClient(constant(extraction_answer())), monkeypatch)
    assert payload["table_talk_filter"]["applied"] is False
    assert "absent" in payload["table_talk_filter"]["reason"]
    assert any("table talk was not filtered" in warning for warning in payload["warnings"])


def test_elements_carry_resolved_evidence_and_derived_times(
    workspace: Workspace, monkeypatch
) -> None:
    adopt(workspace)

    def handler(kwargs: dict, _index: int):
        ids = user_turn_ids(kwargs)
        if turn_id_for(0) not in ids:
            return extraction_answer()
        return extraction_answer(
            scenes=[scene("s1", "Церковь", 0, 2)],
            events=[event("Нашли алтарь", 2, impact="local", ref="s1")],
            entities=[
                {
                    "kind": "npc",
                    "name_as_heard": "Вагзар",
                    "canonical": "Вазгар",
                    "evidence": [turn_id_for(2)],
                    "confidence": 0.9,
                }
            ],
        )

    payload = _run(workspace, FakeClient(handler), monkeypatch)
    assert payload["status"] == "ok"
    assert payload["elements"]["scenes"] == 1
    assert payload["elements"]["events"] == 1

    elements = _elements(workspace)
    scene_row = elements["scenes"][0]
    assert scene_row["id"] == "s1"
    assert scene_row["t0"] == 0.0 and scene_row["t1"] == 28.0
    assert [entry["turn_id"] for entry in scene_row["evidence"]] == [
        turn_id_for(0),
        turn_id_for(2),
    ]
    assert scene_row["evidence"][0]["speaker"] == "alice"
    assert elements["events"][0]["scene"] == "s1", "the window-local scene ref is remapped"
    assert elements["entities"][0]["first_mention_t"] == 20.0


def test_an_element_without_evidence_is_rejected(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)
    payload = _run(
        workspace,
        FakeClient(constant(extraction_answer(scenes=[scene("s1", "Ничем не подтверждено")]))),
        monkeypatch,
    )
    assert payload["elements"]["scenes"] == 0
    assert payload["rejected_count"] == payload["windows"]
    assert all(row["reason"].startswith("no evidence") for row in payload["rejected_elements"])


def test_an_invented_turn_id_does_not_become_evidence(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)
    bad = scene("s1", "Церковь")
    bad["evidence"] = ["t-invented-1"]
    payload = _run(workspace, FakeClient(constant(extraction_answer(scenes=[bad]))), monkeypatch)
    assert payload["elements"]["scenes"] == 0
    assert payload["rejected_elements"][0]["unknown_turn_ids"] == ["t-invented-1"]


def test_an_out_of_enum_value_is_rejected_by_the_reduce_guardrail() -> None:
    """Belt and braces: strict mode should stop this, and the reduce checks anyway."""
    from session_ingest.extract import reduce_windows
    from session_ingest.segment import build_windows

    from .test_segment import _row

    turns = [_row(index, t0=index * 10.0, t1=index * 10.0 + 8.0) for index in range(3)]
    windows = build_windows(turns, window_minutes=15, overlap_pct=0)
    bad_event = {
        "scene_ref": None,
        "kind": "social",
        "summary": "Что-то произошло",
        "outcome": None,
        "world_impact": "apocalyptic",
        "needs_owner": False,
        "evidence": ["t0"],
        "confidence": 0.5,
    }
    no_flag = dict(bad_event, world_impact="none", needs_owner="возможно")
    emitted, rejected, _ = reduce_windows(
        windows, [(0, extraction_answer(events=[bad_event, no_flag]))]
    )
    assert emitted["events"] == []
    reasons = [row["reason"] for row in rejected]
    assert any("world_impact" in reason for reason in reasons)
    assert any("needs_owner is not a boolean" in reason for reason in reasons)


# ------------------------------------------------------------ overlap dedup


def _two_window_handler(first: list[dict[str, Any]], second: list[dict[str, Any]]):
    def handler(kwargs: dict, _index: int):
        ids = user_turn_ids(kwargs)
        if turn_id_for(0) in ids:
            return extraction_answer(scenes=first)
        if turn_id_for(3) in ids and turn_id_for(8) in ids:
            return extraction_answer(scenes=second)
        return extraction_answer()

    return handler


def test_identical_elements_inside_the_overlap_zone_merge(
    workspace: Workspace, monkeypatch
) -> None:
    adopt(workspace)
    handler = _two_window_handler(
        [scene("s1", "Церковь", 4), scene("s2", "Лес", 0)],
        [scene("s1", "церковь", 4), scene("s2", "Таверна", 8)],
    )
    payload = _run(workspace, FakeClient(handler), monkeypatch)
    assert payload["elements"]["scenes"] == 3, "the duplicate in the overlap collapsed"

    titles = [row["title"] for row in _elements(workspace)["scenes"]]
    assert titles == ["Лес", "Церковь", "Таверна"], "scenes are ordered by t0"
    church = next(row for row in _elements(workspace)["scenes"] if row["title"] == "Церковь")
    assert church["merged_from_overlap"] == 1
    assert len(church["evidence"]) == 1, "the union of two identical citations is one"


def test_identical_titles_outside_the_overlap_zone_stay_separate(
    workspace: Workspace, monkeypatch
) -> None:
    adopt(workspace)
    handler = _two_window_handler([scene("s1", "Церковь", 0)], [scene("s1", "Церковь", 8)])
    payload = _run(workspace, FakeClient(handler), monkeypatch)
    assert payload["elements"]["scenes"] == 2, "a party can revisit the same place twice"


def test_near_miss_titles_are_never_merged(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)
    handler = _two_window_handler([scene("s1", "Церковь", 4)], [scene("s1", "Церковь!", 4)])
    payload = _run(workspace, FakeClient(handler), monkeypatch)
    assert payload["elements"]["scenes"] == 2, "exact equality only — no fuzzy matching"


def test_evidence_is_unioned_across_the_overlap(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)
    handler = _two_window_handler([scene("s1", "Церковь", 3, 4)], [scene("s1", "Церковь", 4, 5)])
    payload = _run(workspace, FakeClient(handler), monkeypatch)
    assert payload["elements"]["scenes"] == 1
    church = _elements(workspace)["scenes"][0]
    assert [entry["turn_id"] for entry in church["evidence"]] == [
        turn_id_for(3),
        turn_id_for(4),
        turn_id_for(5),
    ]
    assert church["t0"] == 30.0 and church["t1"] == 58.0


# ------------------------------------------------------------------ failures


def test_a_failed_window_leaves_a_visible_hole(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)

    def handler(kwargs: dict, _index: int):
        if turn_id_for(0) in user_turn_ids(kwargs):
            return RuntimeError("rate limited")
        return extraction_answer()

    payload = _run(workspace, FakeClient(handler), monkeypatch)
    assert payload["status"] == "ok"
    assert len(payload["failed_windows"]) == 1
    assert payload["failed_windows"][0]["attempts"] == 3
    assert any("failed after retries" in warning for warning in payload["warnings"])
    assert read_json(workspace.roots.session(SESSION_ID).extraction_json)["failed_windows"]


def test_extract_skips_on_the_second_run(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)
    client = FakeClient(constant(extraction_answer()))
    assert _run(workspace, client, monkeypatch)["status"] == "ok"
    calls = len(client.calls)
    again = _run(workspace, client, monkeypatch)
    assert again["status"] == "skipped"
    assert len(client.calls) == calls


def test_growing_the_class_file_invalidates_the_extraction(
    workspace: Workspace, monkeypatch
) -> None:
    adopt(workspace)
    client = FakeClient(constant(extraction_answer()))
    assert _run(workspace, client, monkeypatch)["status"] == "ok"
    write_classes(workspace, {turn_id_for(0): "table_talk"})
    assert _run(workspace, client, monkeypatch)["status"] == "ok"
