from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click

from .config import build_env, find_project_root, resolve_llm_config, resolve_llm_mode
from .converter import ConvertOptions
from .overview import refresh_overview
from .pipeline import IngestOptions, IngestResult, ingest_pdf
from .summarize import cmd_summarize
from .system_classify import cmd_classify_system
from .tag import cmd_tag, cmd_tag_manual
from .validate import read_existing_marker_report, validate_book_dir


def _optional_int_env(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    return int(str(value).strip())


def _resolve_string_setting(
    cli_value: str | None, env: dict[str, str], env_key: str, default: str
) -> tuple[str, str]:
    if cli_value:
        return cli_value, "cli"
    if env.get(env_key):
        return env[env_key], "env"
    return default, "default"


def _resolve_optional_int_setting(
    cli_value: int | None, env: dict[str, str], env_key: str
) -> tuple[int | None, str]:
    if cli_value is not None:
        return cli_value, "cli"
    if env.get(env_key):
        return _optional_int_env(env.get(env_key)), "env"
    return None, "default"


def _torch_status() -> dict[str, Any]:
    try:
        import torch

        available = bool(torch.cuda.is_available())
        return {
            "cuda_available": available,
            "cuda_device": torch.cuda.get_device_name(0) if available else None,
            "torch_version": getattr(torch, "__version__", "unknown"),
        }
    except Exception as exc:
        return {"cuda_available": None, "cuda_device": None, "torch_error": type(exc).__name__}


def _sourced(name: str, value: Any, source: str) -> str:
    return f"{name}={value}[{source}]"


def _quote_if_needed(value: Any) -> str:
    text = str(value)
    return json.dumps(text) if any(ch.isspace() for ch in text) else text


def _format_run_status(
    *,
    llm: Any,
    device: str,
    device_source: str,
    page_range: str | None,
    page_range_source: str,
    force_ocr: bool,
    batch_sizes: dict[str, int | None],
    batch_sources: dict[str, str],
    torch_status: dict[str, Any],
) -> str:
    parts = [_sourced("llm.mode", llm.mode, llm.enabled_source)]
    if llm.enabled:
        parts.append(_sourced("llm.model", llm.model, llm.model_source))
        parts.append(_sourced("llm.concurrency", llm.max_concurrency, llm.max_concurrency_source))
    parts.append(_sourced("device", device, device_source))
    cuda = str(torch_status.get("cuda_available")).lower()
    parts.append(f"torch.cuda={cuda}")
    if gpu := torch_status.get("cuda_device"):
        parts.append(f"torch.gpu={_quote_if_needed(gpu)}")
    if page_range:
        parts.append(_sourced("page_range", page_range, page_range_source))
    if force_ocr:
        parts.append(_sourced("force_ocr", "true", "cli"))
    for name, value in batch_sizes.items():
        source = batch_sources.get(name, "unknown")
        if value is not None and source != "default":
            parts.append(_sourced(f"batch.{name}", value, source))
    return "book-ingest: " + " ".join(parts)


def _iter_pdfs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".pdf":
            raise click.ClickException("input must be a PDF file or directory of PDFs")
        return [input_path]
    pdfs = sorted(p for p in input_path.rglob("*.pdf") if p.is_file())
    if not pdfs:
        raise click.ClickException(f"no PDFs found under {input_path}")
    return pdfs


def _stringify_paths(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _stringify_paths(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify_paths(v) for v in value]
    return value


def _finding_summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    by_code: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for finding in findings:
        code = str(finding.get("code", "unknown"))
        severity = str(finding.get("severity", "unknown"))
        by_code[code] = by_code.get(code, 0) + 1
        by_severity[severity] = by_severity.get(severity, 0) + 1
    return {"total": len(findings), "by_code": by_code, "by_severity": by_severity}


def _result_to_dict(r: IngestResult) -> dict[str, Any]:
    d = asdict(r)
    findings = d.get("findings") or []
    d["finding_summary"] = _finding_summary(findings)
    d.pop("warnings", None)
    d.pop("errors", None)
    return _stringify_paths(d)


def _preserve_report_followons(report: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    structural_keys = {"status", "marker", "findings", "stats"}
    merged = dict(report)
    existing_stats = existing.get("stats")
    merged_stats = merged.get("stats")
    if isinstance(existing_stats, dict) and isinstance(merged_stats, dict):
        for key in ("sections_omitted_empty", "omitted_empty_sections"):
            if key in existing_stats:
                merged_stats[key] = existing_stats[key]
    for key, value in existing.items():
        if key not in structural_keys:
            merged[key] = value
    return merged


def _print_human(result: IngestResult) -> None:
    if result.skipped:
        click.echo(f"skip: {result.chapter_dir} ({result.section_count} sections, hash match)")
    if result.status == "dry-run":
        click.echo(f"dry-run: would write {result.chapter_dir} and {result.overview_path}")
        return

    click.echo(f"Ingested: {result.book_slug}")
    click.echo(f"  Overview:  {result.overview_path}")
    click.echo(f"  Chapters:  {result.chapter_dir}/  ({result.section_count} sections)")
    click.echo(f"  Pages:     {result.page_count}")
    click.echo(f"  System:    {result.system}")
    click.echo(f"  Status:    {result.quality_status}")
    click.echo("")
    click.echo("Required next:")
    for step in result.next_steps:
        if step.get("required"):
            click.echo(f"  - {step.get('command')}")
    review_steps = [s for s in result.next_steps if s.get("id") == "review_findings"]
    if review_steps:
        click.echo("")
        click.echo("Review:")
        for step in review_steps:
            click.echo(f"  - {step.get('summary')}")
            click.echo(f"    See {step.get('report_path')}")
    optional = [
        s for s in result.next_steps if not s.get("required") and s.get("id") != "review_findings"
    ]
    if optional:
        click.echo("")
        click.echo("Optional (in order):")
        for step in optional:
            click.echo(f"  - {step.get('summary')}")
            if command := step.get("command"):
                click.echo(f"      {command}")


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Convert TTRPG PDFs into structured Markdown under vault/library/books/."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command("ingest", short_help="Ingest a PDF (default subcommand).")
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output", default="vault/library/books", show_default=True, type=click.Path(path_type=Path)
)
@click.option("--force", is_flag=True, help="Re-ingest even if hash matches.")
@click.option("--dry-run", is_flag=True, help="Show intended output without writing.")
@click.option(
    "--keep-backup/--no-keep-backup",
    default=False,
    show_default=True,
    help=(
        "When replacing an existing book directory/overview, keep the previous "
        "output under dot-prefixed .<slug>.<timestamp>.bak artifacts."
    ),
)
@click.option(
    "--llm",
    "llm_mode",
    default=None,
    type=click.Choice(["no", "all", "images-only", "text-only"]),
    help="Marker LLM mode. CLI > TTRPG_MARKER_LLM_MODE > no.",
)
@click.option(
    "--openai-model", default=None, help="Override TTRPG_MARKER_OPENAI_MODEL / default gpt-4o-mini."
)
@click.option("--openai-base-url", default=None, help="Override TTRPG_MARKER_OPENAI_BASE_URL.")
@click.option("--page-range", default=None, help="Marker page_range, e.g. '0,5-10,20'.")
@click.option("--force-ocr", is_flag=True, help="Marker force_ocr.")
@click.option(
    "--device",
    default=None,
    show_default="TTRPG_MARKER_DEVICE or auto",
    type=click.Choice(["auto", "cuda", "cpu", "mps"]),
)
@click.option("--layout-batch-size", default=None, type=int)
@click.option("--detection-batch-size", default=None, type=int)
@click.option("--recognition-batch-size", default=None, type=int)
@click.option("--table-rec-batch-size", default=None, type=int)
@click.option(
    "--json", "json_output", is_flag=True, help="Emit machine-readable summary on stdout."
)
def cmd_ingest(
    input_path: Path,
    output: Path,
    force: bool,
    dry_run: bool,
    keep_backup: bool,
    llm_mode: str | None,
    openai_model: str | None,
    openai_base_url: str | None,
    page_range: str | None,
    force_ocr: bool,
    device: str | None,
    layout_batch_size: int | None,
    detection_batch_size: int | None,
    recognition_batch_size: int | None,
    table_rec_batch_size: int | None,
    json_output: bool,
) -> None:
    project_root = find_project_root()
    env = build_env(project_root)
    try:
        resolved_llm_mode, llm_source = resolve_llm_mode(cli_llm_mode=llm_mode, env=env)
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    llm = resolve_llm_config(
        cli_model=openai_model,
        cli_base_url=openai_base_url,
        env=env,
        llm_mode=resolved_llm_mode,
        llm_mode_source=llm_source,
    )
    device, device_source = _resolve_string_setting(device, env, "TTRPG_MARKER_DEVICE", "auto")
    page_range_source = "cli" if page_range else "default"
    force_ocr_source = "cli" if force_ocr else "default"
    layout_batch_size, layout_batch_source = _resolve_optional_int_setting(
        layout_batch_size, env, "TTRPG_MARKER_LAYOUT_BATCH_SIZE"
    )
    detection_batch_size, detection_batch_source = _resolve_optional_int_setting(
        detection_batch_size, env, "TTRPG_MARKER_DETECTION_BATCH_SIZE"
    )
    recognition_batch_size, recognition_batch_source = _resolve_optional_int_setting(
        recognition_batch_size, env, "TTRPG_MARKER_RECOGNITION_BATCH_SIZE"
    )
    table_rec_batch_size, table_rec_batch_source = _resolve_optional_int_setting(
        table_rec_batch_size, env, "TTRPG_MARKER_TABLE_REC_BATCH_SIZE"
    )
    batch_sources = {
        "layout": layout_batch_source,
        "detection": detection_batch_source,
        "recognition": recognition_batch_source,
        "table_rec": table_rec_batch_source,
    }
    torch_status = _torch_status()

    click.echo(
        _format_run_status(
            llm=llm,
            device=device,
            device_source=device_source,
            page_range=page_range,
            page_range_source=page_range_source,
            force_ocr=force_ocr,
            batch_sizes={
                "layout": layout_batch_size,
                "detection": detection_batch_size,
                "recognition": recognition_batch_size,
                "table_rec": table_rec_batch_size,
            },
            batch_sources=batch_sources,
            torch_status=torch_status,
        ),
        err=True,
    )
    if llm.enabled and not llm.api_key:
        raise click.ClickException(
            "Marker LLM mode is enabled but OPENAI_API_KEY is not set. "
            "Add it to .env, export it, or pass --llm no."
        )

    output_root = (
        (project_root / output).resolve() if not output.is_absolute() else output.resolve()
    )
    options = IngestOptions(
        output_root=output_root,
        project_root=project_root,
        force=force,
        dry_run=dry_run,
        keep_backup=keep_backup,
    )
    convert_options = ConvertOptions(
        llm_mode=llm.mode,
        device=device,
        page_range=page_range,
        force_ocr=force_ocr,
        openai_api_key=llm.api_key,
        openai_model=llm.model,
        openai_base_url=llm.base_url,
        max_concurrency=llm.max_concurrency,
        layout_batch_size=layout_batch_size,
        detection_batch_size=detection_batch_size,
        recognition_batch_size=recognition_batch_size,
        table_rec_batch_size=table_rec_batch_size,
        device_source=device_source,
        page_range_source=page_range_source,
        force_ocr_source=force_ocr_source,
        batch_size_sources=batch_sources,
        torch_cuda_available=torch_status.get("cuda_available"),
        torch_cuda_device=torch_status.get("cuda_device"),
    )

    pdfs = _iter_pdfs(input_path)
    results: list[IngestResult] = []
    for pdf in pdfs:
        if not _free_disk_ok(pdf):
            raise click.ClickException(f"not enough free disk for ingest scratch: {pdf}")
        results.append(ingest_pdf(pdf, options, convert_options, llm))

    if json_output:
        if len(results) == 1:
            click.echo(json.dumps(_result_to_dict(results[0]), indent=2))
        else:
            for result in results:
                click.echo(json.dumps(_result_to_dict(result)))
        return

    for result in results:
        _print_human(result)
        if result is not results[-1]:
            click.echo("")


@cli.command("refresh-overview", short_help="Refresh a book overview from chapter frontmatter.")
@click.argument("slug")
def cmd_refresh_overview(slug: str) -> None:
    project_root = find_project_root()
    book_dir = (project_root / "vault/library/books" / slug).resolve()
    try:
        overview_path = refresh_overview(book_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"overview refreshed: {overview_path}")


@cli.command("validate", short_help="Re-run quality checks on an existing book directory.")
@click.argument("book_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--json", "json_output", is_flag=True)
@click.option(
    "--write/--no-write", default=True, show_default=True, help="Write .ingest/report.json."
)
def cmd_validate(book_dir: Path, json_output: bool, write: bool) -> None:
    book_dir = book_dir.resolve()
    overview_path = book_dir / f"__{book_dir.name}.md"
    report_path = book_dir / ".ingest" / "report.json"
    existing_report: dict[str, Any] = {}
    if report_path.exists():
        try:
            loaded = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing_report = loaded
        except Exception:
            existing_report = {}
    marker = read_existing_marker_report(book_dir)
    report = validate_book_dir(book_dir, overview_path=overview_path, marker=marker)
    report = _preserve_report_followons(report, existing_report)
    if write:
        ingest_dir = book_dir / ".ingest"
        ingest_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if json_output:
        click.echo(json.dumps(report, indent=2))
        return
    errors = [f for f in report["findings"] if f.get("severity") == "error"]
    warnings = [f for f in report["findings"] if f.get("severity") == "warning"]
    click.echo(f"Quality: {report['status']}")
    click.echo(f"  warnings: {len(warnings)}")
    click.echo(f"  errors:   {len(errors)}")
    for finding in report["findings"][:10]:
        detail = finding.get("detail") or {}
        target = detail.get("section") or detail.get("path") or detail.get("image") or ""
        click.echo(f"  - {finding['code']}: {target}".rstrip())


def _free_disk_ok(pdf: Path) -> bool:
    try:
        usage = shutil.disk_usage(pdf.parent)
        return usage.free >= pdf.stat().st_size * 5
    except Exception:
        return True


cli.add_command(cmd_summarize)
cli.add_command(cmd_classify_system)
cli.add_command(cmd_tag)
cli.add_command(cmd_tag_manual)


_SUBCOMMANDS = {
    "ingest",
    "validate",
    "summarize",
    "refresh-overview",
    "classify-system",
    "tag",
    "tag-manual",
}
_HELP_FLAGS = {"--help", "-h"}


def main(args: list[str] | None = None) -> None:
    """Entry point. Inject ``ingest`` when the user runs ``book-ingest <pdf>`` directly."""
    import sys

    argv = list(sys.argv[1:] if args is None else args)
    if argv and not (set(argv) & _SUBCOMMANDS) and not (argv and argv[0] in _HELP_FLAGS):
        argv = ["ingest", *argv]
    cli.main(args=argv, standalone_mode=True)


if __name__ == "__main__":
    main()
