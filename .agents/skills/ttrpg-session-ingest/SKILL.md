---
name: ttrpg-session-ingest
description: |
  End-to-end processing of a recorded play session: Craig share URL → craig-stt
  dataset → rendered transcript in vault/transcripts/ → recap draft and
  record.json, from which the agent authors the session note. Use when the user
  pastes a Craig share link or asks to "process the session recording",
  "transcribe the session", "write a session recap", «что было на сессии», or
  complains that names/terms came out wrong in a transcript. Also the routing
  skill for quoting what someone actually said at the table.
---

# ttrpg-session-ingest

Canonical pipeline doc for recorded sessions. Drives the full path from a Craig
share URL to a searchable transcript under `vault/transcripts/<session-id>/`,
a `recap.draft.md`, and one structured `record.json`.

Two layers, one handoff:

```text
LAYER 1 — the pipeline (this skill)
  share URL ─► craig-stt ─► dataset ─► session-ingest ─► transcript + recap.draft.md + record.json
LAYER 2 — the campaign note (ttrpg-vault-authoring)
  record.json + recap.draft.md ─► AGENT ─► session note under vault/notes/
```

`record.json` is the **only** thing layer 2 reads from layer 1. The verbatim
transcript stays available for quoting, but the session note is built from the
distilled record, never from bulk transcript.

## When to use

- A Craig share URL was pasted, or the user asked to process/transcribe a
  recording.
- The user wants a recap of the last session, or asks «что было на сессии».
- Transcription quality complaints — wrong names, garbled terms, a player's
  voice attributed to someone else.
- "What did X actually say about Y?" — see [Fact-checking](#fact-checking-what-was-said).

**Don't use this for:** writing the durable session note (that's
`ttrpg-vault-authoring`), campaign/arc design (`ttrpg-campaign-design`), or
prose lookup in books and prep notes (`ttrpg-library-search`).

## The chain

```text
plan → craig-stt transcribe --json (GPU ~10 min) → adopt → qa
  ├─ thresholds ok → render → qmd update && qmd embed → segment → extract → ┬─ recap
  │                                                                         └─ record
  └─ thresholds crossed → glossary → plan --run 2 → craig-stt (scratch dir, LOCAL zip,
       no re-download) → adopt --run 2 → qa --run 2 --compare 1 → adopt --promote → render …
then: the AGENT reads recap.draft.md + record.json and authors the session note
```

**`--json` `next_steps` is the ordering authority**, exactly as with
`book-ingest`. Pass `--json`, read `next_steps`, run the entries in order. Do
not invent a step it omitted and do not reorder. Every verb also prints one
stderr status line showing where each resolved setting came from (`[cli]`,
`[env]`, `[default]`, `[manifest]`).

`session-ingest` **never spawns craig-stt**: `plan` emits the exact
`.agents/bin/craig-stt transcribe … --json` command in `next_steps` and the agent
runs it, because a ten-minute GPU job with progress output should not hide behind
another process.

**A re-run's work dir is a GLOBAL flag, before the subcommand:**

```bash
.agents/bin/craig-stt --work-dir .cache/sessions/scratch/2026-08-08-r2 \
  transcribe <local .flac.zip> --json
```

Never `CRAIG_STT_WORK_DIR=… .agents/bin/craig-stt transcribe …`. `.agents/env.sh`
exports that variable unconditionally and the launcher sources the contract
*inside* itself, after your environment is already set — so the prefix is
clobbered, the re-run writes into the live dataset root, and it lands on top of
the run you meant to compare it against. `plan` emits the correct form; run it
verbatim. `session-ingest doctor` reports the resolved work dir and warns when the
environment carries a conflicting one.

**craig-stt speaks the same `--json` contract.** The emitted command carries
`--json`, so the run answers with exactly **one JSON object on stdout** —
`recording_id`, `dataset_dir`, `status`, `segments_digest`, `schema_version`,
`counts`, one row per track (`transcribed`, `skipped`, `skip_category`,
`skip_reason`) and a `provenance_summary`. Progress and logs go to stderr; a
failure is `{"error": {"type", "message"}}` with exit 1. Parse that object rather
than the terminal output: `recording_id` is what the next `adopt` takes, and
`tracks[].skip_category` tells you in advance whether `adopt` will need a flag —
or will refuse, when a skipped track carries no category at all.
`craig-stt info --json` describes a recording the same way, before any cook.

| Verb | Kind | Purpose |
|---|---|---|
| `doctor` | det. | Roots, disk by class, SDK vs dataset versions, craig-stt presence, key presence, qmd collection state |
| `lexicon` | det. | List the loaded lexicon terms; `--expand` also prints every generated case form and skip reason. Read-only |
| `plan` | det. (`--rank` metered) | Build the biasing files from `_lexicon.yaml`; emit the exact `craig-stt transcribe … --json` command. The biasing flags reach that command only under `--with-biasing` |
| `adopt <dataset\|id>` | det. | Bind a finished dataset to a date-based session id; verify date, completeness, that no track was lost |
| `qa` | det. | Eight quality metrics and which configured thresholds they cross; `--compare N` for a run A/B |
| `render` | det. | Write `vault/transcripts/<id>/` chunks + overview, `anchors.json`, the grep index |
| `grep` | det. | Exact slicing over the rendered transcript: `--speaker --from --to --regex --context` |
| `segment` | metered | Per-turn `in_character\|table_talk\|mechanics\|ambiguous` |
| `extract` | metered | Map-reduce over the turn stream; evidence + confidence + `world_impact` per event |
| `recap` | metered | Russian recap draft; every bullet ends in an evidence link |
| `record` | **det.** | Assemble and validate `record.json`; works without an API key |
| `glossary` | metered | Attach this session's observed variants to canonical terms in `_lexicon.yaml` |
| `prune` | det. | SDK `prune()` of the regenerable ~2 GB; never `rm` |

Full flag surface, JSON envelope, and metric definitions:
`.agents/cli/session-ingest/README.md`.

**Names that come out in the wrong case.** Russian declines, so a lexicon entry
for «Марвика» leaves «Марвике» and «Марвикой» uncorrected. Adding
`morph: true` to that term generates its case forms mechanically (pymorphy3) and
pairs each misheard case with the *same* case of the correct name, for `render`
substitution and `qa` counting — never for biasing. Explicit hand-written oblique
entries always win. Audit the whole generated table before trusting it:
`.agents/bin/session-ingest lexicon --expand`.

### Biasing: written always, passed only on request

`plan` always writes `inputs/hotwords.txt` and `inputs/initial_prompt.txt` — they
cost nothing and are useful to read. It does **not** put
`--hotwords-file` / `--initial-prompt-file` into the emitted transcribe command
unless you pass `--with-biasing`.

**The default and measured-effective path is the deterministic lexicon
substitution in `render`**, which replaces strings enumerated before the
transcript was read. Whisper hotword / `initial_prompt` biasing is
**EXPERIMENTAL** and measured locally, on a real session, as *harmful*: instead of
correcting names the model recited the term list back (prompt echo). Whole turns
came out as the biasing list, `compression_outlier_count` went 6 → 85,
lexicon-term hits grew by 412 while the transcript grew by 130 words, and
`word_p10` fell — all while `lexicon_miss_rate` **improved**, because every
recitation counts as a canonical hit. That run was rejected.

So: **a falling `lexicon_miss_rate` is not proof a biased run is better.** If a
biased run is tried at all, the echo signals in `qa --run N --compare M` are the
tripwire:

| Signal | Raised when |
|---|---|
| `term_hits_outran_words` | lexicon-term hits grew more than 3× faster than the word count (`TTRPG_SESSION_QA_ECHO_TERM_FACTOR`) |
| `compression_outliers_jumped` | whisper's repetition-loop outliers grew more than 10× or by 50+ |

Either one beside a falling miss rate means read the outlier segments before
promoting anything. They are facts, not a verdict — `qa` still renders none.

### Metered stages

`segment`, `extract`, `recap` and `glossary` cost money. Without
`OPENAI_API_KEY` they exit **0** with `{"status": "skipped"}` and are **omitted
from `next_steps`** — that omission is the instruction, not an oversight. Never
substitute a hand-rolled LLM pass for a stage the CLI skipped.

`record` is deterministic and always emitted: `world_impact` and `needs_owner`
come out of `extract`, so a keyless machine still produces a valid
`record.json`, minus the LLM-derived fields.

A verb that exits **2** with `{"status": "not_implemented"}` is not built yet.
Report that and stop; do not work around it by reading the dataset yourself.

## The quality loop

`qa` reports facts and which thresholds were crossed — never a verdict. When one
is crossed, `next_steps` switches to the re-run chain:

1. **`glossary`** — the LLM attaches this session's observed misrecognitions to
   canonical terms and merges them append-only into `vault/transcripts/_lexicon.yaml`.
   Without a key, add the variants by hand instead; the file is one of the two
   hand-maintained files in the system.
2. **`plan --run 2`** — re-emits the biasing files from the grown lexicon plus
   the exact `craig-stt` command for the retry. The grown lexicon is what makes
   the retry worth running: it feeds `render`'s substitution, whether or not the
   transcribe command carries the biasing files.
3. **Re-transcribe** with the command `plan` emitted verbatim. It carries
   `--work-dir <scratch>` **before** the subcommand and points at the **local
   source archive already on disk** — no re-download, and the first run's dataset
   is untouched.
4. **`qa --run 2 --compare 1`** — run-level aggregates, per-term
   canonical-vs-variant counts (`Вазгар: run1 6 canonical/35 variant → run2 …`),
   and the echo signals. Read the signals before reading the miss rate.
   Segment-by-segment comparison across runs is refused by construction: a
   re-transcription renumbers every segment, so segment N in two runs is not the
   same utterance.
5. **`adopt --promote`** — makes run 2 active and invalidates downstream
   artifacts by digest. It **refuses when the session note already exists**,
   because renumbered turn IDs would silently break evidence links already
   approved in the vault. `--force-relink` overrides; only use it when the user
   accepts re-checking those links.

**Retry economics: say them out loud when recommending one.** A ~3 h session
costs roughly ten GPU minutes and zero API spend to re-transcribe. So when a
threshold is crossed **and** the lexicon grew since that run, one retry is
essentially always worth it. Conversely, a retry with an unchanged lexicon
changes almost nothing — don't burn the GPU on it.

## Hard rules

These are the ways this pipeline gets damaged. None is stylistic.

- **Never read `.cache/sessions/datasets/**` files directly.** `segments.jsonl`,
  `dataset.json`, `speech.jsonl` and the rest are reached only through the
  `craig-stt-dataset` SDK, which the CLI already does for you. The agent never
  opens `segments.jsonl` — not "just this once" to check something.
- **Never `cat` or Read a rendered transcript chunk.** They are tens of
  thousands of words of table speech. Use `.agents/bin/session-ingest grep` for
  a known time window or speaker, or `.agents/bin/qmd get` for a search hit.
- **Never hand-edit `vault/transcripts/`.** Rendered chunks are machine-owned
  and regenerable — fix the lexicon and re-`render` instead. The two exceptions
  are `_speakers.yaml` and `_lexicon.yaml`, which are hand-maintained user data
  and the only non-regenerable files in the whole system.
- **The CLI never writes `vault/notes/`.** The agent is the only writer of
  campaign notes, via `ttrpg-vault-authoring`, from `recap.draft.md` +
  `record.json`.
- **A transcript quote is evidence of what was SAID, not of what is TRUE.**
  Players misremember, joke, and speak out of character; `segment` marks table
  talk but does not adjudicate truth. Present quotes as speech with a timestamp.
- **`world_impact != "none"` or `needs_owner: true` goes to the owner** before
  any durable canon change. Same for anything that contradicts an existing note.
  Quiet events may be drafted; world-breaking ones are asked about.

## Fact-checking what was said

| The question gives you | Do this |
|---|---|
| A time window, a speaker, or an exact string | `.agents/bin/session-ingest grep --session <id> --speaker <name> --from 1:12:00 --to 1:20:00 --context 2` |
| A topic, no timestamps | `.agents/bin/qmd search "<terms>" -c transcripts`, then `.agents/bin/qmd get <doc-id>` |
| A fuzzy/semantic question | `.agents/bin/qmd query "<question>" -c transcripts`, then `.agents/bin/qmd get` |

The `transcripts` collection is **excluded from default search** — prep and lore
retrieval must never surface table speech. Ask for it explicitly with `-c
transcripts`.

Always `qmd get` before quoting, and quote with `hh:mm:ss` and the speaker:

> [01:14:32] **Морвика**: «…»

Never paste a whole chunk into the response. If the transcript and a campaign
note disagree, that is a contradiction finding for the owner — surface both,
don't silently pick one.

## Configuration

`.agents/env.sh` owns every path (`TTRPG_SESSIONS_DIR`,
`TTRPG_SESSION_DATASETS_DIR`, `TTRPG_TRANSCRIPTS_DIR`, `CRAIG_STT_WORK_DIR`,
`CRAIG_STT_CACHE_DIR`) — those decide where gigabytes land and cannot be
overridden from `.env`.

| Where | What |
|---|---|
| `.env` | `CRAIG_STT_CRAIG__BASE_URL`, `..__IGNORE_BOTS`, `..__IGNORE_USERS` (JSON list — keep it quoted), TLS/CA settings; `CRAIG_STT_STT__MODEL/LANGUAGE/TASK`; `TTRPG_SESSION_*` window/gap/budget/model/concurrency knobs, the three QA thresholds, and `TTRPG_SESSION_QA_ECHO_TERM_FACTOR` (the `qa --compare` echo signal, default `3.0`) |
| The share URL | `?key=…` — the recording key, passed once, per recording |
| `.agents/state/craig-stt.toml` | Optional. Genuinely nested craig-stt config (e.g. `audio.filters`). The launcher injects `--config` when it exists, which also shadows any host `~/.config/craig-stt/config.toml` |
| `vault/transcripts/_speakers.yaml`, `_lexicon.yaml` | User data, not config: hand-editable, backed up, cleanup-protected, snapshotted into `inputs/` on every render |

**`CRAIG_STT_CRAIG__KEY` is deliberately not in `.env`.** The key is
per-recording and belongs in the share URL, so pass the whole URL. A persistent
key is stale for every new recording, turns a forgotten `?key=` into a confusing
403, and `.env` is injected into every agent shell command.

The `stt.*` section is `extra="forbid"` upstream: a misspelt `CRAIG_STT_STT__*`
key fails loudly instead of silently un-biasing a ten-minute GPU run. Don't
"fix" that startup error by deleting the key — fix the spelling.

## Storage and disk

```text
.cache/sessions/
├── datasets/<recording_id>/   craig-stt's work dir — SDK-only access
│   ├── source/                the Craig archive — IRREPLACEABLE (Craig expires recordings)
│   ├── pcm/  stt/             ~2 GB, regenerable at GPU cost, prunable via the SDK
│   └── dataset.json · segments.jsonl · meta.json
├── scratch/                   A/B re-transcription work dirs (disposable)
└── <session-id>/              session-ingest's derived artifacts:
                               session.json · provenance.json · inputs/ · runs/<N>/qa.json ·
                               anchors.json · extraction.json · record.json · recap.draft.md ·
                               index.sqlite

vault/transcripts/
├── _speakers.yaml             hand-maintained: discord user_id → player/character/role
├── _lexicon.yaml              hand-maintained: variant → canonical; biasing + correction dictionary
└── <session-id>/
    ├── __<session-id>.md      overview: participants, stats, chunk TOC, QA summary
    └── NN-mmm-mmm.md          chunks: [hh:mm:ss] **Speaker**: text ^t<turn-id>
```

`anchors.json` is the ID bridge that turns `record.json` evidence into clickable
`[[transcripts/<id>/NN-…#^t…]]` wikilinks. Deleting it breaks every evidence
link already cited in the vault until `render` runs again.

About ~3.1 GB per unpruned session, ~0.5 GB pruned. **Offer `prune` only after
the session note is written and approved** — `.agents/bin/session-ingest prune
--dry-run` first, and it refuses while the note is missing. It drives the SDK's
`prune()`; never reclaim that space with `rm`, which is one glob away from the
irreplaceable `source/`.

> **Deleting inside WSL does not give the disk back.** The ext4 VHDX grows and
> never shrinks on its own, so `du` drops by 2 GB and Windows reports no change.
> Say this when reporting a large cleanup, so nobody deletes more looking for
> the space.

Destructive removal of any of this is `ttrpg-system-data-cleanup` — scopes
`transcripts-rendered`, `session-derived`, `session-audio-prune`, and the
nuclear `session-corpus:<id>`.

## Failure handling

- **`status: failed`** — the envelope carries `code`, `message`, `detail` and
  usually a repairing `next_steps`. Run the repair; don't improvise.
- **Missing `dataset.json`** — `next_steps` emits
  `.agents/bin/craig-stt manifest <dir>`, which re-describes the directory
  without re-transcribing.
- **Date mismatch** — `adopt` reports both values and refuses to choose. Ask the
  user which session id is right.
- **Skipped tracks** — read `skip_category`; there are three cases. `ignored` is
  a deliberate producer-side skip of a track craig-stt *did* look at — in
  practice one below the VAD speech floor — and is adopted with no flag at all.
  `failed` lost a speaker and refuses until `adopt --allow-skipped-tracks` — an
  operator decision, so ask before passing it; the flag hides nothing, since
  every skip lands in `session.json` and the envelope with its category and
  reason. **No category at all** is a refusal
  (`tracks_missing_skip_category`) that the flag does *not* lift: an older
  craig-stt wrote that dataset without recording why the track went, so re-run
  the `craig-stt transcribe` step `next_steps` emits — it reads the local archive,
  never re-downloads — and adopt the new dataset. Say which kind you accepted
  when reporting.
- **Config-excluded bots and users never show up here.** The current craig-stt
  drops them before counting: no row in `meta.tracks`, not in `counts.tracks`,
  and `skipped_tracks` comes back empty. An empty skipped list therefore does not
  mean nothing was left out — check the run's `IGNORE_BOTS`/`IGNORE_USERS`
  configuration and `provenance.ignored_tracks` instead, and never tell the user
  a bot "was skipped as ignored" on the strength of the dataset alone.
- **Unmapped speakers** — `qa` lists the `user_id`s. Fill them into
  `_speakers.yaml`; `render` marks unmapped speakers rather than guessing.
- **403 from Craig** — the share URL's `?key=` is missing or expired. Ask for a
  fresh URL; do not add a key to `.env`.

## Reference

Full CLI contract, flags, JSON envelope, metric definitions, skip-if-done
semantics, and current implementation status:
`.agents/cli/session-ingest/README.md`.
