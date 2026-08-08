"""``morph: true`` — mechanical Russian case-form expansion for lexicon terms.

A term the owner marks ``morph: true`` in ``_lexicon.yaml`` gets its six
singular case forms generated with pymorphy3, so «Марвике» is corrected to
«Морвике» without the owner hand-writing an entry per case.

**Why this does not violate "no heuristic semantics in tools".** The project rule
forbids a tool from *inferring meaning* — classifying, tagging, guessing which
canonical entity a string refers to. Nothing here does that. The owner has
already made every semantic decision: which strings are the same entity
(``variants``), what the correct spelling is (``display_ru``), and which terms
decline at all (``morph: true``). What this module adds is a lookup in a
published grammatical dictionary — OpenCorpora's paradigm tables, shipped as
package data — which is a deterministic function of (surface string, dictionary
version), the same class of operation as ``str.casefold()``. There is no
similarity, distance, ranking or confidence anywhere in this file. Where the
grammar is genuinely ambiguous the module **skips and records why** rather than
choosing: a multi-word name whose parts do not agree, a word that does not parse
as a declinable noun or adjective, a form the analyzer cannot produce. And the
whole table is auditable — ``session-ingest lexicon --expand`` prints every
generated pair, and ``render`` writes the table into provenance.

**Pairing, not parsing.** Both sides of every substitution are generated: the
variant is inflected to case *c* and the display form is inflected to the same
case *c*, and the two are paired by that tag. The case of each surface form is
therefore known by construction, and no text is ever parsed at match time — the
render substituter still only replaces enumerated strings.

**Hyphenated names.** pymorphy3's own ``HyphenatedWordsAnalyzer`` inflects
«Кор-Вазгар» sensibly (last component declines: «Кор-Вазгара», «Кор-Вазгару», …),
so this module hands hyphenated surfaces to the library unchanged rather than
splitting them itself. ``tests/test_morphs.py`` pins that observed behaviour, so a library
or dictionary upgrade that starts mangling it fails loudly instead of quietly
writing nonsense into transcripts.

**Casing** is transferred mechanically from the source string onto the generated
one, per hyphen component, because pymorphy3 returns everything lowercased and
«кор-вазгара» is not what belongs in a rendered line.

Determinism: same lexicon plus same ``pymorphy3`` and ``pymorphy3-dicts-ru``
versions produce the same table, byte for byte. Both versions are inputs to
:attr:`ExpansionTable.digest`, which is part of the ``render`` and ``qa``
composite keys — upgrading the dictionary invalidates those caches rather than
leaving a transcript rendered with grammar the tool no longer agrees with.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import pymorphy3
import pymorphy3_dicts_ru

from .errors import LexiconExpansionError
from .provenance import sha256_text
from .vaultfiles import Lexicon, LexiconTerm

EXPANSION_SCHEMA = "ttrpg.session-morph-expansion/1"

#: The six singular Russian cases, in the canonical grammatical order. Plural is
#: deliberately absent: a proper noun's plural is almost never what was said at
#: the table, and every generated form is a string the substituter will rewrite.
CASES: tuple[str, ...] = ("nomn", "gent", "datv", "accs", "ablt", "loct")

#: Parts of speech that decline by case and can therefore be expanded.
DECLINABLE_POS = frozenset({"NOUN", "ADJF"})

#: Generated forms shorter than this are dropped. A three-letter surface is a
#: collision waiting to happen — it will match inside ordinary Russian words far
#: more often than it names the entity.
MIN_FORM_LENGTH = 4


# ------------------------------------------------------------------ versions


@dataclass(frozen=True, slots=True)
class MorphVersions:
    """The two package versions that fully determine the generated grammar."""

    pymorphy3: str
    dicts_ru: str

    def to_dict(self) -> dict[str, str]:
        return {"pymorphy3": self.pymorphy3, "pymorphy3_dicts_ru": self.dicts_ru}


@lru_cache(maxsize=1)
def morph_versions() -> MorphVersions:
    """Read both versions without loading the dictionary — cheap enough to always call."""
    return MorphVersions(
        pymorphy3=str(getattr(pymorphy3, "__version__", "unknown")),
        dicts_ru=str(getattr(pymorphy3_dicts_ru, "__version__", "unknown")),
    )


@lru_cache(maxsize=1)
def analyzer() -> Any:
    """The shared analyzer. Loaded lazily: nothing pays for it unless a term opts in.

    ``pymorphy3-dicts-ru`` ships its tables as package data inside the venv, so
    constructing this downloads nothing and writes nothing outside the project.
    """
    return pymorphy3.MorphAnalyzer(lang="ru")


# ------------------------------------------------------------------- casing


def _case_like(source: str, generated: str) -> str:
    """Put ``source``'s capitalisation onto ``generated``. Purely mechanical."""
    if not generated:
        return generated
    if len(source) > 1 and source.isupper():
        return generated.upper()
    if source[:1].isupper():
        return generated[:1].upper() + generated[1:]
    return generated


def transfer_case(source: str, generated: str) -> str:
    """Casing transfer, per hyphen component when the components line up.

    «Кор-Вазгар» + «кор-вазгара» -> «Кор-Вазгара»; «Кор-вазгар» + «кор-вазгара» -> «Кор-вазгара».
    A component-count mismatch falls back to the whole string, which is the only
    other shape the analyzer produces.
    """
    src_parts = source.split("-")
    gen_parts = generated.split("-")
    if len(src_parts) == len(gen_parts):
        return "-".join(_case_like(s, g) for s, g in zip(src_parts, gen_parts, strict=True))
    return _case_like(source, generated)


# --------------------------------------------------------------- paradigms


@dataclass(frozen=True, slots=True)
class Paradigm:
    """One surface string's generated case forms, or the reason there are none."""

    surface: str
    by_case: Mapping[str, str] = field(default_factory=dict)
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.by_case)


def _lemma_parse(token: str) -> Any | None:
    """The first parse that is a declinable nominative singular, in pymorphy's order.

    Requiring nominative singular is what makes the pairing sound: the owner
    writes lemmas in ``_lexicon.yaml``, and a form whose own case is unknown
    cannot be inflected to a *known* one.
    """
    for parse in analyzer().parse(token):
        tag = parse.tag
        if tag.POS not in DECLINABLE_POS:
            continue
        if tag.number != "sing" or tag.case != "nomn":
            continue
        return parse
    return None


def _agree(parses: Sequence[Any]) -> str | None:
    """``None`` when every token can share one case, else the disagreement.

    Gender and animacy are compared only across the tokens that express them —
    an adjective in the nominative carries no animacy, and demanding one would
    reject «Липкий Том», which declines perfectly well.
    """
    for feature in ("gender", "animacy"):
        values = {getattr(parse.tag, feature) for parse in parses}
        values.discard(None)
        if len(values) > 1:
            return f"words disagree in {feature} ({', '.join(sorted(values))})"
    return None


def inflect_surface(surface: str) -> Paradigm:
    """Six singular case forms of one lexicon surface string.

    Single-word (possibly hyphenated) is the primary path and goes straight to
    the analyzer. A multi-word name is inflected per word, and only when every
    word is a declinable nominative singular that agrees with the others —
    otherwise the whole term is skipped with a reason, never half-inflected into
    «Освальда Стоун».
    """
    tokens = surface.split()
    if not tokens:
        return Paradigm(surface, reason="empty string")

    parses: list[Any] = []
    for token in tokens:
        parse = _lemma_parse(token)
        if parse is None:
            return Paradigm(
                surface,
                reason=f"{token!r} does not parse as a declinable nominative singular "
                f"noun or adjective",
            )
        parses.append(parse)

    if len(parses) > 1:
        disagreement = _agree(parses)
        if disagreement is not None:
            return Paradigm(surface, reason=disagreement)

    by_case: dict[str, str] = {}
    for case in CASES:
        words: list[str] = []
        for token, parse in zip(tokens, parses, strict=True):
            inflected = parse.inflect({case, "sing"})
            if inflected is None:
                words = []
                break
            words.append(transfer_case(token, inflected.word))
        if words:
            by_case[case] = " ".join(words)
    if not by_case:
        return Paradigm(surface, reason="the analyzer produced no case forms")
    return Paradigm(surface, by_case=by_case)


# ------------------------------------------------------------------- table


@dataclass(frozen=True, slots=True)
class GeneratedPair:
    """One case-paired substitution: ``variant`` was heard, ``display`` is correct."""

    case: str
    variant: str
    display: str
    from_variant: str

    def to_dict(self) -> dict[str, str]:
        return {
            "case": self.case,
            "variant": self.variant,
            "display": self.display,
            "from_variant": self.from_variant,
        }


@dataclass(frozen=True, slots=True)
class ExpansionNote:
    """Why one string produced nothing. The audit trail, not a warning."""

    term_id: str
    kind: str
    subject: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "term_id": self.term_id,
            "kind": self.kind,
            "subject": self.subject,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class TermExpansion:
    """Everything generated for one ``morph: true`` term."""

    term_id: str
    lemma: str
    display_forms: tuple[str, ...] = ()
    pairs: tuple[GeneratedPair, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "term_id": self.term_id,
            "lemma": self.lemma,
            "display_forms": list(self.display_forms),
            "pairs": [pair.to_dict() for pair in self.pairs],
        }


@dataclass(frozen=True, slots=True)
class ExpansionTable:
    """The generated forms for a whole lexicon, plus why anything is missing."""

    versions: MorphVersions
    lexicon_digest: str | None = None
    terms: Mapping[str, TermExpansion] = field(default_factory=dict)
    notes: tuple[ExpansionNote, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.terms

    def display_forms(self, term_id: str) -> tuple[str, ...]:
        """Generated canonical forms — ``qa`` counts these as the term heard right."""
        expansion = self.terms.get(term_id)
        return expansion.display_forms if expansion else ()

    def variant_forms(self, term_id: str) -> tuple[str, ...]:
        """Generated misrecognition forms, deduplicated in generation order."""
        expansion = self.terms.get(term_id)
        if not expansion:
            return ()
        return tuple(dict.fromkeys(pair.variant for pair in expansion.pairs))

    def iter_pairs(self) -> Iterator[tuple[str, GeneratedPair]]:
        for term_id, expansion in self.terms.items():
            for pair in expansion.pairs:
                yield term_id, pair

    def digest(self) -> str:
        """Content digest, with both library versions folded in.

        The versions are part of the input on purpose: a dictionary upgrade must
        invalidate ``render`` and ``qa`` even in the (likely) case where it
        happens to change none of *these* forms, because the next lexicon edit
        would then be expanded under different grammar than the cached output.
        """
        payload = {
            "schema": EXPANSION_SCHEMA,
            "lexicon_digest": self.lexicon_digest,
            "versions": self.versions.to_dict(),
            "terms": [self.terms[term_id].to_dict() for term_id in sorted(self.terms)],
        }
        return sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": EXPANSION_SCHEMA,
            "digest": self.digest(),
            "lexicon_digest": self.lexicon_digest,
            "versions": self.versions.to_dict(),
            "terms": [self.terms[term_id].to_dict() for term_id in sorted(self.terms)],
            "notes": [note.to_dict() for note in self.notes],
            "pair_count": sum(len(e.pairs) for e in self.terms.values()),
            "display_form_count": sum(len(e.display_forms) for e in self.terms.values()),
        }


def empty_table(lexicon: Lexicon | None = None) -> ExpansionTable:
    """A table with nothing in it — the shape every caller can rely on."""
    return ExpansionTable(
        versions=morph_versions(),
        lexicon_digest=lexicon.digest if lexicon is not None else None,
    )


# ------------------------------------------------------------- the expansion


def _literal_surfaces(lexicon: Lexicon) -> tuple[set[str], set[str]]:
    """Every string the owner wrote down, case-folded: variants and canonical forms.

    Inactive terms are included. An entry the owner switched off is still a
    decision about that string, and a generator must not quietly re-enable it
    under a different mapping.
    """
    variants: set[str] = set()
    canonical: set[str] = set()
    for term in lexicon.terms:
        variants.update(value.casefold() for value in term.variants if value)
        canonical.update(value.casefold() for value in term.canonical_forms())
    return variants, canonical


def _morph_terms(lexicon: Lexicon) -> tuple[list[LexiconTerm], list[ExpansionNote]]:
    selected: list[LexiconTerm] = []
    notes: list[ExpansionNote] = []
    for term in lexicon.terms:
        if not term.morph:
            continue
        if not term.active:
            notes.append(
                ExpansionNote(term.id, "skipped", term.id, "term is inactive; nothing is expanded")
            )
            continue
        selected.append(term)
    return selected, notes


def expand_terms(lexicon: Lexicon) -> ExpansionTable:
    """Generate the case-paired substitution table for every ``morph: true`` term.

    Raises :class:`~session_ingest.errors.LexiconExpansionError` when two terms
    generate the same surface form with different targets — the one situation
    where continuing would put the wrong name in the transcript.
    """
    if not lexicon.present:
        return empty_table(lexicon)

    terms, notes = _morph_terms(lexicon)
    if not terms:
        return ExpansionTable(
            versions=morph_versions(), lexicon_digest=lexicon.digest, notes=tuple(notes)
        )

    literal_variants, literal_canonical = _literal_surfaces(lexicon)
    expansions: dict[str, TermExpansion] = {}
    #: casefolded generated variant -> (term_id, display) — the collision guard.
    claimed: dict[str, tuple[str, str]] = {}

    for term in terms:
        lemma = term.display_ru or term.canonical
        display_paradigm = inflect_surface(lemma)
        if not display_paradigm.ok:
            notes.append(
                ExpansionNote(
                    term.id,
                    "skipped",
                    lemma,
                    f"display form cannot be inflected: {display_paradigm.reason}",
                )
            )
            continue

        display_forms = _display_forms(term.id, lemma, display_paradigm, notes)
        pairs = _pairs_for(
            term,
            display_paradigm=display_paradigm,
            literal_variants=literal_variants,
            literal_canonical=literal_canonical,
            claimed=claimed,
            notes=notes,
        )
        if display_forms or pairs:
            expansions[term.id] = TermExpansion(
                term_id=term.id,
                lemma=lemma,
                display_forms=display_forms,
                pairs=pairs,
            )

    return ExpansionTable(
        versions=morph_versions(),
        lexicon_digest=lexicon.digest,
        terms=expansions,
        # Two cases of one variant can hit the same wall (an inanimate accusative
        # repeats its nominative); the reason is the same sentence either way.
        notes=tuple(dict.fromkeys(notes)),
    )


def _display_forms(
    term_id: str, lemma: str, paradigm: Paradigm, notes: list[ExpansionNote]
) -> tuple[str, ...]:
    """Distinct generated spellings of the correct name, in case order."""
    folded_lemma = lemma.casefold()
    forms: dict[str, str] = {}
    for case in CASES:
        form = paradigm.by_case.get(case)
        if form is None:
            continue
        if len(form) < MIN_FORM_LENGTH:
            notes.append(
                ExpansionNote(
                    term_id,
                    "dropped",
                    form,
                    f"generated display form is shorter than {MIN_FORM_LENGTH} characters",
                )
            )
            continue
        folded = form.casefold()
        if folded == folded_lemma or folded in forms:
            continue
        forms[folded] = form
    return tuple(forms.values())


def _pairs_for(
    term: LexiconTerm,
    *,
    display_paradigm: Paradigm,
    literal_variants: set[str],
    literal_canonical: set[str],
    claimed: dict[str, tuple[str, str]],
    notes: list[ExpansionNote],
) -> tuple[GeneratedPair, ...]:
    """Case-paired (heard, correct) forms for one term, with every guardrail applied."""
    pairs: list[GeneratedPair] = []
    if not term.variant_forms():
        notes.append(
            ExpansionNote(
                term.id,
                "skipped",
                term.id,
                "the term lists no variants, so there is nothing to pair; only the display "
                "forms were generated, and those only widen what `qa` counts as correct",
            )
        )
    for variant in term.variant_forms():
        paradigm = inflect_surface(variant)
        if not paradigm.ok:
            notes.append(
                ExpansionNote(
                    term.id, "skipped", variant, f"variant cannot be inflected: {paradigm.reason}"
                )
            )
            continue
        folded_variant = variant.casefold()
        for case in CASES:
            generated = paradigm.by_case.get(case)
            display = display_paradigm.by_case.get(case)
            if generated is None or display is None:
                continue
            folded = generated.casefold()
            if len(generated) < MIN_FORM_LENGTH or len(display) < MIN_FORM_LENGTH:
                notes.append(
                    ExpansionNote(
                        term.id,
                        "dropped",
                        generated,
                        f"generated form is shorter than {MIN_FORM_LENGTH} characters",
                    )
                )
                continue
            if folded == folded_variant:
                # The lemma itself — the owner already enumerated it. Recorded
                # anyway: it is what explains a term like `varda`, whose variant
                # the analyzer treats as indeclinable and which therefore yields
                # this in all six cases and no pairs at all.
                notes.append(
                    ExpansionNote(
                        term.id,
                        "dropped",
                        generated,
                        "identical to the variant it was generated from; "
                        "the explicit entry already covers it",
                    )
                )
                continue
            if folded == display.casefold():
                notes.append(
                    ExpansionNote(
                        term.id,
                        "dropped",
                        generated,
                        "differs from the correct form only in casing, so substituting it "
                        "would be a no-op",
                    )
                )
                continue
            if folded in literal_variants:
                notes.append(
                    ExpansionNote(
                        term.id,
                        "excluded",
                        generated,
                        "an explicit lexicon entry already lists this variant",
                    )
                )
                continue
            if folded in literal_canonical:
                notes.append(
                    ExpansionNote(
                        term.id,
                        "excluded",
                        generated,
                        "already a canonical/display form in the lexicon",
                    )
                )
                continue
            owner = claimed.get(folded)
            if owner is not None:
                if owner == (term.id, display):
                    continue  # the same pair reached twice; dedupe silently
                raise LexiconExpansionError(
                    f"morphological expansion is ambiguous: {generated!r} would be replaced by "
                    f"{owner[1]!r} for term {owner[0]!r} and by {display!r} for term "
                    f"{term.id!r}. Resolve it in _lexicon.yaml — add an explicit entry for the "
                    f"form, or drop `morph: true` from one of the two terms.",
                    detail={
                        "form": generated,
                        "case": case,
                        "terms": [owner[0], term.id],
                        "replacements": [owner[1], display],
                    },
                )
            claimed[folded] = (term.id, display)
            pairs.append(
                GeneratedPair(case=case, variant=generated, display=display, from_variant=variant)
            )
    return tuple(pairs)
