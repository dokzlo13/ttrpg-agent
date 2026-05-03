from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

from click.testing import CliRunner

from book_ingest.__main__ import cli
from book_ingest.notes import read_chapter, tags_for_value, yaml_frontmatter
from book_ingest.summarize import _summarize_one, _update_report
from book_ingest.system_classify import _coerce_result, build_system_evidence
from book_ingest.tag import _build_tag_evidence, _clean_tags, _parse_tags, _tag_one


def _write_book(root: Path) -> Path:
    book_dir = root / "vault/library/books/sample-book"
    book_dir.mkdir(parents=True)
    (book_dir / ".ingest").mkdir()
    (book_dir / ".ingest" / "provenance.json").write_text(
        json.dumps(
            {
                "source_pdf": "imports/books/sample.pdf",
                "ingested_at": "2026-01-01T00:00:00Z",
                "system": "osr",
                "page_count": 3,
                "plan_source": "marker-toc",
            }
        ),
        encoding="utf-8",
    )
    (book_dir / ".ingest" / "report.json").write_text("{}", encoding="utf-8")
    (book_dir / "__sample-book.md").write_text("# Sample Book\n", encoding="utf-8")
    return book_dir


def test_tag_clean_tags_allows_valid_custom_obsidian_tags_and_limits():
    assert _clean_tags(["npc", "weird custom", "system/osr", "npc", "map", "1984"]) == [
        "npc",
        "weird-custom",
        "system/osr",
    ]


def test_tag_parse_filters_low_confidence_and_requires_evidence():
    tags, details = _parse_tags(
        json.dumps(
            {
                "tags": [
                    {"tag": "npc", "confidence": 0.9, "evidence": "main NPC"},
                    {"tag": "treasure", "confidence": 0.6, "evidence": "small loot"},
                    {"tag": "quest", "confidence": 0.95, "evidence": ""},
                    {"tag": "weird custom", "confidence": 0.8, "evidence": "central concept"},
                ]
            }
        )
    )
    assert tags == ["npc", "quest", "weird-custom"]
    assert details[0]["confidence"] == 0.9


def test_tag_evidence_uses_complete_small_body_even_with_summary():
    fm = {"section": "Ruined Church", "summary": "A summarized church scene."}
    body = "# Ruined Church\n\nA wraith waits by graffiti pointing to the pit."

    evidence, source = _build_tag_evidence(fm, body)

    assert source == "body"
    assert "Body (complete chapter)" in evidence
    assert "A wraith waits" in evidence
    assert "Detailed summary" not in evidence


def test_tag_evidence_uses_summary_for_long_body():
    fm = {"section": "Huge Dungeon", "summary": "Many rooms, traps, and faction clues."}
    body = "# Huge Dungeon\n\n" + ("room text\n" * 3000)

    evidence, source = _build_tag_evidence(fm, body)

    assert source == "summary"
    assert "Many rooms, traps" in evidence
    assert "room text" not in evidence


class _FakeTagClient:
    class chat:
        class completions:
            @staticmethod
            async def create(**_: Any) -> Any:
                message = type("Message", (), {"content": '{"tags": []}'})()
                choice = type("Choice", (), {"message": message})()
                return type("Response", (), {"choices": [choice]})()


def test_tag_one_keeps_empty_when_llm_returns_no_tags(tmp_path):
    path = tmp_path / "01-ruined-church.md"
    body_hash = "sha256:abc"
    path.write_text(
        yaml_frontmatter(
            {
                "book": "sample-book",
                "section": "Ruined Church",
                "section_index": 1,
                "page_start": 1,
                "page_end": 1,
                "body_hash": body_hash,
                "ingested_at": "2026-01-01T00:00:00Z",
            }
        )
        + "# Ruined Church\n\n"
        + ("A wraith waits near graffiti pointing to the pit. " * 10),
        encoding="utf-8",
    )

    result = asyncio.run(
        _tag_one(cast(Any, _FakeTagClient()), asyncio.Semaphore(1), "model", path, False)
    )

    assert result["tags"] == []
    fm, _ = read_chapter(path)
    assert fm["tags"] == []
    assert fm["tags_for"] == tags_for_value(body_hash, None)


def test_tag_missing_api_key_exits_zero(tmp_path, monkeypatch):
    book_dir = _write_book(tmp_path)
    (book_dir / "01-intro.md").write_text(
        yaml_frontmatter(
            {
                "book": "sample-book",
                "section": "Intro",
                "section_index": 1,
                "page_start": 1,
                "page_end": 1,
                "body_hash": "sha256:abc",
                "ingested_at": "2026-01-01T00:00:00Z",
                "status": "draft",
                "summary": "A village full of NPCs.",
            }
        )
        + "# Intro\n\nBody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("book_ingest.tag.find_project_root", lambda: tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = CliRunner().invoke(cli, ["tag", "sample-book", "--json"])

    assert result.exit_code == 0, result.output
    assert '"reason": "missing_api_key"' in result.output
    assert "OPENAI_API_KEY required" in result.stderr


def test_tag_manual_writes_tags_preserves_system_and_refreshes(tmp_path, monkeypatch):
    book_dir = _write_book(tmp_path)
    body_hash = "sha256:abc"
    (book_dir / "01-intro.md").write_text(
        yaml_frontmatter(
            {
                "book": "sample-book",
                "section": "Intro",
                "section_index": 1,
                "page_start": 1,
                "page_end": 1,
                "body_hash": body_hash,
                "ingested_at": "2026-01-01T00:00:00Z",
                "tags": ["book/sample-book", "system/osr", "old-tag"],
            }
        )
        + "# Intro\n\nA haunted inn scene with a central ghost.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("book_ingest.tag.find_project_root", lambda: tmp_path)

    result = CliRunner().invoke(
        cli,
        [
            "tag-manual",
            "sample-book",
            "01-intro.md",
            "--body-hash",
            body_hash,
            "--tag",
            "location",
            "--tag",
            "monster",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    fm, _ = read_chapter(book_dir / "01-intro.md")
    assert fm["tags"] == ["book/sample-book", "system/osr", "location", "monster"]
    assert fm["tags_for"] == tags_for_value(body_hash, None)
    overview = (book_dir / "__sample-book.md").read_text(encoding="utf-8")
    assert "[location, monster]" in overview
    report = json.loads((book_dir / ".ingest" / "report.json").read_text(encoding="utf-8"))
    assert report["tag_manual"]["succeeded"] == 1


def test_tag_manual_rejects_body_hash_mismatch(tmp_path, monkeypatch):
    book_dir = _write_book(tmp_path)
    (book_dir / "01-intro.md").write_text(
        yaml_frontmatter(
            {
                "book": "sample-book",
                "section": "Intro",
                "section_index": 1,
                "page_start": 1,
                "page_end": 1,
                "body_hash": "sha256:current",
                "ingested_at": "2026-01-01T00:00:00Z",
            }
        )
        + "# Intro\n\nBody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("book_ingest.tag.find_project_root", lambda: tmp_path)

    result = CliRunner().invoke(
        cli,
        [
            "tag-manual",
            "sample-book",
            "01-intro.md",
            "--body-hash",
            "sha256:old",
            "--tag",
            "location",
        ],
    )

    assert result.exit_code != 0
    assert "body_hash mismatch" in result.output


def test_summarize_long_only_skips_small_chapters(tmp_path):
    path = tmp_path / "01-intro.md"
    path.write_text(
        yaml_frontmatter(
            {
                "book": "sample-book",
                "section": "Intro",
                "section_index": 1,
                "page_start": 1,
                "page_end": 1,
                "body_hash": "sha256:abc",
                "ingested_at": "2026-01-01T00:00:00Z",
            }
        )
        + "# Intro\n\nSmall body\n",
        encoding="utf-8",
    )

    result = asyncio.run(
        _summarize_one(cast(Any, object()), asyncio.Semaphore(1), "model", path, False, True)
    )

    assert result == {"chapter": "01-intro.md", "status": "skipped", "reason": "small chapter"}


def test_summarize_report_keeps_short_history(tmp_path):
    book_dir = _write_book(tmp_path)
    _update_report(
        book_dir,
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        results=[{"chapter": "01.md", "status": "ok"}],
        duration=1.0,
    )
    _update_report(
        book_dir,
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        results=[{"chapter": "01.md", "status": "skipped"}],
        duration=0.1,
    )

    report = json.loads((book_dir / ".ingest" / "report.json").read_text(encoding="utf-8"))
    assert report["summarize"]["skipped"] == 1
    assert report["summarize_history"][-1]["succeeded"] == 1


def test_validate_preserves_followon_report_blocks(tmp_path):
    book_dir = _write_book(tmp_path)
    (book_dir / ".ingest" / "report.json").write_text(
        json.dumps({"marker": {"duration_seconds": 1}, "system_classification": {"system": "osr"}}),
        encoding="utf-8",
    )
    (book_dir / "01-intro.md").write_text(
        yaml_frontmatter(
            {
                "book": "sample-book",
                "section": "Intro",
                "section_index": 1,
                "page_start": 1,
                "page_end": 1,
                "body_hash": "sha256:abc",
                "ingested_at": "2026-01-01T00:00:00Z",
                "status": "draft",
            }
        )
        + "# Intro\n\nA substantial body long enough not to be considered tiny. " * 10,
        encoding="utf-8",
    )

    result = CliRunner().invoke(cli, ["validate", str(book_dir), "--json"])

    assert result.exit_code == 0, result.output
    assert '"system_classification"' in result.output
    report = json.loads((book_dir / ".ingest" / "report.json").read_text(encoding="utf-8"))
    assert report["system_classification"] == {"system": "osr"}


def test_refresh_overview_collects_summary_and_tags(tmp_path, monkeypatch):
    book_dir = _write_book(tmp_path)
    body_hash = "sha256:abc"
    (book_dir / "01-intro.md").write_text(
        yaml_frontmatter(
            {
                "book": "sample-book",
                "section": "Intro",
                "section_index": 1,
                "page_start": 1,
                "page_end": 1,
                "body_hash": body_hash,
                "ingested_at": "2026-01-01T00:00:00Z",
                "status": "draft",
                "summary": "Introduces the haunted inn.",
                "summary_for": body_hash,
                "tags": ["location", "lore"],
                "tags_for": tags_for_value(body_hash, "Introduces the haunted inn."),
            }
        )
        + "# Intro\n\nBody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("book_ingest.__main__.find_project_root", lambda: tmp_path)

    result = CliRunner().invoke(cli, ["refresh-overview", "sample-book"])

    assert result.exit_code == 0, result.output
    overview = (tmp_path / "vault/library/books/sample-book/__sample-book.md").read_text(
        encoding="utf-8"
    )
    assert (
        "[[library/books/sample-book/01-intro|Intro]] — p. 1 — Introduces the haunted inn. — [location, lore]"
        in overview
    )
    assert "system: osr" in overview


def test_classify_system_keeps_multiple_obsidian_safe_systems():
    result = _coerce_result({"systems": ["OSR", "LotFP", "D&D 3.5e"], "confidence": 2})
    assert result["system"] == "osr"
    assert result["systems"] == ["osr", "lotfp", "d-d-3-5e"]
    assert result["confidence"] == 1.0


def test_build_system_evidence_uses_front_and_back_chapters(tmp_path):
    book_dir = _write_book(tmp_path)
    for idx, title in [(1, "Front"), (2, "Middle"), (3, "Back")]:
        (book_dir / f"{idx:02d}-{title.lower()}.md").write_text(
            yaml_frontmatter(
                {
                    "book": "sample-book",
                    "section": title,
                    "section_index": idx,
                    "page_start": idx,
                    "page_end": idx,
                    "body_hash": f"sha256:{idx}",
                    "ingested_at": "2026-01-01T00:00:00Z",
                    "status": "draft",
                }
            )
            + f"# {title}\n\n{title} evidence\n",
            encoding="utf-8",
        )

    evidence = build_system_evidence(book_dir)

    assert "Front evidence" in evidence
    assert "Back evidence" in evidence


def test_classify_system_missing_api_key_exits_zero(tmp_path, monkeypatch):
    book_dir = _write_book(tmp_path)
    (book_dir / "01-front.md").write_text(
        yaml_frontmatter(
            {
                "book": "sample-book",
                "section": "Front",
                "section_index": 1,
                "page_start": 1,
                "page_end": 1,
                "body_hash": "sha256:abc",
                "ingested_at": "2026-01-01T00:00:00Z",
                "status": "draft",
            }
        )
        + "# Front\n\nSome license page.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("book_ingest.system_classify.find_project_root", lambda: tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = CliRunner().invoke(cli, ["classify-system", "sample-book", "--json"])

    assert result.exit_code == 0, result.output
    assert '"reason": "missing_api_key"' in result.output
    assert "OPENAI_API_KEY required" in result.stderr


def test_summarize_missing_api_key_exits_zero(tmp_path, monkeypatch):
    book_dir = _write_book(tmp_path)
    (book_dir / "01-intro.md").write_text(
        yaml_frontmatter(
            {
                "book": "sample-book",
                "section": "Intro",
                "section_index": 1,
                "page_start": 1,
                "page_end": 1,
                "body_hash": "sha256:abc",
                "ingested_at": "2026-01-01T00:00:00Z",
                "status": "draft",
            }
        )
        + "# Intro\n\nBody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("book_ingest.summarize.find_project_root", lambda: tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = CliRunner().invoke(cli, ["summarize", "sample-book", "--json"])

    assert result.exit_code == 0, result.output
    assert '"reason": "missing_api_key"' in result.output
    assert "OPENAI_API_KEY required" in result.stderr
