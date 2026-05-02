from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import click

from .config import LLMConfig


IMAGE_DESCRIPTION_PROCESSOR = "marker.processors.llm.llm_image_description.LLMImageDescriptionProcessor"
# Marker's --processors replaces the full processor list.  For images-only
# mode, keep Marker's normal non-LLM cleanup processors and add only the image
# description LLM processor, avoiding table/page/header LLM processors.
IMAGE_ONLY_PROCESSORS = (
    "marker.processors.order.OrderProcessor",
    "marker.processors.block_relabel.BlockRelabelProcessor",
    "marker.processors.line_merge.LineMergeProcessor",
    "marker.processors.blockquote.BlockquoteProcessor",
    "marker.processors.code.CodeProcessor",
    "marker.processors.document_toc.DocumentTOCProcessor",
    "marker.processors.equation.EquationProcessor",
    "marker.processors.footnote.FootnoteProcessor",
    "marker.processors.ignoretext.IgnoreTextProcessor",
    "marker.processors.line_numbers.LineNumbersProcessor",
    "marker.processors.list.ListProcessor",
    "marker.processors.page_header.PageHeaderProcessor",
    "marker.processors.sectionheader.SectionHeaderProcessor",
    "marker.processors.table.TableProcessor",
    "marker.processors.text.TextProcessor",
    IMAGE_DESCRIPTION_PROCESSOR,
    "marker.processors.reference.ReferenceProcessor",
    "marker.processors.blank_page.BlankPageProcessor",
    "marker.processors.debug.DebugProcessor",
)


@dataclass(frozen=True)
class MarkerInvocation:
    workers: int
    device: str
    layout_batch_size: int | None
    detection_batch_size: int | None
    recognition_batch_size: int | None
    table_rec_batch_size: int | None
    page_range: str | None
    force_ocr: bool
    llm: LLMConfig
    describe_images: bool = False


def _llm_active_for_format(inv: MarkerInvocation, output_format: str) -> bool:
    if not inv.llm.enabled:
        return False
    # Images-only mode only needs the vision pass in the paginated Markdown run;
    # the JSON run is for deterministic section planning and should stay fast.
    if inv.llm.mode == "images-only" and output_format != "markdown":
        return False
    return True


def _describes_images_for_format(inv: MarkerInvocation, output_format: str) -> bool:
    return output_format == "markdown" and inv.llm.mode in {"all", "images-only"}


def marker_version() -> str:
    try:
        from importlib.metadata import version

        return version("marker-pdf")
    except Exception:
        return "unknown"


def _resolved_marker_path() -> str:
    return shutil.which("marker_single") or "marker_single"


def _build_command(
    pdf: Path,
    output_dir: Path,
    output_format: str,
    config_json_path: Path | None,
    inv: MarkerInvocation,
) -> list[str]:
    cmd: list[str] = [
        _resolved_marker_path(),
        str(pdf),
        "--output_dir",
        str(output_dir),
        "--output_format",
        output_format,
        "--disable_tqdm",
    ]
    if output_format == "markdown":
        cmd.append("--paginate_output")
    if inv.workers == 1:
        cmd.append("--disable_multiprocessing")
    if inv.page_range:
        cmd.extend(["--page_range", inv.page_range])
    if inv.force_ocr:
        cmd.append("--force_ocr")

    batch_args = {
        "--layout_batch_size": inv.layout_batch_size,
        "--detection_batch_size": inv.detection_batch_size,
        "--recognition_batch_size": inv.recognition_batch_size,
        "--table_rec_batch_size": inv.table_rec_batch_size,
    }
    for flag, value in batch_args.items():
        if value is not None:
            cmd.extend([flag, str(value)])

    if _llm_active_for_format(inv, output_format):
        cmd.extend(["--use_llm", "--llm_service", "marker.services.openai.OpenAIService"])
        if inv.llm.mode == "images-only":
            cmd.extend(["--processors", ",".join(IMAGE_ONLY_PROCESSORS)])
        if config_json_path is not None:
            cmd.extend(["--config_json", str(config_json_path)])

    return cmd


def _write_llm_config(
    scratch: Path,
    llm: LLMConfig,
    *,
    describe_images: bool,
    output_format: str,
    llm_mode: str | None = None,
) -> Path:
    """Write a 0600-mode config.json with the API key and optional per-run overrides.

    A separate config file per output_format keeps the image-description toggle
    scoped to the markdown run; the JSON run shouldn't pay for image LLM calls
    that never end up in the planner-visible output.
    """
    if not llm.api_key:
        raise click.ClickException(
            "--use-llm requested but OPENAI_API_KEY is not set. Add it to .env or export it."
        )
    payload: dict[str, object] = {
        "openai_api_key": llm.api_key,
        "openai_model": llm.model,
        "openai_base_url": llm.base_url,
    }
    if describe_images and output_format == "markdown":
        # Marker's per-processor BOOLEAN CLI parsing is unreliable for this key;
        # the config-json route is the only one that actually flips the gate.
        # Counterintuitively, this class-specific flag must be False before
        # LLMImageDescriptionProcessor emits descriptions while the renderer
        # can still extract/copy image files normally.
        payload["LLMImageDescriptionProcessor_extract_images"] = False
        # Global use_llm=True also toggles LineMergeProcessor, which is a
        # non-LLM text cleanup pass.  In images-only mode we override it below
        # to keep text processing equivalent to no-LLM mode.
        if llm_mode == "images-only":
            payload["LineMergeProcessor_use_llm"] = False

    path = scratch / f"marker-config-{output_format}.json"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


@dataclass(frozen=True)
class MarkerRun:
    """Result of one ``marker_single`` invocation."""

    output_dir: Path
    log_path: Path
    returncode: int
    duration_seconds: float


def run_marker(
    pdf: Path,
    scratch: Path,
    output_format: str,
    inv: MarkerInvocation,
    log_dir: Path | None = None,
) -> MarkerRun:
    """Run ``marker_single`` once.

    Always captures stdout/stderr to a log file. ``log_dir`` defaults to the
    scratch directory; callers normally pass the cache dir so logs survive
    after teardown.
    """
    out_root = scratch / output_format
    out_root.mkdir(parents=True, exist_ok=True)

    config_json_path: Path | None = None
    if _llm_active_for_format(inv, output_format):
        config_json_path = _write_llm_config(
            scratch,
            inv.llm,
            describe_images=_describes_images_for_format(inv, output_format),
            output_format=output_format,
            llm_mode=inv.llm.mode,
        )

    cmd = _build_command(pdf, out_root, output_format, config_json_path, inv)
    env = os.environ.copy()
    if inv.device != "auto":
        env["TORCH_DEVICE"] = inv.device

    log_dir = log_dir or scratch
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"marker-{output_format}.log"

    # The key lives in --config_json (mode 0600), never on the command line.
    active_llm = _llm_active_for_format(inv, output_format)
    click.echo(
        f"marker[{output_format}] device={inv.device} workers={inv.workers} "
        f"llm_mode={inv.llm.mode} use_llm={active_llm} log={log_path}",
        err=True,
    )
    started = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    duration = time.monotonic() - started

    _write_log(log_path, cmd, proc, duration)

    if proc.returncode != 0:
        tail = proc.stderr.strip().splitlines()[-30:]
        raise click.ClickException(
            f"marker {output_format} failed for {pdf.name} (rc={proc.returncode}). "
            f"Tail of stderr (full log: {log_path}):\n" + "\n".join(tail)
        )

    candidates = [p for p in out_root.iterdir() if p.is_dir()]
    if not candidates:
        raise click.ClickException(f"marker produced no output directory under {out_root}")
    return MarkerRun(
        output_dir=candidates[0],
        log_path=log_path,
        returncode=proc.returncode,
        duration_seconds=duration,
    )


def _write_log(
    log_path: Path,
    cmd: list[str],
    proc: subprocess.CompletedProcess[str],
    duration: float,
) -> None:
    redacted_cmd = _redact_cmd(cmd)
    header = (
        f"# marker_single log\n"
        f"# command: {' '.join(redacted_cmd)}\n"
        f"# returncode: {proc.returncode}\n"
        f"# duration_seconds: {duration:.2f}\n"
        f"# marker_version: {marker_version()}\n"
        "\n--- stdout ---\n"
    )
    log_path.write_text(
        header + (proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or ""),
        encoding="utf-8",
    )


def _redact_cmd(cmd: list[str]) -> list[str]:
    """Drop --config_json values from a logged command line for safety.

    The config file itself contains the secret; we never log its path either.
    """
    out: list[str] = []
    skip = False
    for token in cmd:
        if skip:
            out.append("<redacted>")
            skip = False
            continue
        if token == "--config_json":
            out.append(token)
            skip = True
            continue
        out.append(token)
    return out


def redacted_command_record(
    pdf: Path,
    output_format: str,
    inv: MarkerInvocation,
    *,
    log_path: Path | None = None,
    duration_seconds: float | None = None,
    returncode: int | None = None,
) -> dict:
    return {
        "marker_version": marker_version(),
        "executable": _resolved_marker_path(),
        "pdf": pdf.name,
        "output_format": output_format,
        "device": inv.device,
        "workers": inv.workers,
        "page_range": inv.page_range,
        "force_ocr": inv.force_ocr,
        "batch_sizes": {
            "layout": inv.layout_batch_size,
            "detection": inv.detection_batch_size,
            "recognition": inv.recognition_batch_size,
            "table_rec": inv.table_rec_batch_size,
        },
        "llm": inv.llm.redacted() | {"active_for_run": _llm_active_for_format(inv, output_format)},
        "describe_images": _describes_images_for_format(inv, output_format),
        "log_path": str(log_path) if log_path is not None else None,
        "duration_seconds": duration_seconds,
        "returncode": returncode,
    }
