"""``glossary`` — grow ``_lexicon.yaml`` from what this session actually heard. Metered.

DESIGN §4 and §5, RESEARCH §5:

* The LLM's job is **attachment**: given this session's spellings and the existing
  canonical terms, decide which observed variant belongs to which term. The
  tool's job is verification and merging — a similarity score never decides
  identity here.
* The guardrail is deterministic and total: a proposed variant is kept only if it
  appears **verbatim** (case-folded) in the session text the model was shown.
  Everything else is dropped and reported. That is the RECOVER-style pattern the
  research prescribes — the model proposes, the tool verifies against enumerated
  input.
* The merge into ``vault/transcripts/_lexicon.yaml`` is **append-only**. It is
  one of only two non-regenerable files in the whole system, so nothing here
  rewrites, reorders or drops an existing entry: new variants are inserted into
  the term's own block and new terms are appended after the last one, leaving
  every other byte — including the owner's comments — untouched.
* Re-emitting the biasing files is left to ``plan``: this verb emits the exact
  command in ``next_steps`` rather than reaching into another stage's module.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import yaml

from .adopt import load_session_link, resolve_active_dataset
from .config import SessionConfig
from .errors import SessionIngestError
from .llm import Usage, client_for, load_prompt, map_reduce, metered_skip, nullable, object_schema
from .nextsteps import next_steps_for
from .paths import Roots, SessionTree
from .provenance import CompositeKey, Provenance, sha256_text, utc_now
from .segment import TurnWindow, build_windows, collect_turns
from .vaultfiles import Lexicon, LexiconTerm, load_lexicon, load_speakers
from .writer import write_text

PROMPT_VERSION = "glossary/1"

#: New terms land inactive-adjacent: mid priority, active, provenance recorded.
NEW_TERM_PRIORITY = 3

KINDS = ("npc", "location", "faction", "item", "creature", "quest", "other")

_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_ITEM_RE = re.compile(r"^(\s*)-\s")
_TERMS_KEY_RE = re.compile(r"^terms:\s*$")

GLOSSARY_SCHEMA = object_schema(
    {
        "terms": {
            "type": "array",
            "items": object_schema(
                {
                    "id": {"type": "string"},
                    "existing": {"type": "boolean"},
                    "canonical": {"type": "string"},
                    "display_ru": nullable({"type": "string"}),
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "variants_observed": {"type": "array", "items": {"type": "string"}},
                }
            ),
        }
    }
)


# ----------------------------------------------------------------- proposals


@dataclass(slots=True)
class Proposal:
    """One canonical term with the variants the model attached to it."""

    term_id: str
    canonical: str
    display_ru: str | None
    kind: str | None
    variants: list[str] = field(default_factory=list)
    existing: bool = False

    def add(self, variant: str) -> None:
        if variant not in self.variants:
            self.variants.append(variant)


def _slug_available(candidate: Any, taken: Iterable[str]) -> str | None:
    if not isinstance(candidate, str):
        return None
    slug = candidate.strip().lower()
    if not _ID_RE.match(slug) or slug in set(taken):
        return None
    return slug


def collect_proposals(
    answers: Iterable[tuple[int, dict[str, Any]]], lexicon: Lexicon
) -> tuple[list[Proposal], list[dict[str, Any]]]:
    """Fold per-window answers into one proposal per term id. First window wins."""
    by_id: dict[str, Proposal] = {}
    rejected: list[dict[str, Any]] = []
    existing_by_id = lexicon.by_id()
    existing_by_canonical = {term.canonical.strip().casefold(): term for term in lexicon.terms}

    for window_index, payload in sorted(answers, key=lambda pair: pair[0]):
        rows = payload.get("terms")
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            canonical = raw.get("canonical")
            if not isinstance(canonical, str) or not canonical.strip():
                rejected.append({"window": window_index, "reason": "canonical is empty"})
                continue
            canonical = canonical.strip()

            match = existing_by_canonical.get(canonical.casefold())
            raw_id = raw.get("id")
            if (
                match is None
                and isinstance(raw_id, str)
                and raw_id.strip().lower() in existing_by_id
            ):
                match = existing_by_id[raw_id.strip().lower()]

            if match is not None:
                proposal = by_id.setdefault(
                    match.id,
                    Proposal(
                        term_id=match.id,
                        canonical=match.canonical,
                        display_ru=match.display_ru,
                        kind=match.kind,
                        existing=True,
                    ),
                )
            else:
                slug = _slug_available(raw_id, set(existing_by_id) | set(by_id))
                if slug is None:
                    slug = _fallback_slug(canonical, set(existing_by_id) | set(by_id))
                proposal = by_id.setdefault(
                    slug,
                    Proposal(
                        term_id=slug,
                        canonical=canonical,
                        display_ru=(
                            raw.get("display_ru")
                            if isinstance(raw.get("display_ru"), str)
                            else None
                        ),
                        kind=raw.get("kind") if raw.get("kind") in KINDS else None,
                        existing=False,
                    ),
                )

            for variant in raw.get("variants_observed") or []:
                if isinstance(variant, str) and variant.strip():
                    proposal.add(variant.strip())

    return list(by_id.values()), rejected


def _fallback_slug(canonical: str, taken: set[str]) -> str:
    """A last-resort identifier when the model's ``id`` is unusable.

    Deliberately opaque rather than transliterated: guessing a Latin spelling for
    a Cyrillic name is exactly the kind of inference this project keeps out of
    tools. The owner can rename it; the id only has to be unique and stable.
    """
    base = "glossary-" + sha256_text(canonical).split(":", 1)[1][:8]
    slug = base
    suffix = 2
    while slug in taken:
        slug = f"{base}-{suffix}"
        suffix += 1
    return slug


def verify_variants(
    proposals: Sequence[Proposal], *, haystack_casefolded: str, lexicon: Lexicon
) -> tuple[list[Proposal], list[dict[str, Any]]]:
    """Drop every variant that is not literally in the session text.

    Also drops variants that already are a canonical form, or that the term
    already lists: the lexicon must not learn that a name is a misrecognition of
    itself, and re-running must add nothing.
    """
    kept: list[Proposal] = []
    dropped: list[dict[str, Any]] = []
    known = lexicon.by_id()

    for proposal in proposals:
        term = known.get(proposal.term_id)
        already = {value.casefold() for value in (term.variants if term else ())}
        canonical_forms = {
            value.casefold()
            for value in (
                term.canonical_forms()
                if term
                else (proposal.canonical, proposal.display_ru or proposal.canonical)
            )
        }
        surviving: list[str] = []
        for variant in proposal.variants:
            folded = variant.casefold()
            if folded not in haystack_casefolded:
                dropped.append(
                    {
                        "term_id": proposal.term_id,
                        "variant": variant,
                        "reason": "not observed verbatim in the session text",
                    }
                )
                continue
            if folded in canonical_forms:
                dropped.append(
                    {
                        "term_id": proposal.term_id,
                        "variant": variant,
                        "reason": "identical to a canonical form",
                    }
                )
                continue
            if folded in already:
                dropped.append(
                    {
                        "term_id": proposal.term_id,
                        "variant": variant,
                        "reason": "already recorded for this term",
                    }
                )
                continue
            if folded in {value.casefold() for value in surviving}:
                continue
            surviving.append(variant)
        proposal.variants = surviving
        if surviving or not proposal.existing:
            kept.append(proposal)
    return kept, dropped


# -------------------------------------------------------- append-only merge


def _quote(value: str) -> str:
    """One YAML scalar, quoted exactly as much as it needs.

    Dumped as a one-element flow list and unwrapped: dumping a bare scalar emits
    a document-end marker, and hand-quoting would eventually meet a name with a
    colon in it.
    """
    return yaml.safe_dump(
        [value], allow_unicode=True, default_flow_style=True, width=10**6
    ).strip()[1:-1]


def render_new_term(proposal: Proposal, *, session_id: str, indent: str) -> str:
    """A YAML block for one brand-new term, in the documented field order."""
    variants = ", ".join(_quote(variant) for variant in proposal.variants)
    lines = [
        f"{indent}- id: {proposal.term_id}",
        f"{indent}  canonical: {_quote(proposal.canonical)}",
    ]
    if proposal.display_ru:
        lines.append(f"{indent}  display_ru: {_quote(proposal.display_ru)}")
    lines.append(f"{indent}  variants: [{variants}]")
    if proposal.kind:
        lines.append(f"{indent}  kind: {proposal.kind}")
    lines.append(f"{indent}  active: true")
    lines.append(f"{indent}  priority: {NEW_TERM_PRIORITY}")
    lines.append(f"{indent}  source: glossary/{session_id}")
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class TermBlock:
    """One ``- id: …`` item in the ``terms:`` list, located in the raw text."""

    term_id: str
    start: int
    end: int
    indent: str


def locate_terms(lines: Sequence[str]) -> tuple[list[TermBlock], int, int, str] | None:
    """Find the ``terms:`` list and every item block inside it.

    Returns ``(blocks, region_start, region_end, indent)`` or ``None`` when the
    file is not shaped the way this merger can edit safely. Refusing beats
    guessing: this is hand-maintained user data.
    """
    key_line = next((index for index, line in enumerate(lines) if _TERMS_KEY_RE.match(line)), None)
    if key_line is None:
        return None
    region_start = key_line + 1
    region_end = region_start
    for index in range(region_start, len(lines)):
        line = lines[index]
        if not line.strip() or line[:1].isspace():
            region_end = index + 1
            continue
        break
    else:
        region_end = len(lines)

    first = next(
        (index for index in range(region_start, region_end) if _ITEM_RE.match(lines[index])),
        None,
    )
    if first is None:
        return [], region_start, region_end, "  "
    indent_match = _ITEM_RE.match(lines[first])
    indent = indent_match.group(1) if indent_match else "  "

    # Only items at the list's own indentation start a term. A nested `- variant`
    # line is part of the block it sits in, not a new one.
    starts = [
        index
        for index in range(region_start, region_end)
        if (match := _ITEM_RE.match(lines[index])) is not None and match.group(1) == indent
    ]

    blocks: list[TermBlock] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else region_end
        while end > start and not lines[end - 1].strip():
            end -= 1
        chunk = "\n".join(
            line[len(indent) :] if line.startswith(indent) else line for line in lines[start:end]
        )
        try:
            parsed = yaml.safe_load(chunk)
        except yaml.YAMLError:
            return None
        if not isinstance(parsed, list) or len(parsed) != 1 or not isinstance(parsed[0], dict):
            return None
        term_id = parsed[0].get("id")
        if not isinstance(term_id, str):
            return None
        blocks.append(TermBlock(term_id=term_id, start=start, end=end, indent=indent))
    return blocks, region_start, region_end, indent


def insert_variants(lines: list[str], block: TermBlock, variants: Sequence[str]) -> bool:
    """Add ``variants`` to one term's block in place. Returns False if it is unsafe."""
    if not variants:
        return True
    child_indent = block.indent + "  "
    for index in range(block.start, block.end):
        line = lines[index]
        stripped = line.strip()
        if not stripped.startswith("variants:"):
            continue
        key_indent = line[: len(line) - len(line.lstrip())]
        value = stripped[len("variants:") :].strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            joined = ", ".join(_quote(variant) for variant in variants)
            lines[index] = f"{key_indent}variants: [{inner + ', ' if inner else ''}{joined}]"
            return True
        if value:
            return False  # a scalar or an anchor: not something to edit blind

        # Block style: append items after the last one, matching its indentation.
        last = index
        item_indent = key_indent + "  "
        for probe in range(index + 1, block.end):
            match = _ITEM_RE.match(lines[probe])
            if match is not None and len(match.group(1)) > len(key_indent):
                last = probe
                item_indent = match.group(1)
            elif lines[probe].strip():
                break
        if last == index:
            return False
        for offset, variant in enumerate(variants, start=1):
            lines.insert(last + offset, f"{item_indent}- {_quote(variant)}")
        return True

    # No `variants:` key at all — add one right after the block's first line.
    lines.insert(
        block.start + 1,
        f"{child_indent}variants: [{', '.join(_quote(variant) for variant in variants)}]",
    )
    return True


@dataclass(slots=True)
class MergeResult:
    text: str | None
    terms_added: list[str] = field(default_factory=list)
    terms_extended: list[str] = field(default_factory=list)
    variants_added: int = 0
    deferred: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.text is not None


def merge_lexicon(original: str, proposals: Sequence[Proposal], *, session_id: str) -> MergeResult:
    """Append-only merge. Returns ``text=None`` when nothing changed."""
    result = MergeResult(text=None)
    if not proposals:
        return result

    if not original.strip():
        blocks: list[TermBlock] = []
        lines = ["terms:"]
        region_end = 1
        indent = "  "
    else:
        located = locate_terms(original.splitlines())
        if located is None:
            result.deferred = [
                {
                    "reason": (
                        "_lexicon.yaml is not shaped as a top-level `terms:` list this merger "
                        "can edit without rewriting the owner's formatting; nothing was written"
                    )
                }
            ]
            return result
        blocks, _region_start, region_end, indent = located
        lines = original.splitlines()

    by_id = {block.term_id: block for block in blocks}
    additions: list[str] = []

    # Existing terms first: inserting lines shifts later blocks, so walk backwards.
    extendable = [
        (by_id[proposal.term_id], proposal)
        for proposal in proposals
        if proposal.term_id in by_id and proposal.variants
    ]
    for block, proposal in sorted(extendable, key=lambda pair: pair[0].start, reverse=True):
        before = len(lines)
        if insert_variants(lines, block, proposal.variants):
            result.terms_extended.append(proposal.term_id)
            result.variants_added += len(proposal.variants)
            region_end += len(lines) - before
        else:
            result.deferred.append(
                {
                    "term_id": proposal.term_id,
                    "reason": "the term's `variants` field is not a form this merger edits",
                }
            )

    for proposal in proposals:
        if proposal.term_id in by_id:
            continue
        additions.append(render_new_term(proposal, session_id=session_id, indent=indent))
        result.terms_added.append(proposal.term_id)
        result.variants_added += len(proposal.variants)

    if not result.terms_added and not result.terms_extended:
        return result

    if additions:
        lines[region_end:region_end] = "\n".join(additions).splitlines()

    result.text = "\n".join(lines).rstrip("\n") + "\n"
    return result


# --------------------------------------------------------------------- verb


def composite_key(
    *,
    dataset_digest: str | None,
    lexicon_digest: str | None,
    config: SessionConfig,
    prompt_digest: str,
    budget_chars: int,
) -> CompositeKey:
    return CompositeKey(
        dataset_digest=dataset_digest,
        lexicon_digest=lexicon_digest,
        prompt_version=PROMPT_VERSION,
        model=config.openai_model,
        knobs={
            "window_minutes": config.window_minutes,
            "merge_gap_s": config.merge_gap_s,
            "budget_chars": budget_chars,
            "prompt_digest": prompt_digest,
        },
    )


def lexicon_inventory(lexicon: Lexicon, *, limit: int = 200) -> str:
    """The existing dictionary as the glossary prompt sees it: id, canonical, variants."""
    terms: Sequence[LexiconTerm] = lexicon.terms[:limit]
    if not terms:
        return "(словарь пуст — все термины будут новыми)"
    lines = []
    for term in terms:
        variants = ", ".join(term.variants) if term.variants else "—"
        lines.append(f"- {term.id}: {term.canonical} | варианты: {variants}")
    return "\n".join(lines)


def run(
    *,
    roots: Roots,
    config: SessionConfig,
    session_id: str,
    run: int | None = None,
    budget_chars: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Attach observed variants to canonical terms. Returns the ``--json`` payload."""
    skipped = metered_skip(config, "glossary")
    if skipped is not None:
        return skipped

    tree: SessionTree = roots.session(session_id)
    recording, dataset = resolve_active_dataset(tree, run)
    link = load_session_link(tree)
    effective_run = run if run is not None else link.active_run
    budget = budget_chars if budget_chars is not None else config.glossary_budget_chars

    lexicon = load_lexicon(roots.lexicon_file)
    speakers = load_speakers(roots.speakers_file)
    prompt = load_prompt(PROMPT_VERSION)

    key = composite_key(
        dataset_digest=recording.dataset_digest,
        lexicon_digest=lexicon.digest,
        config=config,
        prompt_digest=prompt.digest,
        budget_chars=budget,
    )
    provenance = Provenance.load(tree.provenance_json)

    if provenance.should_skip("glossary", key, force=force, root=tree.root):
        return {
            "status": "skipped",
            "session": session_id,
            "run": effective_run,
            "lexicon": str(roots.lexicon_file),
            "usage": Usage().to_dict(),
            "warnings": ["glossary already run for this dataset/lexicon key; --force to re-run"],
            "next_steps": next_steps_for(
                "glossary", session_id=session_id, api_key_present=True, run=effective_run
            ),
        }

    turns = collect_turns(
        dataset, merge_gap_s=config.merge_gap_s, drop_bleed=False, speakers=speakers
    )
    windows = build_windows(turns, window_minutes=config.window_minutes, overlap_pct=0)
    inventory = lexicon_inventory(lexicon)

    def system_for(window: TurnWindow) -> str:
        return prompt.render(
            lexicon_terms=inventory,
            window_index=str(window.index + 1),
            window_count=str(len(windows)),
            window_span=window.span,
        )

    result = map_reduce(
        config=config,
        prompt_version=PROMPT_VERSION,
        windows=windows,
        system_prompt=system_for,
        schema=GLOSSARY_SCHEMA,
        render=lambda window: window.render(),
        client=client_for(config),
        schema_name="glossary_candidates",
    )

    proposals, malformed = collect_proposals(result.succeeded(), lexicon)
    haystack = "\n".join(turn.text for turn in turns).casefold()
    proposals, rejected = verify_variants(proposals, haystack_casefolded=haystack, lexicon=lexicon)
    proposals = [proposal for proposal in proposals if proposal.variants or not proposal.existing]

    original = (
        roots.lexicon_file.read_text(encoding="utf-8") if roots.lexicon_file.is_file() else ""
    )
    merge = merge_lexicon(original, proposals, session_id=session_id)
    if merge.text is not None:
        write_text(roots.lexicon_file, merge.text)
        # The merged file must still parse, or the next run reads a broken lexicon.
        try:
            load_lexicon(roots.lexicon_file)
        except SessionIngestError:
            write_text(roots.lexicon_file, original)
            raise

    warnings: list[str] = []
    if rejected:
        warnings.append(
            f"{len(rejected)} proposed variant(s) were dropped by the guardrail — a variant is "
            f"kept only when it appears verbatim in this session's text"
        )
    if merge.deferred:
        warnings.append(
            f"{len(merge.deferred)} term(s) could not be merged without reformatting "
            f"{roots.lexicon_file.name}; they were left for the owner"
        )
    if result.failed_windows():
        warnings.append(f"{len(result.failed_windows())} window(s) failed after retries")

    provenance.mark_done(
        "glossary",
        key,
        outputs=[roots.lexicon_file] if merge.changed else [],
        extra={
            "run": effective_run,
            "terms_added": merge.terms_added,
            "terms_extended": merge.terms_extended,
            "variants_added": merge.variants_added,
            "usage": result.usage.to_dict(),
            "generated_at": utc_now(),
        },
    )

    steps = next_steps_for(
        "glossary", session_id=session_id, api_key_present=True, run=effective_run
    )
    return {
        "status": "ok",
        "session": session_id,
        "run": effective_run,
        "lexicon": str(roots.lexicon_file),
        "lexicon_changed": merge.changed,
        "model": config.openai_model,
        "prompt_version": PROMPT_VERSION,
        "windows": len(windows),
        "terms_added": merge.terms_added,
        "terms_extended": merge.terms_extended,
        "variants_added": merge.variants_added,
        "variants_rejected": rejected,
        "malformed_proposals": malformed,
        "deferred": merge.deferred,
        "biasing_files": "deferred_to_plan",
        "failed_windows": result.failed_windows(),
        "usage": result.usage.to_dict(),
        "warnings": warnings,
        "next_steps": steps,
    }
