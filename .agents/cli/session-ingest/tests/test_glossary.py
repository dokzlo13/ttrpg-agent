"""``glossary`` — the verbatim-observation guardrail and the append-only merge."""

from __future__ import annotations

from typing import Any

from session_ingest import glossary as glossary_mod
from session_ingest.glossary import Proposal, merge_lexicon, verify_variants
from session_ingest.vaultfiles import load_lexicon

from .conftest import LEXICON_YAML, SESSION_ID, Workspace
from .fakes import FakeClient, adopt, constant, make_config

#: What the fixture dataset actually says (conftest.TERM_TEXT).
OBSERVED = "Вазгар говорит. вазгар кивает. Вагзар идёт. Вазагар молчит. Освальд Стоун здесь."


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
                term("stoun", "Стоун", "Освальд Стоун", existing=False),
                term("vagzar", "Вазгар", "Вагзар"),
            )
        )
    )
    payload = _run(workspace, client, monkeypatch)

    assert payload["status"] == "ok"
    assert payload["lexicon_changed"] is True
    assert payload["terms_extended"] == ["oswald"]
    assert payload["terms_added"] == ["stoun"]
    assert payload["variants_added"] == 2
    assert payload["biasing_files"] == "deferred_to_plan"

    rejected = {(row["variant"], row["reason"]) for row in payload["variants_rejected"]}
    assert ("Никогда не звучало", "not observed verbatim in the session text") in rejected
    assert ("Вагзар", "already recorded for this term") in rejected

    lexicon = load_lexicon(workspace.roots.lexicon_file)
    by_id = lexicon.by_id()
    assert by_id["oswald"].variants == ("Освалд", "Освальд Стоун")
    assert by_id["vagzar"].variants == ("Вагзар", "Вазагар"), "untouched, in order"
    assert by_id["stoun"].source == f"glossary/{SESSION_ID}"
    assert by_id["stoun"].priority == 3
    assert by_id["stoun"].active is True

    assert any("dropped by the guardrail" in warning for warning in payload["warnings"])
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
