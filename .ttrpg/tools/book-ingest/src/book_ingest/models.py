from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SectionPlan:
    index: int
    title: str
    slug: str
    page_start: int
    page_end: int
    source: str
    confidence: float = 1.0


@dataclass(frozen=True)
class BookFacts:
    title: str
    slug: str
    page_count: int
    source_pdf: Path
    source_hash: str
    pdf_metadata: dict
    pdf_outline_count: int


@dataclass(frozen=True)
class MarkerArtifacts:
    cache_dir: Path
    markdown_path: Path
    json_path: Path
    meta_path: Path
    images_dir: Path
    marker_version: str


@dataclass
class QualityFinding:
    code: str
    severity: str
    detail: dict = field(default_factory=dict)
