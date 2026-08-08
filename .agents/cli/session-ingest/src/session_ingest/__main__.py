"""The ``session-ingest`` command surface (DESIGN §4).

Conventions copied from book-ingest, because an agent that has learned one of
this project's CLIs should not have to learn a second:

* ``--json`` everywhere, emitting one object with ordered ``next_steps``.
  Accepted both before and after the verb, so neither habit is wrong.
* One structural status line on **stderr** before any work, with the resolved
  source of each setting in brackets:
  ``session-ingest: session=2026-08-08[cli] dataset=sha256:8f3a…[manifest] window=15m[env]``
* Exit codes: ``0`` ok or cleanly skipped, ``1`` failed, ``2`` not implemented.
  A verb that exists but has no wave-2 body returns
  ``{"status": "not_implemented", "verb": …}`` rather than pretending.

Every verb's work — including resolving the roots, the session id and the
config — happens inside :func:`execute`, so there is exactly one place that
turns a failure into an envelope and an exit code.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click

from . import extract as extract_mod
from . import glossary as glossary_mod
from . import grep as grep_mod
from . import lexicon as lexicon_mod
from . import plan as plan_mod
from . import prune as prune_mod
from . import recap as recap_mod
from . import record as record_mod
from . import render as render_mod
from . import segment as segment_mod
from .adopt import run_adopt
from .config import SessionConfig, build_env, find_project_root, resolve_config
from .doctor import run_doctor
from .errors import NotImplementedStage, SessionIngestError
from .nextsteps import CLI as CLI_PATH
from .nextsteps import step
from .paths import Roots, resolve_roots
from .provenance import short_digest
from .qa import run_qa

TOOL = "session-ingest"

Printer = Callable[[dict[str, Any]], None]
Body = Callable[[], "tuple[dict[str, Any], Printer]"]


# ------------------------------------------------------------------ context


@dataclass
class AppContext:
    """Resolved once per invocation and shared by every verb."""

    project_root: Path
    env: dict[str, str]
    json_output: bool = False
    _roots: Roots | None = field(default=None, repr=False)

    def roots(self) -> Roots:
        if self._roots is None:
            self._roots = resolve_roots(self.env)
        return self._roots

    def config(self, **cli: Any) -> SessionConfig:
        return resolve_config(env=self.env, **cli)


def _context(ctx: click.Context, json_flag: bool) -> AppContext:
    app = ctx.find_object(AppContext)
    if app is None:  # pragma: no cover - the group always installs one
        project_root = find_project_root()
        app = AppContext(project_root=project_root, env=build_env(project_root))
    app.json_output = app.json_output or json_flag
    return app


# ------------------------------------------------------------- status line


def sourced(name: str, value: Any, source: str) -> str:
    return f"{name}={value}[{source}]"


def f_session(session_id: str, source: str) -> str:
    return sourced("session", session_id, source)


def f_run(run: int, source: str = "cli") -> str:
    return sourced("run", run, source)


def f_dataset(digest: str | None) -> str:
    return f"dataset={short_digest(digest)}[manifest]"


def f_window(config: SessionConfig) -> str:
    return sourced("window", f"{config.window_minutes}m", config.source("window_minutes"))


def f_overlap(config: SessionConfig) -> str:
    return sourced("overlap", f"{config.window_overlap_pct}%", config.source("window_overlap_pct"))


def f_merge_gap(config: SessionConfig) -> str:
    return sourced("merge_gap", f"{config.merge_gap_s}s", config.source("merge_gap_s"))


def f_budget(config: SessionConfig) -> str:
    return sourced(
        "budget", f"{config.glossary_budget_chars}c", config.source("glossary_budget_chars")
    )


def f_model(config: SessionConfig) -> str:
    return sourced("llm.model", config.openai_model, config.source("openai_model"))


def f_key(config: SessionConfig) -> str:
    return f"key={'present' if config.api_key_present else 'absent'}"


def emit_status(fields: list[str]) -> None:
    """One line, stderr, before work. Never stdout — that carries the JSON envelope."""
    click.echo(f"{TOOL}: " + " ".join(f for f in fields if f), err=True)


# ------------------------------------------------------------ verb plumbing


def resolve_session(cli_value: str | None, app: AppContext, *, roots: Roots) -> tuple[str, str]:
    """CLI > ``TTRPG_SESSION_ID`` > the most recent adopted session."""
    if cli_value:
        return cli_value, "cli"
    from_env = (app.env.get("TTRPG_SESSION_ID") or "").strip()
    if from_env:
        return from_env, "env"
    known = roots.known_session_ids()
    if not known:
        raise SessionIngestError(
            f"no --session given and no adopted session found under {roots.sessions}",
            code="no_session",
            next_steps=[
                step(
                    "adopt",
                    "Adopt a dataset to create the session first.",
                    command=f"{CLI_PATH} adopt <dataset|recording-id>",
                )
            ],
        )
    return known[-1], "default"


def _print_steps(steps: list[dict[str, Any]]) -> None:
    if not steps:
        return
    click.echo("")
    click.echo("Next:")
    for entry in steps:
        marker = "-" if entry.get("required") else "·"
        click.echo(f"  {marker} {entry.get('summary', entry.get('id'))}")
        if command := entry.get("command"):
            click.echo(f"      {command}")


def _print_json(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False))


def _print_lines(payload: dict[str, Any]) -> None:
    """Human output for verbs that pre-format their own report lines.

    The verb owns the wording — a grep hit and a prune inventory read nothing
    alike — while this stays the single place that appends ``next_steps``.
    """
    for line in payload.get("lines") or []:
        click.echo(line)
    _print_steps(payload.get("next_steps") or [])


def _fail(app: AppContext, verb: str, exc: SessionIngestError) -> None:
    if isinstance(exc, NotImplementedStage):
        payload: dict[str, Any] = {
            "tool": TOOL,
            "verb": verb,
            "status": "not_implemented",
            "summary": exc.summary,
            "message": exc.message,
            "next_steps": [],
        }
    else:
        payload = {
            "tool": TOOL,
            "verb": verb,
            "status": "failed",
            "code": exc.code,
            "message": exc.message,
            "detail": exc.detail,
            "next_steps": exc.next_steps,
        }
    if app.json_output:
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        click.echo(f"{TOOL} {verb}: {exc.message}", err=True)
        _print_steps(exc.next_steps)
    raise SystemExit(exc.exit_code)


def execute(app: AppContext, verb: str, body: Body) -> None:
    """Run one verb, mapping every typed failure onto the shared envelope."""
    try:
        payload, printer = body()
    except SessionIngestError as exc:
        _fail(app, verb, exc)
        return
    envelope = {"tool": TOOL, "verb": verb, **payload}
    if app.json_output:
        click.echo(json.dumps(envelope, indent=2, ensure_ascii=False))
    else:
        printer(payload)


json_option = click.option(
    "--json", "json_output", is_flag=True, help="Emit one machine-readable object on stdout."
)
session_option = click.option(
    "--session",
    "session_id",
    default=None,
    help="Session id (YYYY-MM-DD[-suffix]). CLI > TTRPG_SESSION_ID > most recent adopted.",
)
force_option = click.option(
    "--force", is_flag=True, help="Re-run even when the composite digest key matches."
)


# ------------------------------------------------------------------- group


@click.group(invoke_without_command=True)
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable output on stdout.")
@click.pass_context
def cli(ctx: click.Context, json_output: bool) -> None:
    """Turn a craig-stt dataset into a transcript, a recap draft and one session record."""
    project_root = find_project_root()
    ctx.obj = AppContext(
        project_root=project_root, env=build_env(project_root), json_output=json_output
    )
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ------------------------------------------------------------------ doctor


@cli.command("doctor", short_help="Report SDK/version skew, roots, disk by class, key presence.")
@json_option
@click.pass_context
def cmd_doctor(ctx: click.Context, json_output: bool) -> None:
    """Read-only environment facts. Writes nothing, repairs nothing."""
    app = _context(ctx, json_output)

    def body() -> tuple[dict[str, Any], Printer]:
        config = app.config()
        emit_status([f_key(config)])
        report = run_doctor(
            roots=app.roots(), project_root=app.project_root, config=config, env=app.env
        )
        return report.to_dict(), _print_doctor

    execute(app, "doctor", body)


def _human_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _print_doctor(payload: dict[str, Any]) -> None:
    sdk = payload["sdk"]
    click.echo(f"Status: {payload['status']}")
    click.echo(f"  SDK:       {sdk['package']} {sdk['reader_version'] or 'MISSING'}")
    if sdk["producer_versions_on_disk"]:
        click.echo(f"  Producers: {', '.join(sdk['producer_versions_on_disk'])}")
    click.echo("  Roots:")
    for name, state in payload["roots"].items():
        mark = "ok " if state["exists"] else "-- "
        click.echo(f"    {mark}{name}: {state['path']}")
    tools = payload["tools"]
    click.echo(f"  craig-stt work dir: {tools['craig_stt_work_dir']}")
    click.echo(f"    note: {tools['craig_stt_work_dir_note']}")
    disk = payload["disk"]
    click.echo("  Disk:")
    click.echo(f"    datasets:         {_human_bytes(disk['datasets_bytes'])}")
    click.echo(f"    session-derived:  {_human_bytes(disk['session_derived_bytes'])}")
    click.echo(f"    scratch:          {_human_bytes(disk['scratch_bytes'])}")
    click.echo(f"    transcripts:      {_human_bytes(disk['transcripts_bytes'])}")
    click.echo(f"  Datasets:  {len(payload['datasets'])}")
    click.echo(f"  Sessions:  {payload['sessions']['count']}")
    click.echo(
        f"  API key:   {'present' if payload['llm']['openai_api_key_present'] else 'absent'}"
    )
    transcripts = payload["qmd"].get("transcripts")
    if transcripts:
        excluded = not transcripts.get("include_by_default")
        click.echo(f"  qmd transcripts collection: {'excluded' if excluded else 'INCLUDED'}")
    for finding in payload["findings"]:
        click.echo(f"  [{finding['severity']}] {finding['code']}: {finding['message']}")


# ----------------------------------------------------------------- lexicon


@cli.command("lexicon", short_help="List lexicon terms and, with --expand, generated case forms.")
@click.option(
    "--expand",
    is_flag=True,
    help="Also generate the morph:true case-form table and show every pair and skip reason.",
)
@json_option
@click.pass_context
def cmd_lexicon(ctx: click.Context, expand: bool, json_output: bool) -> None:
    """Read-only inspection of vault/transcripts/_lexicon.yaml. Writes nothing."""
    app = _context(ctx, json_output)

    def body() -> tuple[dict[str, Any], Printer]:
        roots = app.roots()
        emit_status(
            [
                sourced("lexicon", roots.lexicon_file, "env"),
                sourced("expand", str(expand).lower(), "cli"),
            ]
        )
        return lexicon_mod.run(roots=roots, expand=expand), _print_lines

    execute(app, "lexicon", body)


# -------------------------------------------------------------------- plan


@cli.command("plan", short_help="Build hotwords/initial_prompt and emit the transcribe command.")
@session_option
@click.option("--run", "run", default=1, show_default=True, type=int, help="Transcription run.")
@click.option(
    "--entities-file",
    type=click.Path(path_type=Path),
    default=None,
    help="Extra entity names (one per line) from active-arc notes.",
)
@click.option(
    "--rank", is_flag=True, help="Rank terms with an LLM instead of active/priority (metered)."
)
@click.option(
    "--budget-chars", type=int, default=None, help="Combined biasing budget, default 450."
)
@click.option(
    "--with-biasing",
    is_flag=True,
    help=(
        "Put --hotwords-file/--initial-prompt-file into the emitted transcribe command. "
        "EXPERIMENTAL: measured locally as prompt echo, not correction. The files are "
        "written either way."
    ),
)
@force_option
@json_option
@click.pass_context
def cmd_plan(
    ctx: click.Context,
    session_id: str | None,
    run: int,
    entities_file: Path | None,
    rank: bool,
    budget_chars: int | None,
    with_biasing: bool,
    force: bool,
    json_output: bool,
) -> None:
    """Emit the biasing inputs for a transcription run."""
    app = _context(ctx, json_output)

    def body() -> tuple[dict[str, Any], Printer]:
        config = app.config(cli_glossary_budget_chars=budget_chars)
        roots = app.roots()
        session, source = resolve_session(session_id, app, roots=roots)
        emit_status(
            [
                f_session(session, source),
                f_run(run),
                f_budget(config),
                sourced("biasing", "on" if with_biasing else "off", "cli"),
                f_key(config),
            ]
        )
        return (
            plan_mod.run(
                roots=roots,
                config=config,
                session_id=session,
                run=run,
                entities_file=entities_file,
                rank=rank,
                budget_chars=budget_chars,
                with_biasing=with_biasing,
                force=force,
            ),
            _print_lines,
        )

    execute(app, "plan", body)


# ------------------------------------------------------------------- adopt


@cli.command("adopt", short_help="Bind a craig-stt dataset to a session id (SDK-verified).")
@click.argument("target")
@session_option
@click.option(
    "--run", "run", default=None, type=int, help="Run number; defaults to the active run."
)
@click.option("--promote", is_flag=True, help="Make this run the active one.")
@click.option("--allow-partial", is_flag=True, help="Adopt a capture taken while still recording.")
@click.option(
    "--allow-skipped-tracks",
    is_flag=True,
    help=(
        "Adopt although a track failed to decode or vanished from meta.tracks. Tracks "
        "craig-stt marked `ignored` never need this; skips with no skip_category at all "
        "are refused outright and this flag does not lift that."
    ),
)
@click.option(
    "--force-relink",
    is_flag=True,
    help="Promote even though a chronicle exists; its evidence links then need re-linking.",
)
@force_option
@json_option
@click.pass_context
def cmd_adopt(
    ctx: click.Context,
    target: str,
    session_id: str | None,
    run: int | None,
    promote: bool,
    allow_partial: bool,
    allow_skipped_tracks: bool,
    force_relink: bool,
    force: bool,
    json_output: bool,
) -> None:
    """Adopt TARGET (a dataset directory or a recording id) into a session."""
    app = _context(ctx, json_output)

    def body() -> tuple[dict[str, Any], Printer]:
        config = app.config()
        roots = app.roots()
        session_source = "cli" if session_id else "manifest"

        def announce(resolved_session: str, digest: str, resolved_run: int) -> None:
            emit_status(
                [
                    f_session(resolved_session, session_source),
                    f_dataset(digest),
                    f_run(resolved_run, "cli" if run is not None else "default"),
                ]
            )

        result = run_adopt(
            target=target,
            roots=roots,
            project_root=app.project_root,
            config=config,
            session_id=session_id,
            run=run,
            promote=promote,
            allow_partial=allow_partial,
            allow_skipped_tracks=allow_skipped_tracks,
            force_relink=force_relink,
            force=force,
            on_resolved=announce,
        )
        return result.to_dict(), _print_adopt

    execute(app, "adopt", body)


def _print_adopt(payload: dict[str, Any]) -> None:
    click.echo(f"{payload['status']}: {payload['session']} <- {payload['recording_id']}")
    click.echo(f"  Dataset:   {payload['dataset_path']}")
    click.echo(f"  Digest:    {short_digest(payload['dataset_digest'])}")
    click.echo(f"  Run:       {payload['run']} (active {payload['active_run']})")
    click.echo(f"  Capture:   {payload['dataset_status']}")
    if payload["skipped_tracks"]:
        click.echo(f"  Skipped:   {len(payload['skipped_tracks'])} track(s)")
    for warning in payload["warnings"]:
        click.echo(f"  warning: {warning}")
    _print_steps(payload["next_steps"])


# ---------------------------------------------------------------------- qa


@cli.command("qa", short_help="Transcription quality facts and which thresholds they cross.")
@session_option
@click.option("--run", "run", default=None, type=int, help="Run to measure; defaults to active.")
@click.option(
    "--compare",
    default=None,
    type=int,
    help="Baseline run for a run-level A/B. Segment-level comparison is refused by construction.",
)
@force_option
@json_option
@click.pass_context
def cmd_qa(
    ctx: click.Context,
    session_id: str | None,
    run: int | None,
    compare: int | None,
    force: bool,
    json_output: bool,
) -> None:
    """Report QA facts for one run. Never a verdict."""
    app = _context(ctx, json_output)

    def body() -> tuple[dict[str, Any], Printer]:
        config = app.config()
        roots = app.roots()
        session, source = resolve_session(session_id, app, roots=roots)

        def announce(resolved_session: str, digest: str | None, resolved_run: int) -> None:
            emit_status(
                [
                    f_session(resolved_session, source),
                    f_dataset(digest),
                    f_run(resolved_run, "cli" if run is not None else "default"),
                ]
            )

        result = run_qa(
            roots=roots,
            config=config,
            session_id=session,
            run=run,
            compare=compare,
            force=force,
            on_resolved=announce,
        )
        return result.to_dict(), _print_qa

    execute(app, "qa", body)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _compare_row(row: dict[str, Any]) -> str:
    values = " ".join(f"{k}={_fmt(v)}" for k, v in row.items() if k.startswith("run_"))
    return f"{row['metric']}: {values} delta={_fmt(row['delta'])}"


def _print_echo(comparison: dict[str, Any]) -> None:
    """The prompt-echo counts and any signal they raised. Facts, then one reading."""
    echo = comparison.get("echo") or {}
    click.echo("  Echo check:")
    for row in echo.get("facts") or []:
        click.echo(f"    {_compare_row(row)}")
    signals = comparison.get("signals") or []
    if not signals:
        click.echo("    signals: none")
        return
    notes = echo.get("signal_notes") or {}
    click.echo("    signals:")
    for name in signals:
        click.echo(f"      {name}: {notes.get(name, '')}")
    click.echo(f"    {echo.get('reading', '')}")


def _print_qa(payload: dict[str, Any]) -> None:
    metrics = (payload.get("report") or {}).get("metrics") or {}
    click.echo(f"{payload['status']}: {payload['session']} run {payload['run']}")
    click.echo(f"  word_p10:              {_fmt(metrics.get('word_p10'))}")
    click.echo(f"  low_logprob_share:     {_fmt(metrics.get('low_logprob_share'))}")
    click.echo(f"  compression_outliers:  {_fmt(metrics.get('compression_outlier_count'))}")
    click.echo(f"  bleed_rate:            {_fmt(metrics.get('bleed_rate'))}")
    click.echo(f"  overlap_rate:          {_fmt(metrics.get('overlap_rate'))}")
    click.echo(f"  lexicon_miss_rate:     {_fmt(metrics.get('lexicon_miss_rate'))}")
    click.echo(f"  unmapped_speakers:     {len(metrics.get('unmapped_speakers') or [])}")
    click.echo(f"  tracks_missing:        {_fmt(metrics.get('tracks_missing'))}")
    crossed = payload.get("thresholds_crossed") or []
    if crossed:
        click.echo("  thresholds crossed:")
        for entry in crossed:
            comparison = "<" if entry.get("direction") == "min" else ">"
            click.echo(
                f"    {entry['metric']}={_fmt(entry['value'])} "
                f"{comparison} {_fmt(entry['threshold'])}"
            )
    else:
        click.echo("  thresholds crossed:    none")
    comparison_block = payload.get("comparison")
    if comparison_block:
        click.echo("")
        click.echo(f"  Comparison runs {comparison_block['runs']}:")
        for row in comparison_block["aggregates"]:
            click.echo(f"    {_compare_row(row)}")
        for row in comparison_block["lexicon_terms"]:
            click.echo(
                f"    term {row['canonical']}: delta_miss_rate={_fmt(row['delta_miss_rate'])}"
            )
        _print_echo(comparison_block)
        click.echo(f"  {comparison_block['segment_level_reason']}")
    for warning in payload.get("warnings") or []:
        click.echo(f"  warning: {warning}")
    click.echo(f"  Report: {payload['qa_path']}")
    _print_steps(payload.get("next_steps") or [])


# ------------------------------------------------------------------ render


@cli.command("render", short_help="Render turn-boundary transcript chunks into the vault.")
@session_option
@click.option("--run", "run", default=None, type=int, help="Run to render; defaults to active.")
@click.option("--window-minutes", type=int, default=None, help="Chunk size, default 15.")
@click.option("--merge-gap-s", type=float, default=None, help="Turn merge gap, default 1.5.")
@force_option
@json_option
@click.pass_context
def cmd_render(
    ctx: click.Context,
    session_id: str | None,
    run: int | None,
    window_minutes: int | None,
    merge_gap_s: float | None,
    force: bool,
    json_output: bool,
) -> None:
    """Write vault/transcripts/<id>/ plus anchors.json and index.sqlite."""
    app = _context(ctx, json_output)

    def body() -> tuple[dict[str, Any], Printer]:
        config = app.config(cli_window_minutes=window_minutes, cli_merge_gap_s=merge_gap_s)
        roots = app.roots()
        session, source = resolve_session(session_id, app, roots=roots)
        emit_status([f_session(session, source), f_window(config), f_merge_gap(config)])
        return (
            render_mod.run(
                roots=roots,
                config=config,
                session_id=session,
                run=run,
                window_minutes=window_minutes,
                merge_gap_s=merge_gap_s,
                force=force,
            ),
            _print_lines,
        )

    execute(app, "render", body)


# -------------------------------------------------------------------- grep


@cli.command("grep", short_help="Exact speaker/time/regex slicing over rendered transcripts.")
@session_option
@click.option("--all", "all_sessions", is_flag=True, help="Search every rendered session.")
@click.option("--speaker", default=None, help="Restrict to one speaker name or discord id.")
@click.option("--from", "time_from", default=None, help="Start time (hh:mm:ss or seconds).")
@click.option("--to", "time_to", default=None, help="End time (hh:mm:ss or seconds).")
@click.option("--regex", default=None, help="Pattern to match against turn text.")
@click.option(
    "--context", default=0, show_default=True, type=int, help="Surrounding turns to show."
)
@json_option
@click.pass_context
def cmd_grep(
    ctx: click.Context,
    session_id: str | None,
    all_sessions: bool,
    speaker: str | None,
    time_from: str | None,
    time_to: str | None,
    regex: str | None,
    context: int,
    json_output: bool,
) -> None:
    """Slice the transcript index. Never cat a chunk; this is the read path."""
    app = _context(ctx, json_output)

    def body() -> tuple[dict[str, Any], Printer]:
        config = app.config()
        roots = app.roots()
        session: str | None = None
        if all_sessions:
            emit_status(["scope=all[cli]"])
        else:
            session, source = resolve_session(session_id, app, roots=roots)
            emit_status([f_session(session, source)])
        return (
            grep_mod.run(
                roots=roots,
                config=config,
                session_id=session,
                all_sessions=all_sessions,
                speaker=speaker,
                time_from=time_from,
                time_to=time_to,
                regex=regex,
                context=context,
            ),
            _print_lines,
        )

    execute(app, "grep", body)


# ----------------------------------------------------------------- segment


@cli.command(
    "segment", short_help="Classify each turn in_character/table_talk/mechanics/ambiguous."
)
@session_option
@click.option("--run", "run", default=None, type=int, help="Run to classify; defaults to active.")
@force_option
@json_option
@click.pass_context
def cmd_segment(
    ctx: click.Context, session_id: str | None, run: int | None, force: bool, json_output: bool
) -> None:
    """Metered. Writes turns.class.jsonl."""
    app = _context(ctx, json_output)

    def body() -> tuple[dict[str, Any], Printer]:
        config = app.config()
        roots = app.roots()
        session, source = resolve_session(session_id, app, roots=roots)
        emit_status(
            [
                f_session(session, source),
                f_window(config),
                f_overlap(config),
                f_model(config),
                f_key(config),
            ]
        )
        return (
            segment_mod.run(roots=roots, config=config, session_id=session, run=run, force=force),
            _print_json,
        )

    execute(app, "segment", body)


# ----------------------------------------------------------------- extract


@cli.command("extract", short_help="Map-reduce play into evidence-bearing elements.")
@session_option
@click.option("--run", "run", default=None, type=int, help="Run to extract; defaults to active.")
@click.option(
    "--keep-bleed", is_flag=True, help="Keep bleed-suspect turns instead of dropping them."
)
@force_option
@json_option
@click.pass_context
def cmd_extract(
    ctx: click.Context,
    session_id: str | None,
    run: int | None,
    keep_bleed: bool,
    force: bool,
    json_output: bool,
) -> None:
    """Metered. Writes extraction.json."""
    app = _context(ctx, json_output)

    def body() -> tuple[dict[str, Any], Printer]:
        config = app.config()
        roots = app.roots()
        session, source = resolve_session(session_id, app, roots=roots)
        emit_status(
            [
                f_session(session, source),
                f_window(config),
                f_overlap(config),
                f_model(config),
                f_key(config),
            ]
        )
        return (
            extract_mod.run(
                roots=roots,
                config=config,
                session_id=session,
                run=run,
                keep_bleed=keep_bleed,
                force=force,
            ),
            _print_json,
        )

    execute(app, "extract", body)


# ------------------------------------------------------------------- recap


@cli.command("recap", short_help="Draft the Russian recap from extraction.json only.")
@session_option
@click.option(
    "--audience",
    type=click.Choice(["dm", "players"]),
    default="dm",
    show_default=True,
    help="Who the recap is written for.",
)
@force_option
@json_option
@click.pass_context
def cmd_recap(
    ctx: click.Context, session_id: str | None, audience: str, force: bool, json_output: bool
) -> None:
    """Metered. Writes recap.draft.md."""
    app = _context(ctx, json_output)

    def body() -> tuple[dict[str, Any], Printer]:
        config = app.config()
        roots = app.roots()
        session, source = resolve_session(session_id, app, roots=roots)
        emit_status(
            [
                f_session(session, source),
                sourced("audience", audience, "cli"),
                f_model(config),
                f_key(config),
            ]
        )
        return (
            recap_mod.run(
                roots=roots, config=config, session_id=session, audience=audience, force=force
            ),
            _print_json,
        )

    execute(app, "recap", body)


# ------------------------------------------------------------------ record


@cli.command("record", short_help="Assemble and validate record.json (deterministic).")
@session_option
@click.option("--run", "run", default=None, type=int, help="Run to assemble; defaults to active.")
@force_option
@json_option
@click.pass_context
def cmd_record(
    ctx: click.Context, session_id: str | None, run: int | None, force: bool, json_output: bool
) -> None:
    """Deterministic: works without an API key. Writes record.json."""
    app = _context(ctx, json_output)

    def body() -> tuple[dict[str, Any], Printer]:
        config = app.config()
        roots = app.roots()
        session, source = resolve_session(session_id, app, roots=roots)
        emit_status([f_session(session, source), f_merge_gap(config)])
        return (
            record_mod.run(roots=roots, config=config, session_id=session, run=run, force=force),
            _print_json,
        )

    execute(app, "record", body)


# ---------------------------------------------------------------- glossary


@cli.command("glossary", short_help="Attach observed variants to canonical lexicon terms.")
@session_option
@click.option("--run", "run", default=None, type=int, help="Run to learn from; defaults to active.")
@click.option(
    "--budget-chars", type=int, default=None, help="Combined biasing budget, default 450."
)
@force_option
@json_option
@click.pass_context
def cmd_glossary(
    ctx: click.Context,
    session_id: str | None,
    run: int | None,
    budget_chars: int | None,
    force: bool,
    json_output: bool,
) -> None:
    """Metered. Append-only merge into vault/transcripts/_lexicon.yaml."""
    app = _context(ctx, json_output)

    def body() -> tuple[dict[str, Any], Printer]:
        config = app.config(cli_glossary_budget_chars=budget_chars)
        roots = app.roots()
        session, source = resolve_session(session_id, app, roots=roots)
        emit_status([f_session(session, source), f_budget(config), f_model(config), f_key(config)])
        return (
            glossary_mod.run(
                roots=roots,
                config=config,
                session_id=session,
                run=run,
                budget_chars=budget_chars,
                force=force,
            ),
            _print_json,
        )

    execute(app, "glossary", body)


# ------------------------------------------------------------------- prune


@cli.command("prune", short_help="Reclaim regenerable pcm/stt via the SDK. Never rm.")
@session_option
@click.option("--dry-run", is_flag=True, help="Report the inventory without deleting.")
@force_option
@json_option
@click.pass_context
def cmd_prune(
    ctx: click.Context, session_id: str | None, dry_run: bool, force: bool, json_output: bool
) -> None:
    """Refuses while the session chronicle is unwritten."""
    app = _context(ctx, json_output)

    def body() -> tuple[dict[str, Any], Printer]:
        config = app.config()
        roots = app.roots()
        session, source = resolve_session(session_id, app, roots=roots)
        emit_status([f_session(session, source), sourced("dry_run", str(dry_run).lower(), "cli")])
        return (
            prune_mod.run(
                roots=roots,
                config=config,
                session_id=session,
                project_root=app.project_root,
                dry_run=dry_run,
                force=force,
            ),
            _print_lines,
        )

    execute(app, "prune", body)


def main(args: list[str] | None = None) -> None:
    """Console-script entry point."""
    argv = list(sys.argv[1:] if args is None else args)
    cli.main(args=argv, standalone_mode=True)


if __name__ == "__main__":
    main()
