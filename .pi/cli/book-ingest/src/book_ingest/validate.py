from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from statistics import median
from typing import Any

from .models import QualityFinding, SectionPlan
from .notes import chapter_body_text, read_chapter, section_filename
from .planner import looks_like_ocr_noise, slugify

_LOCAL_IMAGE = re.compile(r"!\[[^\]]*\]\((images/[^)\s]+)\)")
_OVERSIZED_CHARS = 40_000
_OVERSIZED_PAGES = 20
_OVERSIZED_RATIO = 0.30
_UNEVEN_GINI = 0.65
_UNEVEN_MAX_MEDIAN = 8.0
_UNEVEN_TOP1_RATIO = 0.50
_SPARSE_PLAN_MIN_PAGES = 40
_SPARSE_PLAN_RATIO = 8
_SPARSE_PLAN_HARD_PAGES = 60
_SPARSE_PLAN_HARD_SECTIONS = 10
_TINY_BYTES = 200


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _gini(xs: list[int]) -> float:
    xs = sorted(xs)
    n = len(xs)
    s = sum(xs)
    if n == 0 or s == 0:
        return 0.0
    return (2 * sum((i + 1) * v for i, v in enumerate(xs)) / (n * s)) - (n + 1) / n


def default_marker_report() -> dict[str, Any]:
    return {
        "duration_seconds": 0.0,
        "exception": None,
        "warnings": [],
        "llm": {"mode": "no", "requested": 0, "succeeded": 0, "calls": []},
    }


def read_existing_marker_report(book_dir: Path) -> dict[str, Any]:
    report_path = book_dir / ".ingest" / "report.json"
    if not report_path.exists():
        return default_marker_report()
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return default_marker_report()
    marker = data.get("marker")
    return marker if isinstance(marker, dict) else default_marker_report()


def plans_from_chapters(book_dir: Path) -> list[SectionPlan]:
    plans: list[SectionPlan] = []
    for path in sorted(book_dir.glob("*.md")):
        if path.name.startswith((".", "__")):
            continue
        fm, _ = read_chapter(path)
        if not fm:
            continue
        title = str(fm.get("section") or path.stem)
        index = int(fm.get("section_index") or _index_from_name(path) or len(plans) + 1)
        page_start = int(fm.get("page_start") or 1) - 1
        page_end = int(fm.get("page_end") or page_start + 1) - 1
        stem_slug = re.sub(r"^\d+-", "", path.stem)
        plans.append(
            SectionPlan(
                index=index,
                title=title,
                slug=stem_slug or slugify(title),
                page_start=page_start,
                page_end=page_end,
                source="chapter-frontmatter",
            )
        )
    return sorted(plans, key=lambda p: p.index)


def validate_book_dir(
    book_dir: Path,
    plans: list[SectionPlan] | None = None,
    *,
    overview_path: Path | None = None,
    marker: dict[str, Any] | None = None,
    stats: dict[str, Any] | None = None,
    extra_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate an ingested book directory and return the current report."""
    findings: list[QualityFinding] = []
    plans = plans if plans is not None else plans_from_chapters(book_dir)
    overview_path = overview_path or (book_dir / f"__{book_dir.name}.md")
    marker = marker or default_marker_report()

    chapter_texts: dict[str, str] = {}
    body_sizes: dict[str, int] = {}
    for plan in plans:
        path = book_dir / section_filename(plan)
        if not path.exists():
            continue
        text = _read(path)
        chapter_texts[plan.slug] = text
        body_sizes[plan.slug] = len(chapter_body_text(text).strip())

    total_body_chars = sum(body_sizes.values())
    total_pages = int(stats.get("pages", 0)) if isinstance(stats, dict) else 0
    if total_pages <= 0:
        total_pages = max((p.page_end for p in plans), default=-1) + 1

    seen_slugs: dict[str, int] = {}
    last_page_end = -1
    char_sizes: list[int] = []

    for plan in plans:
        path = book_dir / section_filename(plan)
        if not path.exists():
            findings.append(
                QualityFinding(
                    code="section_file_missing",
                    severity="error",
                    detail={"section": plan.slug, "expected": str(path.relative_to(book_dir))},
                )
            )
            continue

        text = chapter_texts.get(plan.slug, _read(path))
        body_only = chapter_body_text(text)
        size = body_sizes.get(plan.slug, len(body_only.strip()))
        char_sizes.append(size)
        pages = plan.page_end - plan.page_start + 1
        chars_ratio = size / max(1, total_body_chars)
        pages_ratio = pages / max(1, total_pages)

        if size == 0 or "_(no text extracted" in body_only:
            findings.append(
                QualityFinding(
                    code="empty_section_body", severity="warning", detail={"section": plan.slug}
                )
            )
        else:
            if (
                size > _OVERSIZED_CHARS
                or pages > _OVERSIZED_PAGES
                or chars_ratio > _OVERSIZED_RATIO
                or pages_ratio > _OVERSIZED_RATIO
            ):
                findings.append(
                    QualityFinding(
                        code="oversized_section",
                        severity="warning",
                        detail={
                            "section": plan.slug,
                            "chars": size,
                            "pages": pages,
                            "chars_ratio": chars_ratio,
                            "pages_ratio": pages_ratio,
                            "limits": {
                                "chars": _OVERSIZED_CHARS,
                                "pages": _OVERSIZED_PAGES,
                                "ratio": _OVERSIZED_RATIO,
                            },
                        },
                    )
                )
            if size < _TINY_BYTES:
                findings.append(
                    QualityFinding(
                        code="tiny_section",
                        severity="warning",
                        detail={"section": plan.slug, "size": size, "limit": _TINY_BYTES},
                    )
                )

        if looks_like_ocr_noise(plan.title):
            findings.append(
                QualityFinding(
                    code="title_looks_like_ocr_noise",
                    severity="warning",
                    detail={"section": plan.slug, "title": plan.title},
                )
            )

        if plan.slug in seen_slugs:
            findings.append(
                QualityFinding(
                    code="duplicate_slug",
                    severity="warning",
                    detail={"slug": plan.slug, "occurrence": seen_slugs[plan.slug] + 1},
                )
            )
        seen_slugs[plan.slug] = seen_slugs.get(plan.slug, 0) + 1

        if plan.page_start < last_page_end:
            findings.append(
                QualityFinding(
                    code="non_monotonic_pages",
                    severity="warning",
                    detail={
                        "section": plan.slug,
                        "page_start": plan.page_start,
                        "previous_end": last_page_end,
                    },
                )
            )
        last_page_end = max(last_page_end, plan.page_end)

        for m in _LOCAL_IMAGE.finditer(text):
            href = m.group(1)
            target = book_dir / href
            if not target.exists():
                findings.append(
                    QualityFinding(
                        code="broken_image_target",
                        severity="warning",
                        detail={"section": plan.slug, "image": href},
                    )
                )

    findings.extend(_distribution_findings(char_sizes, len(plans), total_body_chars, total_pages))

    if not overview_path.exists():
        findings.append(
            QualityFinding(
                code="book_index_missing", severity="error", detail={"path": str(overview_path)}
            )
        )

    marker_exception = marker.get("exception") if isinstance(marker, dict) else None
    if marker_exception:
        findings.append(
            QualityFinding(code="marker_exception", severity="error", detail=marker_exception)
        )
    marker_warnings = marker.get("warnings") if isinstance(marker, dict) else []
    if marker_warnings:
        findings.append(
            QualityFinding(
                code="marker_warnings",
                severity="warning",
                detail={"count": len(marker_warnings), "warnings": marker_warnings[:10]},
            )
        )
    llm = marker.get("llm", {}) if isinstance(marker, dict) else {}
    calls = llm.get("calls", []) if isinstance(llm, dict) else []
    failed = sum(1 for call in calls if call.get("status") == "failed")
    if failed:
        findings.append(
            QualityFinding(
                code="llm_calls_failed",
                severity="warning",
                detail={"failed": failed, "of": len(calls)},
            )
        )

    for finding in extra_findings or []:
        findings.append(
            QualityFinding(
                code=str(finding.get("code")),
                severity=str(finding.get("severity", "warning")),
                detail=dict(finding.get("detail") or {}),
            )
        )

    errors = [f for f in findings if f.severity == "error"]
    status = "failed" if errors else ("review" if findings else "ok")
    return {
        "status": status,
        "marker": marker,
        "findings": [asdict(f) for f in findings],
        "stats": stats or _stats_from_book_dir(book_dir, plans),
    }


def _distribution_findings(
    char_sizes: list[int], section_count: int, total_chars: int, total_pages: int
) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    if char_sizes and total_chars > 0:
        max_chars = max(char_sizes)
        median_chars = float(median(char_sizes))
        max_median_ratio = max_chars / median_chars if median_chars > 0 else 0.0
        gini = _gini(char_sizes)
        top1_ratio = max_chars / total_chars
        if (
            max_median_ratio > _UNEVEN_MAX_MEDIAN
            or gini > _UNEVEN_GINI
            or top1_ratio > _UNEVEN_TOP1_RATIO
        ):
            findings.append(
                QualityFinding(
                    code="uneven_section_distribution",
                    severity="warning",
                    detail={
                        "sections": section_count,
                        "total_chars": total_chars,
                        "max_chars": max_chars,
                        "median_chars": median_chars,
                        "max_median_ratio": max_median_ratio,
                        "gini": gini,
                        "top1_ratio": top1_ratio,
                        "limits": {
                            "gini": _UNEVEN_GINI,
                            "max_median": _UNEVEN_MAX_MEDIAN,
                            "top1_ratio": _UNEVEN_TOP1_RATIO,
                        },
                    },
                )
            )

    if (
        total_pages >= _SPARSE_PLAN_MIN_PAGES and section_count < total_pages / _SPARSE_PLAN_RATIO
    ) or (total_pages >= _SPARSE_PLAN_HARD_PAGES and section_count < _SPARSE_PLAN_HARD_SECTIONS):
        findings.append(
            QualityFinding(
                code="suspicious_sparse_plan",
                severity="warning",
                detail={
                    "sections": section_count,
                    "pages": total_pages,
                    "limits": {
                        "min_pages": _SPARSE_PLAN_MIN_PAGES,
                        "ratio": _SPARSE_PLAN_RATIO,
                        "hard_pages": _SPARSE_PLAN_HARD_PAGES,
                        "hard_sections": _SPARSE_PLAN_HARD_SECTIONS,
                    },
                },
            )
        )
    return findings


def _stats_from_book_dir(book_dir: Path, plans: list[SectionPlan]) -> dict[str, Any]:
    chars_total = 0
    for path in book_dir.glob("*.md"):
        if not path.name.startswith((".", "__")):
            chars_total += len(chapter_body_text(_read(path)))
    return {
        "sections": len(plans),
        "pages": max((p.page_end for p in plans), default=-1) + 1,
        "images_extracted": len(list((book_dir / "images").glob("*")))
        if (book_dir / "images").exists()
        else 0,
        "chars_total": chars_total,
    }


def _index_from_name(path: Path) -> int | None:
    m = re.match(r"^(\d+)-", path.stem)
    return int(m.group(1)) if m else None
