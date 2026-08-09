"""``registry`` — exact canonical-name resolution, and the ways it refuses to guess."""

from __future__ import annotations

import pytest

from session_ingest.errors import VaultFileError
from session_ingest.registry import (
    load_entity_registry,
    normalize_name,
)

from .conftest import Workspace


def _loaded(workspace: Workspace, text: str | None = None):
    if text is None:
        workspace.write_entity_registry()
    else:
        workspace.write_entity_registry(text)
    return load_entity_registry(workspace.roots.entity_registry_file)


def _block(entities: str) -> str:
    return (
        f"# Реестр\n\nprose\n\n```yaml\nschema: ttrpg.entity-registry/1\nentities:\n{entities}```\n"
    )


# ------------------------------------------------------------------ normalising


def test_normalize_collapses_case_and_whitespace() -> None:
    assert normalize_name("  Морвика   Тень ") == "морвика тень"
    assert normalize_name("KOSHIKAWA") == normalize_name("koshikawa")


def test_normalize_keeps_punctuation() -> None:
    """«Зак-Зак» and «Зак Зак» stay different strings; merging them is an owner decision."""
    assert normalize_name("Зак-Зак") != normalize_name("Зак Зак")


# ------------------------------------------------------------------ loading


def test_absent_file_is_normal_and_resolves_nothing(workspace: Workspace) -> None:
    registry = load_entity_registry(workspace.roots.entity_registry_file)
    assert registry.present is False
    assert registry.digest is None
    assert registry.entries == ()
    assert registry.resolve("Морвика") is None


def test_the_yaml_block_is_found_inside_the_note(workspace: Workspace) -> None:
    registry = _loaded(workspace)
    assert registry.present is True
    assert registry.digest is not None
    assert {e.slug for e in registry.entries} == {"morvika", "vagzar", "kilverin"}


def test_a_note_without_a_yaml_block_is_an_error(workspace: Workspace) -> None:
    with pytest.raises(VaultFileError, match="no ```yaml block"):
        _loaded(workspace, "# Реестр\n\nвсё на словах\n")


def test_two_yaml_blocks_are_an_error_rather_than_a_guess(workspace: Workspace) -> None:
    text = _block('  - {slug: a, ru: "А"}\n') + "\n```yaml\nentities: []\n```\n"
    with pytest.raises(VaultFileError, match="2 ```yaml blocks"):
        _loaded(workspace, text)


def test_an_unterminated_block_is_an_error(workspace: Workspace) -> None:
    with pytest.raises(VaultFileError, match="never closed"):
        _loaded(workspace, "```yaml\nentities: []\n")


def test_unknown_schema_is_refused(workspace: Workspace) -> None:
    text = "```yaml\nschema: ttrpg.entity-registry/99\nentities: []\n```\n"
    with pytest.raises(VaultFileError, match="unknown registry schema"):
        _loaded(workspace, text)


# ------------------------------------------------------------------ validation


def test_slug_must_be_kebab_case(workspace: Workspace) -> None:
    with pytest.raises(VaultFileError, match="kebab-case"):
        _loaded(workspace, _block('  - {slug: "Not A Slug", ru: "А"}\n'))


def test_unknown_kind_is_refused(workspace: Workspace) -> None:
    with pytest.raises(VaultFileError, match="`kind` must be one of"):
        _loaded(workspace, _block('  - {slug: a, kind: spaceship, ru: "А"}\n'))


def test_duplicate_slug_is_refused(workspace: Workspace) -> None:
    text = _block('  - {slug: a, ru: "А"}\n  - {slug: a, ru: "Б"}\n')
    with pytest.raises(VaultFileError, match="duplicate slug"):
        _loaded(workspace, text)


def test_one_spelling_cannot_resolve_to_two_entities(workspace: Workspace) -> None:
    """Otherwise which one wins would depend on file order — a silent wrong answer."""
    text = _block('  - {slug: a, ru: "Лили"}\n  - {slug: b, ru: "Лилиан", aliases: ["Лили"]}\n')
    with pytest.raises(VaultFileError, match="already claimed by"):
        _loaded(workspace, text)


def test_an_entry_with_no_matchable_name_is_refused(workspace: Workspace) -> None:
    with pytest.raises(VaultFileError, match="at least one of"):
        _loaded(workspace, _block("  - {slug: a, kind: npc}\n"))


# ------------------------------------------------------------------ resolving


def test_resolves_ru_en_and_aliases(workspace: Workspace) -> None:
    registry = _loaded(workspace)
    for spelling in ("Морвика", "Morvika", "Марвика", "  морвика  "):
        entry = registry.resolve(spelling)
        assert entry is not None, spelling
        assert entry.slug == "morvika"


def test_resolves_the_composite_forms_extract_emits(workspace: Workspace) -> None:
    """``extract`` writes canonical names as ``"Koshikawa (Кошикава)"`` about half the time."""
    registry = _loaded(workspace)
    assert registry.resolve("Morvika (Морвика)").slug == "morvika"
    assert registry.resolve("Морвика (Morvika)").slug == "morvika"


def test_composites_cover_aliases_not_just_the_primary_pair(workspace: Workspace) -> None:
    """The real corpus emitted ``"Klein Forest (Клейн)"`` — en paired with an *alias*."""
    registry = _loaded(workspace)
    assert registry.resolve("Morvika (Марвика)").slug == "morvika"
    assert registry.resolve("Марвика (Morvika)").slug == "morvika"


def test_a_composite_of_unrelated_names_still_does_not_match(workspace: Workspace) -> None:
    """Cross-product is within one entry only; it never joins two entities."""
    registry = _loaded(workspace)
    assert registry.resolve("Morvika (Вазгар)") is None


def test_an_unknown_name_stays_unresolved(workspace: Workspace) -> None:
    """The whole point: «священник» is not silently attached to somebody plausible."""
    registry = _loaded(workspace)
    assert registry.resolve("священник") is None
    assert registry.resolve("Морвик") is None  # near-miss, still no fuzzy match
    assert registry.resolve("") is None
    assert registry.resolve(None) is None


def test_vault_note_comes_from_the_registry(workspace: Workspace) -> None:
    registry = _loaded(workspace)
    assert registry.resolve("Вазгар").note == "npcs/vagzar.md"
    assert registry.resolve("Кильверин").note is None


def test_unresolved_lists_distinct_names_in_first_seen_order(workspace: Workspace) -> None:
    registry = _loaded(workspace)
    missing = registry.unresolved(
        ["Морвика", "священник", None, "Вазгар", "  священник ", "девочка", ""]
    )
    assert missing == ["священник", "девочка"]
