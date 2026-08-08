"""``grep`` — exact slicing over index.sqlite, so nobody ever cats a chunk."""

from __future__ import annotations

import pytest

from session_ingest import grep, render
from session_ingest.errors import SessionIngestError

from .conftest import SESSION_ID, Workspace

#: Fixture: 30 segments 10s apart, tracks alternate 1/2, so track 1 is `u-alice`
#: (mapped to the character Морвика) on the even indices.
ALICE_SEGMENTS = list(range(0, 30, 2))


@pytest.fixture
def indexed(workspace: Workspace) -> Workspace:
    workspace.write_lexicon()
    workspace.write_speakers()
    workspace.adopt()
    render.run(
        roots=workspace.roots,
        config=workspace.config(),
        session_id=SESSION_ID,
        window_minutes=1,
    )
    return workspace


def _grep(workspace: Workspace, **kwargs):
    return grep.run(
        roots=workspace.roots, config=workspace.config(), session_id=SESSION_ID, **kwargs
    )


def _indices(result) -> list[int]:
    return [row["i"] for row in result["rows"] if row["match"]]


# ------------------------------------------------------ substitution auditing


def test_a_corrected_row_carries_the_verbatim_text_it_replaced(indexed: Workspace) -> None:
    """`render` stores text_raw so a correction can be audited; grep must expose it."""
    result = _grep(indexed, regex="Вазгар")
    corrected = {row["i"]: row for row in result["rows"] if row.get("substituted")}
    # 4 and 14 say "Вагзар", 24 says "Вазагар"; 2 and 12 already said it correctly.
    assert sorted(corrected) == [4, 14, 24]
    assert "Вазагар" in corrected[24]["text_raw"]
    assert "Вагзар" in corrected[4]["text_raw"]
    assert "Вазгар" in corrected[4]["text"]
    assert "Вагзар" not in corrected[4]["text"]


def test_an_untouched_row_carries_no_raw_copy(indexed: Workspace) -> None:
    result = _grep(indexed, regex="Вазгар")
    untouched = next(row for row in result["rows"] if row["i"] == 2)
    assert "text_raw" not in untouched
    assert "substituted" not in untouched


# ------------------------------------------------------------------- filters


def test_speaker_resolves_through_the_character_name(indexed: Workspace) -> None:
    result = _grep(indexed, speaker="Морвика")
    assert _indices(result) == ALICE_SEGMENTS
    assert result["filters"]["speaker_user_ids"] == ["u-alice"]
    assert {row["speaker"] for row in result["rows"]} == {"Морвика"}


def test_speaker_also_resolves_a_raw_discord_username_and_id(indexed: Workspace) -> None:
    assert _indices(_grep(indexed, speaker="alice")) == ALICE_SEGMENTS
    assert _indices(_grep(indexed, speaker="u-alice")) == ALICE_SEGMENTS
    # an unmapped participant is still greppable by the name the transcript shows
    assert _indices(_grep(indexed, speaker="bob")) == list(range(1, 30, 2))


def test_an_unknown_speaker_matches_nothing_and_says_why(indexed: Workspace) -> None:
    result = _grep(indexed, speaker="Морви")
    assert result["matches"] == 0
    assert any("matched no _speakers.yaml entry" in w for w in result["warnings"])


def test_speaker_and_time_compose(indexed: Workspace) -> None:
    result = _grep(indexed, speaker="Морвика", time_from="00:01:00", time_to="00:02:00")
    # segments 6, 8, 10 start in [60, 120) on track 1
    assert _indices(result) == [6, 8, 10]


def test_time_accepts_seconds_and_hh_mm(indexed: Workspace) -> None:
    assert grep.parse_time("90", flag="--from") == 90.0
    assert grep.parse_time("01:30", flag="--from") == 5400.0
    assert grep.parse_time("00:01:30", flag="--from") == 90.0
    assert grep.parse_time(None, flag="--from") is None
    assert _indices(_grep(indexed, time_from="0", time_to="25")) == [0, 1, 2]


def test_a_nonsense_time_is_a_clean_failure(indexed: Workspace) -> None:
    with pytest.raises(SessionIngestError) as excinfo:
        _grep(indexed, time_from="half past four")
    assert excinfo.value.code == "invalid_time"


def test_an_inverted_range_is_refused(indexed: Workspace) -> None:
    with pytest.raises(SessionIngestError) as excinfo:
        _grep(indexed, time_from="100", time_to="50")
    assert excinfo.value.code == "invalid_time_range"


def test_regex_matches_the_substituted_text(indexed: Workspace) -> None:
    """Grep finds what the chunk shows, so a citation and a hit agree."""
    result = _grep(indexed, regex="Вазгар")
    assert _indices(result) == [2, 4, 12, 14, 24]


def test_a_broken_regex_is_a_clean_failure(indexed: Workspace) -> None:
    with pytest.raises(SessionIngestError) as excinfo:
        _grep(indexed, regex="Вазгар(")
    assert excinfo.value.code == "invalid_regex"


def test_context_pulls_in_the_neighbouring_segments(indexed: Workspace) -> None:
    result = _grep(indexed, regex="Реплика 24", context=2)
    assert _indices(result) == [24]
    assert [row["i"] for row in result["rows"]] == [22, 23, 24, 25, 26]
    assert [row["match"] for row in result["rows"]] == [False, False, True, False, False]


def test_context_at_the_edges_does_not_invent_rows(indexed: Workspace) -> None:
    result = _grep(indexed, regex="Реплика 0\\.", context=3)
    assert [row["i"] for row in result["rows"]] == [0, 1, 2, 3]


def test_a_negative_context_is_refused(indexed: Workspace) -> None:
    with pytest.raises(SessionIngestError) as excinfo:
        _grep(indexed, context=-1)
    assert excinfo.value.code == "invalid_context"


# -------------------------------------------------------------------- output


def test_every_row_is_citable(indexed: Workspace) -> None:
    row = next(row for row in _grep(indexed, regex="Реплика 4")["rows"])
    assert row["turn_id"] == "t4000-1"
    assert row["chunk"] == "01-000-001"
    assert row["evidence"]["link"] == f"[[transcripts/{SESSION_ID}/01-000-001#^t4000-1]]"


def test_human_lines_are_time_speaker_text(indexed: Workspace) -> None:
    result = _grep(indexed, regex="Реплика 4\\.")
    assert result["lines"] == ["[00:00:40] Морвика: Реплика 4. Вазгар идёт."]


def test_no_match_says_so(indexed: Workspace) -> None:
    result = _grep(indexed, regex="этого никто не говорил")
    assert result["matches"] == 0
    assert result["lines"] == ["no matches"]


# --------------------------------------------------------------------- scope


def test_all_sessions_prefixes_the_session_id(indexed: Workspace) -> None:
    result = grep.run(
        roots=indexed.roots,
        config=indexed.config(),
        session_id=None,
        all_sessions=True,
        regex="Реплика 4\\.",
    )
    assert result["sessions"] == [SESSION_ID]
    assert result["lines"] == [f"{SESSION_ID} [00:00:40] Морвика: Реплика 4. Вазгар идёт."]


def test_a_missing_index_names_render(workspace: Workspace) -> None:
    workspace.adopt()
    with pytest.raises(SessionIngestError) as excinfo:
        _grep(workspace, regex="что-нибудь")
    assert excinfo.value.code == "index_missing"
    assert excinfo.value.next_steps[0]["id"] == "render"
    assert "render" in excinfo.value.next_steps[0]["command"]


def test_all_with_nothing_rendered_names_render(workspace: Workspace) -> None:
    with pytest.raises(SessionIngestError) as excinfo:
        grep.run(
            roots=workspace.roots,
            config=workspace.config(),
            session_id=None,
            all_sessions=True,
        )
    assert excinfo.value.code == "index_missing"


def test_neither_session_nor_all_is_refused(workspace: Workspace) -> None:
    with pytest.raises(SessionIngestError) as excinfo:
        grep.run(roots=workspace.roots, config=workspace.config(), session_id=None)
    assert excinfo.value.code == "no_session"
