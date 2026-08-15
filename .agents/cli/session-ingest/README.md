# session-ingest

Turn a craig-stt recording dataset into a searchable transcript under
`vault/transcripts/`, a recap draft, and one structured `record.json` — the only
artifact the campaign tracker in `vault/notes/` ever reads.

This CLI **never writes into `vault/notes/`** and **never spawns craig-stt**.
Transcription is a ten-minute GPU job with progress output; `plan` emits the
exact command in `next_steps` and the agent runs it.

## Quick usage

Run everything through the hermetic entrypoint, from the repository root. It
sources the environment contract, so the three session roots and
`CRAIG_STT_WORK_DIR` resolve correctly with zero harness configuration. A bare
`uv run` from the repo root fails, and from this directory the environment is
unsourced — `session-ingest` then refuses rather than inventing paths under the
cwd.

```bash
.agents/harness bootstrap --apply      # first time only: creates this tool's venv

# Environment facts: SDK vs producer versions, roots, disk by class, key presence.
.agents/bin/session-ingest doctor --json

# Bind a finished dataset to a session id. Accepts a path or a recording id.
.agents/bin/session-ingest adopt xK9mQrTnZp2

# Transcription quality facts, and which configured thresholds they cross.
.agents/bin/session-ingest qa --json

# Measure a re-transcription against the first run.
.agents/bin/session-ingest qa --run 2 --compare 1
```

**Redirect a transcription with the global `--work-dir` flag, never with an
environment prefix.** `.agents/env.sh` exports `CRAIG_STT_WORK_DIR` (deliberately
— it is what keeps the corpus inside the project) and `.agents/bin/craig-stt`
sources that contract *inside* the launcher, after the caller's environment is
already set. So the obvious form fails, and fails silently:

```bash
# WRONG — the prefix is clobbered inside the launcher and the run lands in the
# live dataset root, on top of the run you meant to compare it against.
CRAIG_STT_WORK_DIR=.cache/sessions/scratch/2026-08-08-r2 \
  .agents/bin/craig-stt transcribe recording.flac.zip --json

# RIGHT — `--work-dir` is a GLOBAL option, so it goes BEFORE the subcommand.
.agents/bin/craig-stt --work-dir .cache/sessions/scratch/2026-08-08-r2 \
  transcribe recording.flac.zip --json
```

`session-ingest doctor` reports the resolved work dir with this note, and warns
when the environment it is running in carries a `CRAIG_STT_WORK_DIR` that differs
from `TTRPG_SESSION_DATASETS_DIR`.

## The chain

```text
plan → craig-stt transcribe --json (GPU ~10 min) → adopt → qa
  ├─ thresholds ok → render → qmd update && qmd embed → segment → extract → ┬─ recap
  │                                                                         └─ record
  └─ thresholds crossed → glossary → plan --run 2 → craig-stt (scratch dir, LOCAL zip,
       no re-download) → adopt --run 2 → qa --run 2 --compare 1 → adopt --promote → render …
then: view → the AGENT drafts the chronicle (owner questions inside it) → in-chat
  owner review → chronicle --check → chronicle --freeze → prune
```

`next_steps` is the ordering authority. Run what it returns, in order; do not
invent steps it omitted.

The `craig-stt transcribe` step is emitted with `--json`, because craig-stt keeps
the same machine contract this CLI does: **one JSON object on stdout and nothing
else**, progress and logs on stderr. It describes the dataset that was written —
`recording_id`, `dataset_dir`, `status`, `segments_digest`, `schema_version`,
`counts`, one row per track (`transcribed`, `skipped`, `skip_category`,
`skip_reason`) and a `provenance_summary` (model, language, task, compute type,
wall time, RTF). A failure is `{"error": {"type", "message"}}` with exit 1. Parse
it: `recording_id` is what the following `adopt` takes, and `tracks[].skip_category`
is what decides whether that `adopt` needs a flag — or, when a skipped track has
no category at all, refuses outright. `--json` is refused together
with `--dry-run`, which writes no dataset to describe.

## CLI surface

All fifteen verbs are implemented. `det.` verbs need no API key and no network;
`metered` verbs call OpenAI and skip cleanly without a key.

| Verb | Kind | Purpose |
|---|---|---|
| `doctor` | det. | SDK version vs the versions that wrote the datasets on disk, roots, disk by class, craig-stt presence + resolved config, key presence, qmd collection state |
| `lexicon` | det. | List the loaded lexicon terms; `--expand` also prints every generated case form and every skip reason. Read-only |
| `plan` | det. (`--rank` metered) | Build `inputs/hotwords.txt` + `initial_prompt.txt` from `_lexicon.yaml`; emit the exact `craig-stt transcribe … --json` command (biasing flags only under `--with-biasing`) |
| `adopt <dataset\|id>` | det. | `open_dataset(verify=True)`; date vs `start_time`; capture complete; no track lost; missing manifest → emits the `craig-stt manifest` repair |
| `qa` | det. | Eight metrics plus which thresholds they cross; `--compare N` for a run-level A/B |
| `render` | det. | SDK `turns()`; lexicon substitution incl. generated case forms; speaker names; bleed/overlap marks; turn-boundary chunks; `anchors.json`; `index.sqlite` |
| `grep` | det. | Exact slicing over `index.sqlite` |
| `segment` | metered | Per-turn `in_character\|table_talk\|mechanics\|ambiguous` + confidence |
| `extract` | metered | Map-reduce over `turns(drop_bleed=True)` minus table talk; evidence + confidence + `world_impact` per event |
| `recap` | metered | Russian recap from `extraction.json` only; every bullet ends in an evidence link |
| `record` | **det.** | Assemble and schema-validate `record.json`. No second LLM pass — works without a key. Resolves entity names to slugs and vault notes through `state/entity-registry.md` |
| `view` | det. | **The read path into `record.json`**: compact Markdown, one line per element, one evidence link. `--needs-owner`, `--scene`, `--kind`, `--section`, `--links`, `--min-confidence` |
| `chronicle` | det. | `--check` the agent-authored session note: every `[[transcripts/…#^t…]]` citation resolved against `anchors.json`, frontmatter completeness, no open `## Вопросы владельцу` on a canon note, freeze-digest integrity. `--freeze` records the canon note's content digest (the append-allowed `## Реконсиляция` excluded) in `.cache/sessions/<id>/chronicle.freeze.json` — the only thing this verb ever writes, and never `vault/notes/`. `--status` sweeps the whole `vault/notes/sessions/` ledger: per-note status, open questions, drift — the mechanical "am I caught up?" |
| `glossary` | metered | Attach observed variants to canonical terms; append-only `_lexicon.yaml` merge. Five guardrails gate every proposal — see [Glossary guardrails](#glossary-guardrails) |
| `prune` | det. | SDK `prune()`; never `rm`; refuses while the chronicle is unwritten |

### Options

`--json` and `--session` are accepted by every verb (`doctor`, `lexicon` and
`grep --all` excepted for `--session`). `--json` works on either side of the verb:
`session-ingest --json qa` and `session-ingest qa --json` are the same.

| Verb | Options |
|---|---|
| `doctor` | `--json` |
| `lexicon` | `--expand` `--json` |
| `plan` | `--session` `--run N` `--entities-file PATH` `--rank` `--budget-chars N` `--with-biasing` `--force` `--json` |
| `adopt` | `<target>` `--session` `--run N` `--promote` `--allow-partial` `--allow-skipped-tracks` `--force-relink` `--force` `--json` |
| `qa` | `--session` `--run N` `--compare N` `--force` `--json` |
| `render` | `--session` `--run N` `--window-minutes N` `--merge-gap-s F` `--force` `--json` |
| `grep` | `--session` `--all` `--speaker` `--from` `--to` `--regex` `--context N` `--json` |
| `segment` | `--session` `--run N` `--force` `--json` |
| `extract` | `--session` `--run N` `--keep-bleed` `--force` `--json` |
| `recap` | `--session` `--audience dm\|players` `--force` `--json` |
| `record` | `--session` `--run N` `--force` `--json` |
| `view` | `--session` `--section NAME` (repeatable) `--scene ID` `--kind NAME` `--needs-owner` `--min-confidence F` `--links N` `--no-header` `--json` |
| `chronicle` | `--session` `--check` `--freeze` `--status` `--json` (`--status` needs no `--session`) |
| `glossary` | `--session` `--run N` `--budget-chars N` `--force` `--json` |
| `prune` | `--session` `--dry-run` `--force` `--json` |

**Session id resolution:** `--session` > `TTRPG_SESSION_ID` > the most recent
adopted session under `$TTRPG_SESSIONS_DIR`. With none of those, the verb fails
naming `adopt` as the fix. For `adopt` itself, omitting `--session` uses the date
derived from the recording's `start_time`.

## Agent stdout contract

### The stderr status line

Every verb prints one line to **stderr** before doing any work, with the
resolved source of each setting in brackets — `[cli]`, `[env]`, `[default]`, or
`[manifest]` for facts read out of the dataset manifest:

```text
session-ingest: session=2026-08-08[manifest] dataset=sha256:8f3ad7c91b02…[manifest] run=1[default]
session-ingest: session=2026-08-08[cli] window=15m[env] merge_gap=1.5s[default]
session-ingest: session=2026-08-08[cli] window=15m[env] overlap=10%[default] llm.model=gpt-5.6-luna[env] key=present
session-ingest: key=absent
```

Only the fields a verb actually resolved appear. `key=` shows up on metered
verbs and `doctor`; the value of `OPENAI_API_KEY` is never printed anywhere.

### The `--json` envelope

One object on stdout, always carrying `tool`, `verb`, `status`, and — for verbs
that have a successor — ordered `next_steps`:

```json
{
  "tool": "session-ingest",
  "verb": "adopt",
  "status": "ok",
  "session": "2025-01-01",
  "recording_id": "xK9mQrTnZp2",
  "dataset_digest": "sha256:8f3ad7c91b02…",
  "next_steps": [
    {"id": "qa", "required": true, "summary": "…", "command": ".agents/bin/session-ingest qa --session 2025-01-01"}
  ]
}
```

| `status` | Exit | Meaning |
|---|---:|---|
| `ok` | 0 | Work done. |
| `skipped` | 0 | Either the composite digest key already matched (`--force` to redo), or a metered stage found no API key. |
| `failed` | 1 | A gate refused. `code`, `message`, `detail` and often a repairing `next_steps` say what to do. |
| `not_implemented` | 2 | A registered verb with no body. No verb is in this state today; the envelope exists so a future one has to say so instead of pretending. |

A failure emits the same envelope shape, so an agent can branch on `status`
without parsing prose:

```json
{
  "tool": "session-ingest", "verb": "adopt", "status": "failed",
  "code": "missing_manifest",
  "message": "…/xK9mQrTnZp2 has no dataset.json, so it cannot be identified or verified.",
  "next_steps": [{"id": "describe_dataset", "command": ".agents/bin/craig-stt manifest …/xK9mQrTnZp2"}]
}
```

### Glossary guardrails

`render` substitutes lexicon variants **context-free**, so a wrong variant does
not merely fail to help — it rewrites text that was already correct. Being
observed in the transcript is therefore necessary but not sufficient. A proposal
survives only if it clears all five checks; each refusal is reported in
`variants_rejected` with a reason, and collisions name the other term.

| Check | Refuses | Real example |
|---|---|---|
| verbatim | a string the model invented | «Никогда не звучало» |
| cross-term canonical | a form that is *another* term's canonical/display | «Истрид» proposed for `istrid-loc` → would rewrite every correct nominative into «Истриде» |
| cross-term variant | a string another term already claims | «Истрит» belongs to `istrid`; one input cannot have two replacements |
| length floor | anything under `MIN_FORM_LENGTH` (4) | `ЛАС` → would hit «ласково», «класс» |
| enclosing form | a string that sits inside a longer known form | «Истр» clears the length floor yet turns «Истрид» into «Истридеид» |

The last two are complementary, not redundant: the floor guards against ordinary
words the lexicon never sees, the enclosing check against the lexicon's own
forms. A variant that merely *contains* the canonical is fine — «Освальд Стоун»
→ «Освальд» is a normalisation, not a collision.

A **new** term is written only when at least one variant survives: an entry that
corrects nothing is bloat in a hand-maintained file. Biasing-only terms (no
variants) stay the owner's to add by hand.

Unlike `morphs.py`, which raises `LexiconExpansionError` on an ambiguous pair,
these drop and report — the model proposes speculatively and one bad row must
not cost the good ones. The regression case is
`tests/test_glossary.py::test_the_2026_08_15_lexicon_corruption_is_refused`.

#### The direction gate

Structural checks cannot catch a **backwards** proposal — the model naming the
misrecognition as canonical and the correct spelling as its variant. Two of
those passed every check above on 2026-08-15: `Барл` recorded as canonical with
the correct `Баррелла` as its variant, and `дом Вейн` with `Вэйл`.

Direction is a judgement about the campaign, not about strings, so a **new**
term's canonical must already appear in `state/entity-registry.md` — the file
the owner curates during the chronicle review. Held-back terms land in
`unknown_canonicals`; add the entity and re-run. Existing lexicon terms are
exempt: their canonical was the owner's choice, so their direction is fixed.

With no registry file the gate opens and `run` warns (`entity_registry_present:
false`), because refusing everything would make `glossary` useless on a vault
that has not started a registry.

Measured on the real 2026-08-15 batch: 26 proposed new terms → **1 written**
(`Мардан`→`Марден`), 2 held for the owner, 23 refused as variant-less, and all
six damaging variants refused.

### Metered stages

`segment`, `extract`, `recap` and `glossary` cost money. Without
`OPENAI_API_KEY` they exit **0** with `{"status": "skipped"}` and are **omitted
from `next_steps`** — do not invent them. `record` is deterministic and always
emitted: `world_impact` and `needs_owner` come out of `extract`, so a keyless
machine still produces a valid `record.json`, minus the LLM-derived fields.

`record.json` is validated with **no waivers**. Where a number is genuinely
unknown without a metered stage — `play_time_s` and `table_talk_share` need
`segment`'s classification — it is emitted as `null` and named in
`session.missing`. That declaration is what makes the null valid; an *undeclared*
null still fails validation, so nothing can quietly ship a fabricated `0.0`.

## Biasing is opt-in, and experimental

`plan` always writes `inputs/hotwords.txt` and `inputs/initial_prompt.txt` — they
are free to produce and useful to read. The emitted `craig-stt transcribe`
command does **not** carry them unless you ask:

```bash
# Default: one ranked list written to both files, and a command without them.
.agents/bin/session-ingest plan --session 2026-08-08 --json
#   → .agents/bin/craig-stt transcribe '<share-url>' --json

# Opt in. EXPERIMENTAL — see the measurement below.
.agents/bin/session-ingest plan --session 2026-08-08 --with-biasing --json
#   → .agents/bin/craig-stt transcribe '<share-url>' \
#       --hotwords-file …/inputs/hotwords.txt \
#       --initial-prompt-file …/inputs/initial_prompt.txt --json
```

**The validated path is deterministic lexicon substitution in `render`**, which
replaces strings enumerated before the transcript was read. Whisper hotword /
`initial_prompt` biasing is the experimental one: measured locally on a real
session it did not correct names, it made the model recite the term list.

| Signal | Run 1 (no biasing) | Run 2 (biased) |
|---|---:|---:|
| `compression_outlier_count` | 6 | 85 |
| total lexicon-term hits | — | **+412** |
| total words | — | +130 |
| `word_p10` | — | **fell** |
| `lexicon_miss_rate` | — | fell |

Whole turns came back as the biasing list. The miss rate improved *because of*
the echo: every recited name counts as a canonical hit. So a falling
`lexicon_miss_rate` is not on its own evidence that a biased run is better — the
echo signals in `qa --compare` are the tripwire, and the run-2 dataset from that
experiment was rejected.

If you do try it, run the A/B (`qa --run 2 --compare 1`), read the signals, and
read the listed compression outliers before promoting anything.

## What `adopt` checks

Four gates, each of which reports rather than decides:

1. **Exactly one dataset for the id.** Searched across `$CRAIG_STT_WORK_DIR` and
   the scratch roots. Two candidates is an error listing both.
2. **The date matches.** Derived from `meta.recording.start_time` as recorded
   (no timezone conversion — the derived date must not depend on where the
   command runs). A `-b` style same-day suffix is accepted; a genuine mismatch
   prints both values and exits non-zero, never choosing.
3. **The capture is complete.** `--allow-partial` overrides, and the fact is
   recorded in `session.json`.
4. **No track was lost.** craig-stt counts a skipped track in `tracks` but not in
   `tracks_transcribed`, and `TrackStats.skip_category` (craig-stt-dataset 1.2.0)
   says which kind of skip it was:

   | `skip_category` | Meaning | Gate |
   |---|---|---|
   | `"ignored"` | The producer deliberately skipped a track it *did* look at — in practice, one holding less speech than the VAD floor | **adopted without a flag** — the decision was already made at transcription time |
   | `"failed"` | The track would not decode: this dataset lost a speaker | needs `--allow-skipped-tracks` |
   | absent | An older craig-stt wrote the dataset and recorded no category, so a deliberate omission and a lost speaker are the same bytes | **refused** (`tracks_missing_skip_category`) — re-transcribe with the current craig-stt |

   **Config-excluded bots and users are not in this table at all.** The current
   craig-stt drops them *before* counting: there is no row in `meta.tracks`, they
   are not in `counts.tracks`, and `skipped_tracks` comes back empty. So an
   excluded bot never reaches this gate and never appears in `qa`'s skipped list —
   the way to confirm one was excluded is the run's own configuration
   (`CRAIG_STT_CRAIG__IGNORE_BOTS` / `..__IGNORE_USERS`) and
   `provenance.ignored_tracks`, not an absence in the dataset. Do not read an
   empty `skipped_tracks` as "nothing was left out".

   The third row is not an override the flag can grant: the fact is missing from
   the dataset, not withheld by the operator, and asserting it would be a guess.
   A dataset is a regenerable artifact, so the repair is a re-transcription —
   `next_steps` emits `craig-stt transcribe <the dataset's own local .flac.zip>
   --json` (never a re-download; Craig expires recordings) and then the `adopt`
   of the new dataset.

   A shortfall with no matching row in `meta.tracks` — a track that vanished
   from the metadata entirely — gates with `--allow-skipped-tracks` too, since no
   category can describe it. The flag does not hide anything: every skip,
   deliberate or not, lands in `session.json` and in the JSON envelope with its
   category, its reason, and whether `provenance.ignored_tracks` names it.

A missing `dataset.json` is not a failure to work around: `next_steps` emits
`.agents/bin/craig-stt manifest <dir>`, which re-describes the directory without
re-transcribing anything.

**`--promote`** repoints the active run and **refuses when a chronicle note
matching `vault/notes/sessions/*<session-id>*.md` exists** — a re-transcription
renumbers every segment and shifts every turn ID, so promoting would silently
break approved evidence links. Plain `adopt` refuses for the same reason when it
would **replace the active run's dataset** under an existing chronicle (one
session = one transcription adoption); adopting an additional `--run N` for
comparison stays free. `--force-relink` overrides either refusal and warns.

## What `qa` measures

Facts, never verdicts (DESIGN §5). Written to `runs/<N>/qa.json`, copied to
`qa.json` for the active run.

| Metric | Definition |
|---|---|
| `word_p10` | Nearest-rank 10th percentile of `WordSpan.p` across all segments (sorted ascending, rank `ceil(0.10·n)`) |
| `low_logprob_share` | Share of segments with `avg_logprob < -1.0`, over segments that have one |
| `compression_outliers` | Segments with `compression_ratio > 2.4`, listed with `t0` — repetition loops |
| `bleed_rate` | Bleed-suspect segments ÷ segments |
| `overlap_rate` | Segments with a non-empty `overlap` ÷ segments |
| `lexicon_miss_rate` | Variant hits ÷ (canonical + variant) hits, over `_lexicon.yaml` terms |
| `unmapped_speakers` | Speaking `user_id`s with no `_speakers.yaml` entry (skipped tracks excluded) |
| `tracks_missing` | `counts.tracks − counts.tracks_transcribed` |

Plus per-track speech time and share, and the list of skipped tracks.

`lexicon_miss_rate` is **exact substring counting over enumerated strings**,
case-folded: the canonical and `display_ru` forms on one side, the listed
`variants` on the other, plus — for a `morph: true` term — the generated case
forms on the matching side. There is no similarity, distance or fuzzy matching —
a term counts as missed because that spelling was enumerated as a
misrecognition, never because two words look alike. Terms with zero total hits
are excluded so an unused entry cannot move the rate.

`--compare N` produces **run-level aggregates and per-term canonical-vs-variant
counts only**. Segment-level comparison across runs is refused by construction
and the report says why: a re-transcription renumbers every segment index, so
segment N in one run and segment N in another are not the same utterance.

### Echo signals in `--compare`

The comparison also carries an `echo` block and a `signals` list, because the
obvious cross-run reading is wrong in one specific, measured way: a run that
recites the biasing term list *lowers* `lexicon_miss_rate` while making the
transcript worse. Three counts separate that from a real correction — a real one
moves variant hits into canonical hits without inflating the total.

| Fact | Where it comes from |
|---|---|
| `lexicon_term_hits` | sum of `total_hits` over `metrics.lexicon_terms`, per run + delta |
| `words` | `metrics.words_with_probability`, per run + delta |
| `compression_outlier_count` | segments above whisper's 2.4 repetition ratio, per run + delta |

| Signal | Raised when |
|---|---|
| `term_hits_outran_words` | term-hit growth > `max(0, word growth) × TTRPG_SESSION_QA_ECHO_TERM_FACTOR` (default `3.0`); a shrinking transcript clamps the allowance to zero |
| `compression_outliers_jumped` | the outlier count grew more than 10× **or** by 50+ absolute |

These are facts, not verdicts — `qa` still renders none. The block ships one
plain sentence per raised signal plus the reading: a falling `lexicon_miss_rate`
accompanied by these signals suggests prompt echo rather than improvement.

```text
  Echo check:
    lexicon_term_hits: run_1=100 run_2=512 delta=412
    words: run_1=30000 run_2=30130 delta=130
    compression_outlier_count: run_1=6 run_2=85 delta=79
    signals:
      term_hits_outran_words: Lexicon-term hits grew far faster than the transcript did, …
      compression_outliers_jumped: Segments above whisper's own repetition-loop ratio (2.4) …
```

Thresholds come from config; crossing one switches `next_steps` to the re-run
chain. Retry economics justify it almost always — roughly nine minutes of GPU
against zero API cost.

## Environment

Set by `.agents/env.sh`; the CLI fails with a clear message rather than guessing
if they are absent.

| Variable | Purpose |
|---|---|
| `TTRPG_SESSIONS_DIR` | `.cache/sessions` — session-derived artifacts |
| `TTRPG_SESSION_DATASETS_DIR` | `.cache/sessions/datasets`, also `CRAIG_STT_WORK_DIR` |
| `TTRPG_TRANSCRIPTS_DIR` | `vault/transcripts` — rendered Markdown and the two hand-maintained YAML files |
| `TTRPG_NOTES_DIR` | `vault/notes` — layer 2. The CLI only ever **reads** here: `state/entity-registry.md` for name resolution, `sessions/` for the chronicle gates. It has no write path into this tree (`chronicle --freeze` writes its digest record under `.cache/sessions/<id>/`, not here) |

Tunables, resolved CLI > env > default (`.env` is a fallback only; the process
environment always wins):

| Variable | Default | Used by |
|---|---|---|
| `TTRPG_SESSION_WINDOW_MINUTES` | `15` | `render`, `segment`, `extract` |
| `TTRPG_SESSION_WINDOW_OVERLAP_PCT` | `10` | `segment`, `extract` |
| `TTRPG_SESSION_MERGE_GAP_S` | `1.5` | `render`, `record` |
| `TTRPG_SESSION_GLOSSARY_BUDGET_CHARS` | `450` | `plan`, `glossary` |
| `TTRPG_SESSION_OPENAI_MODEL` | `gpt-5.6-luna` | every metered verb |
| `TTRPG_SESSION_OPENAI_MAX_CONCURRENCY` | `4` | every metered verb |
| `TTRPG_SESSION_QA_MIN_WORD_P10` | `0.60` | `qa` |
| `TTRPG_SESSION_QA_MAX_BLEED_RATE` | `0.10` | `qa` |
| `TTRPG_SESSION_QA_MAX_LEXICON_MISS_RATE` | `0.35` | `qa` |
| `TTRPG_SESSION_QA_ECHO_TERM_FACTOR` | `3.0` | `qa --compare` (echo signals; not a threshold and in no cache key) |
| `OPENAI_API_KEY` | — | metered verbs; absent ⇒ skip, exit 0 |
| `TTRPG_SESSION_ID` | — | default session id when `--session` is omitted |

An unparseable value is discarded and reported as `[default]`, never as `[env]`.

## Storage layout

```text
.cache/sessions/
├── datasets/<recording_id>/   CRAIG_STT_WORK_DIR — craig-stt owns this, read-only to us
├── scratch/                   A/B re-transcription work dirs (disposable)
└── <session-id>/
    ├── session.json           date ↔ recording_id(s), active run, dataset digest
    ├── provenance.json        per-stage composite digest keys
    ├── inputs/                snapshots: speakers.yaml, lexicon.yaml, hotwords.txt, initial_prompt.txt
    ├── runs/<N>/qa.json       one per transcription attempt; qa.json mirrors the active run
    ├── turns.class.jsonl      per-turn IC/OOC classification
    ├── anchors.json           {segment_i → turn_id, chunk, t0} — the ID bridge
    ├── extraction.json        map-reduce output
    ├── record.json            THE layer-2 handoff
    ├── chronicle.freeze.json  freeze record: per-note digests written by `chronicle --freeze`
    ├── recap.draft.md
    └── index.sqlite           FTS for `grep` (disposable)

vault/transcripts/
├── _speakers.yaml             hand-maintained: discord user_id → player/character/role
├── _lexicon.yaml              variant → canonical; the biasing glossary AND the GEC dictionary
└── <session-id>/              rendered chunks + overview

vault/notes/                   layer 2 — READ-ONLY to this CLI, written by the agent
├── sessions/sNNN-<id>-*.md    the chronicle. `prune` and `adopt --promote` glob for the id;
│                              a draft carries its owner questions until answered in chat
├── state/entity-registry.md   canonical-name table; `record` resolves slugs and vault notes here
└── npcs/ locations/ factions/
```

`_speakers.yaml` and `_lexicon.yaml` are the only two non-regenerable files in
the whole system. They are user data, cleanup-protected, and snapshotted into
`inputs/` on every render.

```yaml
# _lexicon.yaml — `id` and `canonical` are required; everything else is optional
terms:
  - id: vagzar
    canonical: Вазгар
    display_ru: Вазгар
    variants: [Вагзар, Вазагар]
    kind: npc
    active: true
    priority: 10
    morph: false          # opt in to generated case forms; see below
    source: session/2025-01-01

# _speakers.yaml — keys are discord user ids
speakers:
  "224536012345678901":
    player: Alice
    character: Морвика
    role: pc
```

## `morph: true` — generated case forms

Russian declines. A lexicon that lists «Марвика» corrects the nominative and
leaves «Марвике», «Марвикой» and «Марвики» wrong in the transcript, so the
owner ends up hand-writing one entry per case. Marking a term `morph: true`
generates them instead:

```yaml
  - id: morvika
    canonical: Morvika
    display_ru: Морвика
    variants: [Марвика]
    morph: true           # → Марвике→Морвике, Марвикой→Морвикой, …
```

**Both sides are generated.** For each of the six singular cases the variant is
inflected to that case and `display_ru` is inflected to the *same* case, and the
two are paired by that tag. So the correction keeps the grammar of the sentence —
«Марвикой» becomes «Морвикой», not «Морвика» — and no text is ever parsed at
match time: `render` still only replaces strings that were enumerated before it
started reading the transcript.

**Guardrails**, all enforced in code:

| Rule | Effect |
|---|---|
| Explicit precedence | A form any lexicon entry already lists literally is excluded from the generated table. Hand-written oblique entries (`kor-vazgar-gen`, `morvika-acc`) always win |
| Ambiguity is refused | One generated form claimed by two terms is a hard error naming both. Two identical pairs are deduplicated silently |
| Minimum length | Generated forms shorter than 4 characters are dropped — they collide with ordinary words |
| No duplicates | Forms equal to the lemma, or differing from the correct form only in casing, are dropped |
| Opt-in only | A term without the flag is never expanded; an inactive term is not expanded at all |
| No guessing | A multi-word name is inflected per word only when every word is a declinable nominative singular that agrees with the others; «Освальд Стоун» (animate + inanimate) is **skipped with a reason** rather than half-inflected. Latin strings do not parse and are skipped too |

**Where it applies:** `render` substitution and `qa`'s `lexicon_miss_rate`
counting. Deliberately **not** `plan` — the ~450-character biasing budget buys
acoustic hints, and six endings of one name would evict five other names for no
gain, so hotwords and the initial prompt still carry lemmas only.

**Audit path.** Nothing is hidden:

```bash
.agents/bin/session-ingest lexicon --expand          # every pair, every skip reason
.agents/bin/session-ingest lexicon --expand --json   # the same, machine-readable
```

`render` also writes the whole table into `provenance.json` under
`stages.render.extra.morph_expansion`, and reports substitution counts split
`explicit` vs `generated`, so any correction in a rendered chunk traces back
either to a line the owner wrote or to a listed generated pair.

**Why this is allowed under the project's no-heuristics rule.** The rule forbids
a tool from inferring meaning — classifying, tagging, or guessing which entity a
string refers to — and every semantic decision here is still the owner's: which
strings are the same entity, what the correct spelling is, and which names
decline. What the tool adds is a lookup in a published grammatical dictionary
(OpenCorpora, shipped as package data with `pymorphy3-dicts-ru`), a deterministic
function of surface string and dictionary version with no similarity, ranking or
confidence anywhere in it — and where the grammar is genuinely ambiguous it skips
and records why instead of choosing.

Determinism: same lexicon + same `pymorphy3`/`pymorphy3-dicts-ru` versions → the
same table. Both versions feed the expansion digest, which is part of the
`render` and `qa` composite keys, so upgrading the dictionary re-renders and
re-measures rather than trusting output produced under different grammar.

## Skip-if-done

Every stage caches on a **composite digest key**, never on a file existing:

```json
{"dataset_digest": "sha256:…", "lexicon_digest": "sha256:…", "speakers_digest": "sha256:…",
 "prompt_version": "extract/1", "model": "…",
 "knobs": {"window_minutes": 15, "morph_digest": "sha256:…"}}
```

Change any component — grow the lexicon, edit a prompt, switch models, move a
knob — and the fingerprint moves, so the stage re-runs. `--force` re-runs
regardless. File presence is consulted in one direction only: a recorded output
that has since been deleted denies the skip, because no digest can tell you a
file is gone.

`adopt --promote` additionally drops every downstream stage record, since the
active dataset digest just changed and everything built on it is stale.

## Dataset access

Reads go through `craig-stt-dataset` and nothing else. `json.loads(open(
"segments.jsonl"))` in this codebase is the violation the producer contract
exists to prevent — it is what makes the format unchangeable. `tests/
test_sdk_contract.py` names every SDK field this CLI depends on and asserts it
against a synthetic dataset built with the SDK's own models, so a pin bump that
breaks us fails in the test run.

The pin is a git SHA shared with `.agents/cli/craig-stt`. When upstream tags
`dataset-v1.0.0`, swap `rev` → `tag` in **both** pyprojects in one change set,
relock both, and re-run that test.

## Module map

| Module | Owns |
|---|---|
| `__main__.py` | The click surface, the stderr status line, the `--json` envelope, and the one place an exception becomes an exit code |
| `config.py` `paths.py` | Tunable resolution with provenance (`[cli]`/`[env]`/`[default]`), and the roots + `SessionTree` every verb writes through |
| `errors.py` | The whole failure taxonomy, including `LlmError` / `SchemaViolation` / `PromptMissing` (re-exported from `llm` for call-site readability) |
| `models.py` | `session.json`, `qa.json` and `record.json` shapes, plus `validate_record` |
| `provenance.py` `writer.py` | Composite digest keys / skip-if-done, and atomic writes |
| `vaultfiles.py` | `_lexicon.yaml` and `_speakers.yaml` — the two non-regenerable files |
| `registry.py` | `state/entity-registry.md`: the single fenced yaml block, exact-match resolution, the ordered cross-product of declared name forms |
| `morphs.py` | `morph: true` case-form generation: paradigms, the case-paired table, the guardrails, the versioned digest |
| `llm.py` | The metered plumbing: keyless skip, versioned prompts, schema-first calls, bounded map-reduce |
| `doctor.py` `lexicon.py` `plan.py` `adopt.py` `qa.py` `render.py` `grep.py` `view.py` `chronicle.py` `prune.py` | The deterministic verbs |
| `segment.py` `extract.py` `recap.py` `glossary.py` | The metered verbs |
| `record.py` | Deterministic assembly of `record.json` + the `anchors.json` ID bridge |
| `nextsteps.py` | The ordered `next_steps` every verb hands the agent |

## Quality gates

```bash
cd .agents/cli/session-ingest
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest
```
