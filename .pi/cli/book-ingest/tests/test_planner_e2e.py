from pathlib import Path

from book_ingest.planner import (
    _candidates_from_marker_filtered,
    _choose_split_levels,
    plan_sections,
)


def _toc(title, page, level):
    return {"title": title, "page_id": page, "heading_level": level}


def test_choose_split_levels_prefers_numbered():
    from book_ingest.planner import _RawCandidate

    headers = [
        _RawCandidate("1 Intro", 0, 2, "marker-toc"),
        _RawCandidate("Sub heading", 0, 3, "marker-toc"),
        _RawCandidate("2 Finding", 1, 1, "marker-toc"),
        _RawCandidate("Sub two", 1, 3, "marker-toc"),
        _RawCandidate("3 Padduck", 2, 1, "marker-toc"),
    ]
    levels, numbered_only = _choose_split_levels(headers)
    assert numbered_only is True
    assert levels == {1, 2}


def test_choose_split_levels_falls_back_to_h1():
    from book_ingest.planner import _RawCandidate

    headers = [
        _RawCandidate("First", 0, 1, "marker-toc"),
        _RawCandidate("Second", 1, 1, "marker-toc"),
        _RawCandidate("Sub", 1, 3, "marker-toc"),
    ]
    levels, numbered_only = _choose_split_levels(headers)
    assert numbered_only is False
    assert levels == {1}


def test_marker_toc_candidates_filter():
    toc = [
        _toc("1 Intro", 0, 2),
        _toc("2 Finding", 1, 1),
        _toc("Subheading", 1, 3),
        _toc("3 Padduck", 2, 1),
    ]
    candidates = _candidates_from_marker_filtered(toc)
    titles = [c.title for c in candidates]
    assert titles == ["1 Intro", "2 Finding", "3 Padduck"]
    assert all("Subheading" not in t for t in titles)


def test_marker_toc_candidates_handles_h1_only():
    toc = [
        _toc("Foreword", 0, 1),
        _toc("Chapter One", 2, 1),
        _toc("A subsection", 2, 2),
    ]
    candidates = _candidates_from_marker_filtered(toc)
    titles = [c.title for c in candidates]
    assert titles == ["Foreword", "Chapter One"]


def test_plan_sections_falls_back_to_paginated_markdown_headings():
    markdown = """
{0}------------------------------------------------
# Sample Book
# Adventure Background
#### Living World
{1}------------------------------------------------
# Ground Floor
#### Taproom
{2}------------------------------------------------
# Cellar Level
"""
    plans, source = plan_sections(Path("/tmp/sample.pdf"), [], 3, "Sample Book", markdown)
    assert source == "marker-markdown"
    assert [p.title for p in plans] == ["Adventure Background", "Ground Floor", "Cellar Level"]
    assert [(p.page_start, p.page_end) for p in plans] == [(0, 0), (1, 1), (2, 2)]
