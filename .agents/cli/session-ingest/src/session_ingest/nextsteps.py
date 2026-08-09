"""The one place that knows what to run next.

``next_steps`` is the agent's ordering authority (DESIGN principle 7), so the
chain lives here rather than being re-derived by each verb. Two rules hold
everywhere:

* **Metered steps are omitted without ``OPENAI_API_KEY``.** An agent must never
  be told to run something that will exit 0 doing nothing.
* **``record`` is always emitted.** It is deterministic — ``world_impact`` comes
  out of ``extract``, not a second LLM pass — so it works keyless.

The chain, from DESIGN §4::

    plan → craig-stt transcribe → adopt → qa
      ├─ thresholds ok → render → qmd update && qmd embed → segment → extract → recap / record
      └─ crossed → glossary → plan --run N+1 → craig-stt (scratch, local zip)
                   → adopt --run N+1 → qa --run N+1 --compare N → adopt --promote → render …
"""

from __future__ import annotations

from typing import Any

CLI = ".agents/bin/session-ingest"
CRAIG = ".agents/bin/craig-stt"
QMD = ".agents/bin/qmd"

#: Verbs that spend money and therefore vanish from the chain without a key.
METERED_VERBS = frozenset({"segment", "extract", "recap", "glossary"})

#: Stand-in for the local ``.flac.zip`` when the caller could not resolve one.
#: Callers that hold an open dataset should pass the real path
#: (``adopt.local_archive``) — a step an agent runs verbatim should not contain a
#: placeholder when the manifest names the file.
ARCHIVE_PLACEHOLDER = "<local .flac.zip>"

#: Why every emitted re-run uses the flag and never an environment prefix.
#:
#: ``.agents/env.sh`` exports ``CRAIG_STT_WORK_DIR`` unconditionally (deliberately
#: — it is what pins the corpus inside the project), and ``.agents/bin/craig-stt``
#: sources that contract *after* the caller's environment is already in place. So
#: a ``CRAIG_STT_WORK_DIR=… .agents/bin/craig-stt transcribe …`` prefix is silently
#: overwritten inside the launcher and the re-run lands in the active dataset root,
#: on top of the run it was supposed to be compared against. The global flag
#: outranks the environment, and being global it has to precede the subcommand.
WORK_DIR_NOTE = (
    "`--work-dir` is a GLOBAL craig-stt option: it must come BEFORE `transcribe`. Do not "
    "use a `CRAIG_STT_WORK_DIR=…` environment prefix — .agents/env.sh exports that variable "
    "unconditionally, so the launcher clobbers the prefix and the re-run would overwrite the "
    "dataset it is meant to be compared against."
)

#: The biasing posture, emitted wherever a transcribe command is handed over.
#: Measured locally on a real session: hotwords + initial_prompt biasing made the
#: model recite the term list back (prompt echo), so it is opt-in, not default.
BIASING_NOTE = (
    "Biasing files are optional and EXPERIMENTAL — transcribe runs fine without "
    "--hotwords-file/--initial-prompt-file, and the deterministic lexicon substitution in "
    "`render` is the validated correction path. Re-run `plan --with-biasing` to put them "
    "back into this command, and read the echo signals in `qa --compare` before trusting it."
)


def step(
    step_id: str,
    summary: str,
    *,
    command: str | None = None,
    required: bool = True,
    metered: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": step_id, "required": required, "summary": summary}
    if command is not None:
        payload["command"] = command
    if metered:
        payload["metered"] = True
    payload.update(extra)
    return payload


def _session_flag(session_id: str | None) -> str:
    return f" --session {session_id}" if session_id else ""


def _run_flag(run: int | None) -> str:
    return f" --run {run}" if run and run != 1 else ""


# --------------------------------------------------------------- step factories


#: ``craig-stt`` speaks the same machine contract session-ingest does: with
#: ``--json`` stdout carries exactly one object describing the dataset that was
#: written (``recording_id``, ``dataset_dir``, ``status``, ``segments_digest``,
#: ``schema_version``, ``counts``, per-track rows including ``skip_category``,
#: and a provenance summary), or ``{"error": {...}}`` with exit 1. Progress and
#: logs stay on stderr. Emitting it here means an agent walking the chain reads
#: the recording id and digest it needs for ``adopt`` instead of scraping a table.
TRANSCRIBE_JSON_NOTE = (
    "`--json` puts one JSON object on stdout (recording_id, dataset_dir, status, "
    "segments_digest, counts, tracks[] with skip_category); progress stays on stderr."
)


def transcribe_step(*, share_url_placeholder: str = "<share-url>", run: int = 1) -> dict[str, Any]:
    """The GPU step session-ingest deliberately never spawns itself.

    A ten-minute job with progress output belongs in the agent's own terminal,
    not buried inside another process.
    """
    return step(
        "craig_transcribe",
        "Transcribe the recording on the GPU (~10 min). session-ingest never spawns this. "
        + TRANSCRIBE_JSON_NOTE,
        command=f"{CRAIG} transcribe {share_url_placeholder} --json",
        run=run,
    )


def rerun_transcribe_step(
    *, run: int, scratch_dir: str, archive: str = ARCHIVE_PLACEHOLDER
) -> dict[str, Any]:
    return step(
        "craig_transcribe_rerun",
        (
            "Re-transcribe from the LOCAL archive into a scratch work dir — no re-download, "
            "and the original dataset stays immutable. "
            + WORK_DIR_NOTE
            + " "
            + TRANSCRIBE_JSON_NOTE
        ),
        command=f"{CRAIG} --work-dir {scratch_dir} transcribe {archive} --json",
        run=run,
    )


def qmd_refresh_step() -> dict[str, Any]:
    return step(
        "qmd_refresh",
        "Index the rendered transcript into the (search-excluded) transcripts collection.",
        command=f"{QMD} update && {QMD} embed",
    )


# ------------------------------------------------------------------- the chain


def _post_render_steps(session_id: str | None, *, api_key_present: bool) -> list[dict[str, Any]]:
    steps = [qmd_refresh_step()]
    session = _session_flag(session_id)
    if api_key_present:
        steps.append(
            step(
                "segment",
                "Classify every turn in_character/table_talk/mechanics/ambiguous (metered).",
                command=f"{CLI} segment{session}",
                required=False,
                metered=True,
            )
        )
        steps.append(
            step(
                "extract",
                "Map-reduce the in-character turns into evidence-bearing elements (metered).",
                command=f"{CLI} extract{session}",
                required=False,
                metered=True,
            )
        )
        steps.append(
            step(
                "recap",
                "Draft the Russian recap from extraction.json only (metered).",
                command=f"{CLI} recap{session}",
                required=False,
                metered=True,
            )
        )
    steps.append(
        step(
            "record",
            "Assemble and schema-validate record.json — deterministic, works without a key.",
            command=f"{CLI} record{session}",
        )
    )
    return steps


def _rerun_chain(
    session_id: str | None,
    *,
    run: int,
    api_key_present: bool,
    scratch_dir: str,
    archive: str = ARCHIVE_PLACEHOLDER,
) -> list[dict[str, Any]]:
    """What to do when QA crossed a threshold: grow the lexicon, then re-transcribe.

    Retry economics justify this almost always: ~9 min of GPU, zero API cost.
    """
    session = _session_flag(session_id)
    next_run = run + 1
    steps: list[dict[str, Any]] = []
    if api_key_present:
        steps.append(
            step(
                "glossary",
                "Attach this session's observed variants to canonical lexicon terms (metered).",
                command=f"{CLI} glossary{session}",
                metered=True,
            )
        )
    steps.extend(
        [
            step(
                "plan_rerun",
                "Re-emit hotwords/initial_prompt from the grown lexicon for the next run.",
                command=f"{CLI} plan{session} --run {next_run}",
            ),
            rerun_transcribe_step(run=next_run, scratch_dir=scratch_dir, archive=archive),
            step(
                "adopt_rerun",
                "Adopt the re-transcription as a second run; the first stays intact.",
                command=f"{CLI} adopt <dataset>{session} --run {next_run}",
            ),
            step(
                "qa_compare",
                (
                    "Measure the A/B: run-level aggregates, per-term canonical-vs-variant "
                    "counts, and the echo signals (term hits vs words, compression outliers). "
                    "A falling lexicon_miss_rate beside a raised signal is not an improvement. "
                    "Segment-level comparison is refused by construction."
                ),
                command=f"{CLI} qa{session} --run {next_run} --compare {run}",
            ),
            step(
                "promote",
                (
                    "Promote the better run once you have read the comparison. Refused when a "
                    "chronicle note already exists (--force-relink to override)."
                ),
                command=f"{CLI} adopt <dataset>{session} --run {next_run} --promote",
                required=False,
            ),
        ]
    )
    return steps


def next_steps_for(
    verb: str,
    *,
    session_id: str | None = None,
    api_key_present: bool = False,
    run: int = 1,
    thresholds_crossed: bool = False,
    scratch_dir: str = "$TTRPG_SESSIONS_DIR/scratch/<session-id>-run<N>",
    archive: str = ARCHIVE_PLACEHOLDER,
    dataset_hint: str = "<dataset|recording-id>",
    clean: bool = False,
) -> list[dict[str, Any]]:
    """Ordered steps to run after ``verb`` succeeded."""
    session = _session_flag(session_id)

    if verb == "doctor":
        return []

    if verb == "plan":
        return [
            transcribe_step(run=run),
            step(
                "adopt",
                "Adopt the produced dataset into this session (SDK-verified).",
                command=f"{CLI} adopt {dataset_hint}{session}{_run_flag(run)}",
            ),
        ]

    if verb == "adopt":
        return [
            step(
                "qa",
                "Report transcription quality facts and which configured thresholds they cross.",
                command=f"{CLI} qa{session}{_run_flag(run)}",
            )
        ]

    if verb == "qa":
        if thresholds_crossed:
            return _rerun_chain(
                session_id,
                run=run,
                api_key_present=api_key_present,
                scratch_dir=scratch_dir,
                archive=archive,
            )
        return [
            step(
                "render",
                "Render turn-boundary chunks with lexicon substitution and block IDs.",
                command=f"{CLI} render{session}",
            ),
            *_post_render_steps(session_id, api_key_present=api_key_present),
        ]

    if verb == "render":
        return _post_render_steps(session_id, api_key_present=api_key_present)

    if verb == "segment":
        if not api_key_present:
            return []
        return [
            step(
                "extract",
                "Map-reduce the in-character turns into evidence-bearing elements (metered).",
                command=f"{CLI} extract{session}",
                metered=True,
            )
        ]

    if verb == "extract":
        steps = []
        if api_key_present:
            steps.append(
                step(
                    "recap",
                    "Draft the Russian recap from extraction.json only (metered).",
                    command=f"{CLI} recap{session}",
                    metered=True,
                )
            )
        steps.append(
            step(
                "record",
                "Assemble and schema-validate record.json (deterministic).",
                command=f"{CLI} record{session}",
            )
        )
        return steps

    if verb == "recap":
        return [
            step(
                "record",
                "Assemble and schema-validate record.json (deterministic).",
                command=f"{CLI} record{session}",
            )
        ]

    if verb == "record":
        return [
            step(
                "view",
                (
                    "Read the record compactly — one line per element, one evidence link each. "
                    "Do NOT page record.json itself into context: the rendered view is ~3x "
                    "smaller, because ~60% of the record is the same evidence blocks repeated."
                ),
                command=f"{CLI} view{session}",
            ),
            step(
                "view_owner_queue",
                (
                    "List only what the owner must decide: events flagged needs_owner or "
                    "world_impact != none."
                ),
                command=f"{CLI} view{session} --needs-owner",
            ),
            step(
                "ingest_chronicle",
                (
                    "Agent step: draft the session chronicle under vault/notes/sessions/ and the "
                    "proposals inbox, following the ttrpg-session-chronicle skill. The CLI has no "
                    "write path into vault/notes/ — the agent is the only writer."
                ),
                required=False,
            ),
            step(
                "chronicle_check",
                "Verify the authored chronicle: every citation resolves, frontmatter is complete.",
                command=f"{CLI} chronicle{session} --check",
                required=False,
            ),
        ]

    if verb == "view":
        return [
            step(
                "ingest_chronicle",
                (
                    "Agent step: draft the chronicle and the inbox from what you just read. "
                    "Reconcile against existing canon before writing — a DM recap of earlier "
                    "play looks exactly like this session's events in the record."
                ),
                required=False,
            )
        ]

    if verb == "chronicle":
        if not clean:
            return [
                step(
                    "fix_chronicle",
                    (
                        "Agent step: repair the reported links and frontmatter in the note, then "
                        "re-check. A citation that does not resolve renders as plain text in "
                        "Obsidian and is found at the table, not here."
                    ),
                    required=False,
                ),
                step(
                    "recheck",
                    "Re-run the check once the note is repaired.",
                    command=f"{CLI} chronicle{session} --check",
                    required=False,
                ),
            ]
        return [
            step(
                "prune",
                "The chronicle gate is satisfied. Inventory the reclaimable audio first.",
                command=f"{CLI} prune{session} --dry-run",
                required=False,
            )
        ]

    if verb == "glossary":
        return _rerun_chain(
            session_id,
            run=run,
            api_key_present=api_key_present,
            scratch_dir=scratch_dir,
            archive=archive,
        )

    # grep and prune are terminal: they answer a question or reclaim disk.
    return []
