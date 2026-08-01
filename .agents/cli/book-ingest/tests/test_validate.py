from pathlib import Path

from book_ingest.models import SectionPlan
from book_ingest.notes import render_book_overview, render_section_note, section_filename
from book_ingest.validate import _gini, validate_book_dir


def _plan(idx, title, slug, ps, pe):
    return SectionPlan(index=idx, title=title, slug=slug, page_start=ps, page_end=pe, source="test")


def _write_book(tmp: Path, plans, *, body_overrides=None) -> Path:
    root = tmp / "books"
    root.mkdir()
    book_dir = root / "book"
    book_dir.mkdir()
    body_overrides = body_overrides or {}
    for plan in plans:
        body = body_overrides.get(plan.slug, "Real body content. " * 20)
        note = render_section_note(
            plan=plan,
            plans=plans,
            body=body,
            book_title="Book",
            book_slug="book",
            source_ref="x.pdf",
            ingested_at="2026-01-01T00:00:00Z",
        )
        (book_dir / section_filename(plan)).write_text(note, encoding="utf-8")
    (book_dir / "__book.md").write_text(
        render_book_overview(
            book_title="Book",
            book_slug="book",
            plans=plans,
            source_ref="x.pdf",
            ingested_at="2026-01-01T00:00:00Z",
            system="unknown",
            page_count=max((p.page_end for p in plans), default=-1) + 1,
            plan_source="test",
        ),
        encoding="utf-8",
    )
    (book_dir / ".ingest").mkdir()
    return book_dir


def _codes(report):
    return {f["code"] for f in report["findings"]}


def test_validate_clean_run(tmp_path):
    plans = [
        _plan(1, "Intro", "intro", 0, 0),
        _plan(2, "Body", "body", 1, 1),
        _plan(3, "Middle", "middle", 2, 2),
        _plan(4, "End", "end", 3, 3),
    ]
    book_dir = _write_book(tmp_path, plans)
    report = validate_book_dir(book_dir, plans=plans)
    assert report["status"] == "ok"
    assert report["findings"] == []


def test_validate_detects_tiny_section(tmp_path):
    plans = [_plan(1, "Long", "long", 0, 0), _plan(2, "Short", "short", 1, 1)]
    book_dir = _write_book(tmp_path, plans, body_overrides={"short": "x"})
    report = validate_book_dir(book_dir, plans=plans)
    assert "tiny_section" in _codes(report)
    assert report["status"] == "review"


def test_validate_detects_missing_image(tmp_path):
    plans = [_plan(1, "S", "s", 0, 0)]
    book_dir = _write_book(
        tmp_path,
        plans,
        body_overrides={"s": "lorem ipsum " * 30 + "\n\n![](images/missing.jpeg)\n"},
    )
    report = validate_book_dir(book_dir, plans=plans)
    assert "broken_image_target" in _codes(report)


def test_validate_detects_missing_book_index(tmp_path):
    plans = [_plan(1, "S", "s", 0, 0)]
    book_dir = _write_book(tmp_path, plans)
    (book_dir / "__book.md").unlink()
    report = validate_book_dir(book_dir, plans=plans)
    assert report["status"] == "failed"
    assert "book_index_missing" in _codes(report)


def test_validate_detects_missing_section_file(tmp_path):
    plans = [_plan(1, "S", "s", 0, 0), _plan(2, "T", "t", 1, 1)]
    book_dir = _write_book(tmp_path, plans)
    (book_dir / section_filename(plans[1])).unlink()
    report = validate_book_dir(book_dir, plans=plans)
    assert report["status"] == "failed"
    assert "section_file_missing" in _codes(report)


def test_validate_detects_non_monotonic_pages(tmp_path):
    plans = [_plan(1, "A", "a", 5, 7), _plan(2, "B", "b", 2, 3)]
    book_dir = _write_book(tmp_path, plans)
    report = validate_book_dir(book_dir, plans=plans)
    assert "non_monotonic_pages" in _codes(report)


def test_validate_reports_llm_failures(tmp_path):
    plans = [_plan(1, "S", "s", 0, 0)]
    book_dir = _write_book(tmp_path, plans)
    marker = {
        "duration_seconds": 1,
        "exception": None,
        "warnings": [],
        "llm": {
            "mode": "images-only",
            "requested": 1,
            "succeeded": 0,
            "calls": [{"status": "failed", "error_type": "RateLimitError"}],
        },
    }
    report = validate_book_dir(book_dir, plans=plans, marker=marker)
    assert "llm_calls_failed" in _codes(report)


def test_validate_oversized_section_fires_for_50000_chars(tmp_path):
    plans = [_plan(1, "Huge", "huge", 0, 0)]
    book_dir = _write_book(tmp_path, plans, body_overrides={"huge": "x" * 50_000})

    report = validate_book_dir(book_dir, plans=plans)

    assert "oversized_section" in _codes(report)
    finding = next(f for f in report["findings"] if f["code"] == "oversized_section")
    assert finding["detail"]["chars"] >= 50_000


def test_validate_oversized_section_fires_for_25_pages(tmp_path):
    plans = [_plan(1, "Long Span", "long-span", 0, 24), _plan(2, "Tail", "tail", 25, 29)]
    book_dir = _write_book(tmp_path, plans)

    report = validate_book_dir(book_dir, plans=plans)

    assert "oversized_section" in _codes(report)
    finding = next(f for f in report["findings"] if f["code"] == "oversized_section")
    assert finding["detail"]["pages"] == 25


def test_validate_uneven_section_distribution_fires_for_dfd_shape(tmp_path):
    plans = [_plan(1, f"Section {i}", f"section-{i}", i, i) for i in range(1, 8)]
    body_overrides = {"section-1": "x" * 7000}
    body_overrides.update({f"section-{i}": "x" * 500 for i in range(2, 8)})
    book_dir = _write_book(tmp_path, plans, body_overrides=body_overrides)

    report = validate_book_dir(book_dir, plans=plans)

    assert "uneven_section_distribution" in _codes(report)


def test_validate_suspicious_sparse_plan_fires_for_66_pages_7_sections(tmp_path):
    plans = [
        _plan(1, "Section 1", "section-1", 0, 8),
        _plan(2, "Section 2", "section-2", 9, 17),
        _plan(3, "Section 3", "section-3", 18, 26),
        _plan(4, "Section 4", "section-4", 27, 35),
        _plan(5, "Section 5", "section-5", 36, 44),
        _plan(6, "Section 6", "section-6", 45, 53),
        _plan(7, "Section 7", "section-7", 54, 65),
    ]
    body_overrides = {plan.slug: "x" * 500 for plan in plans}
    book_dir = _write_book(tmp_path, plans, body_overrides=body_overrides)

    report = validate_book_dir(book_dir, plans=plans)

    assert "suspicious_sparse_plan" in _codes(report)


def test_gini_helper():
    assert _gini([1, 1, 1, 1]) == 0.0
    assert _gini([0, 0, 0, 4]) == 0.75
    assert _gini([]) == 0.0
