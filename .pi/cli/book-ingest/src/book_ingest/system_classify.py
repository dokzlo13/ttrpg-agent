from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import click
from openai import OpenAI, OpenAIError

from .config import build_env, find_project_root
from .notes import (
    chapter_body_text,
    clean_obsidian_tags,
    merge_tags,
    read_chapter,
    system_tag_values,
    write_chapter,
)
from .overview import refresh_overview

SYSTEM_PROMPT = """You classify tabletop RPG books by rules system from limited front/back matter evidence.
Return JSON only with keys:
  systems: array of 0-5 Obsidian-safe system tags, most general to most specific
  confidence: number from 0 to 1
  rationale: one short sentence explaining the evidence, no quotes longer than a few words

Rules:
- Keep every plausible useful system label, not just one. Use both family and
  specific clone when evidence supports it, e.g. ["osr", "lotfp"],
  ["osr", "bx"], ["osr", "labyrinth-lord"].
- Prefer explicit title-page, license, compatibility, edition, OGL/SRD,
  publisher, and stat-format evidence.
- If evidence is weak or only genre/theme is visible, return [] with low confidence.
- Tags must be lowercase kebab-case without '#'; allowed examples include:
  osr, bx, becmi, odnd, adnd-1e, adnd-2e, osric, swords-wizardry, lotfp,
  labyrinth-lord, old-school-essentials, dcc, 5e, dnd-3e, dnd-3-5e, dnd-4e,
  pathfinder-1e, pathfinder-2e, coc, mothership, traveller, runequest.
"""
MAX_EVIDENCE_CHARS = 24_000
COMMON_SYSTEMS = {
    "osr",
    "bx",
    "becmi",
    "odnd",
    "adnd-1e",
    "adnd-2e",
    "osric",
    "swords-wizardry",
    "lotfp",
    "labyrinth-lord",
    "old-school-essentials",
    "dcc",
    "5e",
    "dnd-3e",
    "dnd-3-5e",
    "dnd-4e",
    "pathfinder-1e",
    "pathfinder-2e",
    "coc",
    "mothership",
    "traveller",
    "runequest",
}


def _chapter_paths(book_dir: Path) -> list[Path]:
    return sorted(
        p for p in book_dir.glob("*.md") if p.is_file() and not p.name.startswith((".", "__"))
    )


def build_system_evidence(book_dir: Path) -> str:
    chapters = _chapter_paths(book_dir)
    if not chapters:
        return ""
    selected = chapters[:2]
    for path in chapters[-2:]:
        if path not in selected:
            selected.append(path)
    chunks: list[str] = []
    per_chunk = max(MAX_EVIDENCE_CHARS // max(len(selected), 1), 1)
    for path in selected:
        fm, body_text = read_chapter(path)
        title = fm.get("section") if isinstance(fm, dict) else path.stem
        body = chapter_body_text(body_text)
        if not body:
            continue
        chunks.append(f"## {path.name}: {title}\n\n{body[:per_chunk]}")
    return "\n\n---\n\n".join(chunks)[:MAX_EVIDENCE_CHARS]


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("classifier returned non-object JSON")
    return data


def _coerce_result(data: dict[str, Any]) -> dict[str, Any]:
    raw_systems = data.get("systems")
    if raw_systems is None and data.get("system"):
        raw_systems = [data.get("system")]
    systems = clean_obsidian_tags(raw_systems, limit=5)
    systems = [s for s in systems if s != "unknown"]
    # Keep valid custom systems, but put known family/edition tags first when present.
    systems = sorted(systems, key=lambda s: (s not in COMMON_SYSTEMS, systems.index(s)))
    primary = systems[0] if systems else "unknown"
    try:
        confidence = float(data.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(max(confidence, 0.0), 1.0)
    rationale = str(data.get("rationale") or "").strip()
    return {
        "system": primary,
        "systems": systems,
        "confidence": confidence,
        "rationale": rationale[:500],
    }


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _apply_system_tags_to_chapters(book_dir: Path, systems: list[str]) -> None:
    system_tags = system_tag_values(systems)
    for path in _chapter_paths(book_dir):
        fm, body_text = read_chapter(path)
        if not fm:
            continue
        fm["tags"] = merge_tags(fm.get("tags"), system_tags, remove_prefixes=("system/",))
        write_chapter(path, fm, body_text)


def _provenance_path(book_dir: Path) -> Path:
    return book_dir / ".ingest" / "provenance.json"


def _read_ingest_record(book_dir: Path) -> dict[str, Any]:
    ingest_path = _provenance_path(book_dir)
    if not ingest_path.exists():
        return {}
    loaded = json.loads(ingest_path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _update_ingest_record(
    book_dir: Path, result: dict[str, Any], *, model: str, duration: float
) -> None:
    ingest_path = _provenance_path(book_dir)
    record = _read_ingest_record(book_dir)
    record["system"] = result["system"]
    record["systems"] = result["systems"]
    record["system_source"] = "llm"
    record["system_confidence"] = result["confidence"]
    record["system_rationale"] = result["rationale"]
    record["system_classified_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record["system_classifier"] = {"model": model, "duration_seconds": round(duration, 3)}
    ingest_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(ingest_path, record)


def _update_report(book_dir: Path, result: dict[str, Any], *, model: str, duration: float) -> None:
    report_path = book_dir / ".ingest" / "report.json"
    report: dict[str, Any] = {}
    if report_path.exists():
        try:
            loaded = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                report = loaded
        except Exception:
            report = {}
    report["system_classification"] = {
        "model": model,
        "duration_seconds": round(duration, 3),
        **result,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(report_path, report)


@click.command(
    "classify-system", short_help="Classify an ingested book's rules systems with an LLM."
)
@click.argument("slug")
@click.option("--force", is_flag=True, help="Re-classify even when system_source is llm.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable results.")
def cmd_classify_system(slug: str, force: bool, json_output: bool) -> None:
    project_root = find_project_root()
    env = build_env(project_root)
    book_dir = (project_root / "vault/library/books" / slug).resolve()
    if not book_dir.is_dir():
        raise click.ClickException(f"no book directory at {book_dir}")

    existing = _read_ingest_record(book_dir)
    if not force and existing.get("system_source") == "llm" and existing.get("systems"):
        result = {
            "slug": slug,
            "status": "skipped",
            "reason": "stamp match",
            "system": existing.get("system"),
            "systems": existing.get("systems"),
            "confidence": existing.get("system_confidence"),
        }
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(
                f"classify-system {slug}: skipped ({', '.join(existing.get('systems') or [])}, already llm-classified)"
            )
        return

    api_key = env.get("OPENAI_API_KEY")
    if not api_key:
        if json_output:
            click.echo(
                json.dumps(
                    {"slug": slug, "status": "skipped", "reason": "missing_api_key"}, indent=2
                )
            )
        click.echo(
            "OPENAI_API_KEY required for classify-system; leaving system unchanged.", err=True
        )
        return

    evidence = build_system_evidence(book_dir)
    if not evidence:
        raise click.ClickException(f"no chapter evidence under {book_dir}")

    model = env.get("TTRPG_MARKER_OPENAI_MODEL") or "gpt-4o-mini"
    base_url = env.get("TTRPG_MARKER_OPENAI_BASE_URL") or "https://api.openai.com/v1"
    client = OpenAI(api_key=api_key, base_url=base_url)
    started = time.monotonic()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": evidence},
            ],
            temperature=0,
            max_tokens=220,
            response_format={"type": "json_object"},
        )
    except OpenAIError as exc:
        raise click.ClickException(f"OpenAI classification failed: {type(exc).__name__}") from exc
    duration = time.monotonic() - started
    content = resp.choices[0].message.content or "{}"
    result = _coerce_result(_parse_json_object(content))
    _update_ingest_record(book_dir, result, model=model, duration=duration)
    _apply_system_tags_to_chapters(book_dir, result["systems"])
    _update_report(book_dir, result, model=model, duration=duration)
    overview_path = refresh_overview(book_dir)

    payload = {"slug": slug, "status": "ok", "overview_path": str(overview_path), **result}
    if json_output:
        click.echo(json.dumps(payload, indent=2))
        return
    click.echo(
        f"classify-system {slug}: {', '.join(result['systems']) or 'unknown'} "
        f"(confidence {result['confidence']:.2f})"
    )
    click.echo(f"overview refreshed: {overview_path}")
    if result["rationale"]:
        click.echo(f"rationale: {result['rationale']}")
