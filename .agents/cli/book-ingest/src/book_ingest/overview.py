from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

from .models import SectionPlan
from .notes import clean_obsidian_tags, read_chapter, render_book_overview


def overview_filename(book_slug: str) -> str:
    return f"__{book_slug}.md"


def _chapter_slug_from_stem(stem: str) -> str:
    match = re.match(r"^\d+-(.+)$", stem)
    return match.group(1) if match else stem


def _read_frontmatter_only(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    data = yaml.safe_load(text[4:end]) or {}
    return data if isinstance(data, dict) else {}


def scan_chapters(book_dir: Path) -> tuple[list[SectionPlan], dict[str, str], dict[str, list[str]]]:
    plans: list[SectionPlan] = []
    summaries: dict[str, str] = {}
    tags: dict[str, list[str]] = {}
    for path in sorted(p for p in book_dir.glob("*.md") if not p.name.startswith((".", "__"))):
        fm = _read_frontmatter_only(path)
        if not fm:
            continue
        try:
            raw_index = fm.get("section_index")
            if raw_index is None:
                raise TypeError
            index = int(raw_index)
        except (TypeError, ValueError):
            match = re.match(r"^(\d+)-", path.stem)
            if not match:
                continue
            index = int(match.group(1))
        title = str(fm.get("section") or path.stem)
        stem = path.stem
        slug = _chapter_slug_from_stem(stem)
        try:
            page_start = max(int(fm.get("page_start", 1)) - 1, 0)
            page_end = max(int(fm.get("page_end", page_start + 1)) - 1, page_start)
        except (TypeError, ValueError):
            page_start = 0
            page_end = 0
        plans.append(
            SectionPlan(
                index=index,
                title=title,
                slug=slug,
                page_start=page_start,
                page_end=page_end,
                source="frontmatter",
            )
        )
        summary = fm.get("summary")
        if isinstance(summary, str) and summary.strip():
            summaries[stem] = summary.strip()
        clean = [
            tag
            for tag in clean_obsidian_tags(fm.get("tags"))
            if tag != "book-index" and not tag.startswith(("book/", "system/"))
        ]
        if clean:
            tags[stem] = clean
    plans.sort(key=lambda p: p.index)
    return plans, summaries, tags


def _book_title_from_existing(overview_path: Path, slug: str) -> str:
    if overview_path.exists():
        for line in overview_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("# "):
                return line[2:].strip() or slug
    return slug.replace("-", " ").title()


def _overview_extras(book_dir: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    ingest_path = book_dir / ".ingest" / "provenance.json"
    if ingest_path.exists():
        try:
            loaded = json.loads(ingest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:
            data = {}
    return {
        "source_ref": str(data.get("source_pdf") or "unknown"),
        "ingested_at": str(data.get("ingested_at") or "1970-01-01T00:00:00Z"),
        "system": data.get("systems") or str(data.get("system") or "unknown"),
        "page_count": int(data.get("page_count") or 0),
        "plan_source": str(data.get("plan_source") or "frontmatter"),
    }


def refresh_overview(book_dir: Path) -> Path:
    if not book_dir.is_dir():
        raise FileNotFoundError(f"no book directory at {book_dir}")
    slug = book_dir.name
    overview_path = book_dir / overview_filename(slug)
    plans, summaries, tags = scan_chapters(book_dir)
    if not plans:
        raise ValueError(f"no chapter markdown files with frontmatter under {book_dir}")
    rendered = render_book_overview(
        book_title=_book_title_from_existing(overview_path, slug),
        book_slug=slug,
        plans=plans,
        summaries=summaries,
        tags=tags,
        **_overview_extras(book_dir),
    )
    tmp = overview_path.with_suffix(overview_path.suffix + ".tmp")
    tmp.write_text(rendered, encoding="utf-8")
    os.replace(tmp, overview_path)
    return overview_path


def read_summary_inputs(path: Path) -> tuple[dict[str, Any], str, str]:
    """Return frontmatter, body text after frontmatter, and full chapter text."""
    full_text = path.read_text(encoding="utf-8", errors="replace")
    fm, body_text = read_chapter(path)
    return fm, body_text, full_text
