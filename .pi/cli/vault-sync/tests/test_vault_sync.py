from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

import vault_sync.__main__ as vs


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _patch_roots(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "imports" / "source-vault"
    vault = tmp_path / "vault" / "notes"
    source.mkdir(parents=True)
    vault.mkdir(parents=True)
    monkeypatch.setattr(vs, "ROOT", tmp_path)
    monkeypatch.setattr(vs, "SOURCE_ROOT", source)
    monkeypatch.setattr(vs, "VAULT_ROOT", vault)
    return source, vault


def test_inspect_reports_facts_without_type_or_destination_guessing(monkeypatch, tmp_path):
    source, _vault = _patch_roots(monkeypatch, tmp_path)
    note = source / "messy" / "Lord Blackthorne.md"
    _write(source / "messy" / "portrait.png", "fake image bytes")
    _write(
        note,
        """---\ntags: [villain, noble]\naliases: [the old wolf]\n---\n# Lord Blackthorne\n\nSee [[Dunemark]] and ![[portrait.png]].\n\n## Secrets\nOld campaign note.\n""",
    )

    result = CliRunner().invoke(vs.main, ["inspect", str(note)])

    assert result.exit_code == 0, result.output
    assert "source: imports/source-vault/messy/Lord Blackthorne.md" in result.output
    assert "title: Lord Blackthorne" in result.output
    assert "frontmatter_keys:" in result.output
    assert "wikilinks:" in result.output
    assert "target: Dunemark" in result.output
    assert "embeds:" in result.output
    assert "resolves_to_file: imports/source-vault/messy/portrait.png" in result.output
    assert "detected_type" not in result.output
    assert "destination" not in result.output


def test_copy_requires_llm_chosen_destination_and_preserves_markdown_exactly(monkeypatch, tmp_path):
    source, vault = _patch_roots(monkeypatch, tmp_path)
    note = source / "random" / "Dunemark.md"
    original = """---\nold-key: old-value\n---\n# Dunemark\n\nLinks to [[Lord Blackthorne]].\n"""
    _write(note, original)

    result = CliRunner().invoke(vs.main, ["copy", str(note), str(vault / "locations" / "dunemark.md")])

    assert result.exit_code == 0, result.output
    dest = vault / "locations" / "dunemark.md"
    assert dest.read_text(encoding="utf-8") == original
    assert note.read_text(encoding="utf-8") == original
    assert "source: imports/source-vault/random/Dunemark.md" in result.output
    assert "destination: vault/notes/locations/dunemark.md" in result.output


def test_copy_refuses_source_outside_archive(monkeypatch, tmp_path):
    _source, vault = _patch_roots(monkeypatch, tmp_path)
    outside = tmp_path / "outside.md"
    _write(outside, "# Outside\n")

    result = CliRunner().invoke(vs.main, ["copy", str(outside), str(vault / "locations" / "outside.md")])

    assert result.exit_code != 0
    assert "source file must live under imports/source-vault/" in result.output


def test_copy_refuses_destination_outside_vault(monkeypatch, tmp_path):
    source, _vault = _patch_roots(monkeypatch, tmp_path)
    note = source / "note.md"
    _write(note, "# Note\n")

    result = CliRunner().invoke(vs.main, ["copy", str(note), str(tmp_path / "outside.md")])

    assert result.exit_code != 0
    assert "destination file must live under vault/notes/" in result.output


def test_copy_refuses_overwrite(monkeypatch, tmp_path):
    source, vault = _patch_roots(monkeypatch, tmp_path)
    note = source / "note.md"
    dest = vault / "npcs" / "note.md"
    _write(note, "# New\n")
    _write(dest, "# Existing\n")

    result = CliRunner().invoke(vs.main, ["copy", str(note), str(dest)])

    assert result.exit_code != 0
    assert "destination already exists: vault/notes/npcs/note.md" in result.output
    assert dest.read_text(encoding="utf-8") == "# Existing\n"


def test_copy_attachments_copies_resolvable_local_files_without_rewriting_note(monkeypatch, tmp_path):
    source, vault = _patch_roots(monkeypatch, tmp_path)
    note = source / "messy" / "Moon Blade.md"
    original = "# Moon Blade\n\n![[blade.png]]\n\n![Map](maps/room.png)\n"
    _write(note, original)
    _write(source / "messy" / "blade.png", "fake blade")
    _write(source / "messy" / "maps" / "room.png", "fake map")

    result = CliRunner().invoke(
        vs.main,
        ["copy", str(note), str(vault / "items" / "moon-blade.md"), "--copy-attachments"],
    )

    assert result.exit_code == 0, result.output
    dest = vault / "items" / "moon-blade.md"
    assert dest.read_text(encoding="utf-8") == original
    assert (vault / "items" / "blade.png").read_text(encoding="utf-8") == "fake blade"
    assert (vault / "items" / "maps" / "room.png").read_text(encoding="utf-8") == "fake map"
    assert "vault/notes/items/blade.png" in result.output
    assert "vault/notes/items/maps/room.png" in result.output


def test_dry_run_does_not_write_note_or_attachments(monkeypatch, tmp_path):
    source, vault = _patch_roots(monkeypatch, tmp_path)
    note = source / "messy" / "Moon Blade.md"
    _write(note, "# Moon Blade\n\n![[blade.png]]\n")
    _write(source / "messy" / "blade.png", "fake blade")

    result = CliRunner().invoke(
        vs.main,
        ["copy", str(note), str(vault / "items" / "moon-blade.md"), "--copy-attachments", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert "destination: vault/notes/items/moon-blade.md" in result.output
    assert not (vault / "items" / "moon-blade.md").exists()
    assert not (vault / "items" / "blade.png").exists()
