from __future__ import annotations

import io
import os
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

IMAGE_DESCRIPTION_PROCESSOR = (
    "marker.processors.llm.llm_image_description.LLMImageDescriptionProcessor"
)
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
class ConvertOptions:
    llm_mode: str
    device: str
    page_range: str | None
    force_ocr: bool
    openai_api_key: str | None
    openai_model: str
    openai_base_url: str
    max_concurrency: int
    llm_min_interval_seconds: float = 2.0
    layout_batch_size: int | None = None
    detection_batch_size: int | None = None
    recognition_batch_size: int | None = None
    table_rec_batch_size: int | None = None
    device_source: str = "default"
    page_range_source: str = "default"
    force_ocr_source: str = "default"
    batch_size_sources: dict[str, str] = field(default_factory=dict)
    torch_cuda_available: bool | None = None
    torch_cuda_device: str | None = None


@dataclass
class LLMCallStats:
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def requested(self) -> int:
        return len(self.calls)

    @property
    def succeeded(self) -> int:
        return sum(1 for call in self.calls if call.get("status") == "ok")

    @property
    def failed(self) -> int:
        return sum(1 for call in self.calls if call.get("status") == "failed")


@dataclass
class Converted:
    markdown: str
    images: dict[str, bytes]
    table_of_contents: list[dict[str, Any]]
    page_stats: list[dict[str, Any]]
    llm_calls: LLMCallStats
    warnings: list[str]
    duration_seconds: float


class ObservableOpenAIService:  # resolved by Marker via full import path
    """Small observing wrapper around Marker's OpenAIService.

    Marker 1.10.x swallows OpenAI exceptions internally and returns an empty
    dict after its own retry policy gives up. We preserve that behavior and add
    a ledger entry for every service call; empty outputs are recorded as generic
    failures because the precise exception type is no longer available.
    """

    _request_lock = Lock()
    _next_request_at = 0.0

    def __init__(self, config: Any = None) -> None:
        from marker.services.openai import OpenAIService

        self._inner = OpenAIService(config=config)
        self.calls: list[dict[str, Any]] = []
        self._lock = Lock()
        self._min_interval_seconds = _config_float(
            config, "book_ingest_llm_min_interval_seconds", 2.0
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __call__(
        self,
        prompt: str,
        image: Any,
        block: Any,
        response_schema: Any,
        max_retries: int | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        block_id = _block_id(block)
        image_name = _image_filename_from_block_id(block_id)
        try:
            self._throttle()
            out = self._inner(
                prompt,
                image,
                block,
                response_schema,
                max_retries=max_retries,
                timeout=timeout,
            )
        except Exception as exc:
            self._append(
                {
                    "image": image_name,
                    "block_id": block_id,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                }
            )
            raise
        if out:
            self._append({"image": image_name, "block_id": block_id, "status": "ok"})
        else:
            self._append(
                {
                    "image": image_name,
                    "block_id": block_id,
                    "status": "failed",
                    "error_type": "EmptyLLMResponse",
                }
            )
        return out

    def _throttle(self) -> None:
        if self._min_interval_seconds <= 0:
            return
        with self._request_lock:
            now = time.monotonic()
            wait = self._next_request_at - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            type(self)._next_request_at = now + self._min_interval_seconds

    def _append(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.calls.append(record)


def _config_float(config: Any, key: str, default: float) -> float:
    if isinstance(config, dict):
        value = config.get(key)
    else:
        value = getattr(config, key, None)
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _useful_warning(message: str) -> bool:
    ignored = ("Accessing the 'model_fields' attribute on the instance is deprecated",)
    return not any(text in message for text in ignored)


def marker_version() -> str:
    try:
        from importlib.metadata import version

        return version("marker-pdf")
    except Exception:
        return "unknown"


def convert(pdf: Path, opts: ConvertOptions) -> Converted:
    if opts.device != "auto":
        os.environ["TORCH_DEVICE"] = opts.device

    from marker.config.parser import ConfigParser
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict

    config = _build_marker_config(opts)
    parser = ConfigParser(config)
    runtime_config = parser.generate_config_dict()
    # ConfigParser drops falsy CLI-style values, but images-only depends on
    # disabling text LLM cleanup and enabling description calls for existing
    # extracted image blocks.
    if opts.llm_mode == "images-only":
        runtime_config["LineMergeProcessor_use_llm"] = False
    if opts.llm_mode in {"images-only", "all"}:
        runtime_config["LLMImageDescriptionProcessor_extract_images"] = False
    started = time.monotonic()
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        converter = PdfConverter(
            artifact_dict=create_model_dict(device=None if opts.device == "auto" else opts.device),
            config=runtime_config,
            processor_list=parser.get_processors(),
            renderer=parser.get_renderer(),
            llm_service=parser.get_llm_service(),
        )
        rendered = converter(str(pdf))
    duration = time.monotonic() - started

    service = getattr(converter, "llm_service", None)
    calls = list(getattr(service, "calls", []) or [])
    return Converted(
        markdown=str(getattr(rendered, "markdown", "")),
        images={
            str(name): _image_bytes(str(name), img)
            for name, img in (getattr(rendered, "images", {}) or {}).items()
        },
        table_of_contents=list(
            (getattr(rendered, "metadata", {}) or {}).get("table_of_contents") or []
        ),
        page_stats=list((getattr(rendered, "metadata", {}) or {}).get("page_stats") or []),
        llm_calls=LLMCallStats(calls=calls),
        warnings=[message for w in captured if _useful_warning(message := str(w.message))],
        duration_seconds=duration,
    )


def _build_marker_config(opts: ConvertOptions) -> dict[str, Any]:
    config: dict[str, Any] = {
        "output_format": "markdown",
        "paginate_output": True,
        "disable_tqdm": True,
        "disable_multiprocessing": True,
    }
    if opts.page_range:
        config["page_range"] = opts.page_range
    if opts.force_ocr:
        config["force_ocr"] = True
    for key, value in {
        "layout_batch_size": opts.layout_batch_size,
        "detection_batch_size": opts.detection_batch_size,
        "recognition_batch_size": opts.recognition_batch_size,
        "table_rec_batch_size": opts.table_rec_batch_size,
    }.items():
        if value is not None:
            config[key] = value
    if opts.llm_mode != "no":
        config.update(
            {
                "use_llm": True,
                "llm_service": "book_ingest.converter.ObservableOpenAIService",
                "openai_api_key": opts.openai_api_key,
                "openai_model": opts.openai_model,
                "openai_base_url": opts.openai_base_url,
                "max_concurrency": opts.max_concurrency,
                "book_ingest_llm_min_interval_seconds": opts.llm_min_interval_seconds,
            }
        )
        if opts.llm_mode == "images-only":
            config["processors"] = ",".join(IMAGE_ONLY_PROCESSORS)
            config["LineMergeProcessor_use_llm"] = False
        if opts.llm_mode in {"images-only", "all"}:
            config["LLMImageDescriptionProcessor_extract_images"] = False
    return config


def _image_bytes(name: str, image: Any) -> bytes:
    if isinstance(image, bytes):
        return image
    buf = io.BytesIO()
    suffix = Path(name).suffix.lower()
    fmt = "PNG" if suffix == ".png" else "JPEG"
    if fmt == "JPEG" and getattr(image, "mode", "RGB") != "RGB":
        image = image.convert("RGB")
    image.save(buf, format=fmt)
    return buf.getvalue()


def _block_id(block: Any) -> str | None:
    if block is None:
        return None
    bid = getattr(block, "id", None)
    return str(bid) if bid is not None else None


def _image_filename_from_block_id(block_id: str | None) -> str | None:
    if not block_id or not ("/Picture/" in block_id or "/Figure/" in block_id):
        return None
    return block_id.replace("/", "_") + ".jpeg"
