from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import yaml

from .models import SectionPlan

_PAGE_MARKER = re.compile(r"^\{(\d+)\}-+\s*$", re.MULTILINE)
_IMAGE_LINK = re.compile(r"!\[([^\]]*)\]\((?:\./)?(_page_\d+_(?:Picture|Figure)_\d+\.\w+)\)")
_LOCAL_IMAGE_LINK = re.compile(r"!\[([^\]]*)\]\(images/(_page_\d+_(?:Picture|Figure)_\d+\.\w+)\)")
_IMAGE_DESCRIPTION = re.compile(
    r"^Image\s+(/page/\d+/(?:Picture|Figure)/\d+)\s+description:\s*(.*?)(?=\n\s*\n|\Z)",
    re.MULTILINE | re.DOTALL,
)
_PAGE_ANCHOR_SPAN = re.compile(r"<span\s+id=\"page-\d+-\d+\"></span>\s*", re.IGNORECASE)
_HEADING_ORNAMENT = re.compile(r"[·•●◆◇■□▪▫\-\u2013\u2014*~`#]+")
_FOOTER_RULE = re.compile(r"^---\s*$", re.MULTILINE)


def yaml_frontmatter(data: dict) -> str:
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{body}\n---\n\n"


def section_filename(plan: SectionPlan) -> str:
    return f"{plan.index:02d}-{plan.slug}.md"


def split_paginated_markdown(text: str) -> dict[int, tuple[int, int]]:
    """Return ``{page_index: (body_start_offset, body_end_offset)}`` after each page marker."""
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
    return {m.group(2) for m in _IMAGE_LINK.finditer(body)} | {
        m.group(2) for m in _LOCAL_IMAGE_LINK.finditer(body)
    }


def write_referenced_images(
    images: dict[str, bytes],
    target_images_dir: Path,
    referenced: set[str],
) -> tuple[set[str], set[str]]:
    """Write referenced SDK images. Returns (written, missing)."""
    target_images_dir.mkdir(parents=True, exist_ok=True)
    written: set[str] = set()
    missing: set[str] = set()
    for name in referenced:
        data = images.get(name)
        if data is None:
            missing.add(name)
            continue
        dest = target_images_dir / name
        if not dest.exists():
            dest.write_bytes(data)
        written.add(name)
    return written, missing


def reformat_image_descriptions(body: str) -> str:
    def repl(match: re.Match[str]) -> str:
        description = re.sub(r"\s+", " ", match.group(2).strip())
        if not description:
            return ""
        lines = ["> [!image] AI description"]
        lines.extend(f"> {line}" if line else ">" for line in description.splitlines())
        return "\n".join(lines)

    return _IMAGE_DESCRIPTION.sub(repl, body)


def _strip_frontmatter_text(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    return text[end + 5 :] if end != -1 else text


def chapter_body_text(file_text: str) -> str:
    """Return the playable/book text from a generated chapter note."""
    text = _strip_frontmatter_text(file_text).strip()
    lines = text.splitlines()
    first_content = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first_content is not None and lines[first_content].lstrip().startswith("# "):
        lines = lines[first_content + 1 :]
    body = "\n".join(lines).strip()
    footers = list(_FOOTER_RULE.finditer(body))
    if footers:
        body = body[: footers[-1].start()].strip()
    return body


def sha256_str(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def body_hash_of(file_text: str) -> str:
    return sha256_str(chapter_body_text(file_text))


def tags_for_value(body_hash: str, summary: str | None) -> str:
    return sha256_str(f"{body_hash}:{summary or ''}")


def normalize_obsidian_tag(value: object) -> str | None:
    """Return a clean Obsidian tag string without '#', or None if invalid."""
    tag = str(value).strip().lstrip("#").lower()
    tag = re.sub(r"\s+", "-", tag)
    tag = re.sub(r"[^0-9a-z_\-/]+", "-", tag)
    tag = re.sub(r"-{2,}", "-", tag)
    tag = re.sub(r"/{2,}", "/", tag).strip("-_/")
    if not tag or tag.isdigit() or tag.startswith("/") or tag.endswith("/"):
        return None
    if any(part == "" or part.isdigit() for part in tag.split("/")):
        return None
    return tag


def clean_obsidian_tags(values: object, *, limit: int | None = None) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        tag = normalize_obsidian_tag(value)
        if tag and tag not in out:
            out.append(tag)
        if limit is not None and len(out) >= limit:
            break
    return out


def system_tag_values(systems: object) -> list[str]:
    if isinstance(systems, str):
        raw: object = [systems]
    else:
        raw = systems
    return [f"system/{tag}" for tag in clean_obsidian_tags(raw) if tag != "unknown"]


def merge_tags(
    existing: object, additions: list[str], *, remove_prefixes: tuple[str, ...] = ()
) -> list[str]:
    out = [tag for tag in clean_obsidian_tags(existing) if not tag.startswith(remove_prefixes)]
    for tag in clean_obsidian_tags(additions):
        if tag not in out:
            out.append(tag)
    return out


def book_base_tags(book_slug: str, systems: object = None, *, index: bool = False) -> list[str]:
    tags = ["book-index"] if index else []
    tags.append(f"book/{book_slug}")
    tags.extend(system_tag_values(systems or []))
    return tags


def read_chapter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    fm = yaml.safe_load(text[4:end]) or {}
    body = text[end + 5 :]
    return fm, body


def write_chapter(path: Path, fm: dict, body: str) -> None:
    rendered = yaml_frontmatter(fm) + body.lstrip("\n")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(rendered, encoding="utf-8")
    os.replace(tmp, path)


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


def rendered_section_body(body: str, title: str) -> str:
    """Return the final chapter body text after ingest cleanup, without placeholders."""
    rendered_body = strip_leading_heading(body, title).strip()
    return reformat_image_descriptions(rendered_body).strip()


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
    del book_title, source_ref
    page_label = (
        f"{plan.page_start + 1}"
        if plan.page_start == plan.page_end
        else f"{plan.page_start + 1}–{plan.page_end + 1}"
    )
    rendered_body = rendered_section_body(body, plan.title)
    if not rendered_body:
        rendered_body = "_(no text extracted for this page range)_"

    prev_plan = next((p for p in plans if p.index == plan.index - 1), None)
    next_plan = next((p for p in plans if p.index == plan.index + 1), None)
    footer: list[str] = ["", "---", ""]
    if prev_plan is not None:
        prev_stem = Path(section_filename(prev_plan)).stem
        footer.append(f"Previous: [[library/books/{book_slug}/{prev_stem}|{prev_plan.title}]]")
    if next_plan is not None:
        next_stem = Path(section_filename(next_plan)).stem
        footer.append(f"Next: [[library/books/{book_slug}/{next_stem}|{next_plan.title}]]")
    footer.append(f"Pages: {page_label}")
    body_text = f"# {plan.title}\n\n{rendered_body}\n" + "\n".join(footer) + "\n"
    fm = {
        "book": book_slug,
        "section": plan.title,
        "section_index": plan.index,
        "page_start": plan.page_start + 1,
        "page_end": plan.page_end + 1,
        "body_hash": body_hash_of(body_text),
        "ingested_at": ingested_at,
    }
    return yaml_frontmatter(fm) + body_text


def render_book_overview(
    *,
    book_title: str,
    book_slug: str,
    plans: list[SectionPlan],
    source_ref: str,
    ingested_at: str,
    system: str | list[str],
    page_count: int,
    plan_source: str,
    summaries: dict[str, str] | None = None,
    tags: dict[str, list[str]] | None = None,
) -> str:
    summaries = summaries or {}
    tags = tags or {}
    systems = system if isinstance(system, list) else ([] if system == "unknown" else [system])
    primary_system = systems[0] if systems else "unknown"
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
        suffixes = [
            f"pp. {plan.page_start + 1}–{plan.page_end + 1}"
            if plan.page_start != plan.page_end
            else page_label
        ]
        summary = summaries.get(stem) or summaries.get(plan.slug)
        if summary:
            suffixes.append(summary)
        content = tags.get(stem) or tags.get(plan.slug) or []
        if content:
            suffixes.append("[" + ", ".join(content) + "]")
        lines.append(
            f"- [[library/books/{book_slug}/{stem}|{plan.title}]] — " + " — ".join(suffixes)
        )
    body_text = "\n".join(lines) + "\n"
    body_hash = sha256_str(body_text)
    summary = f"Book {book_title} table of contents."
    fm = {
        "type": "book-index",
        "source": source_ref,
        "book": book_slug,
        "created": ingested_at[:10],
        "page_count": page_count,
        "section_count": len(plans),
        "plan_source": plan_source,
        "system": primary_system,
        "systems": systems,
        "body_hash": body_hash,
        "summary": summary,
        "summary_for": body_hash,
        "tags": merge_tags(book_base_tags(book_slug, systems, index=True), ["toc"]),
        "tags_for": tags_for_value(body_hash, summary),
    }
    return yaml_frontmatter(fm) + body_text
