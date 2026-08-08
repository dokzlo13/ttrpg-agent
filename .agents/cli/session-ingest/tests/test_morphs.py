"""Mechanical case-form expansion: the paradigms, the guardrails, the audit trail.

Two kinds of test live here and they are deliberately different in spirit.

The **probe** tests pin what pymorphy3 and its dictionary actually do for the
shapes this project feeds them — a hyphenated invented name, an out-of-vocabulary
surname, an indeclinable one. They are not asserting that the library is correct;
they are asserting that its behaviour has not changed under us, because the whole
feature rests on it and a silent change would rewrite transcripts.

The **guardrail** tests assert the rules this module adds on top: explicit
entries win, ambiguity is refused rather than resolved, short forms are dropped,
and nothing leaks into the biasing stage.
"""

from __future__ import annotations

import pytest

from session_ingest import morphs
from session_ingest.errors import LexiconExpansionError
from session_ingest.vaultfiles import Lexicon, LexiconTerm, load_lexicon


def lexicon_of(*terms: LexiconTerm) -> Lexicon:
    """An in-memory lexicon; the loader is exercised separately in test_vaultfiles."""
    from pathlib import Path

    return Lexicon(
        path=Path("_lexicon.yaml"), present=True, digest="sha256:test", terms=tuple(terms)
    )


def term(term_id: str, display: str, *variants: str, **kwargs) -> LexiconTerm:
    kwargs.setdefault("morph", True)
    return LexiconTerm(
        id=term_id,
        canonical=kwargs.pop("canonical", term_id.title()),
        display_ru=display,
        variants=tuple(variants),
        **kwargs,
    )


def pairs_of(table: morphs.ExpansionTable, term_id: str) -> set[tuple[str, str, str]]:
    """``{(case, variant, display)}`` — the pairing is what every assertion is about."""
    expansion = table.terms.get(term_id)
    if expansion is None:
        return set()
    return {(pair.case, pair.variant, pair.display) for pair in expansion.pairs}


def cases_of(table: morphs.ExpansionTable, term_id: str) -> set[str]:
    return {case for case, _, _ in pairs_of(table, term_id)}


# ---------------------------------------------------------------- the probe


def test_pymorphy3_declines_the_hyphenated_name_by_its_last_component() -> None:
    """«Кор-Вазгар» is the shape this project bet on; pin what the library does with it.

    If this ever fails, the library or dictionary changed how it handles hyphens
    and `morphs` must be re-read before anything is re-rendered — the fallback
    the design contemplated (inflect the final segment and rejoin) becomes
    relevant exactly here.
    """
    paradigm = morphs.inflect_surface("Кор-Вазгар")
    assert paradigm.ok, paradigm.reason
    assert paradigm.by_case == {
        "nomn": "Кор-Вазгар",
        "gent": "Кор-Вазгара",
        "datv": "Кор-Вазгару",
        "accs": "Кор-Вазгара",
        "ablt": "Кор-Вазгаром",
        "loct": "Кор-Вазгаре",
    }


def test_known_paradigms_for_the_names_in_the_live_lexicon() -> None:
    """Out-of-vocabulary names decline predictably; the casing comes back with them."""
    assert morphs.inflect_surface("Марвика").by_case == {
        "nomn": "Марвика",
        "gent": "Марвики",
        "datv": "Марвике",
        "accs": "Марвику",
        "ablt": "Марвикой",
        "loct": "Марвике",
    }
    assert morphs.inflect_surface("Вагзар").by_case == {
        "nomn": "Вагзар",
        "gent": "Вагзара",
        "datv": "Вагзару",
        "accs": "Вагзара",
        "ablt": "Вагзаром",
        "loct": "Вагзаре",
    }


def test_variant_and_display_paradigms_agree_case_by_case() -> None:
    """The pairing premise: both sides inflect, and the tags line up one to one."""
    variant = morphs.inflect_surface("Марвика")
    display = morphs.inflect_surface("Морвика")
    assert set(variant.by_case) == set(display.by_case) == set(morphs.CASES)
    for case in morphs.CASES:
        # Same case, same ending — the two names differ only in their stem.
        assert variant.by_case[case][4:] == display.by_case[case][4:], case


def test_an_indeclinable_name_yields_only_its_own_form() -> None:
    """«Верченцо» is Fixd: every case is the lemma, so expansion has nothing to add."""
    paradigm = morphs.inflect_surface("Верченцо")
    assert set(paradigm.by_case.values()) == {"Верченцо"}
    table = morphs.expand_terms(lexicon_of(term("verchenzo", "Верченцо", "Верченсо")))
    assert table.display_forms("verchenzo") == ()
    assert pairs_of(table, "verchenzo") == set()


def test_latin_strings_do_not_parse_and_are_skipped_with_a_reason() -> None:
    """A term whose only display form is Latin is skipped, never half-expanded."""
    table = morphs.expand_terms(
        lexicon_of(LexiconTerm(id="zz", canonical="Kor-Vazgar", morph=True))
    )
    assert table.empty
    reasons = [note.reason for note in table.notes if note.term_id == "zz"]
    assert any("does not parse as a declinable" in reason for reason in reasons)


# ------------------------------------------------------------------ casing


def test_casing_is_transferred_per_hyphen_component() -> None:
    assert morphs.transfer_case("Кор-Вазгар", "кор-вазгара") == "Кор-Вазгара"
    assert morphs.transfer_case("Кор-вазгар", "кор-вазгара") == "Кор-вазгара"
    assert morphs.transfer_case("морвика", "морвики") == "Морвики".casefold()
    assert morphs.transfer_case("ВАГЗАР", "вагзаром") == "ВАГЗАРОМ"


# -------------------------------------------------------------- guardrails


def test_explicit_entries_take_precedence_over_generated_ones() -> None:
    """A hand-written oblique entry like `kor-vazgar-gen` keeps «Вагзара» out of the table."""
    lexicon = lexicon_of(
        term("kor-vazgar", "Кор-Вазгар", "Вазгар", "Вагзар"),
        LexiconTerm(
            id="kor-vazgar-gen",
            canonical="Kor-Vazgar",
            display_ru="Кор-Вазгара",
            variants=("Вагзара", "Вазгара"),
        ),
    )
    table = morphs.expand_terms(lexicon)
    generated = {pair.variant for _, pair in table.iter_pairs()}
    assert "Вагзара" not in generated
    assert "Вазгара" not in generated
    # …and the exclusion is recorded rather than silent.
    excluded = {note.subject for note in table.notes if note.kind == "excluded"}
    assert {"Вагзара", "Вазгара"} <= excluded
    # The cases the explicit entries do not cover are still generated, from both
    # variants, and every pair is case-consistent on both sides.
    assert pairs_of(table, "kor-vazgar") == {
        ("datv", "Вазгару", "Кор-Вазгару"),
        ("datv", "Вагзару", "Кор-Вазгару"),
        ("ablt", "Вазгаром", "Кор-Вазгаром"),
        ("ablt", "Вагзаром", "Кор-Вазгаром"),
        ("loct", "Вазгаре", "Кор-Вазгаре"),
        ("loct", "Вагзаре", "Кор-Вазгаре"),
    }


def test_an_inactive_explicit_entry_still_wins() -> None:
    """An entry the owner switched off is a decision, not an absence."""
    lexicon = lexicon_of(
        term("morvika", "Морвика", "Марвика"),
        LexiconTerm(
            id="morvika-gen",
            canonical="Morvika",
            display_ru="Морвики",
            variants=("Марвики",),
            active=False,
        ),
    )
    table = morphs.expand_terms(lexicon)
    assert "Марвики" not in {pair.variant for _, pair in table.iter_pairs()}


def test_a_generated_form_claimed_by_two_terms_is_a_hard_error() -> None:
    """One misrecognition the owner attached to two different names must be resolved.

    Substituting «Марвики» would have to pick between «Морвики» and «Норвики» —
    i.e. between two characters — so the tool refuses and names both terms.
    """
    lexicon = lexicon_of(
        term("morvika", "Морвика", "Марвика"),
        term("norvika", "Норвика", "Марвика"),
    )
    with pytest.raises(LexiconExpansionError) as excinfo:
        morphs.expand_terms(lexicon)
    assert excinfo.value.code == "morph_collision"
    assert set(excinfo.value.detail["terms"]) == {"morvika", "norvika"}
    assert "Морвик" in excinfo.value.message and "Норвик" in excinfo.value.message


def test_the_same_pair_reached_twice_is_deduped_silently() -> None:
    """«Марвике» is both dative and prepositional; it is one pair, not a collision."""
    table = morphs.expand_terms(lexicon_of(term("morvika", "Морвика", "Марвика")))
    variants = [pair.variant for _, pair in table.iter_pairs()]
    assert variants.count("Марвике") == 1
    assert ("datv", "Марвике", "Морвике") in pairs_of(table, "morvika")
    assert "loct" not in cases_of(table, "morvika")


def test_forms_shorter_than_the_minimum_are_dropped() -> None:
    """A three-letter surface would match inside ordinary words, so it never ships."""
    assert morphs.MIN_FORM_LENGTH == 4
    table = morphs.expand_terms(lexicon_of(term("tom", "Том", "Ром")))
    for _, pair in table.iter_pairs():
        assert len(pair.variant) >= 4 and len(pair.display) >= 4
    # The nominative pair «Ром»/«Том» is three characters on both sides.
    assert "nomn" not in cases_of(table, "tom")
    dropped = {note.subject for note in table.notes if note.kind == "dropped"}
    assert "Ром" in dropped


def test_forms_identical_to_the_lemma_are_not_emitted() -> None:
    table = morphs.expand_terms(lexicon_of(term("marlok", "Марлок", "Марлак")))
    assert "Марлок" not in table.display_forms("marlok")
    assert "Марлак" not in {pair.variant for _, pair in table.iter_pairs()}


def test_a_no_op_pair_is_dropped_with_a_reason() -> None:
    """«Кор-вазгара» → «Кор-Вазгара» differs only in casing; the substituter is case-blind."""
    table = morphs.expand_terms(lexicon_of(term("kor-vazgar", "Кор-Вазгар", "Кор-вазгар")))
    assert pairs_of(table, "kor-vazgar") == set()
    reasons = [note.reason for note in table.notes if note.term_id == "kor-vazgar"]
    assert any("no-op" in reason for reason in reasons)


# ------------------------------------------------------------- multi-word


def test_a_multiword_name_whose_parts_agree_is_inflected_per_word() -> None:
    """«Липкий Том» declines as a unit: adjective and noun agree in every case."""
    paradigm = morphs.inflect_surface("Липкий Том")
    assert paradigm.ok, paradigm.reason
    assert paradigm.by_case["gent"] == "Липкого Тома"
    assert paradigm.by_case["datv"] == "Липкому Тому"
    assert paradigm.by_case["ablt"] == "Липким Томом"


def test_a_multiword_name_whose_parts_disagree_is_skipped_not_guessed() -> None:
    """«Освальд Стоун» is animate + inanimate: half-inflecting it would be wrong Russian."""
    paradigm = morphs.inflect_surface("Освальд Стоун")
    assert not paradigm.ok
    assert paradigm.reason is not None and "animacy" in paradigm.reason

    table = morphs.expand_terms(lexicon_of(term("oswald", "Освальд Стоун", "Освалд Стоун")))
    assert table.empty
    assert any("animacy" in note.reason for note in table.notes)


# ------------------------------------------------------------ determinism


def test_the_table_is_deterministic_and_carries_the_library_versions() -> None:
    lexicon = lexicon_of(
        term("morvika", "Морвика", "Марвика"),
        term("kelverin", "Кельверин", "Кильверин"),
    )
    first = morphs.expand_terms(lexicon)
    second = morphs.expand_terms(lexicon)
    assert first.to_dict() == second.to_dict()
    assert first.digest() == second.digest()

    versions = first.versions.to_dict()
    assert versions["pymorphy3"] and versions["pymorphy3_dicts_ru"]
    # A dictionary upgrade must move the digest even if no form changed.
    bumped = morphs.ExpansionTable(
        versions=morphs.MorphVersions(pymorphy3="9.9.9", dicts_ru=versions["pymorphy3_dicts_ru"]),
        lexicon_digest=first.lexicon_digest,
        terms=first.terms,
    )
    assert bumped.digest() != first.digest()


def test_a_lexicon_without_morph_terms_expands_to_nothing() -> None:
    """The opt-in is real: nothing is generated for a term that did not ask."""
    table = morphs.expand_terms(lexicon_of(term("plain", "Морвика", "Марвика", morph=False)))
    assert table.empty
    assert table.digest()  # still a stable key


def test_an_absent_lexicon_expands_to_an_empty_table(tmp_path) -> None:
    table = morphs.expand_terms(load_lexicon(tmp_path / "nope.yaml"))
    assert table.empty
    assert table.lexicon_digest is None
