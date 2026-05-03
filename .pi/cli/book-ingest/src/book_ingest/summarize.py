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
from .notes import body_hash_of, chapter_body_text, read_chapter, write_chapter
from .overview import refresh_overview

SYSTEM_PROMPT = (
    "You will receive one chapter from a TTRPG book. Write a detailed retrieval "
    "summary that is useful for later Obsidian tagging. Include the central "
    "NPCs, factions, locations, encounters, hazards/traps, clues/secrets, maps, "
    "tables, rules/mechanics, player options, monsters/statblocks, notable items, "
    "and tone only when they are actually important in the chapter. Avoid weak or "
    "incidental details. Use 2-5 concise sentences or semicolon-separated clauses; "
    "do not quote at length or paraphrase boxed prose. Output only the summary, no "
    "preamble."
)
MAX_INPUT_CHARS = 50_000
DEFAULT_LONG_ONLY_CHARS = 18_000


def _chapter_paths(book_dir: Path) -> list[Path]:
    return sorted(
        p for p in book_dir.glob("*.md") if p.is_file() and not p.name.startswith((".", "__"))
    )


def _usage_dict(resp: Any) -> dict[str, int] | None:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return None
    out: dict[str, int] = {}
    for attr in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, attr, None)
        if isinstance(value, int):
            out[attr] = value
    return out or None


async def _summarize_one(
    client: AsyncOpenAI,
    sem: asyncio.Semaphore,
    model: str,
    path: Path,
    force: bool,
    long_only: bool = False,
    long_threshold: int = DEFAULT_LONG_ONLY_CHARS,
) -> dict[str, Any]:
    fm, body_text = read_chapter(path)
    if not fm:
        return {"chapter": path.name, "status": "skipped", "reason": "no frontmatter"}
    body = chapter_body_text(body_text)
    if not body:
        return {"chapter": path.name, "status": "skipped", "reason": "empty body"}

    body_hash = str(fm.get("body_hash") or body_hash_of(body_text))
    if long_only and len(body) <= long_threshold:
        return {"chapter": path.name, "status": "skipped", "reason": "small chapter"}
    if not force and fm.get("summary") and fm.get("summary_for") == body_hash:
        return {"chapter": path.name, "status": "skipped", "reason": "stamp match"}

    async with sem:
        started = time.monotonic()
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": body[:MAX_INPUT_CHARS]},
                ],
                temperature=0.2,
                max_tokens=200,
            )
        except OpenAIError as exc:
            return {
                "chapter": path.name,
                "status": "failed",
                "error": type(exc).__name__,
                "duration_seconds": round(time.monotonic() - started, 3),
            }

    summary = (resp.choices[0].message.content or "").strip()
    if not summary:
        return {"chapter": path.name, "status": "failed", "error": "empty response"}

    for key in ("summary", "summary_for"):
        fm.pop(key, None)
    fm["body_hash"] = body_hash
    fm["summary"] = summary
    fm["summary_for"] = body_hash
    write_chapter(path, fm, body_text)
    result: dict[str, Any] = {
        "chapter": path.name,
        "status": "ok",
        "summary": summary,
        "duration_seconds": round(time.monotonic() - started, 3),
    }
    usage = _usage_dict(resp)
    if usage:
        result["usage"] = usage
    return result


async def _gather(coros: list[Any]) -> list[dict[str, Any]]:
    return list(await asyncio.gather(*coros))


def _update_report(
    book_dir: Path,
    *,
    model: str,
    base_url: str,
    results: list[dict[str, Any]],
    duration: float,
    long_only: bool = False,
    long_threshold: int = DEFAULT_LONG_ONLY_CHARS,
) -> None:
    report_path = book_dir / ".ingest" / "report.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                report = {}
        except Exception:
            report = {}
    else:
        report = {}
    usage_totals: dict[str, int] = {}
    for result in results:
        usage = result.get("usage")
        if isinstance(usage, dict):
            for key, value in usage.items():
                if isinstance(value, int):
                    usage_totals[key] = usage_totals.get(key, 0) + value
    previous_summarize = report.get("summarize")
    if isinstance(previous_summarize, dict):
        history = report.get("summarize_history")
        if not isinstance(history, list):
            history = []
        history.append(previous_summarize)
        report["summarize_history"] = history[-5:]
    report["summarize"] = {
        "model": model,
        "base_url": base_url,
        "long_only": long_only,
        "long_threshold": long_threshold if long_only else None,
        "duration_seconds": round(duration, 3),
        "requested": sum(1 for r in results if r.get("status") in {"ok", "failed"}),
        "succeeded": sum(1 for r in results if r.get("status") == "ok"),
        "failed": sum(1 for r in results if r.get("status") == "failed"),
        "skipped": sum(1 for r in results if r.get("status") == "skipped"),
        "usage": usage_totals,
        "results": results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = report_path.with_suffix(report_path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(tmp, report_path)


@click.command("summarize", short_help="Generate/update per-chapter summaries for a book slug.")
@click.argument("slug")
@click.option("--force", is_flag=True, help="Re-summarize even when summary_for matches body_hash.")
@click.option(
    "--long-only",
    is_flag=True,
    help="Summarize only chapters too long for the tagging LLM to receive as full text.",
)
@click.option(
    "--long-threshold",
    type=click.IntRange(min=1),
    default=None,
    help="Body character threshold for --long-only. Defaults to TTRPG_TAG_FULL_CHAPTER_CHARS or 18000.",
)
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable results.")
def cmd_summarize(
    slug: str, force: bool, long_only: bool, long_threshold: int | None, json_output: bool
) -> None:
    project_root = find_project_root()
    env = build_env(project_root)
    book_dir = (project_root / "vault/library/books" / slug).resolve()
    if not book_dir.is_dir():
        raise click.ClickException(f"no book directory at {book_dir}")

    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
        message = (
            "OPENAI_API_KEY required for summarize; system functions without it. "
            "Skipping. Run `book-ingest tag` directly to tag without summaries, "
            "or load the `ttrpg-tag-book-manual` skill for the no-key fallback."
        )
        if json_output:
            click.echo(
                json.dumps(
                    {"slug": slug, "status": "skipped", "reason": "missing_api_key"}, indent=2
                )
            )
        click.echo(message, err=True)
        return

    model = env.get("TTRPG_MARKER_OPENAI_MODEL") or "gpt-4o-mini"
    base_url = env.get("TTRPG_MARKER_OPENAI_BASE_URL") or "https://api.openai.com/v1"
    max_concurrency = parse_positive_int_env(env.get("TTRPG_SUMMARIZE_MAX_CONCURRENCY"), default=4)
    resolved_long_threshold = long_threshold or parse_positive_int_env(
        env.get("TTRPG_TAG_FULL_CHAPTER_CHARS"), default=DEFAULT_LONG_ONLY_CHARS
    )

    chapters = _chapter_paths(book_dir)
    if not chapters:
        raise click.ClickException(f"no chapter markdown files under {book_dir}")

    started = time.monotonic()
    client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    sem = asyncio.Semaphore(max_concurrency)
    results = asyncio.run(
        _gather(
            [
                _summarize_one(client, sem, model, path, force, long_only, resolved_long_threshold)
                for path in chapters
            ]
        )
    )
    duration = time.monotonic() - started
    overview_path = refresh_overview(book_dir)
    _update_report(
        book_dir,
        model=model,
        base_url=base_url,
        results=results,
        duration=duration,
        long_only=long_only,
        long_threshold=resolved_long_threshold,
    )

    if json_output:
        click.echo(
            json.dumps(
                {
                    "slug": slug,
                    "overview_path": str(overview_path),
                    "model": model,
                    "max_concurrency": max_concurrency,
                    "long_only": long_only,
                    "long_threshold": resolved_long_threshold if long_only else None,
                    "duration_seconds": round(duration, 3),
                    "results": results,
                },
                indent=2,
            )
        )
        return

    ok = sum(1 for r in results if r.get("status") == "ok")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    failed = [r for r in results if r.get("status") == "failed"]
    click.echo(f"summarize {slug}: {ok} written, {skipped} skipped, {len(failed)} failed")
    click.echo(f"overview refreshed: {overview_path}")
    for result in failed:
        click.echo(f"  - {result.get('chapter')}: {result.get('error')}", err=True)
