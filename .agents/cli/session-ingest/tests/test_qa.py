"""QA metric exactness.

Every expected value in this file is computed **by hand** from the fixture's
declared shape (see ``conftest.Expected``), not by re-running the code under
test. A metric that quietly changes definition therefore fails here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from craig_stt_dataset import open_dataset

from session_ingest.adopt import run_adopt
from session_ingest.config import resolve_config
from session_ingest.errors import SessionIngestError
from session_ingest.morphs import expand_terms
from session_ingest.nextsteps import ARCHIVE_PLACEHOLDER
from session_ingest.qa import (
    ECHO_SIGNAL_NOTES,
    SEGMENT_COMPARISON_REFUSAL,
    compare_runs,
    compute_metrics,
    crossed_thresholds,
    lexicon_counts,
    percentile_nearest_rank,
    run_qa,
)
from session_ingest.vaultfiles import load_lexicon, load_speakers

from .conftest import (
    EXPECTED,
    MORPH_LEXICON_YAML,
    RECORDING_ID,
    SESSION_ID,
    Workspace,
    write_dataset,
)

PERMISSIVE = {
    "TTRPG_SESSION_QA_MIN_WORD_P10": "0.10",
    "TTRPG_SESSION_QA_MAX_BLEED_RATE": "0.90",
    "TTRPG_SESSION_QA_MAX_LEXICON_MISS_RATE": "0.90",
}


def _prepare(workspace: Workspace, *, lexicon: bool = True, speakers: bool = True):
    if lexicon:
        workspace.write_lexicon()
    if speakers:
        workspace.write_speakers()
    run_adopt(
        target=str(workspace.dataset_dir),
        roots=workspace.roots,
        config=resolve_config(env={}),
        allow_skipped_tracks=True,
    )


# ------------------------------------------------------------------ percentile


def test_nearest_rank_percentile_is_the_documented_one() -> None:
    # rank = ceil(0.10 * 10) = 1 -> the smallest value
    assert percentile_nearest_rank([0.9, 0.1, 0.5, 0.7, 0.2, 0.3, 0.4, 0.6, 0.8, 1.0], 0.10) == 0.1
    # rank = ceil(0.10 * 20) = 2 -> the second smallest
    assert percentile_nearest_rank(list(range(20)), 0.10) == 1
    assert percentile_nearest_rank([], 0.10) is None


# --------------------------------------------------------------------- metrics


def test_metrics_match_hand_computed_values(workspace: Workspace) -> None:
    workspace.write_lexicon()
    workspace.write_speakers()
    dataset = open_dataset(workspace.dataset_dir)
    metrics = compute_metrics(
        dataset,
        lexicon=load_lexicon(workspace.roots.lexicon_file),
        speakers=load_speakers(workspace.roots.speakers_file),
    )

    assert metrics.segments == EXPECTED.segments
    assert metrics.words_with_probability == EXPECTED.words_with_probability
    assert metrics.word_p10 == EXPECTED.word_p10
    assert metrics.low_logprob_share == pytest.approx(EXPECTED.low_logprob_share)
    assert len(metrics.compression_outliers) == EXPECTED.compression_outliers
    assert metrics.bleed_rate == pytest.approx(EXPECTED.bleed_rate)
    assert metrics.overlap_rate == pytest.approx(EXPECTED.overlap_rate)
    assert metrics.lexicon_miss_rate == pytest.approx(EXPECTED.lexicon_miss_rate)
    assert len(metrics.unmapped_speakers) == EXPECTED.unmapped_speakers
    assert metrics.tracks_missing == EXPECTED.tracks_missing


def test_compression_outliers_are_strictly_above_the_threshold(workspace: Workspace) -> None:
    metrics = compute_metrics(
        open_dataset(workspace.dataset_dir),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
        speakers=load_speakers(workspace.roots.speakers_file),
    )
    outliers = {outlier.segment_i: outlier for outlier in metrics.compression_outliers}
    assert set(outliers) == {7, 17}, "segment 8 sits exactly on 2.4 and must not count"
    assert outliers[7].t0 == 70.0
    assert outliers[17].compression_ratio == 3.5


def test_lexicon_counting_is_exact_over_enumerated_strings(workspace: Workspace) -> None:
    workspace.write_lexicon()
    metrics = compute_metrics(
        open_dataset(workspace.dataset_dir),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
        speakers=load_speakers(workspace.roots.speakers_file),
    )
    by_id = {term.term_id: term for term in metrics.lexicon_terms}
    # `kilverin` never occurs -> excluded entirely; `oswald` is inactive.
    assert set(by_id) == {"vagzar"}
    vagzar = by_id["vagzar"]
    assert vagzar.canonical_hits == 2, "'Вазгар' and lowercase 'вазгар' both count"
    assert vagzar.variant_hits == 3
    assert vagzar.per_variant == {"Вагзар": 2, "Вазагар": 1}
    assert vagzar.miss_rate == pytest.approx(0.6)


def test_generated_case_forms_are_counted_on_the_side_they_belong_to(
    workspace: Workspace,
) -> None:
    """A `morph: true` term widens the enumeration, not the kind of counting.

    Without the table neither spelling is recognised at all and the term drops
    out of the rate entirely — which is exactly the blindness the feature fixes:
    a session where everyone said «Марвике» would have scored a perfect zero
    miss rate by never noticing the name.
    """
    workspace.write_lexicon(MORPH_LEXICON_YAML)
    lexicon = load_lexicon(workspace.roots.lexicon_file)
    spoken = ["Говорили с Морвикой.", "Подошли к Марвике."]

    blind = lexicon_counts(spoken, lexicon)
    assert blind == (), "neither inflected spelling is enumerated literally"

    counted = {row.term_id: row for row in lexicon_counts(spoken, lexicon, expand_terms(lexicon))}
    assert set(counted) == {"morvika"}
    assert counted["morvika"].canonical_hits == 1, "«Морвикой» is the name heard right"
    assert counted["morvika"].variant_hits == 1, "«Марвике» is the name heard wrong"
    assert counted["morvika"].miss_rate == pytest.approx(0.5)


def test_the_qa_key_moves_when_the_expansion_does(workspace: Workspace) -> None:
    """Upgrading the dictionary must re-measure rather than trust a cached rate."""
    _prepare(workspace, lexicon=False)
    workspace.write_lexicon(MORPH_LEXICON_YAML)
    config = resolve_config(env=workspace.plain_env(**PERMISSIVE))
    first = run_qa(roots=workspace.roots, config=config, session_id=SESSION_ID)
    assert first.status == "ok"

    tree = workspace.roots.session(SESSION_ID)
    stages = json.loads(tree.provenance_json.read_text(encoding="utf-8"))["stages"]
    lexicon = load_lexicon(workspace.roots.lexicon_file)
    assert stages["qa"]["composite_key"]["knobs"]["morph_digest"] == expand_terms(lexicon).digest()

    again = run_qa(roots=workspace.roots, config=config, session_id=SESSION_ID)
    assert again.status == "skipped", "an unchanged expansion must still skip"


def test_absent_lexicon_leaves_the_rate_unmeasured(workspace: Workspace) -> None:
    metrics = compute_metrics(
        open_dataset(workspace.dataset_dir),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
        speakers=load_speakers(workspace.roots.speakers_file),
    )
    assert metrics.lexicon_miss_rate is None
    assert metrics.lexicon_terms == ()


def test_per_track_shares_and_skipped_tracks(workspace: Workspace) -> None:
    metrics = compute_metrics(
        open_dataset(workspace.dataset_dir),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
        speakers=load_speakers(workspace.roots.speakers_file),
    )
    shares = {row.track: row.speech_share for row in metrics.per_track}
    assert shares == pytest.approx(EXPECTED.track_shares)
    assert [row.track for row in metrics.skipped_tracks] == [3]
    assert metrics.skipped_tracks[0].skip_reason == "ignored: bot"


def test_unmapped_speakers_never_include_skipped_tracks(workspace: Workspace) -> None:
    workspace.write_speakers()
    metrics = compute_metrics(
        open_dataset(workspace.dataset_dir),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
        speakers=load_speakers(workspace.roots.speakers_file),
    )
    assert [entry["user_id"] for entry in metrics.unmapped_speakers] == ["u-bob"]


# ------------------------------------------------------------------ thresholds


def test_thresholds_crossed_by_the_fixture(workspace: Workspace) -> None:
    workspace.write_lexicon()
    metrics = compute_metrics(
        open_dataset(workspace.dataset_dir),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
        speakers=load_speakers(workspace.roots.speakers_file),
    )
    crossed = crossed_thresholds(metrics, resolve_config(env={}))
    # word_p10 0.55 < 0.60 and bleed_rate 0.1333 > 0.10; miss rate 0.6 > 0.35 too.
    assert {crossing.metric for crossing in crossed} == {
        "word_p10",
        "bleed_rate",
        "lexicon_miss_rate",
    }
    word = next(c for c in crossed if c.metric == "word_p10")
    assert word.direction == "min"
    assert word.describe().startswith("word_p10=0.55 < 0.6")


def test_permissive_thresholds_cross_nothing(workspace: Workspace) -> None:
    workspace.write_lexicon()
    metrics = compute_metrics(
        open_dataset(workspace.dataset_dir),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
        speakers=load_speakers(workspace.roots.speakers_file),
    )
    assert crossed_thresholds(metrics, resolve_config(env=PERMISSIVE)) == ()


# ------------------------------------------------------------------- the verb


def test_run_qa_writes_both_report_paths(workspace: Workspace) -> None:
    _prepare(workspace)
    result = run_qa(roots=workspace.roots, config=resolve_config(env={}), session_id=SESSION_ID)
    assert result.status == "ok"
    assert Path(result.qa_path).is_file()
    assert result.active_qa_path is not None and Path(result.active_qa_path).is_file()
    assert json.loads(Path(result.qa_path).read_text(encoding="utf-8")) == json.loads(
        Path(result.active_qa_path).read_text(encoding="utf-8")
    )
    payload = json.loads(Path(result.qa_path).read_text(encoding="utf-8"))
    assert payload["schema"] == "ttrpg.session-qa/1"
    assert payload["recording_id"] == RECORDING_ID


def test_run_qa_is_skip_if_done(workspace: Workspace) -> None:
    _prepare(workspace)
    config = resolve_config(env={})
    assert run_qa(roots=workspace.roots, config=config, session_id=SESSION_ID).status == "ok"
    assert run_qa(roots=workspace.roots, config=config, session_id=SESSION_ID).status == "skipped"
    forced = run_qa(roots=workspace.roots, config=config, session_id=SESSION_ID, force=True)
    assert forced.status == "ok"


def test_editing_the_lexicon_invalidates_qa(workspace: Workspace) -> None:
    """The lexicon digest is part of the key, so growing it must force a recompute."""
    _prepare(workspace)
    config = resolve_config(env={})
    run_qa(roots=workspace.roots, config=config, session_id=SESSION_ID)
    workspace.write_lexicon(
        workspace.roots.lexicon_file.read_text(encoding="utf-8") + "    # grown\n"
    )
    assert run_qa(roots=workspace.roots, config=config, session_id=SESSION_ID).status == "ok"


def test_crossed_thresholds_emit_the_rerun_chain(workspace: Workspace) -> None:
    _prepare(workspace)
    result = run_qa(roots=workspace.roots, config=resolve_config(env={}), session_id=SESSION_ID)
    ids = [entry["id"] for entry in result.next_steps]
    assert ids == ["plan_rerun", "craig_transcribe_rerun", "adopt_rerun", "qa_compare", "promote"]
    # glossary is metered and there is no key in this environment.
    assert "glossary" not in ids


def _transcribe_command(result) -> str:
    return next(
        entry["command"] for entry in result.next_steps if entry["id"] == "craig_transcribe_rerun"
    )


def test_the_rerun_step_names_the_local_archive_it_can_find(workspace: Workspace) -> None:
    """The re-run must transcribe the zip on disk — Craig expires recordings."""
    _prepare(workspace)
    archive = workspace.dataset_dir / "source" / "R52_local.flac.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"not really a zip")

    result = run_qa(roots=workspace.roots, config=resolve_config(env={}), session_id=SESSION_ID)
    assert str(archive) in _transcribe_command(result)


def test_the_rerun_step_falls_back_to_a_placeholder_only_when_nothing_is_on_disk(
    workspace: Workspace,
) -> None:
    _prepare(workspace)
    result = run_qa(roots=workspace.roots, config=resolve_config(env={}), session_id=SESSION_ID)
    assert ARCHIVE_PLACEHOLDER in _transcribe_command(result)


def test_clean_thresholds_emit_the_render_chain(workspace: Workspace) -> None:
    _prepare(workspace)
    result = run_qa(
        roots=workspace.roots, config=resolve_config(env=PERMISSIVE), session_id=SESSION_ID
    )
    ids = [entry["id"] for entry in result.next_steps]
    assert ids == ["render", "qmd_refresh", "record"]


# --------------------------------------------------------------------- compare


def test_compare_is_run_level_and_refuses_segment_level(workspace: Workspace) -> None:
    _prepare(workspace)
    config = resolve_config(env={})
    run_qa(roots=workspace.roots, config=config, session_id=SESSION_ID)

    rerun_dir = workspace.roots.scratch / "run2" / RECORDING_ID
    write_dataset(rerun_dir, with_manifest=False)
    segments = rerun_dir / "segments.jsonl"
    segments.write_text(
        segments.read_text(encoding="utf-8").replace("Вагзар", "Вазгар"), encoding="utf-8"
    )
    from craig_stt_dataset import build_manifest, write_manifest

    write_manifest(
        rerun_dir,
        build_manifest(rerun_dir, recording_id=RECORDING_ID, produced_by={"craig-stt": "0.9.0"}),
    )
    run_adopt(
        target=str(rerun_dir),
        roots=workspace.roots,
        config=config,
        run=2,
        allow_skipped_tracks=True,
    )

    result = run_qa(roots=workspace.roots, config=config, session_id=SESSION_ID, run=2, compare=1)
    comparison = result.comparison
    assert comparison is not None
    assert comparison["runs"] == [1, 2]
    assert comparison["segment_level"] == "refused"
    assert comparison["segment_level_reason"] == SEGMENT_COMPARISON_REFUSAL

    miss = next(row for row in comparison["aggregates"] if row["metric"] == "lexicon_miss_rate")
    assert miss["run_1"] == pytest.approx(0.6)
    # Two of the three variant spellings became canonical, so 1 miss out of 5.
    assert miss["run_2"] == pytest.approx(0.2)
    assert miss["delta"] == pytest.approx(-0.4)

    term = next(row for row in comparison["lexicon_terms"] if row["term_id"] == "vagzar")
    assert term["run_1"]["variant_hits"] == 3
    assert term["run_2"]["variant_hits"] == 1


def test_compare_without_a_baseline_names_the_fix(workspace: Workspace) -> None:
    _prepare(workspace)
    with pytest.raises(SessionIngestError) as excinfo:
        run_qa(
            roots=workspace.roots,
            config=resolve_config(env={}),
            session_id=SESSION_ID,
            compare=7,
        )
    assert excinfo.value.code == "qa_report_missing"
    assert "qa --session" in excinfo.value.next_steps[0]["command"]


def test_compare_runs_pairs_terms_by_id() -> None:
    baseline = {
        "run": 1,
        "metrics": {
            "word_p10": 0.5,
            "lexicon_terms": [
                {"term_id": "a", "canonical": "A", "miss_rate": 0.8, "variant_hits": 4}
            ],
        },
    }
    current = {
        "run": 2,
        "metrics": {
            "word_p10": 0.7,
            "lexicon_terms": [
                {"term_id": "a", "canonical": "A", "miss_rate": 0.2, "variant_hits": 1}
            ],
        },
    }
    comparison = compare_runs(baseline, current)
    word = next(row for row in comparison["aggregates"] if row["metric"] == "word_p10")
    assert word["delta"] == pytest.approx(0.2)
    assert comparison["lexicon_terms"][0]["delta_miss_rate"] == pytest.approx(-0.6)


# ----------------------------------------------------------- echo detection


def _run(
    number: int,
    *,
    term_hits: int,
    words: int,
    outliers: int,
    miss_rate: float,
) -> dict:
    """A QA payload carrying only what the echo check reads.

    ``term_hits`` is spread over one term because the check sums ``lexicon_terms``
    rather than trusting a stored total — the per-term rows are the evidence a
    reader can re-add by hand.
    """
    return {
        "run": number,
        "metrics": {
            "words_with_probability": words,
            "compression_outlier_count": outliers,
            "lexicon_miss_rate": miss_rate,
            "lexicon_terms": [
                {
                    "term_id": "vagzar",
                    "canonical": "Вазгар",
                    "total_hits": term_hits,
                    "miss_rate": miss_rate,
                }
            ],
        },
    }


def test_compare_counts_term_hits_words_and_outliers_per_run() -> None:
    comparison = compare_runs(
        _run(1, term_hits=100, words=30_000, outliers=6, miss_rate=0.40),
        _run(2, term_hits=512, words=30_130, outliers=85, miss_rate=0.10),
    )
    facts = {row["metric"]: row for row in comparison["echo"]["facts"]}
    assert facts["lexicon_term_hits"]["run_1"] == 100
    assert facts["lexicon_term_hits"]["run_2"] == 512
    assert facts["lexicon_term_hits"]["delta"] == pytest.approx(412)
    assert facts["words"]["delta"] == pytest.approx(130)
    assert facts["compression_outlier_count"]["run_1"] == 6
    assert facts["compression_outlier_count"]["run_2"] == 85
    assert facts["compression_outlier_count"]["delta"] == pytest.approx(79)


def test_the_measured_echo_run_raises_both_signals() -> None:
    """The real A/B this check was written from: a better-looking rate, worse text.

    `lexicon_miss_rate` fell 0.40 → 0.10, which read alone says the re-run was an
    improvement. It was not: the model recited the biasing list, so term hits grew
    by 412 on 130 extra words and compression outliers went 6 → 85.
    """
    comparison = compare_runs(
        _run(1, term_hits=100, words=30_000, outliers=6, miss_rate=0.40),
        _run(2, term_hits=512, words=30_130, outliers=85, miss_rate=0.10),
    )
    assert comparison["signals"] == ["term_hits_outran_words", "compression_outliers_jumped"]
    miss = next(row for row in comparison["aggregates"] if row["metric"] == "lexicon_miss_rate")
    assert miss["delta"] == pytest.approx(-0.30), "the rate improved and the run still echoed"
    notes = comparison["echo"]["signal_notes"]
    assert set(notes) == set(comparison["signals"])
    assert notes["term_hits_outran_words"] == ECHO_SIGNAL_NOTES["term_hits_outran_words"]
    assert "not a verdict" in comparison["echo"]["reading"]
    assert "prompt echo" in comparison["echo"]["reading"]


def test_a_genuine_correction_raises_no_signal() -> None:
    """What a real fix looks like: variants become canonical, totals barely move."""
    comparison = compare_runs(
        _run(1, term_hits=100, words=30_000, outliers=6, miss_rate=0.40),
        _run(2, term_hits=104, words=30_120, outliers=7, miss_rate=0.05),
    )
    assert comparison["signals"] == []
    assert comparison["echo"]["signal_notes"] == {}


def test_term_hit_growth_is_measured_against_word_growth_times_the_factor() -> None:
    baseline = _run(1, term_hits=100, words=1_000, outliers=0, miss_rate=0.4)

    def signals_at(term_hits: int) -> list[str]:
        current = _run(2, term_hits=term_hits, words=1_030, outliers=0, miss_rate=0.1)
        return compare_runs(baseline, current)["signals"]

    # +30 words at the default factor 3.0 allows exactly +90 term hits, no more.
    assert signals_at(190) == []
    assert signals_at(191) == ["term_hits_outran_words"]


def test_the_echo_factor_is_configurable() -> None:
    baseline = _run(1, term_hits=100, words=1_000, outliers=0, miss_rate=0.4)
    current = _run(2, term_hits=191, words=1_030, outliers=0, miss_rate=0.1)
    assert compare_runs(baseline, current, echo_term_factor=10.0)["signals"] == []
    assert compare_runs(baseline, current, echo_term_factor=10.0)["echo"]["term_factor"] == 10.0


def test_a_shrinking_transcript_clamps_the_allowance_to_zero() -> None:
    """Fewer words but more term hits is echo by definition, whatever the factor."""
    comparison = compare_runs(
        _run(1, term_hits=100, words=30_000, outliers=0, miss_rate=0.4),
        _run(2, term_hits=101, words=29_000, outliers=0, miss_rate=0.1),
    )
    assert comparison["signals"] == ["term_hits_outran_words"]


def test_compression_outliers_jump_on_either_arm() -> None:
    def signals_between(before: int, after: int) -> list[str]:
        return compare_runs(
            _run(1, term_hits=100, words=30_000, outliers=before, miss_rate=0.4),
            _run(2, term_hits=100, words=30_000, outliers=after, miss_rate=0.4),
        )["signals"]

    # 4 → 41 is above 10× but only +37 absolute …
    assert signals_between(4, 41) == ["compression_outliers_jumped"]
    # … and 200 → 250 is +50 absolute without being anywhere near 10×.
    assert signals_between(200, 250) == ["compression_outliers_jumped"]
    assert signals_between(4, 40) == []


def test_missing_counts_never_invent_a_signal() -> None:
    """An old report without the fields must read as unknown, not as a crossing."""
    comparison = compare_runs({"run": 1, "metrics": {}}, {"run": 2, "metrics": {}})
    facts = {row["metric"]: row for row in comparison["echo"]["facts"]}
    assert facts["words"]["delta"] is None
    assert comparison["signals"] == []


def test_the_echo_block_reaches_the_live_compare_payload(workspace: Workspace) -> None:
    """The signals must come out of `qa --compare`, not just the pure function."""
    _prepare(workspace)
    config = resolve_config(env={})
    run_qa(roots=workspace.roots, config=config, session_id=SESSION_ID)

    rerun_dir = workspace.roots.scratch / "run2" / RECORDING_ID
    write_dataset(rerun_dir, with_manifest=False)
    from craig_stt_dataset import build_manifest, write_manifest

    write_manifest(
        rerun_dir,
        build_manifest(rerun_dir, recording_id=RECORDING_ID, produced_by={"craig-stt": "0.9.0"}),
    )
    run_adopt(
        target=str(rerun_dir),
        roots=workspace.roots,
        config=config,
        run=2,
        allow_skipped_tracks=True,
    )
    result = run_qa(roots=workspace.roots, config=config, session_id=SESSION_ID, run=2, compare=1)
    assert result.comparison is not None
    echo = result.comparison["echo"]
    assert {row["metric"] for row in echo["facts"]} == {
        "lexicon_term_hits",
        "words",
        "compression_outlier_count",
    }
    assert echo["term_factor"] == pytest.approx(3.0)
    # Identical datasets: nothing grew, so nothing is signalled.
    assert result.comparison["signals"] == []
