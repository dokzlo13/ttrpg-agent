"""``render`` — the stage every evidence link in the system is built on.

The load-bearing assertions here are the ones a later stage silently depends on:
chunk boundaries fall on turn boundaries, block IDs are byte-identical across two
runs of the same dataset, and every segment index resolves through
``anchors.json`` to exactly one chunk and one turn.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from session_ingest import render
from session_ingest.morphs import expand_terms
from session_ingest.vaultfiles import load_lexicon

from .conftest import (
    LEXICON_YAML,
    MORPH_LEXICON_YAML,
    SESSION_ID,
    TERM_TEXT,
    Workspace,
    write_dataset,
)

LINE_RE = re.compile(r"^- \[(\d{2}:\d{2}:\d{2})\] \*\*(.+?)\*\*: (.*?) \^(t\d+-\d+)$")


@pytest.fixture
def rendered(workspace: Workspace) -> dict:
    workspace.write_lexicon()
    workspace.write_speakers()
    workspace.adopt()
    return render.run(
        roots=workspace.roots,
        config=workspace.config(),
        session_id=SESSION_ID,
        window_minutes=1,
    )


def _chunk_files(workspace: Workspace) -> list[Path]:
    return sorted(
        p
        for p in workspace.roots.transcripts.joinpath(SESSION_ID).glob("*.md")
        if not p.name.startswith("__")
    )


def _turn_lines(path: Path) -> list[re.Match[str]]:
    body = path.read_text(encoding="utf-8").split("---\n", 2)[2]
    return [LINE_RE.match(line) for line in body.splitlines() if line.startswith("- [")]  # type: ignore[misc]


# ------------------------------------------------------------------- chunking


def test_renders_the_expected_chunks(rendered: dict, workspace: Workspace) -> None:
    assert rendered["status"] == "ok"
    # 30 segments 10s apart, no merges (12s gap > 1.5s), 1-minute windows -> 5 chunks.
    assert rendered["turns"] == 30
    assert rendered["segments"] == 30
    assert rendered["chunk_count"] == 5
    assert [chunk["chunk"] for chunk in rendered["chunks"]] == [
        "01-000-001",
        "02-001-002",
        "03-002-003",
        "04-003-004",
        "05-004-005",
    ]
    assert [p.name for p in _chunk_files(workspace)] == [
        f"{chunk['chunk']}.md" for chunk in rendered["chunks"]
    ]


def test_chunk_boundaries_fall_on_turn_boundaries(rendered: dict, workspace: Workspace) -> None:
    """No turn appears twice, and no chunk starts inside one."""
    seen: list[str] = []
    for path in _chunk_files(workspace):
        matches = _turn_lines(path)
        assert matches and all(matches), path.name
        ids = [match.group(4) for match in matches]
        seen.extend(ids)
        window_start = int(path.name.split("-")[1]) * 60
        window_end = int(path.name.split("-")[2].removesuffix(".md")) * 60
        for match in matches:
            hours, minutes, seconds = (int(part) for part in match.group(1).split(":"))
            offset = hours * 3600 + minutes * 60 + seconds
            assert window_start <= offset < window_end, (path.name, match.group(0))
    assert len(seen) == len(set(seen)) == 30


def test_every_segment_resolves_to_exactly_one_chunk_and_turn(
    rendered: dict, workspace: Workspace
) -> None:
    anchors = json.loads(Path(rendered["anchors"]).read_text(encoding="utf-8"))
    assert sorted(int(key) for key in anchors) == list(range(30))

    block_ids: dict[str, str] = {}
    for path in _chunk_files(workspace):
        for match in _turn_lines(path):
            block_ids[match.group(4)] = path.stem

    for index, anchor in anchors.items():
        assert set(anchor) == {"turn_id", "chunk", "t0"}
        assert block_ids[anchor["turn_id"]] == anchor["chunk"], index
    # exactly one: no segment index is claimed by two chunks
    assert len({(a["chunk"], a["turn_id"]) for a in anchors.values()}) == 30


def test_block_ids_are_byte_identical_across_two_runs(rendered: dict, workspace: Workspace) -> None:
    before = {path.name: path.read_bytes() for path in _chunk_files(workspace)}
    again = render.run(
        roots=workspace.roots,
        config=workspace.config(),
        session_id=SESSION_ID,
        window_minutes=1,
        force=True,
    )
    assert again["status"] == "ok"
    after = {path.name: path.read_bytes() for path in _chunk_files(workspace)}
    assert before == after


# --------------------------------------------------------------- substitution


def test_lexicon_substitution_is_applied_counted_and_leaves_the_dataset_alone(
    rendered: dict, workspace: Workspace
) -> None:
    # Three enumerated variants occur in the fixture: Вагзар ×2, Вазагар ×1.
    assert rendered["substitutions"] == {"vagzar": 3}
    assert rendered["substitutions_total"] == 3

    spoken = "\n".join(
        match.group(3) for path in _chunk_files(workspace) for match in _turn_lines(path)
    )
    assert "Вагзар" not in spoken
    assert "Вазагар" not in spoken
    # 2 canonical hits (one lower-cased, left exactly as spoken) + 3 substituted variants
    assert spoken.casefold().count("вазгар") == 5

    verbatim = (workspace.dataset_dir / "segments.jsonl").read_text(encoding="utf-8")
    assert "Вагзар" in verbatim and "Вазагар" in verbatim


def test_inactive_terms_are_never_substituted(rendered: dict, workspace: Workspace) -> None:
    """`oswald` is `active: false`, so its variant list must not touch the text."""
    lexicon = load_lexicon(workspace.roots.lexicon_file)
    assert [term.id for term in lexicon.active_terms()] == ["vagzar", "kilverin"]
    body = "\n".join(path.read_text(encoding="utf-8") for path in _chunk_files(workspace))
    assert "Освальд Стоун" in body


def test_substitution_is_word_boundary_aware_and_case_insensitive(
    workspace: Workspace,
) -> None:
    workspace.write_lexicon()
    substituter = render.build_substituter(load_lexicon(workspace.roots.lexicon_file))
    counts = render.SubstitutionCounts()
    assert substituter.apply("Вагзар и вазагар", counts) == "Вазгар и Вазгар"
    assert counts.explicit == {"vagzar": 2}
    # a variant nested inside a longer word is not a variant
    counts = render.SubstitutionCounts()
    assert substituter.apply("Вагзаром", counts) == "Вагзаром"
    assert counts.explicit == {}


def test_longest_variant_wins(workspace: Workspace) -> None:
    """A short variant nested in a longer one must not clobber it."""
    workspace.write_lexicon(
        "terms:\n  - id: nested\n    canonical: Вазгар\n    variants: [Вазагар, Ваза]\n"
    )
    substituter = render.build_substituter(load_lexicon(workspace.roots.lexicon_file))
    counts = render.SubstitutionCounts()
    assert substituter.apply("Вазагар", counts) == "Вазгар"
    assert counts.explicit == {"nested": 1}


# ------------------------------------------------- morphological substitution


def test_a_generated_form_is_substituted_with_the_matching_declension(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end-to-end proof: «Марвикой» becomes «Морвикой», not «Морвика».

    Both sides of the pair were inflected to the instrumental, so the corrected
    line still reads as Russian. Getting the case right is the entire reason the
    table is built from pairs instead of from a set of variants.
    """
    monkeypatch.setitem(TERM_TEXT, 2, "Реплика 2. Говорили с Марвикой вчера.")
    monkeypatch.setitem(TERM_TEXT, 12, "Реплика 12. Подошли к Марвике.")
    monkeypatch.setitem(TERM_TEXT, 4, "Реплика 4. Ждали Марвики.")
    write_dataset(workspace.dataset_dir)

    workspace.write_lexicon(MORPH_LEXICON_YAML)
    workspace.write_speakers()
    workspace.adopt()
    result = render.run(
        roots=workspace.roots, config=workspace.config(), session_id=SESSION_ID, window_minutes=1
    )

    body = "\n".join(path.read_text(encoding="utf-8") for path in _chunk_files(workspace))
    assert "Морвикой" in body
    assert "Морвике" in body
    assert "Марвик" not in body

    # «Марвики» belongs to the explicit `morvika-gen` entry, so that one
    # replacement is counted as the owner's, not as generated.
    assert result["substitutions"] == {"morvika-gen": 1}
    assert result["substitutions_generated"] == {"morvika": 2}
    assert result["substitutions_total"] == 3
    assert result["morph"]["pairs"] > 0


def test_explicit_variants_win_over_generated_ones(workspace: Workspace) -> None:
    """«Марвики» is enumerated by hand, so the generated table never claims it."""
    workspace.write_lexicon(MORPH_LEXICON_YAML)
    lexicon = load_lexicon(workspace.roots.lexicon_file)
    substituter = render.build_substituter(lexicon, expand_terms(lexicon))

    counts = render.SubstitutionCounts()
    assert substituter.apply("Марвики", counts) == "Морвики"
    assert counts.explicit == {"morvika-gen": 1}
    assert counts.generated == {}

    counts = render.SubstitutionCounts()
    assert substituter.apply("Марвике", counts) == "Морвике"
    assert counts.explicit == {}
    assert counts.generated == {"morvika": 1}


def test_the_expansion_table_is_written_to_provenance_for_audit(
    workspace: Workspace,
) -> None:
    """Every generated pair must be checkable after the fact, pair by pair."""
    workspace.write_lexicon(MORPH_LEXICON_YAML)
    workspace.adopt()
    render.run(roots=workspace.roots, config=workspace.config(), session_id=SESSION_ID)

    tree = workspace.roots.session(SESSION_ID)
    record = json.loads(tree.provenance_json.read_text(encoding="utf-8"))["stages"]["render"]
    table = record["extra"]["morph_expansion"]
    assert table["schema"] == "ttrpg.session-morph-expansion/1"
    assert table["versions"]["pymorphy3"]
    pairs = table["terms"][0]["pairs"]
    assert {(p["case"], p["variant"], p["display"]) for p in pairs} >= {
        ("datv", "Марвике", "Морвике"),
        ("ablt", "Марвикой", "Морвикой"),
    }
    assert record["composite_key"]["knobs"]["morph_digest"] == table["digest"]


def test_a_lexicon_edit_that_only_adds_morph_re_renders(workspace: Workspace) -> None:
    """`morph: true` changes the output, so it must move the skip-if-done key."""
    workspace.write_lexicon(MORPH_LEXICON_YAML.replace("    morph: true\n", ""))
    workspace.adopt()
    first = render.run(roots=workspace.roots, config=workspace.config(), session_id=SESSION_ID)
    assert first["status"] == "ok"

    workspace.write_lexicon(MORPH_LEXICON_YAML)
    second = render.run(roots=workspace.roots, config=workspace.config(), session_id=SESSION_ID)
    assert second["status"] == "ok", "the morph flag must invalidate the render key"
    assert second["morph"]["pairs"] > first["morph"]["pairs"] == 0


def test_search_terms_only_list_terms_that_occur(rendered: dict, workspace: Workspace) -> None:
    first = _chunk_files(workspace)[0].read_text(encoding="utf-8")
    assert "search_terms:" in first
    assert "Вазгар" in first.split("---")[1]
    # `Кильверин` never appears in the fixture, so it must not be advertised.
    assert "Кильверин" not in first.split("---")[1]


# ------------------------------------------------------------------- markers


def test_unmapped_speaker_is_marked_never_guessed(rendered: dict, workspace: Workspace) -> None:
    assert [entry["user_id"] for entry in rendered["unmapped_speakers"]] == ["u-bob"]
    body = "\n".join(path.read_text(encoding="utf-8") for path in _chunk_files(workspace))
    assert "⚠ bob (не сопоставлен)" in body
    assert "**Морвика**" in body  # character beats player beats username


def test_bleed_and_overlap_are_marked(rendered: dict, workspace: Workspace) -> None:
    body = "\n".join(path.read_text(encoding="utf-8") for path in _chunk_files(workspace))
    # fixture: segments 10, 11, 20, 21 are bleed-suspect with louder_track = 3 - track
    assert body.count("возможный блид с трека") == 4
    # fixture: segments 4-7 carry an overlap list
    assert body.count("наложение:") == 4


def test_bleed_turns_are_kept_not_dropped(rendered: dict) -> None:
    """Rendering keeps bleed; only extraction drops it."""
    assert rendered["turns"] == 30


# ----------------------------------------------------------------- artifacts


def test_snapshots_record_the_inputs_the_render_used(rendered: dict, workspace: Workspace) -> None:
    tree = workspace.roots.session(SESSION_ID)
    assert tree.lexicon_snapshot.read_text(encoding="utf-8") == LEXICON_YAML
    assert "u-alice" in tree.speakers_snapshot.read_text(encoding="utf-8")
    assert rendered["snapshots"]["lexicon_present"] is True


def test_absent_vault_files_are_snapshotted_as_absent(workspace: Workspace) -> None:
    workspace.adopt()
    result = render.run(roots=workspace.roots, config=workspace.config(), session_id=SESSION_ID)
    tree = workspace.roots.session(SESSION_ID)
    assert "was absent at render time" in tree.lexicon_snapshot.read_text(encoding="utf-8")
    assert result["snapshots"]["speakers_present"] is False
    body = tree.transcript_dir.joinpath("01-000-015.md").read_text(encoding="utf-8")
    assert "⚠ alice (не сопоставлен)" in body


def test_overview_lists_participants_chunks_and_the_recap_placeholder(
    rendered: dict, workspace: Workspace
) -> None:
    overview = Path(rendered["overview"]).read_text(encoding="utf-8")
    assert overview.startswith("---\n")
    assert "type: transcript-overview" in overview
    assert "| Участник | Discord | Речь | Доля |" in overview
    assert f"[[transcripts/{SESSION_ID}/01-000-001|" in overview
    assert "recap.draft.md" in overview
    assert "vault/notes/sessions/" in overview


def test_index_sqlite_carries_segments_turns_and_fts(rendered: dict) -> None:
    connection = sqlite3.connect(rendered["index"])
    try:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"segments", "turns", "segments_fts", "turns_fts", "meta"} <= tables
        assert connection.execute("SELECT count(*) FROM segments").fetchone()[0] == 30
        assert connection.execute("SELECT count(*) FROM turns").fetchone()[0] == 30
        hits = connection.execute(
            "SELECT count(*) FROM turns_fts WHERE turns_fts MATCH ?", ("Вазгар",)
        ).fetchone()[0]
        assert hits == 5
        raw, substituted = connection.execute(
            "SELECT text_raw, text FROM segments WHERE i = 4"
        ).fetchone()
        assert "Вагзар" in raw and "Вазгар" in substituted
    finally:
        connection.close()


# ------------------------------------------------------------ skip and force


def test_second_render_with_the_same_key_skips(rendered: dict, workspace: Workspace) -> None:
    again = render.run(
        roots=workspace.roots,
        config=workspace.config(),
        session_id=SESSION_ID,
        window_minutes=1,
    )
    assert again["status"] == "skipped"
    assert again["chunk_count"] == 5

    forced = render.run(
        roots=workspace.roots,
        config=workspace.config(),
        session_id=SESSION_ID,
        window_minutes=1,
        force=True,
    )
    assert forced["status"] == "ok"


def test_a_grown_lexicon_invalidates_the_render(rendered: dict, workspace: Workspace) -> None:
    workspace.write_lexicon(LEXICON_YAML + "  - id: extra\n    canonical: Кроненфельд\n")
    again = render.run(
        roots=workspace.roots,
        config=workspace.config(),
        session_id=SESSION_ID,
        window_minutes=1,
    )
    assert again["status"] == "ok"


def test_stale_chunks_from_a_previous_layout_are_removed(
    rendered: dict, workspace: Workspace
) -> None:
    transcript_dir = workspace.roots.transcripts / SESSION_ID
    stale = transcript_dir / "99-999-999.md"
    stale.write_text("# left over from an older window size\n", encoding="utf-8")

    again = render.run(
        roots=workspace.roots,
        config=workspace.config(),
        session_id=SESSION_ID,
        window_minutes=5,
        force=True,
    )
    assert not stale.exists()
    assert again["chunk_count"] == 1
    assert [p.name for p in _chunk_files(workspace)] == ["01-000-005.md"]


def test_render_refuses_without_an_adopted_session(workspace: Workspace) -> None:
    from session_ingest.errors import SessionIngestError

    with pytest.raises(SessionIngestError) as excinfo:
        render.run(roots=workspace.roots, config=workspace.config(), session_id=SESSION_ID)
    assert excinfo.value.code == "not_adopted"


def test_next_steps_lead_to_qmd_then_the_deterministic_record(rendered: dict) -> None:
    ids = [entry["id"] for entry in rendered["next_steps"]]
    assert ids == ["qmd_refresh", "record"]  # keyless: the metered steps are omitted
