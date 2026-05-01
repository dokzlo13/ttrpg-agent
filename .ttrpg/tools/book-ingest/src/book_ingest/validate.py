from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from .models import QualityFinding, SectionPlan
from .notes import section_filename
from .planner import looks_like_ocr_noise

_LOCAL_IMAGE = re.compile(r"!\[[^\]]*\]\((images/[^)\s]+)\)")


_OVERSIZED_BYTES = 200_000
_TINY_BYTES = 200


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def validate_book_dir(book_dir: Path, plans: list[SectionPlan] | None = None) -> dict:
    """Validate an ingested book directory. Returns a JSON-serializable quality report."""
    findings: list[QualityFinding] = []

    manifest_path = book_dir / ".ingest" / "manifest.json"
    if plans is None:
        if manifest_path.exists():
            data = json.loads(_read(manifest_path))
            plans = [SectionPlan(**s) for s in data.get("sections", [])]
        else:
            plans = []
            findings.append(
                QualityFinding(
                    code="manifest_missing", severity="error", detail={"path": str(manifest_path)}
                )
            )

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

        body = _read(path)
        without_frontmatter = re.sub(r"^---.*?---\n+", "", body, count=1, flags=re.DOTALL)
        parts = re.split(r"^## Text\s*$", without_frontmatter, maxsplit=1, flags=re.MULTILINE)
        body_only = parts[1] if len(parts) > 1 else parts[0]
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

        for m in _LOCAL_IMAGE.finditer(body):
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

    if not (book_dir / "_book.md").exists():
        findings.append(
            QualityFinding(code="book_index_missing", severity="error", detail={"path": "_book.md"})
        )

    errors = [f for f in findings if f.severity == "error"]
    warnings_ = [f for f in findings if f.severity == "warning"]
    status = "failed" if errors else ("review_required" if warnings_ else "ok")
    return {
        "status": status,
        "errors": [asdict(f) for f in errors],
        "warnings": [asdict(f) for f in warnings_],
    }
