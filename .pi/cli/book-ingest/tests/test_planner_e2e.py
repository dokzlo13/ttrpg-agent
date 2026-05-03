from pathlib import Path

import pytest

from book_ingest.planner import (
    _candidates_markdown_toc_links,
    _candidates_marker_toc_passthrough,
    _candidates_pdf_outline,
    plan_sections,
)


def _toc(title, page, level):
    return {"title": title, "page_id": page, "heading_level": level}


def _markdown_toc() -> str:
    return """
# Sample Book

## CONTENTS

[The Approach](#page-1-0)
[Page 2](#page-1-0)
[The Vault](#page-3-0)
[The End](#page-5-0)

## Body

<span id="page-1-0"></span>
# The Approach

<span id="page-3-0"></span>
# The Vault

<span id="page-5-0"></span>
# The End
"""


def test_pdf_outline_empty_outline_returns_empty(tmp_path):
    fitz = pytest.importorskip("fitz")
    pdf = tmp_path / "empty-outline.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(pdf))
    doc.close()

    assert _candidates_pdf_outline(pdf, 1, "Empty Outline") == []


def test_markdown_toc_links_happy_path():
    plans = _candidates_markdown_toc_links(_markdown_toc(), 7, "Sample Book")

    assert [p.title for p in plans] == ["The Approach", "The Vault", "The End"]
    assert [(p.page_start, p.page_end) for p in plans] == [(1, 2), (3, 4), (5, 6)]
    assert all(p.source == "markdown-toc-links" for p in plans)


def test_markdown_toc_links_rejects_unmatched_anchors():
    markdown = """
## CONTENTS

[Kept](#page-1-0)
[Dropped](#page-9-0)
[Also Kept](#page-3-0)

## Body

<span id="page-1-0"></span>
<span id="page-3-0"></span>
"""

    plans = _candidates_markdown_toc_links(markdown, 5, "Sample Book")

    assert [p.title for p in plans] == ["Kept", "Also Kept"]
    assert [(p.page_start, p.page_end) for p in plans] == [(1, 2), (3, 4)]


def test_markdown_toc_links_requires_contents_heading_and_plan_falls_through():
    markdown = """
# Not Contents

[Ignored](#page-1-0)

<span id="page-1-0"></span>
"""
    marker_toc = [_toc("Marker Chapter", 0, 2)]

    assert _candidates_markdown_toc_links(markdown, 3, "Sample Book") == []
    plans, source, diagnostics = plan_sections(
        Path("/tmp/missing.pdf"), marker_toc, 3, "Sample Book", markdown
    )

    assert source == "marker-toc"
    assert diagnostics["chosen"] == "marker-toc"
    assert [p.title for p in plans] == ["Marker Chapter"]


def test_marker_toc_uses_all_levels_but_packs_readable_ranges():
    toc = [
        _toc("Chapter One", 0, 1),
        _toc("Room One", 1, 2),
        _toc("Room Detail", 2, 3),
        _toc("Chapter Two", 3, 1),
    ]

    plans = _candidates_marker_toc_passthrough(toc, 5, "Sample Book")

    assert [p.title for p in plans] == ["Chapter One", "Chapter Two"]
    assert [(p.page_start, p.page_end) for p in plans] == [(0, 2), (3, 4)]


def test_page_only_same_page_outline_entries_are_coalesced():
    toc = [
        _toc("Chapter", 0, 1),
        _toc("A", 0, 2),
        _toc("B", 0, 2),
        _toc("C", 0, 2),
    ]

    plans = _candidates_marker_toc_passthrough(toc, 2, "Sample Book")

    assert len(plans) == 1
    assert plans[0].page_start == 0
    assert plans[0].page_end == 1


def test_large_parent_descends_to_children_without_level_choice():
    toc = [
        _toc("Large Chapter", 0, 1),
        _toc("Part One", 0, 2),
        _toc("Part Two", 9, 2),
        _toc("Part Three", 18, 2),
    ]

    plans = _candidates_marker_toc_passthrough(toc, 25, "Sample Book")

    assert [p.title for p in plans] == [
        "Part One",
        "Part One (Part 2)",
        "Part Two",
        "Part Two (Part 2)",
        "Part Three",
        "Part Three (Part 2)",
    ]
    assert [(p.page_start, p.page_end) for p in plans] == [
        (0, 5),
        (6, 8),
        (9, 14),
        (15, 17),
        (18, 23),
        (24, 24),
    ]


def test_large_leaf_splits_by_page_windows():
    toc = [_toc("Large Leaf", 0, 1)]

    plans = _candidates_marker_toc_passthrough(toc, 14, "Sample Book")

    assert [p.title for p in plans] == ["Large Leaf", "Large Leaf (Part 2)", "Large Leaf (Part 3)"]
    assert [(p.page_start, p.page_end) for p in plans] == [(0, 5), (6, 11), (12, 13)]


def test_strict_precedence_markdown_toc_links_wins_over_marker_toc():
    marker_toc = [_toc("Marker Chapter", 0, 1), _toc("Marker Subchapter", 2, 2)]

    plans, source, diagnostics = plan_sections(
        Path("/tmp/missing.pdf"), marker_toc, 7, "Sample Book", _markdown_toc()
    )

    assert source == "markdown-toc-links"
    assert diagnostics["chosen"] == "markdown-toc-links"
    assert [entry["source"] for entry in diagnostics["attempted"]] == [
        "pdf-outline",
        "markdown-toc-links",
    ]
    assert [p.title for p in plans] == ["The Approach", "The Vault", "The End"]


def test_marker_markdown_heading_fallback_uses_token_structure():
    body = "x" * 13_000
    markdown = f"""
{{0}}------------------------------------------------
# Sample Book
# First
{body}
{{1}}------------------------------------------------
# Second
{body}
"""

    plans, source, diagnostics = plan_sections(Path("/tmp/missing.pdf"), [], 2, "Sample Book", markdown)

    assert source == "marker-markdown-headings"
    assert diagnostics["chosen"] == "marker-markdown-headings"
    assert [p.title for p in plans] == ["First", "Second"]
    assert all(p.char_start is not None for p in plans)


def test_whole_book_fallback():
    plans, source, diagnostics = plan_sections(Path("/tmp/missing.pdf"), [], 3, "Sample Book")

    assert source == "whole-book"
    assert diagnostics["chosen"] == "whole-book"
    assert [p.title for p in plans] == ["Sample Book"]
    assert [(p.page_start, p.page_end) for p in plans] == [(0, 2)]
