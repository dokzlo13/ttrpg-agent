"""Readers for the two hand-maintained files under ``vault/transcripts/``.

These are *user data*, not tool config: hand-edited, mirrored to Windows,
cleanup-protected, and snapshotted into ``inputs/`` on every render. Both
readers are tolerant about optional keys and strict about the two fields that
carry meaning (a lexicon term's ``id`` and ``canonical``), because silently
dropping a malformed term would quietly un-bias a transcription.

Each loader returns its content digest alongside the parsed value. That digest
is half of every stage's composite cache key: edit the lexicon and every
downstream stage correctly invalidates.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import VaultFileError
from .provenance import sha256_bytes


@dataclass(frozen=True, slots=True)
class LexiconTerm:
    """One canonical entity plus the misrecognitions observed for it."""

    id: str
    canonical: str
    display_ru: str | None = None
    variants: tuple[str, ...] = ()
    kind: str | None = None
    active: bool = True
    priority: int = 0
    #: Opt in to mechanical case-form generation (:mod:`session_ingest.morphs`).
    #: Off by default: an indeclinable name expanded anyway would only add noise,
    #: and the owner is the one who knows which names decline.
    morph: bool = False
    source: str | None = None

    def canonical_forms(self) -> tuple[str, ...]:
        """Strings that count as the term being heard correctly."""
        forms = [self.canonical]
        if self.display_ru and self.display_ru != self.canonical:
            forms.append(self.display_ru)
        return tuple(dict.fromkeys(f for f in forms if f))

    def variant_forms(self) -> tuple[str, ...]:
        """Strings that count as a misrecognition.

        A variant that duplicates a canonical form is dropped rather than
        counted twice — otherwise the same occurrence would land on both sides
        of the miss rate.
        """
        canonical = set(self.canonical_forms())
        return tuple(dict.fromkeys(v for v in self.variants if v and v not in canonical))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canonical": self.canonical,
            "display_ru": self.display_ru,
            "variants": list(self.variants),
            "kind": self.kind,
            "active": self.active,
            "priority": self.priority,
            "morph": self.morph,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class Lexicon:
    """``vault/transcripts/_lexicon.yaml`` — biasing glossary and GEC dictionary in one."""

    path: Path
    present: bool
    digest: str | None
    terms: tuple[LexiconTerm, ...] = ()

    def active_terms(self) -> tuple[LexiconTerm, ...]:
        return tuple(t for t in self.terms if t.active)

    def by_id(self) -> dict[str, LexiconTerm]:
        return {t.id: t for t in self.terms}

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "present": self.present,
            "digest": self.digest,
            "terms": len(self.terms),
            "active_terms": len(self.active_terms()),
            "morph_terms": sum(1 for t in self.active_terms() if t.morph),
        }


@dataclass(frozen=True, slots=True)
class Speaker:
    """One Discord participant mapped to a person and a character."""

    user_id: str
    player: str | None = None
    character: str | None = None
    role: str | None = None

    def display(self) -> str:
        """Best available human label. Never invented — falls back to the raw id."""
        return self.character or self.player or self.user_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "player": self.player,
            "character": self.character,
            "role": self.role,
        }


@dataclass(frozen=True, slots=True)
class Speakers:
    """``vault/transcripts/_speakers.yaml`` — discord user_id -> player/character/role."""

    path: Path
    present: bool
    digest: str | None
    by_user_id: Mapping[str, Speaker]

    def get(self, user_id: str | None) -> Speaker | None:
        if user_id is None:
            return None
        return self.by_user_id.get(user_id)

    def unmapped(self, user_ids: Iterable[str | None]) -> list[str]:
        """User ids with no entry, in first-seen order. ``None`` ids are not ids."""
        missing: list[str] = []
        for user_id in user_ids:
            if user_id is None:
                continue
            if user_id not in self.by_user_id and user_id not in missing:
                missing.append(user_id)
        return missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "present": self.present,
            "digest": self.digest,
            "speakers": len(self.by_user_id),
        }


def _load_yaml_mapping(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    digest = sha256_bytes(raw)
    try:
        loaded = yaml.safe_load(raw.decode("utf-8"))
    except yaml.YAMLError as exc:
        raise VaultFileError(
            f"{path} is not valid YAML: {exc}", detail={"path": str(path)}
        ) from exc
    if loaded is None:
        return {}, digest
    if not isinstance(loaded, dict):
        raise VaultFileError(
            f"{path} must contain a YAML mapping at the top level, got {type(loaded).__name__}",
            detail={"path": str(path)},
        )
    return loaded, digest


def _as_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ValueError(f"expected a boolean, got {value!r}")


def _as_int(value: Any, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"expected an integer, got {value!r}")
    return value


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"expected a string, got {value!r}")
    text = value.strip()
    return text or None


def load_lexicon(path: Path) -> Lexicon:
    """Read ``_lexicon.yaml``. An absent file is normal, not an error."""
    if not path.is_file():
        return Lexicon(path=path, present=False, digest=None, terms=())

    payload, digest = _load_yaml_mapping(path)
    raw_terms = payload.get("terms")
    if raw_terms is None:
        raw_terms = []
    if not isinstance(raw_terms, list):
        raise VaultFileError(
            f"{path}: `terms` must be a list, got {type(raw_terms).__name__}",
            detail={"path": str(path)},
        )

    terms: list[LexiconTerm] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw_terms):
        where = f"{path}: terms[{index}]"
        if not isinstance(entry, dict):
            raise VaultFileError(f"{where} must be a mapping", detail={"path": str(path)})
        try:
            term_id = _as_str(entry.get("id"))
            canonical = _as_str(entry.get("canonical"))
            if not term_id:
                raise ValueError("`id` is required")
            if not canonical:
                raise ValueError("`canonical` is required")
            raw_variants = entry.get("variants") or []
            if not isinstance(raw_variants, list):
                raise ValueError("`variants` must be a list")
            variants = tuple(v for v in (_as_str(item) for item in raw_variants) if v is not None)
            term = LexiconTerm(
                id=term_id,
                canonical=canonical,
                display_ru=_as_str(entry.get("display_ru")),
                variants=variants,
                kind=_as_str(entry.get("kind")),
                active=_as_bool(entry.get("active"), default=True),
                priority=_as_int(entry.get("priority"), default=0),
                morph=_as_bool(entry.get("morph"), default=False),
                source=_as_str(entry.get("source")),
            )
        except ValueError as exc:
            raise VaultFileError(f"{where}: {exc}", detail={"path": str(path)}) from exc
        if term.id in seen:
            raise VaultFileError(
                f"{where}: duplicate term id {term.id!r}", detail={"path": str(path)}
            )
        seen.add(term.id)
        terms.append(term)

    return Lexicon(path=path, present=True, digest=digest, terms=tuple(terms))


def load_speakers(path: Path) -> Speakers:
    """Read ``_speakers.yaml``. An absent file is normal — every id is then unmapped."""
    if not path.is_file():
        return Speakers(path=path, present=False, digest=None, by_user_id={})

    payload, digest = _load_yaml_mapping(path)
    raw_speakers = payload.get("speakers")
    if raw_speakers is None:
        raw_speakers = {}
    if not isinstance(raw_speakers, dict):
        raise VaultFileError(
            f"{path}: `speakers` must be a mapping of discord user_id -> entry, "
            f"got {type(raw_speakers).__name__}",
            detail={"path": str(path)},
        )

    mapped: dict[str, Speaker] = {}
    for raw_id, entry in raw_speakers.items():
        user_id = str(raw_id).strip()
        where = f"{path}: speakers[{user_id!r}]"
        if not user_id:
            raise VaultFileError(f"{path}: empty discord user_id key", detail={"path": str(path)})
        if entry is None:
            mapped[user_id] = Speaker(user_id=user_id)
            continue
        if not isinstance(entry, dict):
            raise VaultFileError(f"{where} must be a mapping", detail={"path": str(path)})
        try:
            mapped[user_id] = Speaker(
                user_id=user_id,
                player=_as_str(entry.get("player")),
                character=_as_str(entry.get("character")),
                role=_as_str(entry.get("role")),
            )
        except ValueError as exc:
            raise VaultFileError(f"{where}: {exc}", detail={"path": str(path)}) from exc

    return Speakers(path=path, present=True, digest=digest, by_user_id=mapped)
