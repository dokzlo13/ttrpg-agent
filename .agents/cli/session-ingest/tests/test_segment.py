"""``segment`` — windowing, the turn-id guardrail, overlap dedup, and the jsonl."""

from __future__ import annotations

import json

import pytest

from session_ingest import segment as segment_mod
from session_ingest.segment import (
    TurnRow,
    build_windows,
    load_classifications,
    reduce_classifications,
)
from session_ingest.vaultfiles import Speakers, load_speakers

from .conftest import SESSION_ID, Workspace
from .fakes import FakeClient, adopt, classify_all, constant, make_config, turn_id_for

WINDOWED = {"TTRPG_SESSION_WINDOW_MINUTES": "1", "TTRPG_SESSION_WINDOW_OVERLAP_PCT": "50"}


def _run(workspace: Workspace, client: FakeClient, monkeypatch, **env: str):
    monkeypatch.setattr(segment_mod, "client_for", lambda _config: client)
    return segment_mod.run(
        roots=workspace.roots,
        config=make_config(**{**WINDOWED, **env}),
        session_id=SESSION_ID,
    )


def _row(index: int, *, t0: float, t1: float) -> TurnRow:
    return TurnRow(
        turn_id=f"t{index}",
        t0=t0,
        t1=t1,
        track=1,
        user_id="u",
        speaker="Морвика",
        text=f"реплика {index}",
        segment_indices=(index,),
        bleed_suspect=False,
    )


# --------------------------------------------------------------- windowing


def test_windows_split_on_turn_boundaries_and_overlap() -> None:
    turns = [_row(index, t0=index * 10.0, t1=index * 10.0 + 8.0) for index in range(30)]
    windows = build_windows(turns, window_minutes=1, overlap_pct=50)

    assert [window.turns[0].turn_id for window in windows[:3]] == ["t0", "t3", "t6"]
    assert all(window.turns for window in windows)
    # Every turn is covered, and no window ever holds a fragment of one.
    covered = {turn.turn_id for window in windows for turn in window.turns}
    assert covered == {turn.turn_id for turn in turns}
    assert windows[0].turn_ids() & windows[1].turn_ids() == {"t3", "t4", "t5"}


def test_windows_always_advance_even_with_one_enormous_turn() -> None:
    turns = [_row(0, t0=0.0, t1=3600.0), _row(1, t0=3600.0, t1=3610.0)]
    windows = build_windows(turns, window_minutes=15, overlap_pct=90)
    assert len(windows) == 2
    assert [window.turns[0].turn_id for window in windows] == ["t0", "t1"]


def test_no_turns_means_no_windows() -> None:
    assert build_windows([], window_minutes=15, overlap_pct=10) == []


def test_speaker_label_prefers_the_mapping_then_the_handle(workspace: Workspace) -> None:
    workspace.write_speakers()
    speakers = load_speakers(workspace.roots.speakers_file)
    assert (
        segment_mod.speaker_label(user_id="u-alice", username="alice", track=1, speakers=speakers)
        == "Морвика"
    )
    assert (
        segment_mod.speaker_label(user_id="u-bob", username="bob", track=2, speakers=speakers)
        == "bob"
    )
    empty = Speakers(path=workspace.roots.speakers_file, present=False, digest=None, by_user_id={})
    assert segment_mod.speaker_label(user_id=None, username=None, track=7, speakers=empty) == (
        "track 7"
    )


# ---------------------------------------------------------------- guardrail


def test_reduce_rejects_turn_ids_that_were_not_in_the_window() -> None:
    turns = [_row(index, t0=index * 10.0, t1=index * 10.0 + 8.0) for index in range(3)]
    windows = build_windows(turns, window_minutes=15, overlap_pct=0)
    accepted, rejected = reduce_classifications(
        windows,
        [
            (
                0,
                {
                    "turns": [
                        {"turn_id": "t0", "class": "in_character", "confidence": 0.9},
                        {"turn_id": "t-invented", "class": "in_character", "confidence": 1.0},
                        {"turn_id": "t1", "class": "нечто", "confidence": 0.5},
                    ]
                },
            )
        ],
    )
    assert set(accepted) == {"t0"}
    assert {row["reason"] for row in rejected} == {"turn id not in window", "unknown class"}


def test_confidence_is_clamped_into_zero_one() -> None:
    turns = [_row(0, t0=0.0, t1=1.0)]
    windows = build_windows(turns, window_minutes=15, overlap_pct=0)
    accepted, _ = reduce_classifications(
        windows, [(0, {"turns": [{"turn_id": "t0", "class": "ambiguous", "confidence": 4.2}]})]
    )
    assert accepted["t0"].confidence == 1.0


# --------------------------------------------------------------------- verb


def test_segment_without_a_key_skips_cleanly(workspace: Workspace) -> None:
    adopt(workspace)
    payload = segment_mod.run(
        roots=workspace.roots, config=make_config(api_key=None), session_id=SESSION_ID
    )
    assert payload["status"] == "skipped"
    assert payload["code"] == "missing_api_key"
    assert not workspace.roots.session(SESSION_ID).turns_class_jsonl.exists()


def test_segment_classifies_every_turn_and_writes_the_jsonl(
    workspace: Workspace, monkeypatch
) -> None:
    adopt(workspace)
    workspace.write_lexicon()
    workspace.write_speakers()
    client = FakeClient(classify_all(by_turn={turn_id_for(4): "mechanics"}))

    payload = _run(workspace, client, monkeypatch)

    assert payload["status"] == "ok"
    assert payload["turns"] == 30
    assert payload["classified"] == 30
    assert payload["unclassified"] == 0
    assert payload["distribution"]["mechanics"] == 1
    assert payload["distribution"]["in_character"] == 29
    assert payload["usage"]["calls"] == payload["windows"]

    path = workspace.roots.session(SESSION_ID).turns_class_jsonl
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 30
    assert lines[0]["turn_id"] == turn_id_for(0)
    assert set(lines[0]) == {"turn_id", "class", "confidence"}
    # The canonical names reach the prompt, and the speaker mapping reaches the input.
    assert "Вазгар" in client.system_prompts()[0]
    assert "Морвика" in client.user_contents()[0]


def test_a_turn_seen_twice_keeps_its_first_classification(
    workspace: Workspace, monkeypatch
) -> None:
    adopt(workspace)

    def handler(kwargs: dict, index: int):
        label = "in_character" if index == 0 else "table_talk"
        return classify_all(default=label)(kwargs, index)

    payload = _run(workspace, FakeClient(handler), monkeypatch)
    assert payload["status"] == "ok"

    classes = load_classifications(workspace.roots.session(SESSION_ID).turns_class_jsonl)
    assert classes[turn_id_for(3)] == "in_character", "overlap: the earlier window wins"
    assert classes[turn_id_for(6)] == "table_talk"


def test_invented_turn_ids_are_rejected_and_counted(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)
    payload = _run(
        workspace,
        FakeClient(
            constant({"turns": [{"turn_id": "t-nope", "class": "in_character", "confidence": 1.0}]})
        ),
        monkeypatch,
    )
    assert payload["classified"] == 0
    assert payload["rejected_count"] == payload["windows"]
    assert payload["unclassified"] == 30
    assert any("were not classified" in warning for warning in payload["warnings"])


def test_a_failed_window_is_reported_not_dropped(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)

    def handler(kwargs: dict, index: int):
        if turn_id_for(9) in kwargs["messages"][1]["content"]:
            return RuntimeError("provider is down")
        return classify_all()(kwargs, index)

    payload = _run(workspace, FakeClient(handler), monkeypatch)
    assert payload["failed_windows"], "the hole is visible in the envelope"
    assert all(entry["attempts"] == 3 for entry in payload["failed_windows"])
    assert payload["classified"] < 30


def test_segment_skips_on_the_second_run_and_force_overrides(
    workspace: Workspace, monkeypatch
) -> None:
    adopt(workspace)
    client = FakeClient(classify_all())
    assert _run(workspace, client, monkeypatch)["status"] == "ok"
    calls_after_first = len(client.calls)

    again = _run(workspace, client, monkeypatch)
    assert again["status"] == "skipped"
    assert again["classified"] == 30
    assert len(client.calls) == calls_after_first, "a skipped stage spends nothing"

    monkeypatch.setattr(segment_mod, "client_for", lambda _config: client)
    forced = segment_mod.run(
        roots=workspace.roots,
        config=make_config(**WINDOWED),
        session_id=SESSION_ID,
        force=True,
    )
    assert forced["status"] == "ok"
    assert len(client.calls) > calls_after_first


def test_changing_the_model_invalidates_the_cache(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)
    client = FakeClient(classify_all())
    assert _run(workspace, client, monkeypatch)["status"] == "ok"
    other = _run(workspace, client, monkeypatch, TTRPG_SESSION_OPENAI_MODEL="another-model")
    assert other["status"] == "ok", "a different model is a different composite key"


@pytest.mark.parametrize("missing", ["turns", "rows"])
def test_a_malformed_answer_does_not_crash_the_stage(
    workspace: Workspace, monkeypatch, missing: str
) -> None:
    adopt(workspace)
    payload = {"turns": ["not an object"]} if missing == "rows" else {"nothing": []}
    result = _run(workspace, FakeClient(constant(payload)), monkeypatch)
    assert result["status"] in {"ok"}
    assert result["classified"] == 0
