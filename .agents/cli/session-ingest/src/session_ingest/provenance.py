"""Skip-if-done, keyed on content — the first thing built, not the last.

DESIGN principle 5: *every stage is skip-if-done on a composite digest key
(dataset digest + lexicon digest + prompt version + model + knobs), never on
file existence.* CONTRACT rule 3 says the same from the producer's side:
identity is the segments digest, never mtime, size, or existence.

So a stage is "done" when the recorded :class:`CompositeKey` fingerprint equals
the one the caller is about to run with. Existence is used in exactly one
direction — a recorded output that has since been deleted forces a re-run — and
never as a positive signal that work happened.

``provenance.json`` lives at ``.cache/sessions/<id>/provenance.json`` and is
written atomically, because a half-written provenance file is worse than none:
it would claim a stage completed with a key nobody can verify.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROVENANCE_SCHEMA = "ttrpg.session-provenance/1"

_CHUNK = 1024 * 1024

#: Stages that record provenance, in chain order (DESIGN §4).
STAGES: tuple[str, ...] = (
    "plan",
    "adopt",
    "qa",
    "render",
    "segment",
    "extract",
    "recap",
    "record",
    "glossary",
)


def sha256_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    """Digest a file's *contents*. Never its metadata."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def as_sha256(value: str | None) -> str | None:
    """Normalise a bare hex digest (as the SDK manifest reports it) to ``sha256:…``."""
    if value is None:
        return None
    return value if value.startswith("sha256:") else f"sha256:{value}"


def short_digest(value: str | None, *, width: int = 12) -> str:
    """``sha256:abcdef012345…`` — the status-line form."""
    if not value:
        return "none"
    body = value.split(":", 1)[1] if ":" in value else value
    return f"sha256:{body[:width]}…"


def _canonical(value: Any) -> Any:
    """Stable JSON shape: sorted keys, lists preserved, everything else as-is."""
    if isinstance(value, Mapping):
        return {str(k): _canonical(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


@dataclass(frozen=True, slots=True)
class CompositeKey:
    """Everything whose change must invalidate a stage's output.

    ``knobs`` is the escape hatch for stage-specific settings (window minutes,
    audience, ``--keep-bleed``) so adding one does not require a new field here
    and, more importantly, cannot be forgotten in the fingerprint.
    """

    dataset_digest: str | None = None
    lexicon_digest: str | None = None
    speakers_digest: str | None = None
    prompt_version: str | None = None
    model: str | None = None
    knobs: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_digest": self.dataset_digest,
            "lexicon_digest": self.lexicon_digest,
            "speakers_digest": self.speakers_digest,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "knobs": dict(self.knobs),
        }

    def fingerprint(self) -> str:
        payload = json.dumps(_canonical(self.to_dict()), sort_keys=True, ensure_ascii=False)
        return sha256_text(payload)

    def with_knobs(self, **extra: Any) -> CompositeKey:
        merged = dict(self.knobs)
        merged.update(extra)
        return CompositeKey(
            dataset_digest=self.dataset_digest,
            lexicon_digest=self.lexicon_digest,
            speakers_digest=self.speakers_digest,
            prompt_version=self.prompt_version,
            model=self.model,
            knobs=merged,
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CompositeKey:
        knobs = payload.get("knobs")
        return cls(
            dataset_digest=payload.get("dataset_digest"),
            lexicon_digest=payload.get("lexicon_digest"),
            speakers_digest=payload.get("speakers_digest"),
            prompt_version=payload.get("prompt_version"),
            model=payload.get("model"),
            knobs=dict(knobs) if isinstance(knobs, Mapping) else {},
        )


@dataclass(frozen=True, slots=True)
class StageRecord:
    """One completed stage."""

    stage: str
    composite_key: CompositeKey
    fingerprint: str
    completed_at: str
    outputs: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": self.stage,
            "composite_key": self.composite_key.to_dict(),
            "fingerprint": self.fingerprint,
            "completed_at": self.completed_at,
            "outputs": list(self.outputs),
        }
        if self.extra:
            payload["extra"] = dict(self.extra)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StageRecord:
        key = CompositeKey.from_dict(payload.get("composite_key") or {})
        outputs = payload.get("outputs")
        extra = payload.get("extra")
        return cls(
            stage=str(payload.get("stage", "")),
            composite_key=key,
            fingerprint=str(payload.get("fingerprint") or key.fingerprint()),
            completed_at=str(payload.get("completed_at", "")),
            outputs=tuple(str(o) for o in outputs) if isinstance(outputs, list) else (),
            extra=dict(extra) if isinstance(extra, Mapping) else {},
        )


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class Provenance:
    """Read/modify/write ``provenance.json`` for one session."""

    def __init__(self, path: Path, stages: Mapping[str, StageRecord] | None = None) -> None:
        self.path = path
        self._stages: dict[str, StageRecord] = dict(stages or {})

    # ------------------------------------------------------------------- io

    @classmethod
    def load(cls, path: Path) -> Provenance:
        """Load, tolerating an absent file. A corrupt one is reported, not ignored."""
        if not path.is_file():
            return cls(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{path} does not contain a JSON object")
        raw_stages = payload.get("stages")
        stages: dict[str, StageRecord] = {}
        if isinstance(raw_stages, dict):
            for name, entry in raw_stages.items():
                if isinstance(entry, dict):
                    stages[str(name)] = StageRecord.from_dict(entry)
        return cls(path, stages)

    def save(self) -> Path:
        """Write atomically; a torn provenance file would claim unverifiable work."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": PROVENANCE_SCHEMA,
            "updated_at": utc_now(),
            "stages": {name: record.to_dict() for name, record in sorted(self._stages.items())},
        }
        tmp = self.path.with_name(self.path.name + ".part")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        return self.path

    # -------------------------------------------------------------- queries

    def record(self, stage: str) -> StageRecord | None:
        return self._stages.get(stage)

    def stages(self) -> dict[str, StageRecord]:
        return dict(self._stages)

    def should_skip(
        self,
        stage: str,
        key: CompositeKey,
        *,
        force: bool = False,
        root: Path | None = None,
    ) -> bool:
        """True when this exact key already produced still-present outputs.

        The positive signal is the fingerprint and nothing else. Output presence
        is consulted only to *deny* a skip: a recorded artifact somebody deleted
        must be rebuilt, and no digest can tell you it is gone.
        """
        if force:
            return False
        existing = self._stages.get(stage)
        if existing is None:
            return False
        if existing.fingerprint != key.fingerprint():
            return False
        base = root if root is not None else self.path.parent
        for output in existing.outputs:
            candidate = Path(output)
            if not candidate.is_absolute():
                candidate = base / candidate
            if not candidate.exists():
                return False
        return True

    def skip_reason(self, stage: str, key: CompositeKey) -> str | None:
        """Human-readable explanation of why a stage is *not* skippable."""
        existing = self._stages.get(stage)
        if existing is None:
            return "no previous run recorded"
        if existing.fingerprint != key.fingerprint():
            return "composite key changed since the last run"
        return None

    # --------------------------------------------------------------- writes

    def mark_done(
        self,
        stage: str,
        key: CompositeKey,
        *,
        outputs: Sequence[Path | str] = (),
        extra: Mapping[str, Any] | None = None,
        save: bool = True,
    ) -> StageRecord:
        record = StageRecord(
            stage=stage,
            composite_key=key,
            fingerprint=key.fingerprint(),
            completed_at=utc_now(),
            outputs=tuple(str(o) for o in outputs),
            extra=dict(extra or {}),
        )
        self._stages[stage] = record
        if save:
            self.save()
        return record

    def invalidate(self, stages: Sequence[str], *, save: bool = True) -> list[str]:
        """Drop stage records. Used by ``adopt --promote`` to invalidate downstream."""
        dropped = [stage for stage in stages if self._stages.pop(stage, None) is not None]
        if save and dropped:
            self.save()
        return dropped

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PROVENANCE_SCHEMA,
            "path": str(self.path),
            "stages": {name: record.to_dict() for name, record in sorted(self._stages.items())},
        }


def downstream_of(stage: str) -> tuple[str, ...]:
    """Stages that a change at ``stage`` invalidates, in chain order."""
    if stage not in STAGES:
        return ()
    index = STAGES.index(stage)
    return STAGES[index + 1 :]
