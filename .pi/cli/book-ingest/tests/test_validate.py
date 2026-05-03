from pathlib import Path

from book_ingest.models import SectionPlan
from book_ingest.notes import render_book_overview, render_section_note, section_filename
from book_ingest.validate import validate_book_dir


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
            page_count=10,
            plan_source="test",
        ),
        encoding="utf-8",
    )
    (book_dir / ".ingest").mkdir()
    return book_dir


def _codes(report):
    return {f["code"] for f in report["findings"]}


def test_validate_clean_run(tmp_path):
    plans = [_plan(1, "Intro", "intro", 0, 1), _plan(2, "Body", "body", 2, 3)]
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
