from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SectionPlan:
    index: int
    title: str
    slug: str
    page_start: int
    page_end: int
    source: str
    confidence: float = 1.0
    char_start: int | None = None
    char_end: int | None = None


@dataclass
class QualityFinding:
    code: str
    severity: str
    detail: dict = field(default_factory=dict)
