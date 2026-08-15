"""``glossary`` — the verbatim-observation guardrail and the append-only merge."""

from __future__ import annotations

from typing import Any

from session_ingest import glossary as glossary_mod
from session_ingest.glossary import Proposal, gate_new_terms, merge_lexicon, verify_variants
from session_ingest.registry import EntityRegistry, load_entity_registry
from session_ingest.vaultfiles import load_lexicon

from .conftest import LEXICON_YAML, SESSION_ID, Workspace
from .fakes import FakeClient, adopt, constant, make_config

#: What the fixture dataset actually says (conftest.TERM_TEXT).
OBSERVED = (
    "Вазгар говорит. вазгар кивает. Вагзар идёт. Вазагар молчит. "
    "Освальд Стоун здесь. Мардан пришёл."
)


def proposal(**kwargs: Any) -> Proposal:
    base: dict[str, Any] = {
        "term_id": "vagzar",
        "canonical": "Вазгар",
        "display_ru": None,
        "kind": "npc",
        "variants": [],
        "existing": True,
    }
    base.update(kwargs)
    return Proposal(**base)


def answer(*terms: dict[str, Any]) -> dict[str, Any]:
    return {"terms": list(terms)}


def term(term_id: str, canonical: str, *variants: str, existing: bool = True, kind: str = "npc"):
    return {
        "id": term_id,
        "existing": existing,
        "canonical": canonical,
        "display_ru": None,
        "kind": kind,
        "variants_observed": list(variants),
    }


def _run(workspace: Workspace, client: FakeClient, monkeypatch, **kwargs):
    monkeypatch.setattr(glossary_mod, "client_for", lambda _config: client)
    return glossary_mod.run(
        roots=workspace.roots, config=make_config(), session_id=SESSION_ID, **kwargs
    )


# ---------------------------------------------------------------- guardrail


def test_a_variant_not_in_the_session_text_is_dropped(workspace: Workspace) -> None:
    workspace.write_lexicon()
    lexicon = load_lexicon(workspace.roots.lexicon_file)
    kept, dropped = verify_variants(
        [proposal(variants=["Вазагар", "Возгар"])],
        haystack_casefolded=OBSERVED.casefold(),
        lexicon=lexicon,
    )
    assert kept == [] or kept[0].variants == []
    reasons = {(row["variant"], row["reason"]) for row in dropped}
    assert ("Возгар", "not observed verbatim in the session text") in reasons
    assert ("Вазагар", "already recorded for this term") in reasons


def test_a_variant_identical_to_the_canonical_form_is_dropped(workspace: Workspace) -> None:
    workspace.write_lexicon()
    _kept, dropped = verify_variants(
        [proposal(variants=["вазгар"])],
        haystack_casefolded=OBSERVED.casefold(),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
    )
    assert dropped[0]["reason"] == "identical to a canonical form"


# --------------------------------------------- collision and hazard guardrails
#
# Every case below is one that actually reached vault/transcripts/_lexicon.yaml
# on 2026-08-15 and had to be restored from a snapshot. `render` substitutes
# these strings context-free, so a bad variant does not fail quietly — it
# rewrites correct text.


def test_a_canonical_form_of_another_term_is_dropped(workspace: Workspace) -> None:
    """«Вазгар» is `vagzar`'s canonical; attaching it to `oswald` would rewrite it."""
    workspace.write_lexicon()
    kept, dropped = verify_variants(
        [proposal(term_id="oswald", canonical="Освальд", variants=["Вазгар"])],
        haystack_casefolded=OBSERVED.casefold(),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
    )
    assert kept == []
    assert [(row["variant"], row["collides_with"]) for row in dropped] == [("Вазгар", "vagzar")]


def test_a_variant_claimed_by_another_term_is_dropped(workspace: Workspace) -> None:
    """One observed string cannot have two different replacements."""
    workspace.write_lexicon()
    kept, dropped = verify_variants(
        [proposal(term_id="oswald", canonical="Освальд", variants=["Вагзар"])],
        haystack_casefolded=OBSERVED.casefold(),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
    )
    assert kept == []
    assert dropped[0]["collides_with"] == "vagzar"


def test_two_proposals_cannot_both_claim_one_string(workspace: Workspace) -> None:
    """The second claimant loses, so a batch cannot introduce its own ambiguity."""
    workspace.write_lexicon()
    kept, dropped = verify_variants(
        [
            proposal(term_id="oswald", canonical="Освальд", variants=["Освальд Стоун"]),
            proposal(
                term_id="stoun",
                canonical="Стоун",
                variants=["Освальд Стоун"],
                existing=False,
            ),
        ],
        haystack_casefolded=OBSERVED.casefold(),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
    )
    assert [item.term_id for item in kept] == ["oswald"]
    assert dropped[0]["collides_with"] == "oswald"


def test_a_short_variant_is_dropped_as_a_substring_hazard(workspace: Workspace) -> None:
    """«Ваз» would rewrite the middle of «Вазагар» and of unrelated words."""
    workspace.write_lexicon()
    _kept, dropped = verify_variants(
        [proposal(term_id="oswald", canonical="Освальд", variants=["Ваз"])],
        haystack_casefolded=OBSERVED.casefold(),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
    )
    assert "shorter than" in dropped[0]["reason"]


def test_a_new_term_without_a_verified_variant_is_not_written(workspace: Workspace) -> None:
    """A correction dictionary entry that corrects nothing is pure file bloat."""
    workspace.write_lexicon()
    kept, dropped = verify_variants(
        [proposal(term_id="brand-new", canonical="Новое", variants=[], existing=False)],
        haystack_casefolded=OBSERVED.casefold(),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
    )
    assert kept == []
    assert dropped[0]["reason"].startswith("new term proposed with no verified variant")


def test_a_terms_own_canonical_is_still_allowed_for_itself(workspace: Workspace) -> None:
    """The cross-term guard must not fire on the term that legitimately owns the form."""
    workspace.write_lexicon()
    kept, _dropped = verify_variants(
        [proposal(term_id="vagzar", canonical="Вазгар", variants=["Освальд Стоун"])],
        haystack_casefolded=OBSERVED.casefold(),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
    )
    assert kept[0].variants == ["Освальд Стоун"]


#: The live vault's shape at the moment of the incident: a `morph` lemma plus an
#: explicit oblique entry, both built on the same stem. That pairing is what made
#: the model attach the nominative to the locative.
INCIDENT_LEXICON = """\
terms:
  - id: istrid
    canonical: "Istrid"
    display_ru: "Истрид"
    variants: ["Истрит"]
    kind: npc
    active: true
    priority: 1
  - id: istrid-loc
    canonical: "Istrid"
    display_ru: "Истриде"
    variants: ["Истрите"]
    kind: npc
    active: true
    priority: 9
"""

#: Every string below appears verbatim, so the pre-existing verbatim guardrail
#: passes all of them — that is exactly why it was not enough on its own.
INCIDENT_TEXT = (
    "Истрид лечит. Истрите досталось. Истрит кивает. пистрит шумит. "
    "Истр молчит. Истри ждёт. Истритка бежит. ЛАС ласково смотрит."
)


def test_the_2026_08_15_lexicon_corruption_is_refused(workspace: Workspace) -> None:
    """Regression: the real batch that had to be rolled back from a snapshot.

    The model attached seven strings to ``istrid-loc`` (display «Истриде»). Only
    «Истритка» is a usable correction. Everything else would have damaged the
    transcript, and all of it passed the verbatim check.
    """
    workspace.write_lexicon(INCIDENT_LEXICON)
    kept, dropped = verify_variants(
        [
            proposal(
                term_id="istrid-loc",
                canonical="Istrid",
                display_ru="Истриде",
                variants=["Истрите", "Истрит", "пистрит", "Истр", "Истри", "Истрид", "Истритка"],
            )
        ],
        haystack_casefolded=INCIDENT_TEXT.casefold(),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
    )

    why = {row["variant"]: row["reason"] for row in dropped}

    # Every destructive string is refused.
    for damaging in ("Истрид", "Истрит", "Истр", "Истри", "Истрите"):
        assert damaging in why, f"{damaging} must not reach the lexicon"
    # «Истрид» is `istrid`'s canonical display form: recording it here would have
    # rewritten every correct nominative in the transcript into «Истриде».
    assert why["Истрид"].startswith("it is a canonical/display form of term 'istrid'")
    # «Истрит» is already `istrid`'s variant — one string, two replacements.
    assert why["Истрит"].startswith("term 'istrid' already claims this variant")
    # «Истр» clears the four-character floor but sits inside longer known forms.
    assert "occurs inside" in why["Истр"]
    assert "occurs inside" in why["Истри"]
    assert why["Истрите"] == "already recorded for this term"

    # «Истритка» is a real misrecognition and is kept. «пистрит» survives too: it
    # is not a substring of anything, so at worst it rewrites one garbled token.
    assert kept[0].variants == ["пистрит", "Истритка"]


def test_a_short_latin_variant_cannot_rewrite_russian_words(workspace: Workspace) -> None:
    """Regression: `ЛАС` -> «Elias Pinch» would have hit «ласково» and «класс»."""
    workspace.write_lexicon(INCIDENT_LEXICON)
    kept, dropped = verify_variants(
        [
            proposal(
                term_id="elias-pinch",
                canonical="Elias Pinch",
                display_ru="Elias Pinch",
                variants=["ЛАС"],
                existing=False,
            )
        ],
        haystack_casefolded=INCIDENT_TEXT.casefold(),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
    )
    assert kept == []
    assert "shorter than" in dropped[0]["reason"]
    # And with no surviving variant the term is not written at all.
    assert any(row["reason"].startswith("new term proposed") for row in dropped)


# ------------------------------------- the direction gate (entity registry)
#
# A structurally spotless proposal can still be backwards: the model names the
# misrecognition as canonical and the correct spelling as its variant. Both real
# examples below survived every structural check on 2026-08-15.

REGISTRY_MD = """\
# Реестр

```yaml
schema: ttrpg.entity-registry/1
entities:
  - {slug: marden-craves, kind: npc, ru: "Марден Краветс", en: "Marden Craves", aliases: ["Марден"]}
```
"""


def _registry(workspace: Workspace, text: str = REGISTRY_MD) -> EntityRegistry:
    path = workspace.roots.entity_registry_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return load_entity_registry(path)


def test_a_new_canonical_known_to_the_registry_is_written(workspace: Workspace) -> None:
    """«Мардан» -> «Марден» is the one genuinely correct new term from the batch."""
    kept, deferred = gate_new_terms(
        [
            proposal(
                term_id="glossary-a2fd7e0d",
                canonical="Marden",
                display_ru="Марден",
                variants=["Мардан"],
                existing=False,
            )
        ],
        _registry(workspace),
    )
    assert [item.term_id for item in kept] == ["glossary-a2fd7e0d"]
    assert deferred == []


def test_a_backwards_new_term_is_held_for_the_owner(workspace: Workspace) -> None:
    """Real case: «Барл» recorded as canonical, with the CORRECT «Баррелла» as its variant."""
    kept, deferred = gate_new_terms(
        [
            proposal(
                term_id="barl",
                canonical="Барл",
                display_ru="Барл",
                variants=["Баррелла"],
                existing=False,
            )
        ],
        _registry(workspace),
    )
    assert kept == []
    assert deferred[0]["canonical"] == "Барл"
    assert "entity-registry" in deferred[0]["reason"]


def test_the_vale_vane_inversion_is_held_for_the_owner(workspace: Workspace) -> None:
    """Real case: «дом Вейн» as canonical would have rewritten the correct «Вэйл»."""
    kept, deferred = gate_new_terms(
        [
            proposal(
                term_id="house-vane",
                canonical="House Vane",
                display_ru="дом Вейн",
                variants=["Вэйл"],
                existing=False,
            )
        ],
        _registry(workspace),
    )
    assert kept == []
    assert deferred[0]["display_ru"] == "дом Вейн"


def test_the_gate_never_touches_an_existing_term(workspace: Workspace) -> None:
    """An existing term's canonical was chosen by the owner; direction is already fixed."""
    kept, deferred = gate_new_terms(
        [proposal(term_id="vagzar", canonical="Вазгар", variants=["Вагзар"], existing=True)],
        _registry(workspace),
    )
    assert [item.term_id for item in kept] == ["vagzar"]
    assert deferred == []


def test_without_a_registry_the_gate_opens(workspace: Workspace) -> None:
    """A vault with no registry must still get a usable glossary; `run` warns instead."""
    registry = load_entity_registry(workspace.roots.entity_registry_file)
    assert registry.present is False
    kept, deferred = gate_new_terms(
        [proposal(term_id="barl", canonical="Барл", variants=["Баррелла"], existing=False)],
        registry,
    )
    assert [item.term_id for item in kept] == ["barl"]
    assert deferred == []


def test_an_observed_new_variant_survives(workspace: Workspace) -> None:
    workspace.write_lexicon()
    kept, dropped = verify_variants(
        [proposal(term_id="oswald", canonical="Освальд", variants=["Освальд Стоун"])],
        haystack_casefolded=OBSERVED.casefold(),
        lexicon=load_lexicon(workspace.roots.lexicon_file),
    )
    assert dropped == []
    assert kept[0].variants == ["Освальд Стоун"]


# ------------------------------------------------------------- the merge


def test_merge_appends_a_new_term_without_touching_the_rest() -> None:
    merged = merge_lexicon(
        LEXICON_YAML,
        [
            proposal(
                term_id="stoun",
                canonical="Стоун",
                display_ru="Стоун",
                variants=["Освальд Стоун"],
                existing=False,
            )
        ],
        session_id=SESSION_ID,
    )
    assert merged.text is not None
    assert merged.terms_added == ["stoun"]
    assert merged.terms_extended == []
    assert merged.text.startswith(LEXICON_YAML.rstrip("\n")), "existing bytes are untouched"
    assert f"source: glossary/{SESSION_ID}" in merged.text
    assert "priority: 3" in merged.text
    assert "active: true" in merged.text


def test_merge_extends_an_existing_flow_style_variants_list() -> None:
    merged = merge_lexicon(
        LEXICON_YAML,
        [proposal(term_id="oswald", canonical="Освальд", variants=["Освальд Стоун"])],
        session_id=SESSION_ID,
    )
    assert merged.text is not None
    assert merged.terms_extended == ["oswald"]
    assert "variants: [Освалд, Освальд Стоун]" in merged.text
    assert "variants: [Вагзар, Вазагар]" in merged.text, "other terms are byte-identical"


def test_merge_extends_a_block_style_variants_list() -> None:
    block_style = "terms:\n  - id: vagzar\n    canonical: Вазгар\n    variants:\n      - Вагзар\n"
    merged = merge_lexicon(block_style, [proposal(variants=["Вазагар"])], session_id=SESSION_ID)
    assert merged.text is not None
    assert "      - Вагзар\n      - Вазагар" in merged.text


def test_merge_adds_a_variants_key_when_the_term_has_none() -> None:
    bare = "terms:\n  - id: vagzar\n    canonical: Вазгар\n"
    merged = merge_lexicon(bare, [proposal(variants=["Вагзар"])], session_id=SESSION_ID)
    assert merged.text is not None
    assert "variants: [Вагзар]" in merged.text


def test_merge_of_nothing_changes_nothing() -> None:
    assert merge_lexicon(LEXICON_YAML, [], session_id=SESSION_ID).text is None
    unchanged = merge_lexicon(
        LEXICON_YAML, [proposal(term_id="vagzar", variants=[])], session_id=SESSION_ID
    )
    assert unchanged.text is None


def test_merge_refuses_a_shape_it_cannot_edit_safely() -> None:
    merged = merge_lexicon(
        "glossary:\n  - id: x\n", [proposal(existing=False, term_id="new")], session_id=SESSION_ID
    )
    assert merged.text is None
    assert merged.deferred, "refusing beats reformatting hand-maintained user data"


def test_an_empty_file_gets_a_terms_list() -> None:
    merged = merge_lexicon(
        "", [proposal(term_id="stoun", canonical="Стоун", existing=False)], session_id=SESSION_ID
    )
    assert merged.text is not None
    assert merged.text.startswith("terms:\n")


# --------------------------------------------------------------------- verb


def test_glossary_without_a_key_skips_cleanly(workspace: Workspace) -> None:
    adopt(workspace)
    workspace.write_lexicon()
    before = workspace.roots.lexicon_file.read_bytes()
    payload = glossary_mod.run(
        roots=workspace.roots, config=make_config(api_key=None), session_id=SESSION_ID
    )
    assert payload["status"] == "skipped"
    assert payload["code"] == "missing_api_key"
    assert workspace.roots.lexicon_file.read_bytes() == before


def test_glossary_merges_and_reports(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)
    workspace.write_lexicon()
    client = FakeClient(
        constant(
            answer(
                term("oswald", "Освальд", "Освальд Стоун", "Никогда не звучало"),
                term("marden", "Марден", "Мардан", existing=False),
                term("vagzar", "Вазгар", "Вагзар"),
            )
        )
    )
    payload = _run(workspace, client, monkeypatch)

    assert payload["status"] == "ok"
    assert payload["lexicon_changed"] is True
    assert payload["terms_extended"] == ["oswald"]
    assert payload["terms_added"] == ["marden"]
    assert payload["variants_added"] == 2
    assert payload["biasing_files"] == "deferred_to_plan"

    rejected = {(row["variant"], row["reason"]) for row in payload["variants_rejected"]}
    assert ("Никогда не звучало", "not observed verbatim in the session text") in rejected
    assert ("Вагзар", "already recorded for this term") in rejected

    lexicon = load_lexicon(workspace.roots.lexicon_file)
    by_id = lexicon.by_id()
    assert by_id["oswald"].variants == ("Освалд", "Освальд Стоун")
    assert by_id["vagzar"].variants == ("Вагзар", "Вазагар"), "untouched, in order"
    assert by_id["marden"].variants == ("Мардан",)
    assert by_id["marden"].source == f"glossary/{SESSION_ID}"
    assert by_id["marden"].priority == 3
    assert by_id["marden"].active is True

    assert any("dropped by the guardrails" in warning for warning in payload["warnings"])
    assert "plan" in " ".join(step.get("command", "") for step in payload["next_steps"])


def test_re_running_the_merge_adds_nothing(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)
    workspace.write_lexicon()
    client = FakeClient(constant(answer(term("oswald", "Освальд", "Освальд Стоун"))))

    assert _run(workspace, client, monkeypatch)["status"] == "ok"
    after_first = workspace.roots.lexicon_file.read_bytes()

    # --force skips the digest cache, so the model answers again with the same
    # variant; the guardrail must recognise it as already recorded.
    again = _run(workspace, client, monkeypatch, force=True)
    assert again["variants_added"] == 0
    assert again["lexicon_changed"] is False
    assert workspace.roots.lexicon_file.read_bytes() == after_first, "byte-for-byte identical"


def test_glossary_skips_on_the_second_run(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)
    workspace.write_lexicon()
    client = FakeClient(constant(answer()))
    assert _run(workspace, client, monkeypatch)["status"] == "ok"
    calls = len(client.calls)
    assert _run(workspace, client, monkeypatch)["status"] == "skipped"
    assert len(client.calls) == calls


def test_the_existing_dictionary_reaches_the_prompt(workspace: Workspace, monkeypatch) -> None:
    adopt(workspace)
    workspace.write_lexicon()
    client = FakeClient(constant(answer()))
    _run(workspace, client, monkeypatch)
    system = client.system_prompts()[0]
    assert "vagzar: Вазгар | варианты: Вагзар, Вазагар" in system
    assert "Вазгар говорит" in client.user_contents()[0]
