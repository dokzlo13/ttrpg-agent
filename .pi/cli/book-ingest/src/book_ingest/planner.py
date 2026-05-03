from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import SectionPlan

_NOISE_TITLES = {
    "front cover",
    "title",
    "endpaper",
    "contents",
    "table of contents",
}
_NOISE_PREFIXES = ("copyright",)
_NUMBERED_PREFIX = re.compile(r"^[A-Z]?\d+(?:\.\d+)?\s+\w")
_HTML_TAG = re.compile(r"<[^>]+>")
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_PAGE_MARKER = re.compile(r"^\{(\d+)\}-+\s*$", re.MULTILINE)
_PAGE_ANCHOR = re.compile(r"\{\d+\}-+")
_WHITESPACE = re.compile(r"\s+")
_VERSION_BRACKET = re.compile(r"\s*\[v[\d.]+\][^]]*$", re.IGNORECASE)
_TRAILING_PARENS = re.compile(r"\s*\([^)]*\)\s*$")
_TRAILING_VERSION = re.compile(r"\s+v\d+(?:[-.]\d+)?\s*$", re.IGNORECASE)


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


def _choose_split_levels(headers: list[_RawCandidate]) -> tuple[set[int], bool]:
    """Return (levels_to_split_on, prefer_numbered_only)."""
    numbered = [h for h in headers if _NUMBERED_PREFIX.match(h.title)]
    if len(numbered) >= 3:
        return ({h.level for h in numbered}, True)
    levels = sorted({h.level for h in headers})
    if 1 in levels:
        return ({1}, False)
    if levels:
        from collections import Counter

        small = levels[:2]
        counts = Counter(h.level for h in headers if h.level in small)
        top = counts.most_common(1)[0][0]
        return ({top}, False)
    return (set(), False)


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


def _filter_candidates(headers: list[_RawCandidate]) -> list[_RawCandidate]:
    levels, numbered_only = _choose_split_levels(headers)
    out: list[_RawCandidate] = []
    for h in headers:
        if h.level not in levels:
            continue
        if numbered_only and not _NUMBERED_PREFIX.match(h.title):
            continue
        out.append(h)
    return out


def _candidates_from_marker_filtered(
    table_of_contents: list[dict[str, Any]],
) -> list[_RawCandidate]:
    return _filter_candidates(_candidates_from_marker_toc(table_of_contents))


def _candidates_from_markdown_headings(markdown_text: str) -> list[_RawCandidate]:
    page_markers = list(_PAGE_MARKER.finditer(markdown_text))
    if not page_markers:
        return []
    headers: list[_RawCandidate] = []
    for i, page_match in enumerate(page_markers):
        page_index = int(page_match.group(1))
        start = page_match.end()
        end = page_markers[i + 1].start() if i + 1 < len(page_markers) else len(markdown_text)
        page_text = markdown_text[start:end]
        for heading in _MARKDOWN_HEADING.finditer(page_text):
            raw = heading.group(2).strip()
            if raw:
                headers.append(
                    _RawCandidate(
                        title=raw,
                        page_index=page_index,
                        level=len(heading.group(1)),
                        source="marker-markdown",
                        char_start=start + heading.start(),
                    )
                )
    return _filter_candidates(headers)


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


def _candidates_to_plans(
    candidates: Iterable[_RawCandidate],
    page_count: int,
    book_title: str,
) -> list[SectionPlan]:
    sortable = sorted(candidates, key=lambda c: (c.page_index, c.level))
    cleaned: list[_RawCandidate] = []
    for c in sortable:
        title = clean_title(c.title)
        if not title or is_noise_title(title, book_title=book_title):
            continue
        cleaned.append(
            _RawCandidate(
                title=title,
                page_index=c.page_index,
                level=c.level,
                source=c.source,
                char_start=c.char_start,
            )
        )
    cleaned = _dedupe_consecutive(cleaned)
    if not cleaned:
        return []

    plans: list[SectionPlan] = []
    for i, c in enumerate(cleaned):
        page_start = max(0, c.page_index)
        page_end = (cleaned[i + 1].page_index - 1) if i + 1 < len(cleaned) else (page_count - 1)
        if page_end < page_start:
            page_end = page_start
        char_end = cleaned[i + 1].char_start if i + 1 < len(cleaned) else None
        plans.append(
            SectionPlan(
                index=i + 1,
                title=c.title,
                slug=slugify(c.title),
                page_start=page_start,
                page_end=page_end,
                source=c.source,
                char_start=c.char_start,
                char_end=char_end,
            )
        )
    return _uniquify_slugs(plans)


def plan_sections(
    pdf: Path,
    table_of_contents: list[dict[str, Any]],
    page_count: int,
    book_title: str,
    markdown_text: str | None = None,
) -> tuple[list[SectionPlan], str]:
    """Plan sections from Marker metadata, then structural Markdown headings."""
    candidates = _candidates_from_marker_filtered(table_of_contents)
    plans = _candidates_to_plans(candidates, page_count, book_title)
    if plans:
        return plans, "marker-toc"

    if markdown_text:
        candidates = _candidates_from_markdown_headings(markdown_text)
        plans = _candidates_to_plans(candidates, page_count, book_title)
        if plans:
            return plans, "marker-markdown"

    fallback = SectionPlan(
        index=1,
        title=book_title,
        slug=slugify(book_title),
        page_start=0,
        page_end=max(0, page_count - 1),
        source="whole-book",
        confidence=0.5,
    )
    return [fallback], "whole-book"
