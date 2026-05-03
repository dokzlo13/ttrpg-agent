from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import click
from openai import AsyncOpenAI, OpenAIError

from .config import build_env, find_project_root, parse_positive_int_env
from .notes import (
    chapter_body_text,
    clean_obsidian_tags,
    merge_tags,
    normalize_obsidian_tag,
    read_chapter,
    tags_for_value,
    write_chapter,
)
from .overview import refresh_overview

RECOMMENDED_TAGS = {
    # table-facing primitives
    "npc",
    "faction",
    "location",
    "settlement",
    "region",
    "dungeon",
    "room",
    "wilderness",
    "hexcrawl",
    "encounter",
    "combat",
    "social",
    "exploration",
    "hazard",
    "trap",
    "puzzle",
    "clue",
    "secret",
    "quest",
    "rumor",
    "readaloud",
    "boxed-text",
    "random-table",
    "roll-table",
    "map",
    "handout",
    # rules/reference primitives
    "statblock",
    "monster",
    "item",
    "treasure",
    "spell",
    "ritual",
    "class",
    "feat",
    "background",
    "rule",
    "mechanic",
    "procedure",
    "subsystem",
    "generator",
    # book structure / tone
    "lore",
    "history",
    "timeline",
    "calendar",
    "appendix",
    "gm-advice",
    "player-option",
    "horror",
    "mystery",
    "investigation",
}
SYSTEM_PROMPT = """You tag one chapter of a TTRPG book for high-precision Obsidian retrieval.

Goal: prefer missing a marginal tag over adding a false-positive tag. A tag should
mean "this chapter is a strong result if I search this tag".

Preferred vocabulary:
npc, faction, location, settlement, region, dungeon, room, wilderness, hexcrawl,
encounter, combat, social, exploration, hazard, trap, puzzle, clue, secret,
quest, rumor, readaloud, boxed-text, random-table, roll-table, map, handout,
statblock, monster, item, treasure, spell, ritual, class, feat, background,
rule, mechanic, procedure, subsystem, generator, lore, history, timeline,
calendar, appendix, gm-advice, player-option, horror, mystery, investigation

You may add a custom tag only if the vocabulary misses a central retrieval
concept. Custom tags must be lowercase kebab-case Obsidian tags, no spaces, using
only letters, numbers, underscore, hyphen, or slash. Do not include '#'.

Strict inclusion rules:
- Use 1-3 tags for a normal keyed adventure entry. Returning [] for a playable
  keyed scene/site/creature is wrong. Use [] only for non-gameable matter
  (credits, copyright, contents, blank/empty extraction, pure flavor intro).
- Choose only the strongest retrieval facets. If unsure, omit the tag.
- Add a tag only with confidence >= 0.75 and concrete evidence in the chapter.
- A tag qualifies if the chapter is mostly about it, has a dedicated paragraph/list
  about it, or contains an explicit object of that type (statblock, table, map,
  handout, boxed text).
- `encounter` = a complete scene, obstacle, interaction, or keyed situation the
  GM can run at the table.
- `location` = the place/site/room is itself important, not merely a backdrop.
- `npc` = a named or roleplayable person/being is central.
- `monster`/`statblock` = a central creature or explicit statblock appears. If
  the chapter contains a full creature/NPC stat line (AC, HD/hp, attacks, saves,
  morale/alignment/XP, etc.), `monster` is normally a strong tag.
- `hazard`/`trap` = the dangerous obstacle is central to the entry.
- `clue` = information directly helps locate/solve another adventure element.
- If the title names a site/room/place (tomb, church, cave, tree, village, pit)
  and the body describes what is there, `location` is normally a strong tag.
- Do NOT tag incidental mentions or one-line details.
- Do NOT tag item/treasure for ordinary loot, pockets, backpacks, rewards, trade
  goods, coins, gems, or potions in one sentence. Use item/treasure only when the
  chapter is primarily about a notable item/treasure or has a dedicated treasure
  section that is a main reason to retrieve the chapter.
- Do NOT tag quest for generic hooks, progress points, or consequences; use it
  only for an explicit mission/request/objective.
- Do NOT tag mechanic for a single save/roll; use rule/mechanic/procedure only
  for a reusable or prominent procedure/subsystem.

Do not be so conservative that keyed adventure content gets []: if a GM can run
it as a scene/site/creature from this chapter, assign the strongest 1-3 tags.

Calibration examples:
- A disguised briar-pedlar whose main action is luring PCs into snares, plus one
  backpack loot sentence => encounter, trap or hazard. NOT item, NOT treasure.
- A dead old man running a dangerous dice scene, with pocket gems if killed =>
  encounter, hazard. NOT treasure, NOT quest, NOT mechanic.
- A ruined churchyard with a wraith statblock and graffiti directions to the pit
  => location, monster, clue. NOT item, NOT treasure, NOT puzzle.
- A vampire's tomb with a named not-vampire and statblock => location, monster.
- A boggart cave with a rude boggart and statblock => location, npc or monster.
Returning [] for any calibration example above is a failure.

Before answering, silently test each candidate tag against the rules above and
remove weak/incidental tags. Return JSON only in this shape:
{"tags": [{"tag": "location", "confidence": 0.9, "evidence": "brief evidence"}]}
"""
MAX_TAGS = 3
# Small chapters are cheap enough to tag from the actual text. Long chapters use
# the detailed summary when available, which avoids large tagging calls while
# keeping summaries useful as derived classification evidence. If no summary is
# available, the CLI sends a bounded excerpt; no local tag inference is attempted.
DEFAULT_FULL_CHAPTER_CHARS = 18_000
MAX_TAG_INPUT_CHARS = 22_000
MIN_CONFIDENCE = 0.75


def _chapter_paths(book_dir: Path) -> list[Path]:
    return sorted(
        p for p in book_dir.glob("*.md") if p.is_file() and not p.name.startswith((".", "__"))
    )


def _clean_tags(value: Any) -> list[str]:
    """Clean LLM output into Obsidian-visible tags, allowing valid custom tags."""
    return clean_obsidian_tags(value, limit=MAX_TAGS)


def _build_tag_evidence(
    fm: dict[str, Any], body_text: str, full_chapter_chars: int = DEFAULT_FULL_CHAPTER_CHARS
) -> tuple[str, str]:
    body = chapter_body_text(body_text)
    title = str(fm.get("section") or "").strip()
    summary = fm.get("summary") if isinstance(fm.get("summary"), str) else ""
    parts = [f"Title: {title}" if title else ""]

    if body and len(body) <= full_chapter_chars:
        source = "body"
        parts.append(f"Body (complete chapter):\n{body}")
    elif summary:
        source = "summary"
        parts.append(
            "Detailed summary (chapter too long for the tagging call; use this as derived "
            f"classification evidence):\n{summary}"
        )
    elif body:
        source = "truncated_body"
        head_len = max(1, MAX_TAG_INPUT_CHARS - 3_500)
        tail = body[-3_000:]
        parts.append(
            f"Body (truncated for tagging):\n{body[:head_len]}\n\n[...middle omitted...]\n\n{tail}"
        )
    else:
        source = "empty"

    return "\n\n".join(part for part in parts if part).strip()[:MAX_TAG_INPUT_CHARS], source


def _tag_evidence(fm: dict[str, Any], body_text: str) -> str:
    return _build_tag_evidence(fm, body_text)[0]


def _parse_tags(text: str) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        data = json.loads(text.strip())
    except json.JSONDecodeError:
        return [], []
    if not isinstance(data, dict):
        return [], []
    raw = data.get("tags")
    if not isinstance(raw, list):
        return [], []

    tags: list[str] = []
    details: list[dict[str, Any]] = []
    for item in raw:
        confidence = 1.0
        evidence = ""
        value: Any = item
        if isinstance(item, dict):
            value = item.get("tag")
            try:
                confidence = float(item.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0.0
            evidence = str(item.get("evidence") or "").strip()
            if confidence < MIN_CONFIDENCE:
                continue
        cleaned = _clean_tags([value])
        if not cleaned:
            continue
        tag = cleaned[0]
        if tag in tags:
            continue
        tags.append(tag)
        details.append({"tag": tag, "confidence": round(confidence, 3), "evidence": evidence})
        if len(tags) >= MAX_TAGS:
            break
    return tags, details


async def _tag_one(
    client: AsyncOpenAI,
    sem: asyncio.Semaphore,
    model: str,
    path: Path,
    force: bool,
    full_chapter_chars: int = DEFAULT_FULL_CHAPTER_CHARS,
) -> dict[str, Any]:
    fm, body_text = read_chapter(path)
    if not fm:
        return {"chapter": path.name, "status": "skipped", "reason": "no frontmatter"}
    body_hash = fm.get("body_hash")
    if not isinstance(body_hash, str) or not body_hash:
        return {"chapter": path.name, "status": "skipped", "reason": "missing body_hash"}
    summary = fm.get("summary") if isinstance(fm.get("summary"), str) else None
    expected = tags_for_value(body_hash, summary)
    if not force and fm.get("tags") is not None and fm.get("tags_for") == expected:
        return {"chapter": path.name, "status": "skipped", "reason": "stamp match"}

    evidence, evidence_source = _build_tag_evidence(fm, body_text, full_chapter_chars)
    tag_details: list[dict[str, Any]] = []
    if not evidence.strip():
        chapter_tags: list[str] = []
    else:
        async with sem:
            started = time.monotonic()
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": evidence},
                    ],
                    temperature=0,
                    max_tokens=180,
                    response_format={"type": "json_object"},
                )
            except OpenAIError as exc:
                return {
                    "chapter": path.name,
                    "status": "failed",
                    "error": type(exc).__name__,
                    "duration_seconds": round(time.monotonic() - started, 3),
                }
        chapter_tags, tag_details = _parse_tags(resp.choices[0].message.content or "{}")

    preserved_tags = [
        tag
        for tag in clean_obsidian_tags(fm.get("tags"))
        if tag == "book-index" or tag.startswith(("book/", "system/"))
    ]
    for key in ("tags", "tags_for"):
        fm.pop(key, None)
    fm["tags"] = merge_tags(preserved_tags, chapter_tags)
    fm["tags_for"] = expected
    write_chapter(path, fm, body_text)
    result: dict[str, Any] = {
        "chapter": path.name,
        "status": "ok",
        "tags": chapter_tags,
        "evidence_source": evidence_source,
    }
    if tag_details:
        result["tag_details"] = tag_details
    return result


async def _gather(coros: list[Any]) -> list[dict[str, Any]]:
    return list(await asyncio.gather(*coros))


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_report(book_dir: Path) -> dict[str, Any]:
    report_path = book_dir / ".ingest" / "report.json"
    if not report_path.exists():
        return {}
    try:
        loaded = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _append_history(report: dict[str, Any], key: str, history_key: str) -> None:
    previous = report.get(key)
    if not isinstance(previous, dict):
        return
    history = report.get(history_key)
    if not isinstance(history, list):
        history = []
    history.append(previous)
    report[history_key] = history[-5:]


def _update_report(
    book_dir: Path,
    *,
    model: str,
    results: list[dict[str, Any]],
    duration: float,
    full_chapter_chars: int = DEFAULT_FULL_CHAPTER_CHARS,
) -> None:
    report = _load_report(book_dir)
    _append_history(report, "tag_book", "tag_book_history")
    report["tag_book"] = {
        "model": model,
        "full_chapter_chars": full_chapter_chars,
        "duration_seconds": round(duration, 3),
        "requested": sum(1 for r in results if r.get("status") in {"ok", "failed"}),
        "succeeded": sum(1 for r in results if r.get("status") == "ok"),
        "failed": sum(1 for r in results if r.get("status") == "failed"),
        "skipped": sum(1 for r in results if r.get("status") == "skipped"),
        "empty": sum(1 for r in results if r.get("status") == "ok" and r.get("tags") == []),
        "results": results,
    }
    report_path = book_dir / ".ingest" / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(report_path, report)


def _resolve_chapter_path(book_dir: Path, chapter: str) -> Path:
    raw = Path(chapter)
    name = raw.name
    candidates: list[Path] = []
    if name.endswith(".md"):
        candidates.append(book_dir / name)
    else:
        candidates.append(book_dir / f"{name}.md")
        candidates.extend(p for p in _chapter_paths(book_dir) if p.stem == name)
    matches = [p for p in candidates if p.is_file()]
    unique = list(dict.fromkeys(matches))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        raise click.ClickException(f"chapter reference {chapter!r} is ambiguous")
    raise click.ClickException(f"no chapter {chapter!r} under {book_dir}")


def _content_tags(tags: object) -> list[str]:
    return [
        tag
        for tag in clean_obsidian_tags(tags)
        if tag != "book-index" and not tag.startswith(("book/", "system/"))
    ]


def _manual_tag_result(
    book_dir: Path,
    chapter: str,
    *,
    requested_tags: tuple[str, ...],
    expected_body_hash: str,
    empty: bool,
    force: bool,
) -> dict[str, Any]:
    if not (book_dir / ".ingest" / "provenance.json").is_file():
        raise click.ClickException(f"{book_dir} is not a current book-ingest output")
    if empty and requested_tags:
        raise click.ClickException("use either --empty or --tag, not both")
    if not empty and not requested_tags:
        raise click.ClickException("provide at least one --tag, or pass --empty for []")

    path = _resolve_chapter_path(book_dir, chapter)
    fm, body_text = read_chapter(path)
    body_hash = fm.get("body_hash")
    if not isinstance(body_hash, str) or not body_hash:
        raise click.ClickException(f"{path.name} has no body_hash")
    if body_hash != expected_body_hash:
        raise click.ClickException(
            f"body_hash mismatch for {path.name}: current {body_hash}, expected {expected_body_hash}"
        )

    normalized_requested = [] if empty else [normalize_obsidian_tag(tag) for tag in requested_tags]
    if any(tag is None for tag in normalized_requested):
        raise click.ClickException("one or more tags were invalid")
    chapter_tags = [tag for tag in normalized_requested if tag is not None]
    if any(tag == "book-index" or tag.startswith(("book/", "system/")) for tag in chapter_tags):
        raise click.ClickException(
            "manual tags should be content tags, not book/* or system/* tags"
        )
    if len(chapter_tags) != len(set(chapter_tags)):
        raise click.ClickException("duplicate tags after normalization")
    if len(chapter_tags) > MAX_TAGS:
        raise click.ClickException(f"manual chapter tags are limited to {MAX_TAGS}")

    summary = fm.get("summary") if isinstance(fm.get("summary"), str) else None
    expected = tags_for_value(body_hash, summary)
    current_content_tags = _content_tags(fm.get("tags"))
    if not force and fm.get("tags_for") == expected and current_content_tags == chapter_tags:
        return {"chapter": path.name, "status": "skipped", "reason": "stamp and tags match"}

    preserved_tags = [
        tag
        for tag in clean_obsidian_tags(fm.get("tags"))
        if tag == "book-index" or tag.startswith(("book/", "system/"))
    ]
    for key in ("tags", "tags_for"):
        fm.pop(key, None)
    fm["tags"] = merge_tags(preserved_tags, chapter_tags)
    fm["tags_for"] = expected
    write_chapter(path, fm, body_text)
    return {"chapter": path.name, "status": "ok", "tags": chapter_tags}


def _update_manual_report(book_dir: Path, *, result: dict[str, Any], duration: float) -> None:
    report = _load_report(book_dir)
    _append_history(report, "tag_manual", "tag_manual_history")
    report["tag_manual"] = {
        "duration_seconds": round(duration, 3),
        "requested": 1,
        "succeeded": 1 if result.get("status") == "ok" else 0,
        "skipped": 1 if result.get("status") == "skipped" else 0,
        "results": [result],
    }
    report_path = book_dir / ".ingest" / "report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(report_path, report)


@click.command("tag-manual", short_help="Apply agent-chosen tags to one chapter safely.")
@click.argument("slug")
@click.argument("chapter")
@click.option("--tag", "requested_tags", multiple=True, help="Obsidian tag to apply; repeatable.")
@click.option("--empty", is_flag=True, help="Write an explicit empty content-tag set.")
@click.option(
    "--body-hash", "expected_body_hash", required=True, help="body_hash read from the note."
)
@click.option("--force", is_flag=True, help="Write even when tags already match.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable results.")
def cmd_tag_manual(
    slug: str,
    chapter: str,
    requested_tags: tuple[str, ...],
    empty: bool,
    expected_body_hash: str,
    force: bool,
    json_output: bool,
) -> None:
    project_root = find_project_root()
    book_dir = (project_root / "vault/library/books" / slug).resolve()
    if not book_dir.is_dir():
        raise click.ClickException(f"no book directory at {book_dir}")
    started = time.monotonic()
    result = _manual_tag_result(
        book_dir,
        chapter,
        requested_tags=requested_tags,
        expected_body_hash=expected_body_hash,
        empty=empty,
        force=force,
    )
    duration = time.monotonic() - started
    overview_path = refresh_overview(book_dir)
    _update_manual_report(book_dir, result=result, duration=duration)
    payload = {
        "slug": slug,
        "overview_path": str(overview_path),
        "duration_seconds": round(duration, 3),
        "result": result,
    }
    if json_output:
        click.echo(json.dumps(payload, indent=2))
        return
    click.echo(
        f"tag-manual {slug}/{result.get('chapter')}: {result.get('status')} "
        f"{result.get('tags', [])}"
    )
    click.echo(f"overview refreshed: {overview_path}")


@click.command("tag", short_help="Classify chapters with Obsidian tags using an LLM.")
@click.argument("slug")
@click.option("--force", is_flag=True, help="Re-tag even when tags_for matches.")
@click.option(
    "--full-text-chars",
    type=click.IntRange(min=1),
    default=None,
    help="Maximum chapter body chars to send as complete text before using summary/excerpt. Defaults to TTRPG_TAG_FULL_CHAPTER_CHARS or 18000.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable results.")
def cmd_tag(slug: str, force: bool, full_text_chars: int | None, json_output: bool) -> None:
    project_root = find_project_root()
    env = build_env(project_root)
    book_dir = (project_root / "vault/library/books" / slug).resolve()
    if not book_dir.is_dir():
        raise click.ClickException(f"no book directory at {book_dir}")
    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
        if json_output:
            click.echo(
                json.dumps(
                    {"slug": slug, "status": "skipped", "reason": "missing_api_key"}, indent=2
                )
            )
        click.echo("OPENAI_API_KEY required for tag; leaving tags unchanged.", err=True)
        return
    chapters = _chapter_paths(book_dir)
    if not chapters:
        raise click.ClickException(f"no chapter markdown files under {book_dir}")
    model = env.get("TTRPG_MARKER_OPENAI_MODEL") or "gpt-4o-mini"
    base_url = env.get("TTRPG_MARKER_OPENAI_BASE_URL") or "https://api.openai.com/v1"
    max_concurrency = parse_positive_int_env(env.get("TTRPG_TAG_MAX_CONCURRENCY"), default=4)
    full_chapter_chars = full_text_chars or parse_positive_int_env(
        env.get("TTRPG_TAG_FULL_CHAPTER_CHARS"), default=DEFAULT_FULL_CHAPTER_CHARS
    )
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    sem = asyncio.Semaphore(max_concurrency)
    started = time.monotonic()
    results = asyncio.run(
        _gather([_tag_one(client, sem, model, p, force, full_chapter_chars) for p in chapters])
    )
    duration = time.monotonic() - started
    overview_path = refresh_overview(book_dir)
    _update_report(
        book_dir,
        model=model,
        results=results,
        duration=duration,
        full_chapter_chars=full_chapter_chars,
    )
    payload = {
        "slug": slug,
        "overview_path": str(overview_path),
        "model": model,
        "duration_seconds": round(duration, 3),
        "full_chapter_chars": full_chapter_chars,
        "results": results,
    }
    if json_output:
        click.echo(json.dumps(payload, indent=2))
        return
    ok = sum(1 for r in results if r.get("status") == "ok")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    failed = [r for r in results if r.get("status") == "failed"]
    empty = sum(1 for r in results if r.get("status") == "ok" and r.get("tags") == [])
    click.echo(f"tag {slug}: {ok} written, {skipped} skipped, {len(failed)} failed, {empty} empty")
    click.echo(f"overview refreshed: {overview_path}")
