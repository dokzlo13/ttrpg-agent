---
name: ttrpg-vault-navigation
description: |
  Core workspace map for reading and writing in this TTRPG project: active
  notes, ingested book artifacts, imports, session transcripts, qmd collections,
  and source/data boundaries. Use before any task that reads from or writes to
  vault/, imports/, vault/library/books/, vault/transcripts/, or qmd-backed
  collections. This is navigation only;
  compose with ttrpg-vault-authoring for durable note placement/writes.
---

# ttrpg-vault-navigation

Use this first when a task touches local campaign/reference data. It answers
"where is the thing and what am I allowed to do with it?" It does **not**
replace `ttrpg-vault-authoring` for deciding where new durable notes belong.

## Data boundaries

| Path | Read? | Write? | Notes |
|---|---:|---:|---|
| `vault/notes/` | yes | yes | Active authored campaign notes, prep, canvases, images. Use `ttrpg-vault-authoring` before durable writes. |
| `vault/notes/sessions/` | yes | via `ttrpg-session-chronicle` | Append-only chronicle of **played** sessions, `sNNN-YYYY-MM-DD-<slug>.md`. Frozen once `status: canon` — corrections are retcon events, not edits. `prune` and `adopt --promote` both glob this directory for the session id. |
| `vault/notes/state/` | yes | agent-regenerated | Derived projections: `current-state.md`, `story-state.md`, `clocks.md`, `calendar.md`, `entity-registry.md`. `story-state.md` is agent-owned outright; hand edits there are overwritten. |
| `vault/notes/inbox/` | yes | via `ttrpg-session-chronicle` | Per-session proposals awaiting the owner. Empty means caught up. |
| `vault/notes/npcs\|locations\|factions/` | yes | yes | Entities promoted out of the roster; every fact bullet cites a session block. |
| `vault/library/books/` | yes | only via `book-ingest` | Ingested book/reference artifacts. Don't hand-edit chapters. |
| `vault/transcripts/` | via qmd/grep | only via `session-ingest render` | Rendered session transcripts. Machine-generated and regenerable; never `cat`/Read a chunk. The two `_*.yaml` files are the hand-maintained exception. |
| `.cache/sessions/` | no | tooling only | Transcription corpus: `datasets/` is craig-stt's (SDK-only, holds the irreplaceable source recording), `<session-id>/` holds session-ingest's derived artifacts. Never open these files directly. |
| `imports/books/` | list/read input paths | no | Raw user PDFs/EPUBs. Ingest via `ttrpg-import-book-pdf`. |
| `imports/source-vault/` | yes | **no** | Legacy archive; promote via `ttrpg-import-archive-vault`. |
| `imports/fvtt-data/` | yes | yes | Local staging for targeted Foundry VTT exports; preserve raw JSON/TXT/ZIP unless the user asks to clean it. |
| `.cache/vendor/5etools/` | yes | no | Canonical 5e data mirror. Prefer `query_5etools` for creatures/spells/items. |
| `.cache/` | no | tooling only | Single cache root. Rebuildable: `xdg/qmd/index.sqlite*`, `index/index.yml`. Must survive cleanup: `xdg/qmd/models/` and `xdg/datalab/` (~5.5 GB), `huggingface/`, `torch/`, `uv/`, `npm/`, `vendor/`. |
| `.agents/` | yes | it *is* the project | Canonical toolchain: `env.sh`, `bin/`, `cli/`, `skills/`, `prompts/`. `.agents/state/` is durable gitignored state and never a cleanup target. |

Never edit `imports/source-vault/`, raw books, 5etools data, or hand-edit
`vault/library/books/` chapter output or `vault/transcripts/<session-id>/`
rendered chunks.

## Ingested book artifacts

Ingested books live in one directory per slug, with the overview sorted first:

```text
vault/library/books/
└── <slug>/
    ├── __<slug>.md    # book overview / TOC; qmd-indexable and visible first
    ├── 01-…md         # chapter chunks; .agents/bin/qmd search/get usually returns these
    ├── …
    ├── images/
    └── .ingest/
        ├── provenance.json   # source hash, quality_status, system, llm
        └── report.json
```

Read order when retrieval looks wrong:

1. **Chapter file** — cite as `vault/library/books/<slug>/<NN-slug>.md:<line>`.
   Generated chapter notes are `# Title`, chapter text, then a final `---` nav
   footer with full-vault wikilinks. Chapter frontmatter may include `summary`
   and Obsidian-native `tags: [npc, random-table, …]`.
2. **Overview** — `vault/library/books/<slug>/__<slug>.md`; TOC with page ranges and,
   when available, summary/tag suffixes. Overview frontmatter has deterministic
   TOC metadata (`book-index`/`toc` tags and a table-of-contents summary) and is
   not sent to chapter summarize/tag follow-ons.
3. **Provenance** — `<slug>/.ingest/provenance.json`; source hash, system,
   quality status, LLM mode.
4. **Report** — `<slug>/.ingest/report.json`; inspect when status is `review`
   or `failed`:
   - `marker.exception` for Marker failures;
   - `marker.llm.calls[]` for per-image LLM outcomes;
   - `findings[]` for structural problems.

Image descriptions are callouts:

```markdown
> [!image] AI description
> ...
```

They are AI-generated retrieval aids. Do not quote them as book prose.

## Session transcripts and the recording corpus

A recorded session lands in two roots, split by replaceability. Both are owned
by `.agents/bin/session-ingest`; see `ttrpg-session-ingest` for the pipeline.

```text
vault/transcripts/                # small, Obsidian-visible, qmd-indexed
├── _speakers.yaml                # hand-maintained: discord user_id → player/character/role
├── _lexicon.yaml                 # hand-maintained: variant → canonical corrections
└── <session-id>/
    ├── __<session-id>.md         # overview: participants, stats, chunk TOC, QA summary
    └── NN-mmm-mmm.md             # chunks: [hh:mm:ss] **Speaker**: text ^t<turn-id>

.cache/sessions/                  # gigabytes; never opened directly
├── datasets/<recording_id>/      # craig-stt's; source/ is IRREPLACEABLE, SDK-only access
├── scratch/                      # A/B re-transcription work dirs (disposable)
└── <session-id>/                 # derived: anchors.json, extraction.json, record.json, recap.draft.md

vault/notes/                      # layer 2 — the campaign tracker (ttrpg-session-chronicle)
├── sessions/sNNN-YYYY-MM-DD-*.md # append-only play records; block-ID'd fact ledger
├── inbox/sNNN-proposals.md       # the one file the owner reads after a session
├── state/                        # regenerated projections + entity-registry.md
└── npcs/ locations/ factions/    # promoted entities, every fact cited
```

`record.json` is the only thing layer 2 reads from layer 1 — and it is read
through `.agents/bin/session-ingest view`, never opened directly (~280 KB for a
3-hour session, ~60 % of it duplicated evidence blocks).

Read order and access rules:

1. **Search, don't read.** `.agents/bin/qmd search/query -c transcripts` for a
   topic, `.agents/bin/session-ingest grep` for a known speaker/time window.
   Always `.agents/bin/qmd get <doc-id>` before quoting; never `cat`/Read a
   whole chunk — they are tens of thousands of words of table speech.
2. **Cite as** `vault/transcripts/<session-id>/<NN-…>.md` with the `hh:mm:ss`
   and speaker, or follow a `[[transcripts/<id>/NN-…#^t…]]` block link.
3. **A transcript quote is what was SAID, not what is TRUE.** Campaign truth
   lives in `vault/notes/`; a disagreement between them is a finding for the
   owner, not something to resolve silently.
4. **Writes:** `vault/transcripts/<id>/` only via `session-ingest render`;
   everything under `.cache/sessions/` only via the CLI and the SDK. The two
   `_*.yaml` files are hand-editable user data and cleanup-protected.

## qmd collection map

- `books` → `vault/library/books/**/*.md` (book overviews + chapters).
- `notes` → `vault/notes/**/*.md` (active authored notes).
- `archive` → legacy vault material; use only when explicitly requested.
- `transcripts` → `vault/transcripts/**/*.md` (rendered session transcripts).
  **Excluded from default search** — prep retrieval must never surface table
  speech, so request it explicitly with `-c transcripts`. The `**/*.md` mask
  deliberately misses `_speakers.yaml` / `_lexicon.yaml`, which stay unindexed.

Use `.agents/bin/qmd search/query/get` for prose retrieval; use `query_5etools` first for
canonical creature/spell/item filters.

## Frontmatter metadata helper

`vault_frontmatter` (pi tool; `.agents/bin/vault-frontmatter` elsewhere) is a
read-only scout for inspecting YAML frontmatter in the
active notes and ingested book Markdown files. It reads only from:

- `vault/notes/**/*.md`
- `vault/library/books/**/*.md`

It can list fields/values, filter by simple metadata predicates, and inspect one
file's frontmatter/title/page/tags. It does **not** search body prose, build an
index, use qmd, infer tags, or write files. Use it as an optional scout for broad
or unclear library/note searches; missing metadata is never proof that content is
absent.

## Writing rule of thumb

- New active note/canvas/handout? Use `ttrpg-vault-authoring`, then optionally
  rich-note/canvas skills.
- New or changed ingested book? Use `ttrpg-import-book-pdf` / `book-ingest`.
- New session recording or transcript? Use `ttrpg-session-ingest` for the
  pipeline, then `ttrpg-session-chronicle` for the durable record, inbox and
  projections. A played-session record is never hand-placed by
  `ttrpg-vault-authoring`.
- Promoting old-vault content? Use `ttrpg-import-archive-vault`.
- Search/index stale? Use `ttrpg-system-qmd-maintenance`.
