"""The two hand-maintained vault files: tolerant about optional keys, strict about meaning."""

from __future__ import annotations

from pathlib import Path

import pytest

from session_ingest.errors import VaultFileError
from session_ingest.vaultfiles import load_lexicon, load_speakers

from .conftest import LEXICON_YAML, SPEAKERS_YAML


def test_absent_files_are_normal(tmp_path: Path) -> None:
    lexicon = load_lexicon(tmp_path / "_lexicon.yaml")
    speakers = load_speakers(tmp_path / "_speakers.yaml")
    assert (lexicon.present, lexicon.digest, lexicon.terms) == (False, None, ())
    assert (speakers.present, speakers.digest, dict(speakers.by_user_id)) == (False, None, {})


def test_lexicon_parses_and_digests(tmp_path: Path) -> None:
    path = tmp_path / "_lexicon.yaml"
    path.write_text(LEXICON_YAML, encoding="utf-8")
    lexicon = load_lexicon(path)
    assert lexicon.present is True
    assert lexicon.digest is not None and lexicon.digest.startswith("sha256:")
    assert [term.id for term in lexicon.terms] == ["vagzar", "kilverin", "oswald"]
    assert [term.id for term in lexicon.active_terms()] == ["vagzar", "kilverin"]

    vagzar = lexicon.by_id()["vagzar"]
    assert vagzar.priority == 10
    assert vagzar.canonical_forms() == ("Вазгар",), (
        "display_ru equal to canonical is not counted twice"
    )
    assert vagzar.variant_forms() == ("Вагзар", "Вазагар")


def test_a_variant_duplicating_the_canonical_is_dropped(tmp_path: Path) -> None:
    path = tmp_path / "_lexicon.yaml"
    path.write_text(
        "terms:\n  - id: t\n    canonical: Вазгар\n    variants: [Вазгар, Вагзар]\n",
        encoding="utf-8",
    )
    term = load_lexicon(path).terms[0]
    assert term.variant_forms() == ("Вагзар",)


def test_optional_lexicon_keys_may_be_absent(tmp_path: Path) -> None:
    path = tmp_path / "_lexicon.yaml"
    path.write_text("terms:\n  - id: t\n    canonical: X\n", encoding="utf-8")
    term = load_lexicon(path).terms[0]
    assert (term.display_ru, term.variants, term.kind, term.active, term.priority) == (
        None,
        (),
        None,
        True,
        0,
    )


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("terms:\n  - canonical: X\n", "`id` is required"),
        ("terms:\n  - id: t\n", "`canonical` is required"),
        ("terms:\n  - id: t\n    canonical: X\n  - id: t\n    canonical: Y\n", "duplicate term id"),
        ("terms: 3\n", "`terms` must be a list"),
        ("terms:\n  - id: t\n    canonical: X\n    variants: nope\n", "`variants` must be a list"),
    ],
)
def test_meaningful_lexicon_errors_are_loud(tmp_path: Path, text: str, fragment: str) -> None:
    path = tmp_path / "_lexicon.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(VaultFileError) as excinfo:
        load_lexicon(path)
    assert fragment in str(excinfo.value)


def test_speakers_mapping(tmp_path: Path) -> None:
    path = tmp_path / "_speakers.yaml"
    path.write_text(SPEAKERS_YAML, encoding="utf-8")
    speakers = load_speakers(path)
    alice = speakers.get("u-alice")
    assert alice is not None
    assert (alice.player, alice.character, alice.role) == ("Alice", "Морвика", "pc")
    assert alice.display() == "Морвика"
    assert speakers.get("u-bob") is None
    assert speakers.unmapped(["u-alice", "u-bob", None, "u-bob"]) == ["u-bob"]


def test_speaker_display_never_invents_a_name(tmp_path: Path) -> None:
    path = tmp_path / "_speakers.yaml"
    path.write_text('speakers:\n  "u-x": {}\n', encoding="utf-8")
    assert load_speakers(path).by_user_id["u-x"].display() == "u-x"


def test_invalid_yaml_is_reported_with_the_path(tmp_path: Path) -> None:
    path = tmp_path / "_lexicon.yaml"
    path.write_text("terms: [\n", encoding="utf-8")
    with pytest.raises(VaultFileError) as excinfo:
        load_lexicon(path)
    assert str(path) in str(excinfo.value)


def test_morph_defaults_to_false_and_is_parsed_when_present(tmp_path: Path) -> None:
    """The flag is opt-in and, like every other optional key, tolerantly read."""
    path = tmp_path / "_lexicon.yaml"
    path.write_text(
        "terms:\n"
        "  - id: plain\n    canonical: Морвика\n"
        "  - id: opted-in\n    canonical: Кельверин\n    morph: true\n",
        encoding="utf-8",
    )
    by_id = load_lexicon(path).by_id()
    assert by_id["plain"].morph is False
    assert by_id["opted-in"].morph is True
    assert by_id["opted-in"].to_dict()["morph"] is True


def test_a_non_boolean_morph_is_reported_with_the_term(tmp_path: Path) -> None:
    path = tmp_path / "_lexicon.yaml"
    path.write_text(
        "terms:\n  - id: x\n    canonical: Y\n    morph: yes-please\n", encoding="utf-8"
    )
    with pytest.raises(VaultFileError) as excinfo:
        load_lexicon(path)
    assert "terms[0]" in str(excinfo.value)
