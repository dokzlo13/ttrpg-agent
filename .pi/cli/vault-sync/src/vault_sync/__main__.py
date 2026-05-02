from __future__ import annotations

import fnmatch
import re
import shutil
from pathlib import Path
from typing import Any

import click
import frontmatter
import yaml


WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]]+)\]\]")
EMBED_RE = re.compile(r"!\[\[([^\]]+)\]\]")
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
LOCAL_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def _find_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "AGENTS.md").exists() and (parent / ".pi").exists():
            return parent
    return Path.cwd().resolve()


ROOT = _find_root()
SOURCE_ROOT = ROOT / "imports" / "source-vault"
VAULT_ROOT = ROOT / "vault" / "notes"


def _as_root_relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _require_inside(path: Path, root: Path, message: str) -> None:
    root = root.resolve()
    path = path.resolve()
    if path != root and not path.is_relative_to(root):
        raise click.ClickException(message)


def _resolve_source(source_file: str) -> Path:
    path = Path(source_file)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    _require_inside(path, SOURCE_ROOT, "source file must live under imports/source-vault/")
    if not path.exists() or not path.is_file():
        raise click.ClickException(f"source file not found: {_as_root_relative(path)}")
    return path


def _resolve_dest(dest_file: str) -> Path:
    path = Path(dest_file)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    _require_inside(path, VAULT_ROOT, "destination file must live under vault/notes/")
    if path.exists():
        raise click.ClickException(f"destination already exists: {_as_root_relative(path)}")
    if path.suffix.lower() != ".md":
        raise click.ClickException("destination must be an explicit .md file path")
    return path


def _load_note(path: Path) -> tuple[dict[str, Any], str]:
    post = frontmatter.load(path)
    return dict(post.metadata), post.content


def _title_from_body(path: Path, body: str) -> str:
    for line in body.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return path.stem


def _headings(body: str, limit: int = 20) -> list[str]:
    headings: list[str] = []
    for line in body.splitlines():
        if re.match(r"^#{1,6}\s+", line):
            headings.append(line.strip())
            if len(headings) >= limit:
                break
    return headings


def _split_obsidian_target(raw: str) -> str:
    return raw.split("|", 1)[0].strip()


def _strip_heading(target: str) -> str:
    return target.split("#", 1)[0].strip()


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _local_markdown_target(raw: str) -> str | None:
    target = raw.strip().strip("<>")
    if not target or target.startswith("#") or LOCAL_SCHEME_RE.match(target):
        return None
    return _strip_heading(target)


def _resolve_local_file(source_path: Path, raw_target: str) -> Path | None:
    target = _strip_heading(_split_obsidian_target(raw_target))
    if not target:
        return None
    candidate = (source_path.parent / target).resolve()
    try:
        _require_inside(candidate, SOURCE_ROOT, "attachment must live under imports/source-vault/")
    except click.ClickException:
        return None
    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def _link_report(source_path: Path, raw_values: list[str], *, obsidian: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _ordered_unique(raw_values):
        target = _split_obsidian_target(raw) if obsidian else (_local_markdown_target(raw) or raw)
        target_no_heading = _strip_heading(target)
        local_file = _resolve_local_file(source_path, raw)
        rows.append(
            {
                "raw": raw,
                "target": target,
                "path_part": target_no_heading,
                "resolves_to_file": _as_root_relative(local_file) if local_file else None,
            }
        )
    return rows


def _inspect(path: Path) -> dict[str, Any]:
    metadata, body = _load_note(path)
    lines = body.splitlines()
    wikilinks = WIKILINK_RE.findall(body)
    embeds = EMBED_RE.findall(body)
    markdown_images = [target for target in MARKDOWN_IMAGE_RE.findall(body) if _local_markdown_target(target)]
    return {
        "source": _as_root_relative(path),
        "title": _title_from_body(path, body),
        "size_bytes": path.stat().st_size,
        "body_line_count": len(lines),
        "frontmatter": metadata,
        "frontmatter_keys": list(metadata.keys()),
        "headings": _headings(body),
        "wikilinks": _link_report(path, wikilinks, obsidian=True),
        "embeds": _link_report(path, embeds, obsidian=True),
        "markdown_images": _link_report(path, markdown_images, obsidian=False),
    }


def _candidate_attachments(source_path: Path, body: str) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    raw_targets = EMBED_RE.findall(body)
    raw_targets.extend(target for target in MARKDOWN_IMAGE_RE.findall(body) if _local_markdown_target(target))

    for raw in _ordered_unique(raw_targets):
        source_attachment = _resolve_local_file(source_path, raw)
        if source_attachment is None or source_attachment.suffix.lower() == ".md":
            continue
        try:
            rel = source_attachment.relative_to(source_path.parent)
        except ValueError:
            continue
        pairs.append((source_attachment, rel))
    return pairs


@click.group()
def main() -> None:
    """Safe, dumb copier from imports/source-vault/ into vault/notes/."""


@main.command()
@click.argument("source_file")
def inspect(source_file: str) -> None:
    """Report factual metadata about an archive note. Makes no placement decisions."""
    path = _resolve_source(source_file)
    click.echo(yaml.safe_dump(_inspect(path), sort_keys=False, allow_unicode=True), nl=False)


@main.command(name="list")
@click.option("--filter", "filter_", default="*.md", show_default=True, help="Glob filter under imports/source-vault/.")
def list_cmd(filter_: str) -> None:
    """List markdown candidates. No type or destination inference."""
    for path in sorted(SOURCE_ROOT.rglob("*.md")):
        rel = _as_root_relative(path)
        if not fnmatch.fnmatch(path.name, filter_) and not fnmatch.fnmatch(rel, filter_):
            continue
        _metadata, body = _load_note(path)
        title = _title_from_body(path, body).replace("\t", " ")
        click.echo(f"{rel}\t{len(body.splitlines())}\t{path.stat().st_size}\t{title}")


@main.command()
@click.argument("source_file")
@click.argument("dest_file")
@click.option("--copy-attachments", is_flag=True, help="Copy resolvable local non-md embeds/images next to the destination.")
@click.option("--dry-run", is_flag=True, help="Validate and print the copy plan without writing.")
def copy(source_file: str, dest_file: str, copy_attachments: bool, dry_run: bool) -> None:
    """Copy one archive markdown file to an explicit vault/notes destination."""
    source_path = _resolve_source(source_file)
    dest_path = _resolve_dest(dest_file)
    body = source_path.read_text(encoding="utf-8")

    attachment_pairs: list[tuple[Path, Path]] = []
    attachment_destinations: list[Path] = []
    if copy_attachments:
        _metadata, parsed_body = _load_note(source_path)
        attachment_pairs = _candidate_attachments(source_path, parsed_body)
        attachment_destinations = [dest_path.parent / rel for _src, rel in attachment_pairs]
        for attachment_dest in attachment_destinations:
            if attachment_dest.exists():
                raise click.ClickException(f"attachment destination already exists: {_as_root_relative(attachment_dest)}")

    plan = {
        "source": _as_root_relative(source_path),
        "destination": _as_root_relative(dest_path),
        "copy_attachments": copy_attachments,
        "attachments": [
            {"source": _as_root_relative(src), "destination": _as_root_relative(dest_path.parent / rel)}
            for src, rel in attachment_pairs
        ],
    }

    if dry_run:
        click.echo(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True), nl=False)
        return

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(body, encoding="utf-8")
    for source_attachment, attachment_dest in zip((src for src, _rel in attachment_pairs), attachment_destinations, strict=True):
        attachment_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_attachment, attachment_dest)

    click.echo(yaml.safe_dump(plan, sort_keys=False, allow_unicode=True), nl=False)


if __name__ == "__main__":
    main()
