from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import click
from pypdf import PdfReader

from . import SCHEMA_VERSION
from .config import LLMConfig
from .marker_run import (
    MarkerInvocation,
    marker_version,
    redacted_command_record,
    run_marker,
)
from .notes import (
    copy_referenced_images,
    referenced_image_names,
    render_book_index,
    render_section_note,
    rewrite_image_links,
    section_filename,
    slice_pages,
)
from .planner import book_title_from, plan_sections, slugify
from .validate import validate_book_dir


@dataclass(frozen=True)
class IngestOptions:
    output_root: Path
    cache_root: Path
    project_root: Path
    force: bool
    dry_run: bool
    keep_cache: bool
    keep_backup: bool


@dataclass
class IngestResult:
    status: str
    book_slug: str
    output_path: Path
    section_count: int
    page_count: int
    quality_status: str
    plan_source: str
    schema_version: int
    warnings: list[dict]
    errors: list[dict]
    cache_path: Path | None
    skipped: bool = False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _system_tag(text: str) -> str:
    sample = text[:50_000]
    hd = len(re.findall(r"\bHD\b", sample))
    challenge = len(re.findall(r"\bChallenge\b", sample))
    if hd >= challenge + 2:
        return "osr"
    if challenge >= hd + 2:
        return "5e"
    return "unknown"


def _source_ref(pdf: Path, project_root: Path) -> str:
    try:
        return pdf.relative_to(project_root).as_posix()
    except ValueError:
        return pdf.as_posix()


def _existing_record(book_dir: Path) -> dict | None:
    record = book_dir / ".ingest.json"
    if not record.exists():
        return None
    try:
        return json.loads(record.read_text(encoding="utf-8"))
    except Exception:
        return None


def _atomic_install(staged: Path, target: Path, force: bool) -> Path | None:
    """Install ``staged`` to ``target``. Returns the backup path if one was created.

    The backup is named with a leading dot (``.<slug>.<timestamp>.bak``) so
    qmd's ``**/*.md`` glob skips it. Without the dot prefix, the backup
    would be indexed as duplicate book content.
    """
    backup: Path | None = None
    if target.exists():
        if not force:
            raise click.ClickException(f"target {target} already exists; pass --force to replace")
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup = target.with_name(f".{target.name}.{timestamp}.bak")
        target.replace(backup)
    try:
        try:
            staged.replace(target)
        except OSError:
            shutil.copytree(staged, target)
            shutil.rmtree(staged)
    except Exception:
        if backup is not None and not target.exists():
            backup.replace(target)
        raise
    return backup


def _utc_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _persist_to_cache(
    cache_dir: Path,
    md_dir: Path,
    json_dir: Path,
    redacted_cmds: list[dict],
) -> Path:
    """Copy raw Marker artifacts into the cache directory.

    Logs are written directly during ``run_marker`` (under ``cache_dir/logs``);
    this only persists the conversion outputs and the redacted command record.
    Existing artifact subdirs are replaced; ``logs/`` is left intact.
    """
    for sub in ("markdown", "json"):
        target = cache_dir / sub
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
    for src in md_dir.iterdir():
        shutil.copy2(src, cache_dir / "markdown" / src.name)
    for src in json_dir.iterdir():
        shutil.copy2(src, cache_dir / "json" / src.name)
    (cache_dir / "marker-cmd.json").write_text(
        json.dumps({"runs": redacted_cmds, "marker_version": marker_version()}, indent=2),
        encoding="utf-8",
    )
    return cache_dir


def _find_one(directory: Path, suffix: str) -> Path:
    matches = sorted(p for p in directory.iterdir() if p.is_file() and p.name.endswith(suffix))
    if not matches:
        raise click.ClickException(f"expected a {suffix} file in {directory}")
    return matches[0]


def _agent_next_text(result: IngestResult) -> str:
    quality_path = f"{result.output_path}/.ingest/quality.json"
    lines = [
        f"Wrote: {result.output_path} ({result.section_count} sections, {result.page_count} pages)",
        f"Plan source: {result.plan_source}",
        f"Quality: {result.quality_status} ({len(result.warnings)} warnings, {len(result.errors)} errors)",
        "",
        "Next:",
        f"  1. Inspect:  cat {quality_path} | jq",
        "  2. Index:    qmd update",
        "  3. Embed:    qmd embed",
    ]
    if result.warnings:
        lines.extend(["", "Warnings:"])
        for w in result.warnings[:10]:
            detail = w.get("detail") or {}
            target = detail.get("section") or detail.get("path") or detail.get("image") or ""
            lines.append(f"  - {w['code']}: {target}".rstrip())
        if len(result.warnings) > 10:
            lines.append(f"  ... ({len(result.warnings) - 10} more)")
    return "\n".join(lines) + "\n"


def ingest_pdf(
    pdf: Path,
    options: IngestOptions,
    invocation: MarkerInvocation,
    llm: LLMConfig,
) -> IngestResult:
    if pdf.suffix.lower() != ".pdf":
        raise click.ClickException(f"not a PDF: {pdf}")
    source_hash = _sha256(pdf)
    book_slug = slugify(pdf.stem)
    target_dir = options.output_root / book_slug

    existing = _existing_record(target_dir)
    if (
        existing
        and existing.get("source_hash") == source_hash
        and existing.get("schema_version") == SCHEMA_VERSION
        and not options.force
    ):
        click.echo(f"skip {pdf.name}: hash + schema match existing ingest", err=True)
        return IngestResult(
            status="skipped",
            book_slug=book_slug,
            output_path=target_dir,
            section_count=int(existing.get("section_count", 0)),
            page_count=int(existing.get("page_count", 0)),
            quality_status=str(existing.get("quality_status", "unknown")),
            plan_source=str(existing.get("plan_source", "unknown")),
            schema_version=int(existing.get("schema_version", 0)),
            warnings=[],
            errors=[],
            cache_path=None,
            skipped=True,
        )

    if existing and existing.get("schema_version", 0) != SCHEMA_VERSION and not options.force:
        click.echo(
            f"schema_version mismatch on {target_dir.name} "
            f"({existing.get('schema_version')} → {SCHEMA_VERSION}); auto-forcing re-ingest",
            err=True,
        )

    if options.dry_run:
        click.echo(f"would ingest {pdf} -> {target_dir}", err=True)
        return IngestResult(
            status="dry-run",
            book_slug=book_slug,
            output_path=target_dir,
            section_count=0,
            page_count=0,
            quality_status="unknown",
            plan_source="unknown",
            schema_version=SCHEMA_VERSION,
            warnings=[],
            errors=[],
            cache_path=None,
        )

    options.output_root.mkdir(parents=True, exist_ok=True)
    options.cache_root.mkdir(parents=True, exist_ok=True)
    ingested_at = _utc_iso()

    cache_dir = options.cache_root / source_hash.replace(":", "-")
    cache_dir.mkdir(parents=True, exist_ok=True)
    log_dir = cache_dir / "logs"

    with tempfile.TemporaryDirectory(prefix=f"book-ingest-{book_slug}-") as td:
        scratch = Path(td)
        md_run = run_marker(pdf, scratch, "markdown", invocation, log_dir=log_dir)
        json_run = run_marker(pdf, scratch, "json", invocation, log_dir=log_dir)
        marker_md_dir = md_run.output_dir
        marker_json_dir = json_run.output_dir
        markdown_path = _find_one(marker_md_dir, ".md")
        json_path = _find_one(marker_json_dir, ".json")

        markdown_text = markdown_path.read_text(encoding="utf-8", errors="replace")
        try:
            page_count = len(PdfReader(str(pdf)).pages)
        except Exception:
            page_count = 0

        pdf_metadata: dict[str, str] = {}
        try:
            raw_meta: dict = dict(PdfReader(str(pdf)).metadata or {})
            pdf_metadata = {str(k): str(v) for k, v in raw_meta.items()}
        except Exception:
            pdf_metadata = {}

        book_title = book_title_from(pdf, pdf_metadata)
        plans, plan_source = plan_sections(pdf, json_path, page_count, book_title)
        system = _system_tag(markdown_text)
        source_ref = _source_ref(pdf, options.project_root)

        staged_dir = scratch / "staged" / book_slug
        staged_dir.mkdir(parents=True, exist_ok=True)
        target_images = staged_dir / "images"

        all_referenced: set[str] = set()
        for plan in plans:
            raw_body = slice_pages(markdown_text, plan.page_start, plan.page_end)
            referenced = referenced_image_names(raw_body)
            all_referenced |= referenced
            rewritten = rewrite_image_links(raw_body)
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

        _, missing = copy_referenced_images(marker_md_dir, target_images, all_referenced)

        book_index = render_book_index(
            book_title=book_title,
            book_slug=book_slug,
            plans=plans,
            source_ref=source_ref,
            ingested_at=ingested_at,
            system=system,
            page_count=page_count,
            plan_source=plan_source,
        )
        (staged_dir / "_book.md").write_text(book_index, encoding="utf-8")

        ingest_dir = staged_dir / ".ingest"
        ingest_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "book_slug": book_slug,
            "book_title": book_title,
            "page_count": page_count,
            "plan_source": plan_source,
            "sections": [asdict(p) for p in plans],
        }
        (ingest_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        redacted_runs = [
            redacted_command_record(
                pdf,
                "markdown",
                invocation,
                log_path=md_run.log_path,
                duration_seconds=md_run.duration_seconds,
                returncode=md_run.returncode,
            ),
            redacted_command_record(
                pdf,
                "json",
                invocation,
                log_path=json_run.log_path,
                duration_seconds=json_run.duration_seconds,
                returncode=json_run.returncode,
            ),
        ]
        (ingest_dir / "marker.json").write_text(
            json.dumps({"runs": redacted_runs}, indent=2), encoding="utf-8"
        )

        quality = validate_book_dir(staged_dir, plans=plans)
        if missing:
            for name in sorted(missing):
                quality["warnings"].append(
                    {
                        "code": "missing_source_image",
                        "severity": "warning",
                        "detail": {"image": name},
                    }
                )
            if quality["status"] == "ok":
                quality["status"] = "review_required"

        if invocation.llm.enabled and not invocation.llm.api_key:
            quality["warnings"].append(
                {
                    "code": "marker_llm_requested_but_skipped",
                    "severity": "warning",
                    "detail": {"reason": "missing api key"},
                }
            )
            if quality["status"] == "ok":
                quality["status"] = "review_required"

        (ingest_dir / "quality.json").write_text(json.dumps(quality, indent=2), encoding="utf-8")

        provenance = {
            "schema_version": SCHEMA_VERSION,
            "book_slug": book_slug,
            "book_title": book_title,
            "source_pdf": source_ref,
            "source_hash": source_hash,
            "ingested_at": ingested_at,
            "engine": "marker",
            "engine_version": marker_version(),
            "page_count": page_count,
            "section_count": len(plans),
            "plan_source": plan_source,
            "system": system,
            "quality_status": quality["status"],
            "options": {
                "device": invocation.device,
                "workers": invocation.workers,
                "page_range": invocation.page_range,
                "force_ocr": invocation.force_ocr,
                "batch_sizes": {
                    "layout": invocation.layout_batch_size,
                    "detection": invocation.detection_batch_size,
                    "recognition": invocation.recognition_batch_size,
                    "table_rec": invocation.table_rec_batch_size,
                },
                "llm": invocation.llm.redacted(),
            },
        }
        (staged_dir / ".ingest.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")

        result = IngestResult(
            status="ok" if quality["status"] != "failed" else "failed",
            book_slug=book_slug,
            output_path=target_dir,
            section_count=len(plans),
            page_count=page_count,
            quality_status=quality["status"],
            plan_source=plan_source,
            schema_version=SCHEMA_VERSION,
            warnings=list(quality["warnings"]),
            errors=list(quality["errors"]),
            cache_path=None,
        )

        if options.keep_cache:
            _persist_to_cache(cache_dir, marker_md_dir, marker_json_dir, redacted_runs)
            result.cache_path = cache_dir
        else:
            # Default policy: drop the heavy markdown/json artifacts (the
            # canonical content lives under vault/library/books/<slug>/),
            # keep only logs/ for an audit trail. Pass --keep-cache to retain.
            for sub in ("markdown", "json"):
                shutil.rmtree(cache_dir / sub, ignore_errors=True)
            (cache_dir / "marker-cmd.json").unlink(missing_ok=True)
            if (cache_dir / "logs").exists():
                result.cache_path = cache_dir

        (ingest_dir / "agent-next.txt").write_text(_agent_next_text(result), encoding="utf-8")

        if quality["status"] == "failed":
            click.echo(
                f"validation failed for {pdf.name}; staged output kept under {staged_dir}", err=True
            )
            return result

        backup = _atomic_install(
            staged_dir, target_dir, force=options.force or (existing is not None)
        )
        if backup is not None and not options.keep_backup:
            shutil.rmtree(backup, ignore_errors=True)
        elif backup is not None:
            click.echo(f"previous output preserved at {backup}", err=True)

    return result
