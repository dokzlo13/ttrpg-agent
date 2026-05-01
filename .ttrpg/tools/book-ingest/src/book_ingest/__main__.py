from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

import click

from .config import (
    build_env,
    default_cache_root,
    find_project_root,
    resolve_llm_config,
    resolve_llm_mode,
)
from .marker_run import MarkerInvocation
from .models import SectionPlan
from .pipeline import IngestOptions, IngestResult, ingest_pdf
from .validate import validate_book_dir


def _optional_int_env(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    return int(str(value).strip())


def _iter_pdfs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".pdf":
            raise click.ClickException("input must be a PDF file or directory of PDFs")
        return [input_path]
    pdfs = sorted(p for p in input_path.rglob("*.pdf") if p.is_file())
    if not pdfs:
        raise click.ClickException(f"no PDFs found under {input_path}")
    return pdfs


def _result_to_dict(r: IngestResult) -> dict:
    d = asdict(r)
    d["output_path"] = str(r.output_path)
    if r.cache_path is not None:
        d["cache_path"] = str(r.cache_path)
    return d


def _print_human(result: IngestResult) -> None:
    if result.skipped:
        click.echo(f"skip: {result.output_path} ({result.section_count} sections, hash match)")
        return
    if result.status == "dry-run":
        click.echo(f"dry-run: would write {result.output_path}")
        return
    quality_path = result.output_path / ".ingest" / "quality.json"
    click.echo(
        f"Wrote: {result.output_path} ({result.section_count} sections, {result.page_count} pages)"
    )
    click.echo(f"Plan source: {result.plan_source}")
    click.echo(
        f"Quality: {result.quality_status} "
        f"({len(result.warnings)} warnings, {len(result.errors)} errors)"
    )
    click.echo("")
    click.echo("Next:")
    click.echo(f"  1. Inspect:  cat {quality_path} | jq")
    click.echo("  2. Index:    qmd update")
    click.echo("  3. Embed:    qmd embed")
    if result.warnings:
        click.echo("")
        click.echo("Warnings:")
        for w in result.warnings[:10]:
            detail = w.get("detail") or {}
            target = detail.get("section") or detail.get("path") or detail.get("image") or ""
            click.echo(f"  - {w['code']}: {target}".rstrip())
        if len(result.warnings) > 10:
            click.echo(f"  ... ({len(result.warnings) - 10} more)")


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
@click.option(
    "--cache",
    default=None,
    type=click.Path(path_type=Path),
    help="Where to keep raw Marker artifacts. Defaults to <project>/.cache/book-ingest.",
)
@click.option("--force", is_flag=True, help="Re-ingest even if hash matches.")
@click.option("--dry-run", is_flag=True, help="Show intended output without writing.")
@click.option(
    "--keep-cache/--no-keep-cache",
    default=False,
    show_default=True,
    help=(
        "Keep raw Marker artifacts (markdown/json/marker-cmd.json) under "
        ".cache/book-ingest/<hash>/ for debugging. Logs always remain "
        "regardless of this flag."
    ),
)
@click.option(
    "--keep-backup/--no-keep-backup",
    default=False,
    show_default=True,
    help=(
        "When replacing an existing book directory, keep the previous "
        "output under <slug>.<timestamp>.bak. Default drops it after "
        "successful install."
    ),
)
@click.option(
    "--llm",
    "llm_mode",
    default=None,
    type=click.Choice(["no", "all", "images-only", "text-only"]),
    help=(
        "Marker LLM mode. no=fast local extraction; images-only=normal extraction plus "
        "LLM image captions only; text-only=full Marker text/table/page LLM cleanup "
        "without image captions; all=full cleanup plus image captions. CLI > "
        "TTRPG_MARKER_LLM_MODE > legacy TTRPG_MARKER_USE_LLM/DESCRIBE_IMAGES > no."
    ),
)
@click.option(
    "--use-llm/--no-use-llm",
    "use_llm",
    default=None,
    help=(
        "Deprecated compatibility flag. Prefer --llm text-only/all/no. "
        "With --describe-images, maps to --llm all; alone maps to --llm text-only."
    ),
)
@click.option(
    "--describe-images/--no-describe-images",
    "describe_images",
    default=None,
    help=(
        "Deprecated compatibility flag. Prefer --llm images-only/all. "
        "Alone maps to --llm images-only; with --use-llm maps to --llm all."
    ),
)
@click.option(
    "--openai-model", default=None, help="Override TTRPG_MARKER_OPENAI_MODEL / default gpt-4o-mini."
)
@click.option("--openai-base-url", default=None, help="Override TTRPG_MARKER_OPENAI_BASE_URL.")
@click.option("--page-range", default=None, help="Marker --page_range, e.g. '0,5-10,20'.")
@click.option("--force-ocr", is_flag=True, help="Marker --force_ocr.")
@click.option("--workers", default=1, show_default=True, type=int)
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
    cache: Path,
    force: bool,
    dry_run: bool,
    keep_cache: bool,
    keep_backup: bool,
    llm_mode: str | None,
    use_llm: bool | None,
    describe_images: bool | None,
    openai_model: str | None,
    openai_base_url: str | None,
    page_range: str | None,
    force_ocr: bool,
    workers: int,
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
        resolved_llm_mode, llm_source = resolve_llm_mode(
            cli_llm_mode=llm_mode,
            cli_use_llm=use_llm,
            cli_describe_images=describe_images,
            env=env,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    llm = resolve_llm_config(
        cli_use_llm=None,
        cli_model=openai_model,
        cli_base_url=openai_base_url,
        env=env,
        llm_mode=resolved_llm_mode,
        llm_mode_source=llm_source,
    )
    describe_images_resolved = resolved_llm_mode in {"all", "images-only"}

    device = device or env.get("TTRPG_MARKER_DEVICE") or "auto"
    layout_batch_size = layout_batch_size if layout_batch_size is not None else _optional_int_env(env.get("TTRPG_MARKER_LAYOUT_BATCH_SIZE"))
    detection_batch_size = detection_batch_size if detection_batch_size is not None else _optional_int_env(env.get("TTRPG_MARKER_DETECTION_BATCH_SIZE"))
    recognition_batch_size = recognition_batch_size if recognition_batch_size is not None else _optional_int_env(env.get("TTRPG_MARKER_RECOGNITION_BATCH_SIZE"))
    table_rec_batch_size = table_rec_batch_size if table_rec_batch_size is not None else _optional_int_env(env.get("TTRPG_MARKER_TABLE_REC_BATCH_SIZE"))

    click.echo(
        f"book-ingest: llm_mode={llm.mode} (source={llm.enabled_source}), "
        f"use_llm={llm.enabled}, describe_images={describe_images_resolved}, "
        f"device={device}, model={llm.model if llm.enabled else 'n/a'}",
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
    if cache is None:
        cache_root = default_cache_root(project_root)
    else:
        cache_root = (
            (project_root / cache).resolve() if not cache.is_absolute() else cache.resolve()
        )
    options = IngestOptions(
        output_root=output_root,
        cache_root=cache_root,
        project_root=project_root,
        force=force,
        dry_run=dry_run,
        keep_cache=keep_cache,
        keep_backup=keep_backup,
    )
    invocation = MarkerInvocation(
        workers=workers,
        device=device,
        layout_batch_size=layout_batch_size,
        detection_batch_size=detection_batch_size,
        recognition_batch_size=recognition_batch_size,
        table_rec_batch_size=table_rec_batch_size,
        page_range=page_range,
        force_ocr=force_ocr,
        llm=llm,
        describe_images=describe_images_resolved,
    )

    pdfs = _iter_pdfs(input_path)
    results: list[IngestResult] = []
    for pdf in pdfs:
        if not _free_disk_ok(pdf):
            raise click.ClickException(f"not enough free disk for ingest scratch: {pdf}")
        result = ingest_pdf(pdf, options, invocation, llm)
        results.append(result)

    if json_output:
        if len(results) == 1:
            click.echo(json.dumps(_result_to_dict(results[0]), indent=2))
        else:
            for r in results:
                click.echo(json.dumps(_result_to_dict(r)))
        return

    for r in results:
        _print_human(r)
        if r is not results[-1]:
            click.echo("")


@cli.command("validate", short_help="Re-run quality checks on an existing book directory.")
@click.argument("book_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--json", "json_output", is_flag=True)
@click.option(
    "--write/--no-write", default=True, show_default=True, help="Write .ingest/quality.json."
)
def cmd_validate(book_dir: Path, json_output: bool, write: bool) -> None:
    book_dir = book_dir.resolve()
    manifest = book_dir / ".ingest" / "manifest.json"
    plans: list[SectionPlan] | None = None
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        plans = [SectionPlan(**s) for s in data.get("sections", [])]
    report = validate_book_dir(book_dir, plans=plans)
    if write:
        ingest_dir = book_dir / ".ingest"
        ingest_dir.mkdir(parents=True, exist_ok=True)
        (ingest_dir / "quality.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if json_output:
        click.echo(json.dumps(report, indent=2))
        return
    click.echo(f"Quality: {report['status']}")
    click.echo(f"  warnings: {len(report['warnings'])}")
    click.echo(f"  errors:   {len(report['errors'])}")
    for w in report["warnings"][:10]:
        detail = w.get("detail") or {}
        target = detail.get("section") or detail.get("path") or detail.get("image") or ""
        click.echo(f"  - {w['code']}: {target}".rstrip())


def _free_disk_ok(pdf: Path) -> bool:
    try:
        usage = shutil.disk_usage(pdf.parent)
        return usage.free >= pdf.stat().st_size * 5
    except Exception:
        return True


_SUBCOMMANDS = {"ingest", "validate"}
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
