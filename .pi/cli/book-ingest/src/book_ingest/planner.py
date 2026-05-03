from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

from .models import SectionPlan

_NOISE_TITLES = {
    "front cover",
    "title",
    "endpaper",
    "contents",
    "table of contents",
}
_NOISE_PREFIXES = ("copyright",)
_TOC_TITLES = {"contents", "table of contents"}
_HTML_TAG = re.compile(r"<[^>]+>")
_PAGE_ANCHOR = re.compile(r"\{\d+\}-+")
_PAGE_MARKER_LINE = re.compile(r"^\{(\d+)\}-+\s*$")
_PAGE_SPAN = re.compile(r"<span\s+id=[\"']page-(\d+)-[^\"']*[\"']\s*>\s*</span>", re.IGNORECASE)
_TOC_PAGE_HREF = re.compile(r"^#page-(\d+)(?:-\d+)?$")
_WHITESPACE = re.compile(r"\s+")
_VERSION_BRACKET = re.compile(r"\s*\[v[\d.]+\][^]]*$", re.IGNORECASE)
_TRAILING_PARENS = re.compile(r"\s*\([^)]*\)\s*$")
_TRAILING_VERSION = re.compile(r"\s+v\d+(?:[-.]\d+)?\s*$", re.IGNORECASE)

# Explicit structural materialization thresholds. These are not used to classify
# titles or choose semantic heading levels; they only bound readable note size
# while walking source-authored / Marker-typed hierarchy.
_MAX_CHARS = 35_000
_TARGET_CHARS = 12_000
_MAX_PAGES = 6
_TARGET_PAGES = 3


def slugify(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "section"


def clean_title(raw: str) -> str:
    if not raw:
        return ""
    s = _HTML_TAG.sub("", raw)
    s = _PAGE_ANCHOR.sub("", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = _WHITESPACE.sub(" ", s).strip(" \t\u2014\u2013\u00b7\u2022\u25cf\u25c6\u25c7-_*~`#.\u2009")
    letters = re.sub(r"[^A-Za-z]", "", s)
    if len(letters) > 4 and letters.isupper():
        small = {
            "a",
            "an",
            "and",
            "as",
            "at",
            "but",
            "by",
            "for",
            "in",
            "of",
            "on",
            "or",
            "the",
            "to",
        }
        words = s.lower().split()
        out = []
        for i, w in enumerate(words):
            out.append(w if i not in (0, len(words) - 1) and w in small else w.capitalize())
        s = " ".join(out)
    return s


def is_noise_title(cleaned: str, book_title: str | None = None) -> bool:
    low = cleaned.lower().strip()
    if not low:
        return True
    if low in _NOISE_TITLES:
        return True
    if any(low.startswith(p) for p in _NOISE_PREFIXES):
        return True
    if book_title:
        book_low = book_title.lower().strip()
        if low == book_low or (len(low) > 8 and low in book_low):
            return True
    return False


def looks_like_ocr_noise(cleaned: str) -> bool:
    if not cleaned or len(cleaned) < 2:
        return True
    alpha = re.sub(r"[^A-Za-z]", "", cleaned)
    if len(alpha) < 2:
        return True
    if len(set(alpha.lower())) == 1:
        return True
    return bool(alpha.isupper() and not re.search(r"[AEIOUaeiou]", alpha))


def book_title_from(pdf: Path, pdf_metadata: dict | None = None) -> str:
    title = (pdf_metadata or {}).get("/Title")
    if isinstance(title, str):
        title = title.strip()
        if title and len(title) >= 3:
            return title
    stem = pdf.stem
    cleaned = _VERSION_BRACKET.sub("", stem)
    cleaned = _TRAILING_PARENS.sub("", cleaned)
    cleaned = _TRAILING_VERSION.sub("", cleaned)
    cleaned = cleaned.strip(" -_")
    return cleaned or stem


@dataclass(frozen=True)
class _RawCandidate:
    title: str
    page_index: int
    level: int
    source: str
    char_start: int | None = None


@dataclass
class _StructureNode:
    title: str
    page_start: int
    level: int
    source: str
    char_start: int | None = None
    char_end: int | None = None
    page_end: int = 0
    children: list[_StructureNode] = field(default_factory=list)


@dataclass(frozen=True)
class _PackedSegment:
    first_title: str
    last_title: str
    page_start: int
    page_end: int
    source: str
    char_start: int | None = None
    char_end: int | None = None

    @property
    def title(self) -> str:
        return self.first_title if self.first_title == self.last_title else f"{self.first_title} – {self.last_title}"

    @property
    def pages(self) -> int:
        return self.page_end - self.page_start + 1

    @property
    def chars(self) -> int | None:
        if self.char_start is None or self.char_end is None:
            return None
        return max(0, self.char_end - self.char_start)


def _raw_candidates_pdf_outline(pdf: Path) -> list[_RawCandidate]:
    import fitz

    with fitz.open(str(pdf)) as doc:
        toc = doc.get_toc(simple=True)

    headers: list[_RawCandidate] = []
    for level, title, page_1based in toc:
        if not isinstance(page_1based, int) or page_1based < 1:
            continue
        headers.append(
            _RawCandidate(
                title=str(title),
                page_index=page_1based - 1,
                level=int(level),
                source="pdf-outline",
            )
        )
    return headers


def _candidates_pdf_outline(pdf: Path, page_count: int, book_title: str) -> list[SectionPlan]:
    return _pack_candidates(_raw_candidates_pdf_outline(pdf), page_count, book_title)


def _candidates_from_marker_toc(table_of_contents: list[dict[str, Any]]) -> list[_RawCandidate]:
    headers: list[_RawCandidate] = []
    for entry in table_of_contents:
        title = str(entry.get("title") or "")
        page = entry.get("page_id", entry.get("page_index", entry.get("page")))
        level = entry.get("heading_level", entry.get("level", 1))
        if page is None:
            continue
        try:
            page_index = int(page)
            heading_level = int(level)
        except (TypeError, ValueError):
            continue
        if title:
            headers.append(
                _RawCandidate(
                    title=title,
                    page_index=page_index,
                    level=heading_level,
                    source="marker-toc",
                )
            )
    return headers


def _heading_level(token: Any) -> int:
    tag = str(getattr(token, "tag", ""))
    if len(tag) >= 2 and tag[0].lower() == "h" and tag[1:].isdigit():
        return int(tag[1:])
    markup = str(getattr(token, "markup", ""))
    return len(markup) if markup else 1


def _link_text(children: list[Any], start: int) -> str:
    parts: list[str] = []
    for child in children[start + 1 :]:
        if child.type == "link_close":
            break
        content = str(getattr(child, "content", ""))
        if content:
            parts.append(content)
    return "".join(parts).strip()


def _choose_same_page_toc_entry(current: _RawCandidate, new: _RawCandidate) -> _RawCandidate:
    current_title = clean_title(current.title)
    new_title = clean_title(new.title)
    current_noise = is_noise_title(current_title)
    new_noise = is_noise_title(new_title)
    if current_noise and not new_noise:
        return new
    if new_noise and not current_noise:
        return current
    if len(new_title) > len(current_title):
        return new
    return current


def _raw_candidates_markdown_toc_links(markdown_text: str) -> tuple[list[_RawCandidate], str | None]:
    valid_anchor_pages = {int(m.group(1)) for m in _PAGE_SPAN.finditer(markdown_text)}
    tokens = MarkdownIt("commonmark").parse(markdown_text)

    contents_level: int | None = None
    scanning = False
    found_contents = False
    entries: list[_RawCandidate] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == "heading_open":
            level = _heading_level(token)
            inline_content = ""
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                inline_content = str(getattr(tokens[i + 1], "content", ""))
            title = clean_title(inline_content).lower()
            if scanning and contents_level is not None and level <= contents_level:
                break
            if title in _TOC_TITLES:
                scanning = True
                found_contents = True
                contents_level = level
                i += 1
                continue

        if scanning and token.type == "inline":
            children = list(getattr(token, "children", None) or [])
            for child_index, child in enumerate(children):
                if child.type != "link_open":
                    continue
                href = child.attrGet("href") if hasattr(child, "attrGet") else None
                if href is None:
                    attrs = getattr(child, "attrs", {}) or {}
                    href = attrs.get("href") if isinstance(attrs, dict) else None
                m = _TOC_PAGE_HREF.fullmatch(str(href or ""))
                if not m:
                    continue
                page_index = int(m.group(1))
                if page_index not in valid_anchor_pages:
                    continue
                text = _link_text(children, child_index)
                if not text:
                    text = str(getattr(token, "content", ""))
                if text:
                    entries.append(
                        _RawCandidate(
                            title=text,
                            page_index=page_index,
                            level=1,
                            source="markdown-toc-links",
                        )
                    )
        i += 1

    if not found_contents:
        return [], "contents heading not found"
    if not entries:
        return [], "all anchors unresolved"

    grouped: list[_RawCandidate] = []
    for entry in entries:
        if grouped and grouped[-1].page_index == entry.page_index:
            grouped[-1] = _choose_same_page_toc_entry(grouped[-1], entry)
        else:
            grouped.append(entry)
    return grouped, None


def _line_starts(markdown_text: str) -> list[int]:
    starts = [0]
    starts.extend(m.end() for m in re.finditer("\n", markdown_text))
    return starts


def _page_by_line(markdown_text: str) -> list[int]:
    lines = markdown_text.splitlines()
    out: list[int] = []
    current = 0
    for line in lines:
        m = _PAGE_MARKER_LINE.fullmatch(line.strip())
        if m:
            current = int(m.group(1))
        out.append(current)
    return out


def _raw_candidates_markdown_headings(markdown_text: str) -> list[_RawCandidate]:
    tokens = MarkdownIt("commonmark").parse(markdown_text)
    starts = _line_starts(markdown_text)
    pages = _page_by_line(markdown_text)
    headers: list[_RawCandidate] = []
    for i, token in enumerate(tokens):
        if token.type != "heading_open" or not token.map:
            continue
        line = int(token.map[0])
        if i + 1 >= len(tokens) or tokens[i + 1].type != "inline":
            continue
        title = str(getattr(tokens[i + 1], "content", ""))
        if not title:
            continue
        offset = starts[line] if line < len(starts) else len(markdown_text)
        page_index = pages[line] if line < len(pages) else 0
        headers.append(
            _RawCandidate(
                title=title,
                page_index=page_index,
                level=_heading_level(token),
                source="marker-markdown-headings",
                char_start=offset,
            )
        )
    return headers


def _candidates_markdown_toc_links(
    markdown_text: str, page_count: int, book_title: str
) -> list[SectionPlan]:
    raw, _ = _raw_candidates_markdown_toc_links(markdown_text)
    return _pack_candidates(raw, page_count, book_title)


def _candidates_marker_toc_passthrough(
    table_of_contents: list[dict[str, Any]], page_count: int, book_title: str
) -> list[SectionPlan]:
    return _pack_candidates(_candidates_from_marker_toc(table_of_contents), page_count, book_title)


def _dedupe_consecutive(candidates: list[_RawCandidate]) -> list[_RawCandidate]:
    out: list[_RawCandidate] = []
    for c in candidates:
        if out and out[-1].page_index == c.page_index and out[-1].title == c.title:
            continue
        out.append(c)
    return out


def _uniquify_slugs(plans: list[SectionPlan]) -> list[SectionPlan]:
    seen: dict[str, int] = {}
    out: list[SectionPlan] = []
    for plan in plans:
        base = plan.slug
        seen[base] = seen.get(base, 0) + 1
        slug = base if seen[base] == 1 else f"{base}-{seen[base]}"
        out.append(
            SectionPlan(
                index=plan.index,
                title=plan.title,
                slug=slug,
                page_start=plan.page_start,
                page_end=plan.page_end,
                source=plan.source,
                confidence=plan.confidence,
                char_start=plan.char_start,
                char_end=plan.char_end,
            )
        )
    return out


def _clean_candidates(
    candidates: Iterable[_RawCandidate], book_title: str
) -> list[_RawCandidate]:
    cleaned: list[_RawCandidate] = []
    max_page_seen = -1
    for c in candidates:
        title = clean_title(c.title)
        if not title or is_noise_title(title, book_title=book_title):
            continue
        page_index = max(0, c.page_index)
        # Page-only bookmark/TOC sources sometimes contain cross-reference groups
        # whose destinations jump backwards in the document (maps, tables,
        # sidebars). For chunking, those would duplicate earlier pages and can
        # create non-monotonic output. Char-precise Markdown headings use source
        # offsets instead and are allowed to share/go back in page metadata.
        if c.char_start is None and page_index < max_page_seen:
            continue
        max_page_seen = max(max_page_seen, page_index)
        cleaned.append(
            _RawCandidate(
                title=title,
                page_index=page_index,
                level=max(1, c.level),
                source=c.source,
                char_start=c.char_start,
            )
        )
    return _dedupe_consecutive(cleaned)


def _build_forest(
    candidates: Iterable[_RawCandidate],
    page_count: int,
    book_title: str,
    markdown_text: str | None = None,
) -> list[_StructureNode]:
    cleaned = _clean_candidates(candidates, book_title)
    if not cleaned:
        return []

    nodes = [
        _StructureNode(
            title=c.title,
            page_start=min(max(0, c.page_index), max(0, page_count - 1)),
            page_end=min(max(0, c.page_index), max(0, page_count - 1)),
            level=c.level,
            source=c.source,
            char_start=c.char_start,
        )
        for c in cleaned
    ]

    for i, node in enumerate(nodes):
        next_boundary: _StructureNode | None = None
        for later in nodes[i + 1 :]:
            if later.level <= node.level:
                next_boundary = later
                break
        if next_boundary is None:
            node.page_end = max(0, page_count - 1)
            if node.char_start is not None and markdown_text is not None:
                node.char_end = len(markdown_text)
        else:
            node.page_end = max(node.page_start, next_boundary.page_start - 1)
            if node.char_start is not None and next_boundary.char_start is not None:
                node.char_end = next_boundary.char_start
        if node.page_end < node.page_start:
            node.page_end = node.page_start

    root = _StructureNode("__root__", 0, 0, "root", page_end=max(0, page_count - 1))
    stack: list[_StructureNode] = [root]
    for node in nodes:
        while stack and node.level <= stack[-1].level:
            stack.pop()
        stack[-1].children.append(node)
        stack.append(node)
    return root.children


def _segment_from_node(node: _StructureNode) -> _PackedSegment:
    return _PackedSegment(
        first_title=node.title,
        last_title=node.title,
        page_start=node.page_start,
        page_end=node.page_end,
        source=node.source,
        char_start=node.char_start,
        char_end=node.char_end,
    )


def _split_leaf_node(node: _StructureNode) -> list[_PackedSegment]:
    pages = node.page_end - node.page_start + 1
    if pages <= _MAX_PAGES:
        return [_segment_from_node(node)]
    segments: list[_PackedSegment] = []
    part = 1
    for page_start in range(node.page_start, node.page_end + 1, _MAX_PAGES):
        page_end = min(node.page_end, page_start + _MAX_PAGES - 1)
        title = node.title if part == 1 else f"{node.title} (Part {part})"
        segments.append(
            _PackedSegment(
                first_title=title,
                last_title=title,
                page_start=page_start,
                page_end=page_end,
                source=node.source,
            )
        )
        part += 1
    return segments


def _prefix_segment(parent: _StructureNode) -> _PackedSegment | None:
    if not parent.children:
        return None
    first_child = parent.children[0]
    if parent.char_start is not None and first_child.char_start is not None:
        if first_child.char_start > parent.char_start:
            return _PackedSegment(
                first_title=parent.title,
                last_title=parent.title,
                page_start=parent.page_start,
                page_end=max(parent.page_start, first_child.page_start),
                source=parent.source,
                char_start=parent.char_start,
                char_end=first_child.char_start,
            )
        return None
    if first_child.page_start > parent.page_start:
        return _PackedSegment(
            first_title=parent.title,
            last_title=parent.title,
            page_start=parent.page_start,
            page_end=first_child.page_start - 1,
            source=parent.source,
        )
    return None


def _node_fits(node: _StructureNode) -> bool:
    pages = node.page_end - node.page_start + 1
    chars = None
    if node.char_start is not None and node.char_end is not None:
        chars = max(0, node.char_end - node.char_start)
    return pages <= _MAX_PAGES and (chars is None or chars <= _MAX_CHARS)


def _pack_node(node: _StructureNode) -> list[_PackedSegment]:
    if not node.children:
        return _split_leaf_node(node)
    if _node_fits(node):
        return [_segment_from_node(node)]

    segments: list[_PackedSegment] = []
    prefix = _prefix_segment(node)
    if prefix is not None:
        segments.append(prefix)
    for child in node.children:
        segments.extend(_pack_node(child))
    return _merge_adjacent(segments)


def _combined_segment(a: _PackedSegment, b: _PackedSegment) -> _PackedSegment:
    return _PackedSegment(
        first_title=a.first_title,
        last_title=b.last_title,
        page_start=min(a.page_start, b.page_start),
        page_end=max(a.page_end, b.page_end),
        source=a.source,
        char_start=a.char_start if a.char_start is not None else b.char_start,
        char_end=b.char_end if b.char_end is not None else a.char_end,
    )


def _can_merge(a: _PackedSegment, b: _PackedSegment) -> bool:
    combined_pages = max(a.page_end, b.page_end) - min(a.page_start, b.page_start) + 1
    if a.char_start is None and b.char_start is None and b.page_start <= a.page_end:
        return True
    if combined_pages > _TARGET_PAGES:
        return False
    if a.char_start is not None and a.char_end is not None and b.char_end is not None:
        start = min(a.char_start, b.char_start or a.char_start)
        chars = b.char_end - start
        return chars <= _TARGET_CHARS
    return True


def _merge_adjacent(segments: list[_PackedSegment]) -> list[_PackedSegment]:
    if not segments:
        return []
    out: list[_PackedSegment] = []
    current = segments[0]
    for segment in segments[1:]:
        if _can_merge(current, segment):
            current = _combined_segment(current, segment)
        else:
            out.append(current)
            current = segment
    out.append(current)
    return out


def _segments_to_plans(segments: list[_PackedSegment]) -> list[SectionPlan]:
    plans = [
        SectionPlan(
            index=i,
            title=segment.title,
            slug=slugify(segment.title),
            page_start=segment.page_start,
            page_end=segment.page_end,
            source=segment.source,
            char_start=segment.char_start,
            char_end=segment.char_end,
        )
        for i, segment in enumerate(segments, start=1)
    ]
    return _uniquify_slugs(plans)


def _pack_candidates(
    candidates: Iterable[_RawCandidate],
    page_count: int,
    book_title: str,
    markdown_text: str | None = None,
) -> list[SectionPlan]:
    forest = _build_forest(candidates, page_count, book_title, markdown_text)
    segments: list[_PackedSegment] = []
    for node in forest:
        segments.extend(_pack_node(node))
    return _segments_to_plans(_merge_adjacent(segments))


def _candidates_to_plans(
    candidates: Iterable[_RawCandidate],
    page_count: int,
    book_title: str,
) -> list[SectionPlan]:
    return _pack_candidates(candidates, page_count, book_title)


def _attempt_entry(source: str, raw: int, plans: int, reason: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"source": source, "raw": raw, "plans": plans}
    if reason:
        entry["reason"] = reason
    return entry


def _fallback_plan(page_count: int, book_title: str) -> SectionPlan:
    return SectionPlan(
        index=1,
        title=book_title,
        slug=slugify(book_title),
        page_start=0,
        page_end=max(0, page_count - 1),
        source="whole-book",
        confidence=0.5,
    )


def plan_sections(
    pdf: Path,
    table_of_contents: list[dict[str, Any]],
    page_count: int,
    book_title: str,
    markdown_text: str | None = None,
) -> tuple[list[SectionPlan], str, dict[str, Any]]:
    """Plan readable sections from deterministic structural sources in strict precedence order."""
    attempted: list[dict[str, Any]] = []

    try:
        raw = _raw_candidates_pdf_outline(pdf)
        plans = _pack_candidates(raw, page_count, book_title)
        attempted.append(
            _attempt_entry("pdf-outline", len(raw), len(plans), None if plans else "empty")
        )
        if plans:
            return plans, "pdf-outline", {"chosen": "pdf-outline", "attempted": attempted}
    except Exception as exc:
        attempted.append(_attempt_entry("pdf-outline", 0, 0, f"error: {type(exc).__name__}: {exc}"))

    if markdown_text is not None:
        try:
            raw, reason = _raw_candidates_markdown_toc_links(markdown_text)
            plans = _pack_candidates(raw, page_count, book_title)
            attempted.append(
                _attempt_entry(
                    "markdown-toc-links",
                    len(raw),
                    len(plans),
                    None if plans else (reason or "empty"),
                )
            )
            if plans:
                return plans, "markdown-toc-links", {
                    "chosen": "markdown-toc-links",
                    "attempted": attempted,
                }
        except Exception as exc:
            attempted.append(
                _attempt_entry(
                    "markdown-toc-links", 0, 0, f"error: {type(exc).__name__}: {exc}"
                )
            )

    try:
        raw = _candidates_from_marker_toc(table_of_contents)
        plans = _pack_candidates(raw, page_count, book_title)
        attempted.append(_attempt_entry("marker-toc", len(raw), len(plans), None if plans else "empty"))
        if plans:
            return plans, "marker-toc", {"chosen": "marker-toc", "attempted": attempted}
    except Exception as exc:
        attempted.append(_attempt_entry("marker-toc", 0, 0, f"error: {type(exc).__name__}: {exc}"))

    if markdown_text is not None:
        try:
            raw = _raw_candidates_markdown_headings(markdown_text)
            plans = _pack_candidates(raw, page_count, book_title, markdown_text)
            attempted.append(
                _attempt_entry(
                    "marker-markdown-headings", len(raw), len(plans), None if plans else "empty"
                )
            )
            if plans:
                return plans, "marker-markdown-headings", {
                    "chosen": "marker-markdown-headings",
                    "attempted": attempted,
                }
        except Exception as exc:
            attempted.append(
                _attempt_entry(
                    "marker-markdown-headings", 0, 0, f"error: {type(exc).__name__}: {exc}"
                )
            )

    fallback = _fallback_plan(page_count, book_title)
    attempted.append(_attempt_entry("whole-book", 1, 1))
    return [fallback], "whole-book", {"chosen": "whole-book", "attempted": attempted}
