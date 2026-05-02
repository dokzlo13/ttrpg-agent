from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml

from .models import SectionPlan

_PAGE_MARKER = re.compile(r"^\{(\d+)\}-+\s*$", re.MULTILINE)
_IMAGE_LINK = re.compile(r"!\[([^\]]*)\]\((_page_\d+_(?:Picture|Figure)_\d+\.\w+)\)")
_PAGE_ANCHOR_SPAN = re.compile(r"<span\s+id=\"page-\d+-\d+\"></span>\s*", re.IGNORECASE)
_HEADING_ORNAMENT = re.compile(r"[·•●◆◇■□▪▫\-\u2013\u2014*~`#]+")


def yaml_frontmatter(data: dict) -> str:
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{body}\n---\n\n"


def section_filename(plan: SectionPlan) -> str:
    return f"{plan.index:02d}-{plan.slug}.md"


def split_paginated_markdown(text: str) -> dict[int, tuple[int, int]]:
    """Return ``{page_index: (body_start_offset, body_end_offset)}`` covering everything after each page marker."""
    matches = list(_PAGE_MARKER.finditer(text))
    out: dict[int, tuple[int, int]] = {}
    for i, m in enumerate(matches):
        page = int(m.group(1))
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[page] = (body_start, body_end)
    return out


def slice_pages(text: str, page_start: int, page_end: int) -> str:
    pages = split_paginated_markdown(text)
    chunks: list[str] = []
    for p in range(page_start, page_end + 1):
        rng = pages.get(p)
        if not rng:
            continue
        chunk = text[rng[0] : rng[1]]
        chunks.append(chunk.strip("\n"))
    body = ("\n\n".join(c for c in chunks if c)).strip()
    return _PAGE_ANCHOR_SPAN.sub("", body)


def rewrite_image_links(body: str, image_subdir: str = "images") -> str:
    return _IMAGE_LINK.sub(lambda m: f"![{m.group(1)}]({image_subdir}/{m.group(2)})", body)


def referenced_image_names(body: str) -> set[str]:
    return {m.group(2) for m in _IMAGE_LINK.finditer(body)}


def _normalize_heading_text(s: str) -> str:
    s = _HEADING_ORNAMENT.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.lower()


def strip_leading_heading(body: str, title: str) -> str:
    """Drop a leading heading (H1-H6) that duplicates the section title."""
    lines = body.splitlines()
    target = _normalize_heading_text(title)
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        m = re.match(r"^#{1,6}\s+(.*?)\s*$", line)
        if not m:
            return body
        if _normalize_heading_text(m.group(1)) == target:
            return "\n".join(lines[i + 1 :]).lstrip("\n")
        return body
    return body


def copy_referenced_images(
    raw_images_dir: Path,
    target_images_dir: Path,
    referenced: set[str],
) -> tuple[set[str], set[str]]:
    """Copy referenced images. Returns (copied, missing)."""
    target_images_dir.mkdir(parents=True, exist_ok=True)
    copied: set[str] = set()
    missing: set[str] = set()
    for name in referenced:
        src = raw_images_dir / name
        if not src.exists():
            # marker may write images at the artifacts root rather than inside a subdir
            alt = list(raw_images_dir.parent.rglob(name))
            if alt:
                src = alt[0]
            else:
                missing.add(name)
                continue
        dest = target_images_dir / name
        if not dest.exists():
            shutil.copy2(src, dest)
        copied.add(name)
    return copied, missing


def render_section_note(
    *,
    plan: SectionPlan,
    plans: list[SectionPlan],
    body: str,
    book_title: str,
    book_slug: str,
    source_ref: str,
    ingested_at: str,
) -> str:
    page_label = (
        f"{plan.page_start + 1}"
        if plan.page_start == plan.page_end
        else f"{plan.page_start + 1}–{plan.page_end + 1}"
    )
    fm = {
        "type": "book-chunk",
        "source": source_ref,
        "book": book_slug,
        "section": plan.title,
        "section_index": plan.index,
        "page_start": plan.page_start + 1,
        "page_end": plan.page_end + 1,
        "ingested_at": ingested_at,
        "tags": ["book-chunk", book_slug],
        "status": "draft",
    }
    nav = [f"# {plan.title}", "", f"Book: [[_book|{book_title}]]"]
    prev_plan = next((p for p in plans if p.index == plan.index - 1), None)
    next_plan = next((p for p in plans if p.index == plan.index + 1), None)
    if prev_plan is not None:
        nav.append(f"Previous: [[{Path(section_filename(prev_plan)).stem}|{prev_plan.title}]]")
    if next_plan is not None:
        nav.append(f"Next: [[{Path(section_filename(next_plan)).stem}|{next_plan.title}]]")
    nav.extend([f"Pages: {page_label}", "", "## Text", "", ""])
    rendered_body = strip_leading_heading(body, plan.title).strip()
    if not rendered_body:
        rendered_body = "_(no text extracted for this page range)_"
    return yaml_frontmatter(fm) + "\n".join(nav) + rendered_body + "\n"


def render_book_index(
    *,
    book_title: str,
    book_slug: str,
    plans: list[SectionPlan],
    source_ref: str,
    ingested_at: str,
    system: str,
    page_count: int,
    plan_source: str,
) -> str:
    fm = {
        "type": "book-index",
        "source": source_ref,
        "book": book_slug,
        "created": ingested_at[:10],
        "page_count": page_count,
        "section_count": len(plans),
        "plan_source": plan_source,
        "system": system,
        "tags": ["book-index", book_slug],
        "status": "draft",
    }
    lines = [
        f"# {book_title}",
        "",
        f"Source: `{source_ref}`",
        f"Pages: {page_count}",
        f"Sections: {len(plans)} (planned via {plan_source})",
        "",
        "## Sections",
        "",
    ]
    for plan in plans:
        page_label = (
            f"p. {plan.page_start + 1}"
            if plan.page_start == plan.page_end
            else f"pp. {plan.page_start + 1}–{plan.page_end + 1}"
        )
        stem = Path(section_filename(plan)).stem
        lines.append(f"- [[{stem}|{plan.title}]] — {page_label}")
    return yaml_frontmatter(fm) + "\n".join(lines) + "\n"
