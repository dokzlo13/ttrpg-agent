"""``plan`` — build the biasing inputs and emit the transcribe command.

Contract (DESIGN §4, §5):

* Read ``_lexicon.yaml`` and optionally ``--entities-file`` (active-arc entities).
  Rank terms deterministically by ``active``/``priority``; ``--rank`` swaps the
  ranking for a metered LLM pick, and is the only metered part of this verb.
* Emit **one ranked list twice**, into ``inputs/hotwords.txt`` and
  ``inputs/initial_prompt.txt``, with *opposite* truncation: hotwords truncate
  from the front (best terms LAST), the initial prompt from the back (best terms
  FIRST), to ``--budget-chars`` (~450).
* Emit the exact ``craig-stt transcribe`` command in ``next_steps``, with ``--json``
  so the run answers with one parseable object rather than a table.
  ``session-ingest`` never spawns craig-stt: a ten-minute GPU job with progress
  output must not hide inside another process.
* Skip-if-done on ``CompositeKey(lexicon_digest, knobs={run, budget_chars, rank})``.

**The emitted command does not carry the biasing files unless asked.** Both files
are still written — they are free to produce and useful to read — but
``--hotwords-file`` / ``--initial-prompt-file`` only reach the command under
``--with-biasing``. Measured locally on a real session, whisper biasing did not
correct names: it recited the term list back. Whole turns came out as the biasing
list, ``compression_outlier_count`` went 6 → 85, lexicon-term hits grew by 412
while the transcript grew by 130 words, and ``word_p10`` *fell* — with a
``lexicon_miss_rate`` that looked better, because the extra "hits" were echo. The
validated correction path is the deterministic lexicon substitution in ``render``,
which changes strings that were enumerated before the transcript was read.

**Re-runs pass the work dir as a global flag**, never as an environment prefix —
see :data:`session_ingest.nextsteps.WORK_DIR_NOTE`.

Three decisions the design left open, resolved here and documented so a reader
does not have to reverse-engineer them from the sort key.

**Priority is 1 = highest**, matching how the owner writes it down. ``priority``
defaults to ``0`` in ``_lexicon.yaml``, which means *unset*, not *most
important*, so an unset term sorts below every explicitly-prioritised one
(:data:`UNSET_PRIORITY`).

**``--entities-file`` terms land at :data:`ENTITIES_PRIORITY`** — the middle of
the documented 1..10 band. Active-arc entities the owner typed for *this* session
therefore outrank low-priority lexicon entries and yield to the handful the owner
marked most important. A line that already names a lexicon term (exact,
case-folded, against canonical/display/variants) is not added twice; it keeps its
lexicon priority.

**The budget applies to each emitted file.** It is one ranked list rendered
twice, so DESIGN's "combined ~450 chars" is the size of that list; the two
renderings differ only in separator and direction, and each is capped at
``budget_chars``.

**Morphological expansion deliberately does not reach this stage.** A term marked
``morph: true`` biases with its lemma and nothing else: whisper's hotwords and
initial prompt are a ~450-character budget, and spending it on six declensions of
one name would evict five other names for no gain — the acoustic model is being
told *what sounds to expect*, not what endings to write. So ``count_term`` is
called here without an expansion table, and the generated forms exist only where
they correct text that already exists (``render``) or measure it (``qa``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from craig_stt_dataset import Dataset

from . import llm
from .adopt import local_archive, resolve_active_dataset
from .config import SessionConfig
from .errors import DatasetAdoptError, SessionIngestError
from .nextsteps import (
    ARCHIVE_PLACEHOLDER,
    BIASING_NOTE,
    CLI,
    CRAIG,
    TRANSCRIBE_JSON_NOTE,
    WORK_DIR_NOTE,
    step,
)
from .paths import Roots, SessionTree
from .provenance import CompositeKey, Provenance, sha256_file
from .qa import count_term
from .vaultfiles import Lexicon, LexiconTerm, load_lexicon
from .writer import write_text

PROMPT_VERSION = "plan/1"

#: ``priority: 0`` in the lexicon means "not ranked", so it sorts last.
UNSET_PRIORITY = 11
#: Where an ``--entities-file`` line lands in the 1..10 band. See the module docstring.
ENTITIES_PRIORITY = 5

PROMPT_PREFIX = "В записи упоминаются: "
PROMPT_SUFFIX = "."


@dataclass(frozen=True, slots=True)
class Candidate:
    """One term competing for space in the two biasing files."""

    key: str
    display: str
    term_id: str | None
    priority: int
    count: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.display,
            "term_id": self.term_id,
            "priority": self.priority,
            "count": self.count,
            "source": self.source,
        }


def effective_priority(term: LexiconTerm) -> int:
    return term.priority if term.priority > 0 else UNSET_PRIORITY


def read_entities_file(path: Path) -> list[str]:
    """One term per line. ``#`` comments and blank lines are ignored, order kept."""
    if not path.is_file():
        raise SessionIngestError(
            f"--entities-file {path} does not exist",
            code="entities_file_missing",
            detail={"path": str(path)},
        )
    seen: set[str] = set()
    terms: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(line)
    return terms


def observed_counts(dataset: Dataset, lexicon: Lexicon) -> dict[str, int]:
    """Exact enumerated-string counts for every active term, over one dataset.

    Canonical and variant hits are summed: for ranking, what matters is that the
    term came up at all, not whether whisper spelled it correctly last time.
    """
    if not lexicon.present:
        return {}
    haystack = "\n".join(segment.text for segment in dataset.iter_segments() if segment.text)
    folded = haystack.casefold()
    return {term.id: count_term(folded, term).total_hits for term in lexicon.active_terms()}


def rank_candidates(
    lexicon: Lexicon, *, counts: Mapping[str, int], extra_terms: Sequence[str]
) -> list[Candidate]:
    """Priority first, observed count second, alphabetical last — fully deterministic."""
    candidates: list[Candidate] = []
    known: set[str] = set()
    for term in lexicon.active_terms():
        display = term.display_ru or term.canonical
        candidates.append(
            Candidate(
                key=display.casefold(),
                display=display,
                term_id=term.id,
                priority=effective_priority(term),
                count=counts.get(term.id, 0),
                source="lexicon",
            )
        )
        for form in (*term.canonical_forms(), *term.variant_forms()):
            known.add(form.casefold())
    for line in extra_terms:
        if line.casefold() in known:
            continue
        known.add(line.casefold())
        candidates.append(
            Candidate(
                key=line.casefold(),
                display=line,
                term_id=None,
                priority=ENTITIES_PRIORITY,
                count=0,
                source="entities-file",
            )
        )
    candidates.sort(key=lambda c: (c.priority, -c.count, c.key))
    return candidates


# ------------------------------------------------------------------- budgets


def fit_hotwords(terms: Sequence[str], budget: int) -> list[str]:
    """Best terms kept; the file itself lists them **last**.

    craig-stt truncates a too-long hotwords string from the front, so the terms
    that matter most have to be at the end.
    """
    kept: list[str] = []
    length = 0
    for term in terms:
        extra = len(term) + (1 if kept else 0)
        if length + extra > budget:
            break
        kept.append(term)
        length += extra
    return kept


def fit_prompt(terms: Sequence[str], budget: int) -> list[str]:
    """Best terms kept and listed **first**; whisper truncates a prompt from the back."""
    kept: list[str] = []
    length = len(PROMPT_PREFIX) + len(PROMPT_SUFFIX)
    for term in terms:
        extra = len(term) + (2 if kept else 0)
        if length + extra > budget:
            break
        kept.append(term)
        length += extra
    return kept


def hotwords_text(kept: Sequence[str]) -> str:
    return " ".join(reversed(list(kept)))


def prompt_text(kept: Sequence[str]) -> str:
    if not kept:
        return ""
    return PROMPT_PREFIX + ", ".join(kept) + PROMPT_SUFFIX


# ------------------------------------------------------------------ ranking


_RANK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "terms": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Candidate terms, most useful to bias for first.",
        }
    },
    "required": ["terms"],
}

_RANK_SYSTEM = (
    "You order transcription-biasing terms for a Russian tabletop RPG session. "
    "Return the given candidates re-ordered, most useful first. Never invent a term, "
    "never translate one, never change spelling — copy each string exactly as given."
)


def _rank_with_llm(
    config: SessionConfig, candidates: Sequence[Candidate]
) -> tuple[list[Candidate], llm.Usage]:
    """Re-order candidates with one structured call, then verify against the input.

    The LLM may only *order*. Anything it returns that was not offered is dropped,
    and anything it forgets is appended in deterministic order — an LLM silently
    losing the owner's most important name is exactly the failure this guardrail
    exists for.

    Reads the ordering out of :attr:`llm.StructuredResult.data`. Treating the
    result itself as the payload is a real regression risk here: ``StructuredResult``
    is a ``NamedTuple``, so a sloppy ``isinstance(result, (list, tuple))`` check
    happily iterates ``(data, usage, attempts)``, finds no strings, and falls back
    to the deterministic order — a metered call paid for and silently discarded.
    """
    by_key = {candidate.key: candidate for candidate in candidates}
    result = llm.structured_call(
        config=config,
        prompt_version=PROMPT_VERSION,
        system_prompt=_RANK_SYSTEM,
        user_content="\n".join(candidate.display for candidate in candidates),
        schema=_RANK_SCHEMA,
    )
    raw = result.data.get("terms")
    if not isinstance(raw, (list, tuple)):
        return list(candidates), result.usage
    ordered: list[Candidate] = []
    taken: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        candidate = by_key.get(item.strip().casefold())
        if candidate is None or candidate.key in taken:
            continue
        taken.add(candidate.key)
        ordered.append(candidate)
    if not ordered:
        return list(candidates), result.usage
    ordered.extend(candidate for candidate in candidates if candidate.key not in taken)
    return ordered, result.usage


# --------------------------------------------------------------- next steps


def _archive_hint(tree: SessionTree, run: int) -> str:
    """The local ``.flac.zip`` a re-run transcribes from — never a re-download."""
    for previous in range(run - 1, 0, -1):
        try:
            recording, dataset = resolve_active_dataset(tree, previous)
        except SessionIngestError:
            continue
        archive = local_archive(Path(recording.dataset_path), dataset)
        if archive is not None:
            return str(archive)
    return ARCHIVE_PLACEHOLDER


def _posture_line(with_biasing: bool) -> str:
    """One line for the human report saying which posture the command was emitted in."""
    if with_biasing:
        return (
            "biasing:        --with-biasing — the command carries hotwords + initial_prompt. "
            "EXPERIMENTAL; check `qa --compare` for the echo signals before trusting the run."
        )
    return (
        "biasing:        files written for reference only; the emitted command omits them "
        "(--with-biasing to include). Corrections come from `render`'s lexicon substitution."
    )


def transcribe_steps(
    *,
    tree: SessionTree,
    roots: Roots,
    session_id: str,
    run: int,
    hotwords: Path,
    initial_prompt: Path,
    with_biasing: bool = False,
) -> list[dict[str, Any]]:
    """The transcribe step for this run, plus the ``adopt`` that follows it.

    ``with_biasing`` is the only thing that puts ``--hotwords-file`` /
    ``--initial-prompt-file`` into the command; the files themselves are written
    either way. See the module docstring for the measurement behind that default.
    """
    biasing = (
        f" --hotwords-file {hotwords} --initial-prompt-file {initial_prompt}"
        if with_biasing
        else ""
    )
    biasing_note = "" if with_biasing else " " + BIASING_NOTE
    if run <= 1:
        transcribe = step(
            "craig_transcribe",
            (
                "Transcribe the recording on the GPU (~10 min). Replace <share-url> with the "
                "Craig share link including its ?key= — the key is per-recording and is "
                "deliberately not stored anywhere. session-ingest never spawns this. "
                + TRANSCRIBE_JSON_NOTE
                + biasing_note
            ),
            command=f"{CRAIG} transcribe '<share-url>'{biasing} --json",
            run=run,
        )
    else:
        scratch = roots.scratch / f"{session_id}-r{run}"
        transcribe = step(
            "craig_transcribe_rerun",
            (
                "Re-transcribe from the LOCAL archive into a scratch work dir — no re-download, "
                "and the original dataset stays immutable. "
                + WORK_DIR_NOTE
                + " "
                + TRANSCRIBE_JSON_NOTE
                + biasing_note
            ),
            command=(
                f"{CRAIG} --work-dir {scratch} transcribe "
                f"{_archive_hint(tree, run)}{biasing} --json"
            ),
            run=run,
        )
    run_flag = f" --run {run}" if run != 1 else ""
    return [
        transcribe,
        step(
            "adopt",
            "Adopt the produced dataset into this session (SDK-verified).",
            command=f"{CLI} adopt <dataset|recording-id> --session {session_id}{run_flag}",
        ),
    ]


# ----------------------------------------------------------------- the verb


def run(
    *,
    roots: Roots,
    config: SessionConfig,
    session_id: str,
    run: int = 1,
    entities_file: Path | None = None,
    rank: bool = False,
    budget_chars: int | None = None,
    with_biasing: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Returns the ``--json`` payload: written input paths, term count, next_steps.

    ``with_biasing`` changes only the emitted command, never what is written, so it
    is deliberately absent from the composite key: asking for the flags back must
    not force a rewrite of two files that would come out byte-identical.
    """
    if run < 1:
        raise SessionIngestError("--run must be >= 1", code="invalid_run")

    tree = roots.session(session_id)
    budget = budget_chars if budget_chars is not None else config.glossary_budget_chars
    if budget <= 0:
        raise SessionIngestError("--budget-chars must be > 0", code="invalid_budget")

    if rank:
        skipped = llm.metered_skip(config, "plan")
        if skipped is not None:
            skipped["session"] = session_id
            skipped["next_steps"] = [
                step(
                    "plan_deterministic",
                    "Re-run without --rank: the deterministic active/priority ranking is free.",
                    command=f"{CLI} plan --session {session_id}"
                    + (f" --run {run}" if run != 1 else "")
                    + (" --with-biasing" if with_biasing else ""),
                )
            ]
            skipped["lines"] = [
                "skipped: --rank needs OPENAI_API_KEY; the deterministic ranking does not."
            ]
            return skipped

    lexicon = load_lexicon(roots.lexicon_file)
    extra_terms = read_entities_file(entities_file) if entities_file is not None else []
    entities_digest = sha256_file(entities_file) if entities_file is not None else None

    warnings: list[str] = []
    counts: dict[str, int] = {}
    counted_from: str | None = None
    if run > 1:
        try:
            recording, dataset = resolve_active_dataset(tree, None)
        except DatasetAdoptError as exc:
            warnings.append(
                f"run {run} was planned without observed counts ({exc.code}); terms are ranked "
                f"by priority and name only"
            )
        else:
            counts = observed_counts(dataset, lexicon)
            counted_from = recording.recording_id

    key = CompositeKey(
        lexicon_digest=lexicon.digest,
        prompt_version=PROMPT_VERSION if rank else None,
        model=config.openai_model if rank else None,
        knobs={
            "run": run,
            "budget_chars": budget,
            "rank": rank,
            "entities_digest": entities_digest,
            "counts_from": counted_from,
        },
    )
    provenance = Provenance.load(tree.provenance_json)
    if provenance.should_skip("plan", key, force=force, root=tree.root):
        return {
            "status": "skipped",
            "session": session_id,
            "run": run,
            "hotwords_file": str(tree.hotwords_file),
            "initial_prompt_file": str(tree.initial_prompt_file),
            "budget_chars": budget,
            "with_biasing": with_biasing,
            "warnings": ["biasing inputs already emitted for this lexicon key; --force to rewrite"],
            "lines": [
                f"skipped: {session_id} run {run} biasing inputs are current",
                f"  {_posture_line(with_biasing)}",
            ],
            "next_steps": transcribe_steps(
                tree=tree,
                roots=roots,
                session_id=session_id,
                run=run,
                hotwords=tree.hotwords_file,
                initial_prompt=tree.initial_prompt_file,
                with_biasing=with_biasing,
            ),
        }

    candidates = rank_candidates(lexicon, counts=counts, extra_terms=extra_terms)
    ranking = "priority+count" if counts else "priority+name"
    usage = llm.Usage()
    if rank and candidates:
        candidates, usage = _rank_with_llm(config, candidates)
        ranking = "llm"

    displays = [candidate.display for candidate in candidates]
    hotwords_kept = fit_hotwords(displays, budget)
    prompt_kept = fit_prompt(displays, budget)
    if candidates and not hotwords_kept:
        warnings.append(
            f"--budget-chars {budget} is too small for even the highest-ranked term; "
            f"hotwords.txt is empty"
        )
    dropped = len(displays) - max(len(hotwords_kept), len(prompt_kept))
    if dropped > 0:
        warnings.append(f"{dropped} term(s) did not fit the {budget}-char budget")
    if not lexicon.present:
        warnings.append(f"{roots.lexicon_file} is absent — there is nothing to bias with")

    write_text(tree.hotwords_file, hotwords_text(hotwords_kept) + "\n")
    write_text(tree.initial_prompt_file, prompt_text(prompt_kept) + "\n")

    provenance.mark_done(
        "plan",
        key,
        outputs=[tree.hotwords_file, tree.initial_prompt_file],
        extra={
            "run": run,
            "ranking": ranking,
            "candidates": len(candidates),
            "hotwords_terms": len(hotwords_kept),
            "prompt_terms": len(prompt_kept),
            "budget_chars": budget,
        },
    )

    next_steps = transcribe_steps(
        tree=tree,
        roots=roots,
        session_id=session_id,
        run=run,
        hotwords=tree.hotwords_file,
        initial_prompt=tree.initial_prompt_file,
        with_biasing=with_biasing,
    )
    lines = [
        f"ok: {session_id} run {run} — {len(candidates)} candidate term(s), ranking={ranking}",
        f"  hotwords:       {tree.hotwords_file} ({len(hotwords_kept)} terms, best last)",
        f"  initial_prompt: {tree.initial_prompt_file} ({len(prompt_kept)} terms, best first)",
        f"  {_posture_line(with_biasing)}",
    ]
    lines.extend(f"  warning: {message}" for message in warnings)

    return {
        "status": "ok",
        "session": session_id,
        "run": run,
        "ranking": ranking,
        "with_biasing": with_biasing,
        "biasing_note": BIASING_NOTE,
        "counts_from": counted_from,
        "usage": usage.to_dict() if rank else None,
        "budget_chars": budget,
        "hotwords_file": str(tree.hotwords_file),
        "initial_prompt_file": str(tree.initial_prompt_file),
        "hotwords": hotwords_text(hotwords_kept),
        "initial_prompt": prompt_text(prompt_kept),
        "hotwords_terms": list(reversed(hotwords_kept)),
        "prompt_terms": list(prompt_kept),
        "candidates": [candidate.to_dict() for candidate in candidates],
        "entities_file": str(entities_file) if entities_file is not None else None,
        "warnings": warnings,
        "lines": lines,
        "next_steps": next_steps,
    }
