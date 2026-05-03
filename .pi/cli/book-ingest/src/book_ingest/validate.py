from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import QualityFinding, SectionPlan
from .notes import chapter_body_text, read_chapter, section_filename
from .planner import looks_like_ocr_noise, slugify

_LOCAL_IMAGE = re.compile(r"!\[[^\]]*\]\((images/[^)\s]+)\)")
_OVERSIZED_BYTES = 200_000
_TINY_BYTES = 200


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


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

    seen_slugs: dict[str, int] = {}
    last_page_end = -1

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

        text = _read(path)
        body_only = chapter_body_text(text)
        size = len(body_only.strip())

        if size == 0 or "_(no text extracted" in body_only:
            findings.append(
                QualityFinding(
                    code="empty_section_body", severity="warning", detail={"section": plan.slug}
                )
            )
        elif size > _OVERSIZED_BYTES:
            findings.append(
                QualityFinding(
                    code="oversized_section",
                    severity="warning",
                    detail={"section": plan.slug, "size": size, "limit": _OVERSIZED_BYTES},
                )
            )
        elif size < _TINY_BYTES:
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
