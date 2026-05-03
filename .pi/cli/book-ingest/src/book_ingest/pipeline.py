from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from .config import LLMConfig
from .converter import Converted, ConvertOptions, convert, marker_version
from .notes import (
    chapter_body_text,
    referenced_image_names,
    render_book_overview,
    render_section_note,
    rendered_section_body,
    rewrite_image_links,
    section_filename,
    slice_pages,
    write_referenced_images,
)
from .planner import book_title_from, plan_sections, slugify
from .validate import validate_book_dir


@dataclass(frozen=True)
class IngestOptions:
    output_root: Path
    project_root: Path
    force: bool
    dry_run: bool
    keep_backup: bool


@dataclass
class IngestResult:
    status: str
    book_slug: str
    overview_path: Path
    chapter_dir: Path
    output_path: Path
    section_count: int
    page_count: int
    quality_status: str
    plan_source: str
    findings: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    report_path: Path
    system: str
    next_steps: list[dict[str, Any]]
    skipped: bool = False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def overview_filename(book_slug: str) -> str:
    return f"__{book_slug}.md"


def _system_tag(text: str) -> str:
    """Return the ingest-time system tag.

    Ingest intentionally does not guess rules systems from brittle text
    heuristics. Use `book-ingest classify-system <slug>` after ingest for the
    metered LLM classifier, which reads bounded front/back matter evidence and
    updates `.ingest/provenance.json` plus the overview.
    """
    del text
    return "unknown"


def _source_ref(pdf: Path, project_root: Path) -> str:
    try:
        return pdf.relative_to(project_root).as_posix()
    except ValueError:
        return pdf.as_posix()


def _provenance_path(book_dir: Path) -> Path:
    return book_dir / ".ingest" / "provenance.json"


def _existing_record(book_dir: Path) -> dict[str, Any] | None:
    record = _provenance_path(book_dir)
    if not record.exists():
        return None
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _atomic_install_book_dir(
    staged_dir: Path,
    target_dir: Path,
    *,
    force: bool,
) -> Path | None:
    backup_dir: Path | None = None
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    if target_dir.exists() and not force:
        raise click.ClickException(f"target {target_dir} already exists; pass --force to replace")
    try:
        if target_dir.exists():
            backup_dir = target_dir.with_name(f".{target_dir.name}.{timestamp}.bak")
            target_dir.replace(backup_dir)
        staged_dir.replace(target_dir)
    except Exception:
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        if backup_dir is not None and backup_dir.exists():
            backup_dir.replace(target_dir)
        raise
    return backup_dir


def _utc_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slice_plan_body(markdown_text: str, plan: Any) -> str:
    if getattr(plan, "char_start", None) is not None:
        raw = markdown_text[plan.char_start : plan.char_end]
        raw = re.sub(r"^\{\d+\}-+\s*$", "", raw, flags=re.MULTILINE)
        return raw.strip()
    return slice_pages(markdown_text, plan.page_start, plan.page_end)


def _marker_report(converted: Converted, llm: LLMConfig) -> dict[str, Any]:
    return {
        "duration_seconds": round(converted.duration_seconds, 3),
        "exception": None,
        "warnings": converted.warnings,
        "llm": {
            "mode": llm.mode,
            "requested": converted.llm_calls.requested,
            "succeeded": converted.llm_calls.succeeded,
            "calls": converted.llm_calls.calls,
        },
    }


def _next_steps(result: IngestResult, *, api_key_present: bool) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if result.findings:
        steps.append(
            {
                "id": "review_findings",
                "required": False,
                "summary": _findings_summary(result.findings),
                "report_path": str(result.report_path),
            }
        )
    if api_key_present:
        steps.extend(
            [
                {
                    "id": "classify_system",
                    "required": False,
                    "summary": "Classify the book's rules system from bounded front/back matter evidence (metered).",
                    "command": f"book-ingest classify-system {result.book_slug}",
                },
                {
                    "id": "summarize",
                    "required": False,
                    "summary": "Generate detailed retrieval summaries only for chapters too long for full-text tagging (metered).",
                    "command": f"book-ingest summarize {result.book_slug} --long-only",
                },
                {
                    "id": "tag_book",
                    "required": False,
                    "summary": "Classify chapters with Obsidian tags; uses full text for small chapters and summaries for long ones (metered).",
                    "command": f"book-ingest tag {result.book_slug}",
                },
            ]
        )
    steps.append({"id": "qmd_refresh", "required": True, "command": "qmd update && qmd embed"})
    return steps


def _findings_summary(findings: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for finding in findings:
        code = str(finding.get("code", "unknown"))
        counts[code] = counts.get(code, 0) + 1
    return "; ".join(f"{count} {code}" for code, count in sorted(counts.items()))


def ingest_pdf(
    pdf: Path,
    options: IngestOptions,
    convert_options: ConvertOptions,
    llm: LLMConfig,
) -> IngestResult:
    if pdf.suffix.lower() != ".pdf":
        raise click.ClickException(f"not a PDF: {pdf}")
    source_hash = _sha256(pdf)
    book_slug = slugify(pdf.stem)
    target_dir = options.output_root / book_slug
    target_overview = target_dir / overview_filename(book_slug)
    report_path = target_dir / ".ingest" / "report.json"
    target_provenance = _provenance_path(target_dir)

    existing = _existing_record(target_dir)
    existing_page_range = None
    if existing:
        existing_page_range = (existing.get("options") or {}).get("page_range")
    if (
        existing
        and existing.get("source_hash") == source_hash
        and existing_page_range == convert_options.page_range
        and target_overview.exists()
        and target_provenance.exists()
        and not options.force
    ):
        click.echo(f"skip {pdf.name}: source hash matches existing ingest", err=True)
        result = IngestResult(
            status="skipped",
            book_slug=book_slug,
            overview_path=target_overview,
            chapter_dir=target_dir,
            output_path=target_dir,
            section_count=int(existing.get("section_count", 0)),
            page_count=int(existing.get("page_count", 0)),
            quality_status=str(existing.get("quality_status", "unknown")),
            plan_source=str(existing.get("plan_source", "unknown")),
            findings=[],
            warnings=[],
            errors=[],
            report_path=report_path,
            system=str(existing.get("system", "unknown")),
            next_steps=[],
            skipped=True,
        )
        result.next_steps = _next_steps(result, api_key_present=bool(llm.api_key))
        return result

    if options.dry_run:
        click.echo(f"would ingest {pdf} -> {target_dir} + {target_overview}", err=True)
        result = IngestResult(
            status="dry-run",
            book_slug=book_slug,
            overview_path=target_overview,
            chapter_dir=target_dir,
            output_path=target_dir,
            section_count=0,
            page_count=0,
            quality_status="unknown",
            plan_source="unknown",
            findings=[],
            warnings=[],
            errors=[],
            report_path=report_path,
            system="unknown",
            next_steps=[],
        )
        result.next_steps = _next_steps(result, api_key_present=bool(llm.api_key))
        return result

    options.output_root.mkdir(parents=True, exist_ok=True)
    ingested_at = _utc_iso()

    with tempfile.TemporaryDirectory(prefix=f"book-ingest-{book_slug}-") as td:
        scratch = Path(td)
        converted = convert(pdf, convert_options)
        markdown_text = converted.markdown
        page_count = len(converted.page_stats)
        if page_count == 0:
            page_count = max((p.get("page_id", -1) for p in converted.page_stats), default=-1) + 1

        book_title = book_title_from(pdf)
        plans, plan_source, planning_diag = plan_sections(
            pdf, converted.table_of_contents, page_count, book_title, markdown_text
        )
        system = _system_tag(markdown_text)
        source_ref = _source_ref(pdf, options.project_root)

        staged_root = scratch / "staged"
        staged_dir = staged_root / book_slug
        staged_overview = staged_dir / overview_filename(book_slug)
        staged_dir.mkdir(parents=True, exist_ok=True)
        target_images = staged_dir / "images"

        section_inputs: list[tuple[Any, str]] = []
        omitted_empty_sections: list[dict[str, Any]] = []
        for plan in plans:
            raw_body = _slice_plan_body(markdown_text, plan)
            rewritten = rewrite_image_links(raw_body)
            if not rendered_section_body(rewritten, plan.title):
                omitted_empty_sections.append(
                    {"section": plan.slug, "title": plan.title, "index": plan.index}
                )
                continue
            section_inputs.append((plan, rewritten))
        if not section_inputs:
            raise click.ClickException(f"no non-empty sections extracted from {pdf}")
        plans = [replace(plan, index=i) for i, (plan, _) in enumerate(section_inputs, start=1)]
        section_inputs = [(plans[i], body) for i, (_, body) in enumerate(section_inputs)]

        all_referenced: set[str] = set()
        for plan, rewritten in section_inputs:
            referenced = referenced_image_names(rewritten)
            all_referenced |= referenced
            note = render_section_note(
                plan=plan,
                plans=plans,
                body=rewritten,
                book_title=book_title,
                book_slug=book_slug,
                source_ref=source_ref,
                ingested_at=ingested_at,
            )
            (staged_dir / section_filename(plan)).write_text(note, encoding="utf-8")

        _, missing = write_referenced_images(converted.images, target_images, all_referenced)

        book_overview = render_book_overview(
            book_title=book_title,
            book_slug=book_slug,
            plans=plans,
            source_ref=source_ref,
            ingested_at=ingested_at,
            system=system,
            page_count=page_count,
            plan_source=plan_source,
        )
        staged_overview.write_text(book_overview, encoding="utf-8")

        ingest_dir = staged_dir / ".ingest"
        ingest_dir.mkdir(parents=True, exist_ok=True)

        marker = _marker_report(converted, llm)
        stats = {
            "sections": len(plans),
            "pages": page_count,
            "images_extracted": len(converted.images),
            "sections_omitted_empty": len(omitted_empty_sections),
            "omitted_empty_sections": omitted_empty_sections,
            "chars_total": sum(
                len(
                    chapter_body_text(
                        (staged_dir / section_filename(plan)).read_text(encoding="utf-8")
                    )
                )
                for plan in plans
            ),
        }
        extra_findings = [
            {
                "code": "missing_source_image",
                "severity": "warning",
                "detail": {"image": name},
            }
            for name in sorted(missing)
        ]
        report = validate_book_dir(
            staged_dir,
            plans=plans,
            overview_path=staged_overview,
            marker=marker,
            stats=stats,
            extra_findings=extra_findings,
        )
        report["planning"] = planning_diag
        (ingest_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

        provenance = {
            "book_slug": book_slug,
            "book_title": book_title,
            "source_pdf": source_ref,
            "source_hash": source_hash,
            "ingested_at": ingested_at,
            "engine": "marker-sdk",
            "engine_version": marker_version(),
            "page_count": page_count,
            "section_count": len(plans),
            "sections_omitted_empty": len(omitted_empty_sections),
            "omitted_empty_sections": omitted_empty_sections,
            "plan_source": plan_source,
            "system": system,
            "systems": [] if system == "unknown" else [system],
            "quality_status": report["status"],
            "llm": llm.redacted(),
            "options": {
                "device": convert_options.device,
                "device_source": convert_options.device_source,
                "page_range": convert_options.page_range,
                "page_range_source": convert_options.page_range_source,
                "force_ocr": convert_options.force_ocr,
                "force_ocr_source": convert_options.force_ocr_source,
                "batch_sizes": {
                    "layout": convert_options.layout_batch_size,
                    "detection": convert_options.detection_batch_size,
                    "recognition": convert_options.recognition_batch_size,
                    "table_rec": convert_options.table_rec_batch_size,
                },
                "batch_size_sources": convert_options.batch_size_sources,
                "torch": {
                    "cuda_available": convert_options.torch_cuda_available,
                    "cuda_device": convert_options.torch_cuda_device,
                },
            },
        }
        (ingest_dir / "provenance.json").write_text(
            json.dumps(provenance, indent=2), encoding="utf-8"
        )

        findings = list(report["findings"])
        warnings_ = [f for f in findings if f.get("severity") == "warning"]
        errors = [f for f in findings if f.get("severity") == "error"]
        result = IngestResult(
            status=report["status"],
            book_slug=book_slug,
            overview_path=target_overview,
            chapter_dir=target_dir,
            output_path=target_dir,
            section_count=len(plans),
            page_count=page_count,
            quality_status=report["status"],
            plan_source=plan_source,
            findings=findings,
            warnings=warnings_,
            errors=errors,
            report_path=report_path,
            system=system,
            next_steps=[],
        )
        result.next_steps = _next_steps(result, api_key_present=bool(llm.api_key))

        if report["status"] == "failed":
            click.echo(
                f"validation failed for {pdf.name}; staged output kept under {staged_dir}", err=True
            )
            return result

        backup_dir = _atomic_install_book_dir(
            staged_dir,
            target_dir,
            force=options.force or (existing is not None),
        )
        if not options.keep_backup:
            if backup_dir is not None:
                shutil.rmtree(backup_dir, ignore_errors=True)
        elif backup_dir is not None:
            click.echo(f"previous chapter dir preserved at {backup_dir}", err=True)

    return result
