"""Reader for ``vault/notes/state/entity-registry.md`` — the canonical-name table.

DESIGN Part II §8: ``record.json`` carries the names the extractor heard and
generalised (``"Koshikawa"``, ``"Koshikawa (Кошикава)"``, ``"местный лорд"``).
The registry is the one place that turns those into a **slug**, so the same
person lands in the same note in session 1 and in session 40.

Two properties matter more than convenience:

* **The file is a note, not config.** It lives in the vault, the owner reads and
  edits it in Obsidian, and it carries prose. The machine surface is exactly one
  fenced ``yaml`` block; everything outside it is for the human. A second block
  is an error rather than a guess about which one was meant.
* **Matching is exact, never fuzzy.** A name is normalised (casefold, collapsed
  whitespace) and compared against ``ru``, ``en``, every alias and the composite
  ``"en (ru)"`` / ``"ru (en)"`` forms the extractor emits. Nothing else matches.
  An unknown name stays unresolved and goes to the owner — inferring that
  «священник» means Jeeton is exactly the heuristic the project forbids, and it
  would silently attach a fact to the wrong person's note.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import VaultFileError
from .provenance import sha256_bytes

SCHEMA = "ttrpg.entity-registry/1"

#: Kinds the registry may declare. Deliberately wider than ``record.json``'s
#: entity kinds: the registry also names deities and abstractions the extractor
#: has no category for.
REGISTRY_KINDS = (
    "pc",
    "npc",
    "faction",
    "location",
    "item",
    "creature",
    "deity",
    "concept",
)

_FENCE_RE = re.compile(r"^```[ \t]*ya?ml[ \t]*$", re.IGNORECASE)
_FENCE_END_RE = re.compile(r"^```[ \t]*$")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_WS_RE = re.compile(r"\s+")


def normalize_name(value: str) -> str:
    """Casefold, NFC-normalise and collapse whitespace. No other transformation.

    Punctuation is preserved on purpose: «Зак-Зак» and «Зак Зак» are different
    strings, and deciding they are the same name is the owner's call, made by
    adding an alias.
    """
    text = unicodedata.normalize("NFC", value).strip()
    return _WS_RE.sub(" ", text).casefold()


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """One canonical entity: its slug, its names, and where its note lives."""

    slug: str
    kind: str | None = None
    ru: str | None = None
    en: str | None = None
    aliases: tuple[str, ...] = ()
    note: str | None = None
    roster: str | None = None
    lexicon: str | None = None

    def match_forms(self) -> tuple[str, ...]:
        """Every string that resolves to this entry, normalised and deduplicated.

        ``extract`` writes a canonical name as ``"<latin> (<cyrillic>)"`` whenever
        it knows both spellings — but it picks whichever pair it heard, not the
        pair the registry calls primary: a session produced both
        ``"Klein Forest (Клейн)"`` (en × alias) and ``"Istrid (Истрид)"``
        (alias × alias). So the composite is the full ordered cross-product of the
        entry's declared forms, not just ``ru`` × ``en``.

        This is expansion, not inference: every part comes from a string the owner
        wrote in this entry, exactly as the lexicon's ``morph`` flag expands a
        declared lemma into its declared paradigm. Nothing is guessed, and a
        composite that collides with another entry is a load-time error.
        """
        declared = [f for f in (self.ru, self.en, *self.aliases) if f and f.strip()]
        forms = list(declared)
        for outer in declared:
            for inner in declared:
                if outer != inner:
                    forms.append(f"{outer} ({inner})")
        return tuple(dict.fromkeys(normalize_name(f) for f in forms))


@dataclass(frozen=True, slots=True)
class EntityRegistry:
    """The parsed registry plus the lookup built from it."""

    path: Path
    present: bool
    digest: str | None
    entries: tuple[RegistryEntry, ...]
    #: Normalised spelling -> entry. Built once at load; the only lookup path.
    _by_form: Mapping[str, RegistryEntry]

    def resolve(self, name: str | None) -> RegistryEntry | None:
        """Exact (normalised) lookup. ``None`` means "ask the owner", not "no match"."""
        if not name or not name.strip():
            return None
        return self._by_form.get(normalize_name(name))

    def unresolved(self, names: Iterable[str | None]) -> list[str]:
        """Distinct names with no entry, in first-seen order — the owner's queue."""
        missing: list[str] = []
        seen: set[str] = set()
        for name in names:
            if not name or not name.strip():
                continue
            if self.resolve(name) is not None:
                continue
            key = normalize_name(name)
            if key in seen:
                continue
            seen.add(key)
            missing.append(name.strip())
        return missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "present": self.present,
            "digest": self.digest,
            "entries": len(self.entries),
            "with_note": sum(1 for e in self.entries if e.note),
            "match_forms": len(self._by_form),
        }


def _as_str(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"`{field}` must be a string, got {type(value).__name__}")
    text = value.strip()
    return text or None


def extract_yaml_block(text: str, path: Path) -> str:
    """Return the single fenced ``yaml`` block's body.

    Zero blocks and two blocks are both hard errors: the first means the note
    lost its machine surface (silently resolving nothing would look like "no
    entity is known"), the second means somebody has to guess which table is
    authoritative, and guessing is the thing this module refuses to do.
    """
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if current is None:
            if _FENCE_RE.match(line):
                current = []
            continue
        if _FENCE_END_RE.match(line):
            blocks.append(current)
            current = None
            continue
        current.append(line)
    if current is not None:
        raise VaultFileError(
            f"{path}: a ```yaml block is opened but never closed.",
            detail={"path": str(path)},
        )
    if not blocks:
        raise VaultFileError(
            f"{path} has no ```yaml block. The registry note carries exactly one, and it is "
            f"the machine surface — without it nothing resolves to a slug.",
            detail={"path": str(path)},
        )
    if len(blocks) > 1:
        raise VaultFileError(
            f"{path} has {len(blocks)} ```yaml blocks; exactly one is the registry. "
            f"Merge them, or move the illustrative one into a plain fenced block.",
            detail={"path": str(path), "blocks": len(blocks)},
        )
    return "\n".join(blocks[0])


def load_entity_registry(path: Path) -> EntityRegistry:
    """Read the registry note. An absent file is normal — everything is then unresolved."""
    if not path.is_file():
        return EntityRegistry(path=path, present=False, digest=None, entries=(), _by_form={})

    raw = path.read_bytes()
    digest = sha256_bytes(raw)
    body = extract_yaml_block(raw.decode("utf-8"), path)
    try:
        loaded = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise VaultFileError(
            f"{path}: the registry's yaml block is not valid YAML: {exc}",
            detail={"path": str(path)},
        ) from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise VaultFileError(
            f"{path}: the registry block must be a mapping with `entities`, "
            f"got {type(loaded).__name__}",
            detail={"path": str(path)},
        )

    schema = loaded.get("schema")
    if schema is not None and schema != SCHEMA:
        raise VaultFileError(
            f"{path}: unknown registry schema {schema!r}; this reader speaks {SCHEMA!r}.",
            detail={"path": str(path), "schema": str(schema)},
        )

    raw_entries = loaded.get("entities") or []
    if not isinstance(raw_entries, list):
        raise VaultFileError(
            f"{path}: `entities` must be a list, got {type(raw_entries).__name__}",
            detail={"path": str(path)},
        )

    entries: list[RegistryEntry] = []
    slugs: set[str] = set()
    by_form: dict[str, RegistryEntry] = {}
    for index, item in enumerate(raw_entries):
        where = f"{path}: entities[{index}]"
        if not isinstance(item, dict):
            raise VaultFileError(f"{where} must be a mapping", detail={"path": str(path)})
        try:
            slug = _as_str(item.get("slug"), "slug")
            if not slug:
                raise ValueError("`slug` is required")
            if not _SLUG_RE.match(slug):
                raise ValueError(
                    f"`slug` must be lowercase kebab-case (a-z, 0-9, single hyphens), got {slug!r}"
                )
            kind = _as_str(item.get("kind"), "kind")
            if kind is not None and kind not in REGISTRY_KINDS:
                raise ValueError(f"`kind` must be one of {', '.join(REGISTRY_KINDS)}, got {kind!r}")
            raw_aliases = item.get("aliases") or []
            if not isinstance(raw_aliases, list):
                raise ValueError("`aliases` must be a list")
            aliases = tuple(
                a for a in (_as_str(x, "aliases[]") for x in raw_aliases) if a is not None
            )
            entry = RegistryEntry(
                slug=slug,
                kind=kind,
                ru=_as_str(item.get("ru"), "ru"),
                en=_as_str(item.get("en"), "en"),
                aliases=aliases,
                note=_as_str(item.get("note"), "note"),
                roster=_as_str(item.get("roster"), "roster"),
                lexicon=_as_str(item.get("lexicon"), "lexicon"),
            )
            if not entry.match_forms():
                raise ValueError("needs at least one of `ru`, `en` or `aliases` to be matchable")
        except ValueError as exc:
            raise VaultFileError(f"{where}: {exc}", detail={"path": str(path)}) from exc

        if entry.slug in slugs:
            raise VaultFileError(
                f"{where}: duplicate slug {entry.slug!r}", detail={"path": str(path)}
            )
        slugs.add(entry.slug)

        for form in entry.match_forms():
            existing = by_form.get(form)
            if existing is not None and existing.slug != entry.slug:
                # Two slugs claiming one spelling is unresolvable by construction:
                # whichever won would depend on file order. Fail loudly instead.
                raise VaultFileError(
                    f"{where}: {form!r} is already claimed by {existing.slug!r}. "
                    f"One spelling cannot resolve to two entities — narrow the alias.",
                    detail={"path": str(path), "form": form, "slugs": [existing.slug, entry.slug]},
                )
            by_form[form] = entry
        entries.append(entry)

    return EntityRegistry(
        path=path,
        present=True,
        digest=digest,
        entries=tuple(entries),
        _by_form=by_form,
    )
