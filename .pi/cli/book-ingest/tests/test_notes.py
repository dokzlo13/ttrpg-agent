import pytest

from book_ingest.models import SectionPlan
from book_ingest.notes import (
    referenced_image_names,
    render_book_index,
    render_section_note,
    rewrite_image_links,
    section_filename,
    slice_pages,
    split_paginated_markdown,
    strip_leading_heading,
)

PAGINATED_SAMPLE = """\

{0}------------------------------------------------

# Cover

cover content

{1}------------------------------------------------

# Page Two

body of page two

{2}------------------------------------------------

trailing content
"""


def test_split_paginated_markdown_indexes_pages():
    pages = split_paginated_markdown(PAGINATED_SAMPLE)
    assert set(pages) == {0, 1, 2}
    assert "cover content" in PAGINATED_SAMPLE[pages[0][0] : pages[0][1]]
    assert "body of page two" in PAGINATED_SAMPLE[pages[1][0] : pages[1][1]]
    assert "trailing content" in PAGINATED_SAMPLE[pages[2][0] : pages[2][1]]


def test_slice_pages_concatenates_range():
    body = slice_pages(PAGINATED_SAMPLE, 0, 1)
    assert "cover content" in body
    assert "body of page two" in body
    assert "trailing content" not in body


def test_slice_pages_strips_page_anchor_spans():
    text = (
        '\n{0}---------\n\n<span id="page-7-0"></span>real text\n\n'
        '{1}---------\n\nmore <span id="page-7-1"></span>text\n'
    )
    body = slice_pages(text, 0, 1)
    assert "<span" not in body
    assert "real text" in body
    assert "more text" in body


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("![](_page_4_Picture_1.jpeg)", "![](images/_page_4_Picture_1.jpeg)"),
        ("![](_page_2_Figure_0.jpeg)", "![](images/_page_2_Figure_0.jpeg)"),
        ("![alt](_page_2_Picture_3.png)", "![alt](images/_page_2_Picture_3.png)"),
        ("![](https://example.com/x.jpg)", "![](https://example.com/x.jpg)"),
        ("![](images/_page_2_Picture_3.png)", "![](images/_page_2_Picture_3.png)"),
    ],
)
def test_rewrite_image_links(body, expected):
    assert rewrite_image_links(body) == expected


def test_referenced_image_names_collects_filenames():
    body = "![](_page_4_Picture_1.jpeg)\n![](_page_5_Figure_2.png)\n"
    assert referenced_image_names(body) == {"_page_4_Picture_1.jpeg", "_page_5_Figure_2.png"}


@pytest.mark.parametrize(
    ("body", "title", "expected"),
    [
        ("# The Graveyard\n\ntext", "The Graveyard", "text"),
        ("#### · THE GRAVEYARD ·\n\ntext", "The Graveyard", "text"),
        ("## Other Title\n\ntext", "The Graveyard", "## Other Title\n\ntext"),
        ("text only", "The Graveyard", "text only"),
        ("", "The Graveyard", ""),
    ],
)
def test_strip_leading_heading(body, title, expected):
    assert strip_leading_heading(body, title) == expected


def _plan(idx, title, slug, ps, pe):
    return SectionPlan(index=idx, title=title, slug=slug, page_start=ps, page_end=pe, source="test")


def test_render_section_note_includes_navigation():
    plans = [
        _plan(1, "Intro", "intro", 0, 1),
        _plan(2, "Mid", "mid", 2, 3),
        _plan(3, "End", "end", 4, 5),
    ]
    out = render_section_note(
        plan=plans[1],
        plans=plans,
        body="Some body text.",
        book_title="Sample Book",
        book_slug="sample-book",
        source_ref="imports/books/sample.pdf",
        ingested_at="2026-01-01T00:00:00Z",
    )
    assert "section: Mid" in out
    assert "section_index: 2" in out
    assert "page_start: 3" in out  # 1-based in the rendered note
    assert "[[01-intro|Intro]]" in out
    assert "[[03-end|End]]" in out
    assert "## Text" in out
    assert "Some body text." in out


def test_render_section_note_marks_empty_body():
    plans = [_plan(1, "Empty", "empty", 0, 0)]
    out = render_section_note(
        plan=plans[0],
        plans=plans,
        body="",
        book_title="Book",
        book_slug="book",
        source_ref="x.pdf",
        ingested_at="2026-01-01T00:00:00Z",
    )
    assert "_(no text extracted" in out


def test_render_book_index_lists_sections():
    plans = [
        _plan(1, "Intro", "intro", 0, 0),
        _plan(2, "Mid", "mid", 1, 3),
    ]
    out = render_book_index(
        book_title="Sample Book",
        book_slug="sample-book",
        plans=plans,
        source_ref="imports/books/sample.pdf",
        ingested_at="2026-01-01T00:00:00Z",
        system="osr",
        page_count=10,
        plan_source="pdf-outline",
    )
    assert "[[01-intro|Intro]]" in out
    assert "[[02-mid|Mid]]" in out
    assert "p. 1" in out
    assert "pp. 2–4" in out
    assert "system: osr" in out


def test_section_filename_format():
    assert section_filename(_plan(7, "Foo Bar", "foo-bar", 0, 1)) == "07-foo-bar.md"
    assert section_filename(_plan(12, "X", "x", 0, 0)) == "12-x.md"
