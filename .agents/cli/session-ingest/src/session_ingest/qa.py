"""``qa`` — facts about a transcription, and which configured thresholds they cross.

DESIGN §5: *reports facts and threshold crossings, never verdicts.* The eight
metrics are computed in one streaming pass over the SDK's segments, because a
three-hour session is thousands of records and nothing here needs two passes.

Two of them deserve a note.

**``word_p10``** uses the nearest-rank percentile — sorted ascending, rank
``ceil(0.10 · n)``. Picked over interpolation because a human checking the
number against a small sample should get the same answer the code did.

**``lexicon_miss_rate``** is exact substring counting over an *enumerated* list
of strings: the canonical/display forms on one side, the listed variants on the
other, case-folded. There is deliberately no similarity, distance or fuzzy
matching anywhere in this file — a term is missed when a string the owner wrote
down as a misrecognition appears, and never because two words merely look alike.
Terms with no occurrences at all are excluded so an unused lexicon entry cannot
move the rate.

A ``morph: true`` term contributes more strings to that enumeration — its
generated case forms, from :mod:`session_ingest.morphs` — but not a different
kind of counting: the list is still fully enumerated before the first character
is compared, and it is the same list ``render`` substitutes with, so the two
stages cannot disagree about what "heard correctly" means.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from craig_stt_dataset import Dataset

from .adopt import load_session_link, local_archive, resolve_active_dataset
from .config import DEFAULT_QA_ECHO_TERM_FACTOR, SessionConfig
from .errors import SessionIngestError
from .models import (
    CompressionOutlier,
    LexiconTermCounts,
    QaMetrics,
    QaReport,
    ThresholdCrossing,
    TrackSpeech,
)
from .morphs import ExpansionTable, expand_terms
from .nextsteps import ARCHIVE_PLACEHOLDER, CLI, next_steps_for, step
from .paths import Roots, SessionTree
from .provenance import CompositeKey, Provenance, utc_now
from .vaultfiles import Lexicon, LexiconTerm, Speakers, load_lexicon, load_speakers
from .writer import read_json, write_json

#: Whisper's own signals of a bad decode. Fixed, documented, not tunable knobs:
#: they mark *what to look at*, while the tunable thresholds decide what to do.
LOW_LOGPROB = -1.0
HIGH_COMPRESSION_RATIO = 2.4

SEGMENT_COMPARISON_REFUSAL = (
    "Segment-level comparison across runs is refused by construction: a re-transcription "
    "renumbers every segment index and shifts every turn ID by milliseconds, so segment N "
    "in one run and segment N in another are not the same utterance. Compare run-level "
    "aggregates and per-term canonical-vs-variant counts instead."
)


def percentile_nearest_rank(values: Sequence[float], fraction: float) -> float | None:
    """Nearest-rank percentile: ``sorted[ceil(fraction · n) - 1]``."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(slots=True)
class _Pass:
    """Everything one streaming pass over the segments collects."""

    segments: int = 0
    word_probabilities: list[float] = field(default_factory=list)
    segments_with_logprob: int = 0
    low_logprob_segments: int = 0
    compression_outliers: list[CompressionOutlier] = field(default_factory=list)
    bleed_segments: int = 0
    overlap_segments: int = 0
    text_chunks: list[str] = field(default_factory=list)


def _scan(dataset: Dataset) -> _Pass:
    state = _Pass()
    for segment in dataset.iter_segments():
        state.segments += 1
        if segment.words:
            state.word_probabilities.extend(word.p for word in segment.words if word.p is not None)
        if segment.avg_logprob is not None:
            state.segments_with_logprob += 1
            if segment.avg_logprob < LOW_LOGPROB:
                state.low_logprob_segments += 1
        if (
            segment.compression_ratio is not None
            and segment.compression_ratio > HIGH_COMPRESSION_RATIO
        ):
            state.compression_outliers.append(
                CompressionOutlier(
                    segment_i=segment.i,
                    t0=segment.t0,
                    compression_ratio=segment.compression_ratio,
                    track=segment.track,
                    username=segment.username,
                )
            )
        if segment.bleed is not None and segment.bleed.suspect:
            state.bleed_segments += 1
        if segment.overlap:
            state.overlap_segments += 1
        if segment.text:
            state.text_chunks.append(segment.text)
    return state


def enumerated_forms(
    term: LexiconTerm, expansion: ExpansionTable | None = None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(canonical_forms, variant_forms)`` — every string counted for one term.

    The generated forms are appended rather than merged in, so the owner's own
    strings keep their order and a reader of ``qa.json`` can see which is which.
    Anything already enumerated literally is not repeated.
    """
    canonical = list(term.canonical_forms())
    variants = list(term.variant_forms())
    if expansion is not None:
        known = {form.casefold() for form in canonical} | {form.casefold() for form in variants}
        for form in (*expansion.display_forms(term.id), *expansion.variant_forms(term.id)):
            folded = form.casefold()
            if folded in known:
                continue
            known.add(folded)
            if form in expansion.display_forms(term.id):
                canonical.append(form)
            else:
                variants.append(form)
    return tuple(canonical), tuple(variants)


def count_term(
    text_casefolded: str, term: LexiconTerm, expansion: ExpansionTable | None = None
) -> LexiconTermCounts:
    """Exact, case-folded occurrence counts for one term's enumerated strings."""
    canonical_forms, variant_forms = enumerated_forms(term, expansion)
    canonical_hits = sum(text_casefolded.count(form.casefold()) for form in canonical_forms)
    per_variant = {variant: text_casefolded.count(variant.casefold()) for variant in variant_forms}
    return LexiconTermCounts(
        term_id=term.id,
        canonical=term.canonical,
        canonical_hits=canonical_hits,
        variant_hits=sum(per_variant.values()),
        per_variant=per_variant,
    )


def lexicon_counts(
    text_chunks: Iterable[str], lexicon: Lexicon, expansion: ExpansionTable | None = None
) -> tuple[LexiconTermCounts, ...]:
    """Per-term counts over the whole transcript.

    Segment texts are joined with newlines so a match can never straddle two
    segments — a substring spanning a boundary was never spoken as one string.
    """
    if not lexicon.present:
        return ()
    haystack = "\n".join(text_chunks).casefold()
    counted = [count_term(haystack, term, expansion) for term in lexicon.active_terms()]
    return tuple(c for c in counted if c.total_hits > 0)


def _per_track(dataset: Dataset) -> tuple[tuple[TrackSpeech, ...], tuple[TrackSpeech, ...]]:
    tracks = dataset.meta.tracks
    total_speech = sum(track.speech_s for track in tracks)
    rows = tuple(
        TrackSpeech(
            track=track.track,
            user_id=track.user_id,
            username=track.username,
            speech_s=track.speech_s,
            speech_share=(track.speech_s / total_speech) if total_speech else 0.0,
            segments=track.segments,
            skipped=track.skipped,
            skip_reason=track.skip_reason,
        )
        for track in tracks
    )
    return rows, tuple(row for row in rows if row.skipped)


def _unmapped(rows: Sequence[TrackSpeech], speakers: Speakers) -> tuple[dict[str, Any], ...]:
    """Discord ids that spoke but have no ``_speakers.yaml`` entry.

    Skipped tracks are excluded: an ignored bot is not a participant the owner
    forgot to map. Never guessed at from the username — that is the mapping the
    file exists to hold.
    """
    return tuple(
        {
            "user_id": row.user_id,
            "username": row.username,
            "track": row.track,
            "speech_s": row.speech_s,
        }
        for row in rows
        if not row.skipped and row.user_id is not None and speakers.get(row.user_id) is None
    )


def compute_metrics(
    dataset: Dataset,
    *,
    lexicon: Lexicon,
    speakers: Speakers,
    expansion: ExpansionTable | None = None,
) -> QaMetrics:
    """The eight DESIGN §5 metrics plus the per-track accounting read beside them."""
    state = _scan(dataset)
    rows, skipped = _per_track(dataset)
    terms = lexicon_counts(state.text_chunks, lexicon, expansion)

    total_hits = sum(term.total_hits for term in terms)
    variant_hits = sum(term.variant_hits for term in terms)
    counts = dataset.meta.counts

    return QaMetrics(
        word_p10=percentile_nearest_rank(state.word_probabilities, 0.10),
        low_logprob_share=(
            state.low_logprob_segments / state.segments_with_logprob
            if state.segments_with_logprob
            else None
        ),
        compression_outliers=tuple(state.compression_outliers),
        bleed_rate=(state.bleed_segments / state.segments) if state.segments else 0.0,
        overlap_rate=(state.overlap_segments / state.segments) if state.segments else 0.0,
        lexicon_miss_rate=(variant_hits / total_hits) if total_hits else None,
        unmapped_speakers=_unmapped(rows, speakers),
        tracks_missing=max(0, counts.tracks - counts.tracks_transcribed),
        segments=state.segments,
        words_with_probability=len(state.word_probabilities),
        segments_with_logprob=state.segments_with_logprob,
        per_track=rows,
        skipped_tracks=skipped,
        lexicon_terms=terms,
    )


def crossed_thresholds(metrics: QaMetrics, config: SessionConfig) -> tuple[ThresholdCrossing, ...]:
    """Which configured thresholds the facts cross. No verdict attached."""
    crossings: list[ThresholdCrossing] = []
    if metrics.word_p10 is not None and metrics.word_p10 < config.qa_min_word_p10:
        crossings.append(
            ThresholdCrossing(
                metric="word_p10",
                value=metrics.word_p10,
                threshold=config.qa_min_word_p10,
                direction="min",
            )
        )
    if metrics.bleed_rate > config.qa_max_bleed_rate:
        crossings.append(
            ThresholdCrossing(
                metric="bleed_rate",
                value=metrics.bleed_rate,
                threshold=config.qa_max_bleed_rate,
                direction="max",
            )
        )
    if (
        metrics.lexicon_miss_rate is not None
        and metrics.lexicon_miss_rate > config.qa_max_lexicon_miss_rate
    ):
        crossings.append(
            ThresholdCrossing(
                metric="lexicon_miss_rate",
                value=metrics.lexicon_miss_rate,
                threshold=config.qa_max_lexicon_miss_rate,
                direction="max",
            )
        )
    return tuple(crossings)


# ------------------------------------------------------------------- compare

_AGGREGATE_METRICS = (
    "word_p10",
    "low_logprob_share",
    "bleed_rate",
    "overlap_rate",
    "lexicon_miss_rate",
    "tracks_missing",
    "segments",
    "compression_outlier_count",
)

#: Absolute jump in ``compression_outlier_count`` that raises the second signal on
#: its own, for the case where the baseline is already large enough that 10× never
#: happens. The run that motivated this went 6 → 85: +79 absolute, and 14×.
COMPRESSION_JUMP_ABSOLUTE = 50
#: …and the multiplicative arm of the same check.
COMPRESSION_JUMP_FACTOR = 10

#: One plain sentence per signal. They describe what was counted, not what it means
#: — the reading is in :data:`ECHO_READING`, and neither is a verdict.
ECHO_SIGNAL_NOTES = {
    "term_hits_outran_words": (
        "Lexicon-term hits grew far faster than the transcript did, so the newer run says "
        "the biased names much more often without saying much more overall."
    ),
    "compression_outliers_jumped": (
        "Segments above whisper's own repetition-loop ratio (2.4) multiplied between the "
        "runs, which is what a decode stuck repeating a list looks like."
    ),
}

ECHO_READING = (
    "These are facts, not a verdict: a falling lexicon_miss_rate accompanied by these "
    "signals suggests prompt echo — the model reciting the biasing term list back — rather "
    "than a better transcription. Read the listed compression outliers before promoting."
)


def _metric_value(report: Mapping[str, Any], metric: str) -> Any:
    return (report.get("metrics") or {}).get(metric)


def _total_term_hits(report: Mapping[str, Any]) -> int | None:
    """Every lexicon hit in one run, canonical and variant alike.

    Summed rather than read off a stored total because ``lexicon_terms`` is the
    per-term evidence a reader can check the number against by hand.
    """
    rows = _metric_value(report, "lexicon_terms")
    if not isinstance(rows, list):
        return None
    return sum(int(row.get("total_hits") or 0) for row in rows if isinstance(row, dict))


def _delta(before: Any, after: Any) -> float | None:
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return float(after) - float(before)
    return None


def echo_signals(
    *,
    term_hits_delta: float | None,
    words_delta: float | None,
    outliers_before: Any,
    outliers_after: Any,
    term_factor: float,
) -> list[str]:
    """Which deterministic sanity checks the two runs crossed. Arithmetic only.

    ``term_hits_outran_words`` fires when term-hit growth exceeds word growth times
    ``term_factor``; a run that lost words clamps to zero, so *any* term-hit growth
    on a shrinking transcript counts. ``compression_outliers_jumped`` fires on more
    than a 10× rise or a rise of 50+ — including a rise from zero, which is exactly
    the shape a repetition loop appearing out of nowhere has.
    """
    signals: list[str] = []
    if (
        term_hits_delta is not None
        and words_delta is not None
        and term_hits_delta > max(0.0, words_delta) * term_factor
    ):
        signals.append("term_hits_outran_words")
    if isinstance(outliers_before, (int, float)) and isinstance(outliers_after, (int, float)):
        grew_multiplicatively = outliers_after > outliers_before * COMPRESSION_JUMP_FACTOR
        grew_absolutely = outliers_after - outliers_before >= COMPRESSION_JUMP_ABSOLUTE
        if grew_multiplicatively or grew_absolutely:
            signals.append("compression_outliers_jumped")
    return signals


def compare_runs(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    echo_term_factor: float = DEFAULT_QA_ECHO_TERM_FACTOR,
) -> dict[str, Any]:
    """Run-level aggregates, per-term counts, and the echo signals — no verdict.

    The echo block exists because the obvious cross-run reading is wrong in one
    specific, measured way. Hotword/initial-prompt biasing can *lower*
    ``lexicon_miss_rate`` while making the transcript worse, by getting the model
    to recite the term list instead of transcribing speech. Every extra recitation
    counts as a canonical hit, so the rate improves. The three counts here are what
    separate that from a real improvement: real corrections move variant hits into
    canonical hits without inflating the total, and they do not multiply whisper's
    repetition-loop outliers.
    """
    base_run = baseline.get("run")
    this_run = current.get("run")

    aggregates: list[dict[str, Any]] = []
    for metric in _AGGREGATE_METRICS:
        before = _metric_value(baseline, metric)
        after = _metric_value(current, metric)
        aggregates.append(
            {
                "metric": metric,
                f"run_{base_run}": before,
                f"run_{this_run}": after,
                "delta": _delta(before, after),
            }
        )

    def echo_row(metric: str, before: Any, after: Any) -> dict[str, Any]:
        return {
            "metric": metric,
            f"run_{base_run}": before,
            f"run_{this_run}": after,
            "delta": _delta(before, after),
        }

    term_hits = echo_row("lexicon_term_hits", _total_term_hits(baseline), _total_term_hits(current))
    words = echo_row(
        "words",
        _metric_value(baseline, "words_with_probability"),
        _metric_value(current, "words_with_probability"),
    )
    outliers = echo_row(
        "compression_outlier_count",
        _metric_value(baseline, "compression_outlier_count"),
        _metric_value(current, "compression_outlier_count"),
    )
    signals = echo_signals(
        term_hits_delta=term_hits["delta"],
        words_delta=words["delta"],
        outliers_before=outliers[f"run_{base_run}"],
        outliers_after=outliers[f"run_{this_run}"],
        term_factor=echo_term_factor,
    )

    def term_rows(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        rows = (report.get("metrics") or {}).get("lexicon_terms") or []
        return {str(row.get("term_id")): row for row in rows if isinstance(row, dict)}

    before_terms = term_rows(baseline)
    after_terms = term_rows(current)
    terms: list[dict[str, Any]] = []
    for term_id in sorted(set(before_terms) | set(after_terms)):
        before = before_terms.get(term_id)
        after = after_terms.get(term_id)
        before_miss = before.get("miss_rate") if before else None
        after_miss = after.get("miss_rate") if after else None
        delta = (
            float(after_miss) - float(before_miss)
            if isinstance(before_miss, (int, float)) and isinstance(after_miss, (int, float))
            else None
        )
        terms.append(
            {
                "term_id": term_id,
                "canonical": (after or before or {}).get("canonical"),
                f"run_{base_run}": before,
                f"run_{this_run}": after,
                "delta_miss_rate": delta,
            }
        )

    return {
        "runs": [base_run, this_run],
        "aggregates": aggregates,
        "echo": {
            "facts": [term_hits, words, outliers],
            "term_factor": echo_term_factor,
            "signal_notes": {name: ECHO_SIGNAL_NOTES[name] for name in signals},
            "reading": ECHO_READING,
        },
        "signals": signals,
        "lexicon_terms": terms,
        "segment_level": "refused",
        "segment_level_reason": SEGMENT_COMPARISON_REFUSAL,
    }


# ----------------------------------------------------------------- the verb


@dataclass(slots=True)
class QaRunResult:
    status: str
    session: str
    run: int
    active_run: int
    dataset_digest: str | None
    recording_id: str | None
    qa_path: str
    active_qa_path: str | None
    thresholds_crossed: list[dict[str, Any]]
    report: dict[str, Any]
    comparison: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    next_steps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "session": self.session,
            "run": self.run,
            "active_run": self.active_run,
            "dataset_digest": self.dataset_digest,
            "recording_id": self.recording_id,
            "qa_path": self.qa_path,
            "active_qa_path": self.active_qa_path,
            "thresholds_crossed": self.thresholds_crossed,
            "report": self.report,
            "comparison": self.comparison,
            "warnings": self.warnings,
            "next_steps": self.next_steps,
        }


def load_qa_report(tree: SessionTree, run: int) -> dict[str, Any]:
    path = tree.run_qa_json(run)
    if not path.is_file():
        raise SessionIngestError(
            f"no QA report for run {run} at {path}",
            code="qa_report_missing",
            next_steps=[
                step(
                    "qa_baseline",
                    f"Produce the run {run} QA report before comparing against it.",
                    command=f"{CLI} qa --session {tree.id} --run {run}",
                )
            ],
            detail={"run": run, "path": str(path)},
        )
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise SessionIngestError(f"{path} does not contain a JSON object", code="qa_report_invalid")
    return payload


def run_qa(
    *,
    roots: Roots,
    config: SessionConfig,
    session_id: str,
    run: int | None = None,
    compare: int | None = None,
    force: bool = False,
    on_resolved: Callable[[str, str | None, int], None] | None = None,
) -> QaRunResult:
    """Compute (or reuse) the QA report for one run of one session."""
    tree = roots.session(session_id)
    recording, dataset = resolve_active_dataset(tree, run)
    link = load_session_link(tree)
    effective_run = run if run is not None else link.active_run

    if on_resolved is not None:
        on_resolved(session_id, recording.dataset_digest, effective_run)

    lexicon = load_lexicon(roots.lexicon_file)
    speakers = load_speakers(roots.speakers_file)
    expansion = expand_terms(lexicon)

    # A crossed threshold routes the agent into the re-run chain, whose transcribe
    # step reads the archive already on disk. We have the dataset open, so resolve
    # the real path now rather than emitting a placeholder the agent has to fill in.
    archive = local_archive(Path(recording.dataset_path), dataset)
    archive_hint = str(archive) if archive is not None else ARCHIVE_PLACEHOLDER

    qa_path = tree.run_qa_json(effective_run)
    is_active = effective_run == link.active_run
    active_path = tree.qa_json if is_active else None

    key = CompositeKey(
        dataset_digest=recording.dataset_digest,
        lexicon_digest=lexicon.digest,
        speakers_digest=speakers.digest,
        knobs={
            "run": effective_run,
            "thresholds": config.thresholds(),
            # The expansion digest carries the pymorphy3 and dictionary versions,
            # so a library upgrade re-measures instead of trusting a stale rate.
            "morph_digest": expansion.digest(),
        },
    )
    provenance = Provenance.load(tree.provenance_json)
    outputs: list[Path] = [qa_path] + ([active_path] if active_path else [])

    if provenance.should_skip("qa", key, force=force, root=tree.root):
        payload = read_json(qa_path)
        report_dict: dict[str, Any] = payload if isinstance(payload, dict) else {}
        comparison = (
            compare_runs(
                load_qa_report(tree, compare),
                report_dict,
                echo_term_factor=config.qa_echo_term_factor,
            )
            if compare is not None
            else None
        )
        return QaRunResult(
            status="skipped",
            session=session_id,
            run=effective_run,
            active_run=link.active_run,
            dataset_digest=recording.dataset_digest,
            recording_id=recording.recording_id,
            qa_path=str(qa_path),
            active_qa_path=str(active_path) if active_path else None,
            thresholds_crossed=list(report_dict.get("thresholds_crossed") or []),
            report=report_dict,
            comparison=comparison,
            warnings=[
                "QA already computed for this dataset/lexicon/speakers key; --force to rerun"
            ],
            next_steps=next_steps_for(
                "qa",
                session_id=session_id,
                api_key_present=config.api_key_present,
                run=effective_run,
                thresholds_crossed=bool(report_dict.get("thresholds_crossed")),
                scratch_dir=str(roots.scratch / f"{session_id}-run{effective_run + 1}"),
                archive=archive_hint,
            ),
        )

    metrics = compute_metrics(dataset, lexicon=lexicon, speakers=speakers, expansion=expansion)
    report = QaReport(
        session=session_id,
        run=effective_run,
        dataset_digest=recording.dataset_digest,
        recording_id=recording.recording_id,
        metrics=metrics,
        thresholds=config.thresholds(),
        thresholds_crossed=crossed_thresholds(metrics, config),
        lexicon=lexicon.to_dict(),
        speakers=speakers.to_dict(),
        generated_at=utc_now(),
    )
    payload = report.to_dict()
    write_json(qa_path, payload)
    if active_path is not None:
        write_json(active_path, payload)

    provenance.mark_done(
        "qa",
        key,
        outputs=outputs,
        extra={
            "run": effective_run,
            "crossed": [c.metric for c in report.thresholds_crossed],
            "morph_digest": expansion.digest(),
        },
    )

    warnings: list[str] = []
    if not lexicon.present:
        warnings.append(f"{roots.lexicon_file} is absent — lexicon_miss_rate could not be measured")
    if not speakers.present:
        warnings.append(
            f"{roots.speakers_file} is absent — every speaking user_id is reported as unmapped"
        )

    comparison = (
        compare_runs(
            load_qa_report(tree, compare), payload, echo_term_factor=config.qa_echo_term_factor
        )
        if compare is not None
        else None
    )

    return QaRunResult(
        status="ok",
        session=session_id,
        run=effective_run,
        active_run=link.active_run,
        dataset_digest=recording.dataset_digest,
        recording_id=recording.recording_id,
        qa_path=str(qa_path),
        active_qa_path=str(active_path) if active_path else None,
        thresholds_crossed=[c.to_dict() for c in report.thresholds_crossed],
        report=payload,
        comparison=comparison,
        warnings=warnings,
        next_steps=next_steps_for(
            "qa",
            session_id=session_id,
            api_key_present=config.api_key_present,
            run=effective_run,
            thresholds_crossed=report.crossed,
            scratch_dir=str(roots.scratch / f"{session_id}-run{effective_run + 1}"),
            archive=archive_hint,
        ),
    )
