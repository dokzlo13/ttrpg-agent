from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

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
_HEADING_TAG = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")
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
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)  # [text](url) -> text
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = _WHITESPACE.sub(" ", s).strip(" \t\u2014\u2013\u00b7\u2022\u25cf\u25c6\u25c7-_*~`#.\u2009")
    letters = re.sub(r"[^A-Za-z]", "", s)
    if len(letters) > 4 and letters.isupper():
        # ALL CAPS → Title Case, but keep small words lowercase except first/last
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
            if i not in (0, len(words) - 1) and w in small:
                out.append(w)
            else:
                out.append(w.capitalize())
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
    return bool(book_title and low == book_title.lower())


def looks_like_ocr_noise(cleaned: str) -> bool:
    if not cleaned or len(cleaned) < 2:
        return True
    alpha = re.sub(r"[^A-Za-z]", "", cleaned)
    if len(alpha) < 2:
        return True
    if len(set(alpha.lower())) == 1:
        return True
    return bool(alpha.isupper() and not re.search(r"[AEIOUaeiou]", alpha))


def book_title_from(pdf: Path, pdf_metadata: dict) -> str:
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
    level: int  # 0 for outline d0; 1..6 for heading levels h1..h6
    source: str


def _walk_outline(reader: PdfReader, outline, depth: int = 0) -> list[_RawCandidate]:
    out: list[_RawCandidate] = []
    for item in outline:
        if isinstance(item, list):
            out.extend(_walk_outline(reader, item, depth + 1))
        else:
            try:
                page_index = reader.get_destination_page_number(item)
            except Exception:
                page_index = None
            title = getattr(item, "title", None)
            if page_index is not None and isinstance(title, str):
                out.append(
                    _RawCandidate(
                        title=title, page_index=int(page_index), level=depth, source="pdf-outline"
                    )
                )
    return out


def _candidates_from_outline(reader: PdfReader) -> list[_RawCandidate]:
    try:
        outline = reader.outline
    except Exception:
        return []
    flat = _walk_outline(reader, outline)
    return [c for c in flat if c.level == 0]


def _has_usable_outline(candidates: list[_RawCandidate], page_count: int) -> bool:
    if len(candidates) < 3:
        return False
    valid = sum(1 for c in candidates if 0 <= c.page_index < page_count)
    return valid >= max(3, int(0.8 * len(candidates)))


def _candidates_from_marker_json(json_path: Path) -> list[_RawCandidate]:
    body = json.loads(json_path.read_text(encoding="utf-8"))
    headers: list[_RawCandidate] = []

    def visit(node, current_page: int | None) -> None:
        if not isinstance(node, dict):
            return
        bt = node.get("block_type")
        if bt == "Page":
            block_id = node.get("id") or ""
            current_page = _page_id_from_block_id(block_id)
        if bt == "SectionHeader":
            html = node.get("html") or ""
            m = _HEADING_TAG.search(html)
            level = int(m.group(1)) if m else 6
            text = (m.group(2) if m else html).strip()
            if text and current_page is not None:
                headers.append(
                    _RawCandidate(
                        title=text,
                        page_index=int(current_page),
                        level=level,
                        source="marker-json",
                    )
                )
        for child in node.get("children") or []:
            visit(child, current_page)

    visit(body, None)
    return headers


def _page_id_from_block_id(block_id: str) -> int | None:
    # block ids look like '/page/0/Page/8' or '/page/3/SectionHeader/2'
    m = re.search(r"/page/(\d+)/", block_id)
    return int(m.group(1)) if m else None


def _choose_split_levels(headers: list[_RawCandidate]) -> tuple[set[int], bool]:
    """Return (levels_to_split_on, prefer_numbered_only)."""
    numbered = [h for h in headers if _NUMBERED_PREFIX.match(h.title)]
    if len(numbered) >= 3:
        return ({h.level for h in numbered}, True)
    levels = sorted({h.level for h in headers})
    if 1 in levels:
        return ({1}, False)
    if levels:
        # most common among smallest two
        small = levels[:2]
        from collections import Counter

        counts = Counter(h.level for h in headers if h.level in small)
        top = counts.most_common(1)[0][0]
        return ({top}, False)
    return (set(), False)


def _candidates_from_marker_filtered(json_path: Path) -> list[_RawCandidate]:
    headers = _candidates_from_marker_json(json_path)
    levels, numbered_only = _choose_split_levels(headers)
    out: list[_RawCandidate] = []
    for h in headers:
        if h.level not in levels:
            continue
        if numbered_only and not _NUMBERED_PREFIX.match(h.title):
            continue
        out.append(h)
    return out


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
        if not title:
            continue
        if is_noise_title(title, book_title=book_title):
            continue
        cleaned.append(
            _RawCandidate(title=title, page_index=c.page_index, level=c.level, source=c.source)
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
        plans.append(
            SectionPlan(
                index=i + 1,
                title=c.title,
                slug=slugify(c.title),
                page_start=page_start,
                page_end=page_end,
                source=c.source,
            )
        )
    return _uniquify_slugs(plans)


def plan_sections(
    pdf: Path,
    json_path: Path,
    page_count: int,
    book_title: str,
) -> tuple[list[SectionPlan], str]:
    """Plan sections deterministically. Returns (plans, source_used)."""
    reader = PdfReader(str(pdf))
    outline_candidates = _candidates_from_outline(reader)
    if _has_usable_outline(outline_candidates, page_count):
        plans = _candidates_to_plans(outline_candidates, page_count, book_title)
        if plans:
            return plans, "pdf-outline"

    json_candidates = _candidates_from_marker_filtered(json_path)
    plans = _candidates_to_plans(json_candidates, page_count, book_title)
    if plans:
        return plans, "marker-json"

    # Whole-book fallback
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
