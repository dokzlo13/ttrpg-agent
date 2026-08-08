"""Skip-if-done is keyed on content, never on a file merely being there."""

from __future__ import annotations

import json
from pathlib import Path

from session_ingest.provenance import (
    CompositeKey,
    Provenance,
    downstream_of,
    sha256_bytes,
    sha256_file,
    sha256_text,
    short_digest,
)


def _key(**overrides) -> CompositeKey:
    base: dict = {
        "dataset_digest": "sha256:aaa",
        "lexicon_digest": "sha256:bbb",
        "speakers_digest": "sha256:ccc",
        "prompt_version": "extract/1",
        "model": "gpt-x",
        "knobs": {"window_minutes": 15},
    }
    base.update(overrides)
    return CompositeKey(**base)


def test_digests_are_content_addressed(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_text("hello", encoding="utf-8")
    assert sha256_file(target) == sha256_text("hello") == sha256_bytes(b"hello")
    assert sha256_file(target).startswith("sha256:")

    # Touching the file changes mtime but not identity.
    target.touch()
    assert sha256_file(target) == sha256_text("hello")


def test_short_digest_is_the_status_line_form() -> None:
    assert short_digest("sha256:0123456789abcdef") == "sha256:0123456789ab…"
    assert short_digest(None) == "none"


def test_fingerprint_is_stable_and_order_independent() -> None:
    left = CompositeKey(dataset_digest="d", knobs={"a": 1, "b": 2})
    right = CompositeKey(dataset_digest="d", knobs={"b": 2, "a": 1})
    assert left.fingerprint() == right.fingerprint()
    assert left.fingerprint() != CompositeKey(dataset_digest="d", knobs={"a": 2}).fingerprint()


def test_every_key_component_invalidates(tmp_path: Path) -> None:
    provenance = Provenance(tmp_path / "provenance.json")
    output = tmp_path / "out.json"
    output.write_text("{}", encoding="utf-8")
    provenance.mark_done("extract", _key(), outputs=[output])

    assert provenance.should_skip("extract", _key()) is True
    for field in ("dataset_digest", "lexicon_digest", "speakers_digest", "prompt_version", "model"):
        assert provenance.should_skip("extract", _key(**{field: "changed"})) is False, field
    assert provenance.should_skip("extract", _key(knobs={"window_minutes": 20})) is False


def test_force_and_deleted_outputs_deny_the_skip(tmp_path: Path) -> None:
    provenance = Provenance(tmp_path / "provenance.json")
    output = tmp_path / "out.json"
    output.write_text("{}", encoding="utf-8")
    provenance.mark_done("render", _key(), outputs=[output])

    assert provenance.should_skip("render", _key()) is True
    assert provenance.should_skip("render", _key(), force=True) is False

    output.unlink()
    assert provenance.should_skip("render", _key()) is False, (
        "a recorded artifact that is gone must be rebuilt"
    )


def test_existence_alone_is_never_a_skip(tmp_path: Path) -> None:
    """The negative case of CONTRACT rule 3: an output present with no record."""
    provenance = Provenance(tmp_path / "provenance.json")
    (tmp_path / "record.json").write_text("{}", encoding="utf-8")
    assert provenance.should_skip("record", _key()) is False
    assert provenance.skip_reason("record", _key()) == "no previous run recorded"


def test_round_trip_through_disk(tmp_path: Path) -> None:
    path = tmp_path / "provenance.json"
    provenance = Provenance(path)
    provenance.mark_done("qa", _key(), outputs=[tmp_path], extra={"run": 2})

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == "ttrpg.session-provenance/1"
    assert payload["stages"]["qa"]["composite_key"]["prompt_version"] == "extract/1"
    assert payload["stages"]["qa"]["extra"]["run"] == 2

    reloaded = Provenance.load(path)
    assert reloaded.should_skip("qa", _key()) is True
    record = reloaded.record("qa")
    assert record is not None
    assert record.extra["run"] == 2


def test_relative_outputs_resolve_against_the_session_root(tmp_path: Path) -> None:
    provenance = Provenance(tmp_path / "provenance.json")
    (tmp_path / "qa.json").write_text("{}", encoding="utf-8")
    provenance.mark_done("qa", _key(), outputs=["qa.json"])
    assert provenance.should_skip("qa", _key()) is True
    assert provenance.should_skip("qa", _key(), root=tmp_path / "elsewhere") is False


def test_invalidate_drops_only_named_stages(tmp_path: Path) -> None:
    provenance = Provenance(tmp_path / "provenance.json")
    for stage in ("adopt", "qa", "render", "extract"):
        provenance.mark_done(stage, _key(), outputs=[])
    dropped = provenance.invalidate(downstream_of("adopt"))
    assert set(dropped) == {"qa", "render", "extract"}
    assert provenance.record("adopt") is not None
    assert provenance.record("render") is None


def test_downstream_order_follows_the_chain() -> None:
    assert downstream_of("qa")[:2] == ("render", "segment")
    assert downstream_of("glossary") == ()
    assert downstream_of("nonsense") == ()
