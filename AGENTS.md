# AGENTS.md

You are running inside **ttrpg-agent**, a workspace for D&D/TTRPG session prep,
local reference search, and book ingestion. Be a creative collaborator by
default, but treat this file as the project contract for source priority, skill
routing, and data boundaries.

The project runs under three harnesses — pi, Claude Code, and Codex. Everything
in this contract applies to all of them. Where they genuinely differ, see
[Harness portability](#harness-portability) at the end; do not assume a
capability that your harness has not actually given you.

## Non-negotiables

- **Do the work.** Ask only when placement, scope, or destructive action would
  materially change meaning.
- **Local sources beat memory.** For rules facts, book references, and campaign
  details, search local data first.
- **Do not edit imports.** `imports/source-vault/`, `imports/books/`, and
  `.cache/vendor/5etools/` are read-only.
- **Do not hand-edit ingested books.** Use `book-ingest` /
  `ttrpg-import-book-pdf` for `vault/library/books/` changes.
- **Do not commit.** The user reviews all repo and data changes before commit.
- **Use the project's tool entrypoints.** Every tool has a hermetic launcher at
  `.agents/bin/<tool>`; use it rather than a bare `qmd`, `marker_single`, or a
  raw `uv run`. A bare invocation runs outside the environment contract and
  writes gigabytes of model weights outside the project. Never run raw
  `python` / `python3`.
- **Run project commands from the repository root.** Paths in these skills are
  repo-root-relative, and Claude and Codex persist the cwd across `cd`.
- **No heuristic smart decisions in tools.** If a pipeline/tool/plugin must
  classify, infer, tag, summarize, or choose semantic metadata, use an LLM or
  leave the field empty/unknown. Do not add regex/text-heuristic fallback guesses.
- **No backward-compatibility shims for generated ingests.** Book-ingest output
  may be regenerated; do not preserve readers/writers for obsolete ingested-book
  layouts or old generated frontmatter fields.
- USER COMMAND ALWAYS HAS HIGHEST PRIORITY. If user ask you to commit, do something unexpected etc - ask for approval and proceed after explicit approval.

## Core navigation first

Use **`ttrpg-vault-navigation`** before any task that reads from or writes to
`vault/`, `imports/`, `vault/library/books/`, or qmd collections. It is the
single source for:

- active notes vs ingested book artifacts vs raw imports;
- qmd collection mapping (`books`, `notes`, `archive`, `transcripts`);
- current book-ingest layout;
- what may be written directly and what must go through a tool.

This navigation skill does **not** replace `ttrpg-vault-authoring`; it only
answers "where is it and what are the boundaries?" Use authoring/rich-note/canvas
skills when creating durable active-vault content.

### Minimal workspace map

| Path | Read? | Write? | Purpose |
|---|---:|---:|---|
| `.agents/` | yes | it *is* the project | Canonical toolchain: `env.sh`, `bin/`, `cli/`, `skills/`, `prompts/`, `chains/`. See `.agents/README.md`. |
| `.agents/state/` | no | tooling only | Durable gitignored state (Foundry credential). Never a cleanup target. |
| `.pi/`, `.claude/`, `.codex/` | yes | **mostly generated** | Harness adapters. Emitted by `.agents/harness sync` — do not hand-edit. Hand-authored exceptions: `.pi/settings.json`, `.pi/mcp.json`, `.pi/extensions/`, `.claude/settings.local.json`. |
| `.cache/` | no | tooling only | Single cache root. Rebuildable: `xdg/qmd/index.sqlite*`, `index/index.yml`. **Must survive cleanup:** `xdg/qmd/models/` and `xdg/datalab/` (~5.5 GB), `huggingface/`, `torch/`, `uv/`, `npm/`, `vendor/`. |
| `.cache/vendor/5etools/` | yes | no | Canonical 5e data mirror — a pinned vendor checkout, reached via `$TTRPG_5ETOOLS_DIR`. |
| `.cache/sessions/` | no | tooling only | Transcription corpus: `datasets/` (craig-stt's, SDK-only) plus per-session derived artifacts. `datasets/*/source/` is the original Craig archive and is **irreplaceable** — Craig expires recordings. Reclaim space only with `session-ingest prune`, never `rm`. |
| `imports/books/` | file list/input only | no | Raw books supplied by the user. |
| `imports/source-vault/` | yes | **no** | Legacy archive, read-only. |
| `imports/fvtt-data/` | yes | **yes** | The one writable exception under `imports/`: local staging for targeted Foundry VTT exports. |
| `vault/notes/` | yes | yes | Active authored campaign notes and table prep. |
| `vault/notes/sessions/` | yes | only via `ttrpg-session-chronicle` | Append-only records of **played** sessions. Frozen at `status: canon`; corrections are retcon events, not edits. `prune` and `adopt --promote` glob here for the session id. |
| `vault/notes/state/` | yes | agent-regenerated | Projections: `current-state`, `story-state`, `clocks`, `calendar`, `entity-registry`. `story-state.md` is agent-owned — hand edits are overwritten. |
| `vault/notes/inbox/` | yes | only via `ttrpg-session-chronicle` | Per-session proposals the owner reviews. Empty means caught up. |
| `vault/library/books/` | yes | only via `book-ingest` | Ingested book/reference artifacts. |
| `vault/transcripts/` | via qmd / `session-ingest grep` | only via `session-ingest render` | Rendered session transcripts: machine-owned and regenerable. `_speakers.yaml` and `_lexicon.yaml` are the hand-maintained exception. |

### Ingested book layout

```text
vault/library/books/
└── <slug>/
    ├── __<slug>.md    # book overview / TOC; first visible file in Obsidian
    ├── 01-…md         # chapter chunks; qmd search/get usually returns these
    ├── images/
    └── .ingest/
        ├── provenance.json   # source hash/status/system/llm
        └── report.json       # validation + follow-on observability
```

Read chapters first for citations, overview second for TOC/page ranges,
`.ingest/provenance.json` for provenance, and `.ingest/report.json` when quality status is
`review` or `failed`. Generated chapters use `# Title`, chapter text, then a
final `---` nav footer with full-vault wikilinks. The `__<slug>.md` overview has
TOC metadata (`book-index`/`toc`) and is not sent to chapter summarize/tag
follow-ons. Image descriptions are `> [!image] AI description` callouts and are
AI-generated retrieval aids, not book prose.

## Skill routing priority

Skills are progressive reference material. If a task matches a skill, read that
`SKILL.md` before acting. Prefer the most specific matching skill, but keep this
order in mind:

> navigation/source → rules/search/import → conversion/format → campaign design → vault placement → rich output/canvas → index/cleanup

### 0. Workspace navigation and source boundaries

Use this whenever local data is involved, before choosing deeper workflow skills.

| Task trigger | Use |
|---|---|
| Any read/write under `vault/`, `imports/`, `vault/library/books/`, qmd collections | `ttrpg-vault-navigation` |
| Unsure if something is active notes, ingested book output, archive, raw import, or qmd index | `ttrpg-vault-navigation` |
| Need to cite or inspect ingested book artifacts | `ttrpg-vault-navigation` |

### 1. Canonical D&D rules and structured data — highest priority for mechanics

Do not answer mechanics from memory when local canonical data exists.

| Task trigger | Use skill/tool |
|---|---|
| Creature, spell, or item lookup; CR/source/type/level/rarity filters | `ttrpg-rules-5etools-query` + `query_5etools` |
| Classes, subclasses, feats, backgrounds, 2014/2024 representation, unsupported 5etools records | `ttrpg-rules-5etools-native` |
| OSR/OSE/BX/AD&D monster, trap, or mechanic conversion to 5e | `ttrpg-rules-osr-to-5e` |

Examples:

- “Show me CR 5–7 fey” → `query_5etools`.
- “What does Paladin get at level 5?” → native 5etools workflow.
- “5e-ify this OSE monster” → OSR conversion, then Foundry formatting if needed.

### 2. Library, book, campaign, and archive search — prose, not structured records

Use qmd-backed search for passages, scenes, lore, statblocks in prose, or
campaign notes. Use `vault_frontmatter` as an optional read-only metadata/facet
scout for broad or unclear book/note searches. For canonical creature/spell/item
filters, use 5etools first.

| Task trigger | Use |
|---|---|
| Find prose/lore/statblock mentions in ingested books or active notes | `ttrpg-vault-navigation` → `ttrpg-library-search` |
| Browse fields/tags/type/status/pages for broad book/note scoping | `ttrpg-vault-navigation` → optional `vault_frontmatter` scout, then qmd/read evidence |
| “Did I already write about X?” | `ttrpg-vault-navigation` → `ttrpg-library-search -c notes` |
| “Where is X discussed in my books?” | `ttrpg-vault-navigation` → `ttrpg-library-search -c books` |
| Old/legacy vault material explicitly requested | `ttrpg-vault-navigation` → `ttrpg-library-search -c archive` and/or `ttrpg-import-archive-vault` |
| qmd results stale/missing/duplicated/wrong collection | `ttrpg-system-qmd-maintenance` |

Collection defaults:

- `books` → `vault/library/books/**/*.md`.
- `notes` → `vault/notes/**/*.md`.
- `archive` → legacy vault; use only when explicitly requested.
- `transcripts` → `vault/transcripts/**/*.md`; **excluded from default search**,
  request it by name. It answers "what was said at the table", never "what is
  true in the campaign" — a disagreement with `notes` is a finding for the owner.

Always `qmd get <doc-id>` before quoting or summarizing a search hit.
`vault_frontmatter` reads only YAML frontmatter plus optional short previews; do
not treat missing tags/frontmatter as absence of content, and always read/qmd-get
candidate files before quoting or summarizing them.

### 3. Vault authoring, rich notes, canvases, and legacy promotion

Use these for durable active campaign notes under `vault/notes/`. Keep book
reference output separate from active authored prep.

| Task trigger | Use |
|---|---|
| Create, save, move, or normalize durable active notes/artifacts | `ttrpg-vault-navigation` → `ttrpg-vault-authoring` |
| Table-ready Markdown, aliases, callouts, embeds, block IDs, source polish | `ttrpg-vault-authoring` → `ttrpg-vault-rich-notes` |
| Obsidian canvas, relationship map, clue board, timeline, encounter/session flow | `ttrpg-vault-authoring` → `ttrpg-vault-canvas` |
| Promote selected legacy notes from `imports/source-vault/` | navigation/search archive → `ttrpg-import-archive-vault` → `ttrpg-vault-authoring` |

Every durable active note should include useful frontmatter plus body wikilinks
and a `## Connections` section. If an important wikilink target does not exist,
create a small stub in the appropriate semantic folder unless it is a throwaway.

Minimal frontmatter pattern:

```yaml
---
type: npc | location | faction | session | monster | item | spell | rules | readaloud | handout | canvas | meta | draft
source: agent | user | imports/source-vault/<path> | imports/books/<file>.pdf | vault/library/books/<book>/<chapter>.md
created: YYYY-MM-DD
tags: [campaign]
status: draft | reviewed | canon
---
```

### 4. Imports and ingests

Use these when new external files need to become usable local reference material.

| Task trigger | Use |
|---|---|
| New PDF book ingest, system classification, summaries, or tags on an ingested book | `ttrpg-import-book-pdf` (canonical end-to-end pipeline doc) |
| Manual tag fallback when `OPENAI_API_KEY` is absent or per-chapter override | `ttrpg-tag-book-manual` |
| One-off non-book PDF/handout/raw Marker debug conversion | `ttrpg-import-raw-pdf` |
| Legacy/archive note import | `ttrpg-import-archive-vault` |

The book-ingest CLI's `next_steps` is the agent contract: run them in order,
don't invent omitted steps. With `OPENAI_API_KEY` present, every metered
follow-on plus `qmd update && qmd embed` is emitted; without a key, only the
qmd refresh is. See `ttrpg-import-book-pdf` for details.

### 5. Session capture and recap

A recorded session is its own ingest path: the CLI produces the transcript and a
structured record, and the agent authors the note.

| Task trigger | Use |
|---|---|
| Craig share URL pasted; "process/transcribe the session recording" | `ttrpg-session-ingest` |
| "Session recap", «что было на сессии», "what happened last session" | `ttrpg-session-ingest`, then `ttrpg-session-chronicle` for the durable record |
| "Write up the session", «занеси сессию в базу»; retcon; clock tick; promote an entity to its own note | `ttrpg-session-chronicle` |
| Reading `record.json` for any purpose | `.agents/bin/session-ingest view` — never open the JSON (~280 KB, ~60 % of it duplicated evidence; the view's text output is ~3× smaller) |
| Wrong names/terms in a transcript; re-transcription request | `ttrpg-session-ingest` (the `qa` → `glossary` → re-run loop) |
| "What did X actually say about Y?" | `ttrpg-session-ingest`: `session-ingest grep` for a known speaker/window, otherwise `qmd … -c transcripts` |

As with book-ingest, the CLI's `--json` `next_steps` is the ordering authority
and metered stages are omitted without `OPENAI_API_KEY` — don't invent them. The
CLI never writes `vault/notes/`: the agent authors the session note from
`recap.draft.md` + `record.json`. A transcript quote is evidence of what was
*said*, not of what is *true*, and anything flagged `world_impact != none` or
`needs_owner` goes to the owner before it becomes canon.

### 6. Foundry VTT tooling

Keep importer format, clickable prose, and system implementation separate.

| Task trigger | Use |
|---|---|
| Foundry 5e-statblock-importer paste text | `ttrpg-foundry-statblock-importer` |
| Foundry actor/item/journal prose with clickable rolls, saves, checks, references | `ttrpg-foundry-enrichers` |
| Foundry dnd5e system implementation: activities, effects, advancements, hooks, formulas | `ttrpg-foundry-dnd5e-wiki` |
| Configure, reconnect, smoke-test, or troubleshoot live Foundry MCP access | `ttrpg-foundry-mcp`; routine live-world operations use the `foundry-vtt` MCP tools directly |

Importer rule: main statblock importer text must be plain WotC-style prose.
Foundry enrichers belong only in a separate post-import section.

### 7. Campaign design, creative prep, and outside inspiration

Use these to design campaign material and produce table-facing output, not to
answer local canonical facts.

| Task trigger | Use |
|---|---|
| Develop, expand, connect, or audit multi-session arcs, storylines, quest networks, mysteries, faction conflicts, or module integration against established campaign truth | `ttrpg-vault-navigation` → `ttrpg-campaign-design` |
| "Plot the next arc" / turn loose ideas or a requested plot into cohesive, non-mandatory world situations | `ttrpg-vault-navigation` → `ttrpg-campaign-design` |
| Several original possibilities for an open-ended design task | Main agent gathers relevant context → `creative-brainstorm` |
| Read-aloud, boxed text, scene description | `ttrpg-create-readaloud` |
| Web inspiration, naming, mythology, current rulings beyond local data | `ttrpg-research-web` |
| Explicit image-generation request | `ttrpg-create-image-gen` |
| Quick NPC sketch | `/npc`; save only if useful via vault authoring |

Campaign design defaults to chat proposals. Explicit write/save/update wording
authorizes a `status: draft` note via `ttrpg-vault-authoring`. One-session
agendas and post-session reconciliation remain normal collaboration, not
campaign design.

Image generation is metered; only call it on explicit user request.

### 8. System maintenance and destructive cleanup

| Task trigger | Use |
|---|---|
| qmd refresh/reindex/rebuild/collection verification | `ttrpg-system-qmd-maintenance` |
| Destructive cleanup/reset/purge/remove of vault/import/index/session data | `ttrpg-system-data-cleanup` |
| Add/change a skill, prompt, tool, MCP server, or harness adapter; a harness stops discovering project resources; caches leak outside the project; something under `.claude/` or `.pi/` looks hand-edited | `ttrpg-harness-engineering` |
| First-run setup, dependency check, `.env` configuration | `/bootstrap`, or `.agents/harness doctor` directly |

`ttrpg-harness-engineering` operates on the toolchain only. It must never touch
`vault/`, `imports/`, or the contents of `.cache/`; when a toolchain change needs
live-data evidence, it hands off to the relevant `ttrpg-*` skill.

Destructive cleanup always requires exact scope, dry-run inventory, and explicit
confirmation before deletion.

## Common chains

- **Canonical monster/spell/item:** `ttrpg-rules-5etools-query`; if absent and
  user wants prose, then navigation → qmd book search.
- **Book/campaign prose lookup:** navigation → optional `vault_frontmatter` for
  broad metadata scouting → `ttrpg-library-search` → `qmd get` before
  quoting/summarizing.
- **PDF ingest:** navigation → `ttrpg-import-book-pdf` (CLI returns ordered
  `next_steps` covering classify/summarize/tag/qmd; run them).
- **Session recording:** `ttrpg-session-ingest` chain (plan → craig-stt
  transcribe → adopt → qa → render → segment/extract → recap + record; follow
  `next_steps`) → `session-ingest view` → agent writes the chronicle, inbox and
  projections via `ttrpg-session-chronicle` → `session-ingest chronicle --check`
  → `qmd update` → offer `prune`.
- **OSR monster to Foundry:** source lookup → `ttrpg-rules-osr-to-5e` →
  `ttrpg-foundry-statblock-importer` → optional Foundry enrichers → vault
  authoring if saved.
- **Foundry item/actor setup:** `ttrpg-foundry-dnd5e-wiki` for system behavior,
  `ttrpg-foundry-enrichers` for description syntax.
- **Live Foundry world:** `ttrpg-foundry-mcp` for setup/diagnostics, then use
  `foundry-vtt` MCP tools; prefer read-only smoke tests before controlled writes.
- **Creative alternatives:** main agent retrieves and verifies relevant context →
  self-contained brief → `creative-brainstorm` parallel ideation and curation →
  main agent triages and implements the selected direction.
- **Campaign/arc design:** navigation → `ttrpg-campaign-design` (loads its own
  references) → `ttrpg-library-search` with read/`qmd get` evidence → optional
  `creative-brainstorm` for structurally distinct options → chat proposals;
  durable drafts via `ttrpg-vault-authoring`.
- **Save durable prep:** navigation → `ttrpg-vault-authoring` → optional
  rich-note/canvas skill → write under `vault/notes/` → qmd refresh if needed.
- **Promote old-vault material:** navigation → archive search/read →
  `ttrpg-import-archive-vault` → vault authoring → optional rich note.
- **Cleanup/reset:** `ttrpg-system-data-cleanup`; never delete before exact
  confirmation.

## Subagents and prompt shortcuts

Use subagents when context/log volume would balloon or independent creative
exploration materially improves the result.

**Available on every harness:**

- Open-ended creative design with several viable directions →
  `creative-brainstorm`. Under pi this is the saved chain; under Claude Code it
  is `.claude/workflows/creative-brainstorm.js`. Its ideator and curator agents
  are internal stages — do not invoke them directly. Both are generated from
  `.agents/chains/creative-brainstorm/spec.json`. Codex has no parallel runner:
  do the four lenses inline, one at a time, keeping them genuinely independent.

**pi only** — `statblock-converter` and `researcher` are pi subagent definitions
in `.pi/agents/` and deliberately have no Claude or Codex equivalent:

- One non-5e statblock conversion → `statblock-converter`.
- Broad fact-finding across books/notes/archive/web → `researcher`.

On Claude Code or Codex, do that work inline instead — read
`ttrpg-rules-osr-to-5e` for the conversion, or `ttrpg-library-search` plus
`ttrpg-research-web` for fact-finding. The routing is a convenience, never a
prerequisite; nothing downstream depends on a subagent having run.

Prompt shortcuts include:

| Prompt | Use |
|---|---|
| `/find-monster` | canonical monster lookup: 5etools first, qmd fallback |
| `/find-anything` | qmd/library search across books/notes/archive; optional `vault_frontmatter` scout for broad/unclear metadata |
| `/convert-monster` | OSR/non-5e monster → 5e + Foundry importer text |
| `/foundry-monster` | normalize existing monster for Foundry importer |
| `/bootstrap` | first-run setup: prerequisites, `.env`, optional data sources, smoke tests |
| `/cleanup` | destructive cleanup workflow with confirmation |
| `/npc` | quick NPC sketch |
| `/readaloud` | boxed text / scene opener |
| `/illustrate` | explicit image-generation request |

Prompt shortcuts do not replace source-backed lookup.

**Codex has no project slash prompts** — user-scope only. Under Codex, name the
workflow instead ("run the cleanup workflow") and load the matching skill; the
prompt bodies live in `.agents/prompts/<name>.md` and can be read directly.

## Citations and copyright posture

The PDFs are the user's local, legitimately purchased materials. You may quote
or restructure for personal prep, but avoid long verbatim passages in chat.
Prefer paraphrase with citations like:

- `vault/library/books/<slug>/<chapter>.md:<line>`
- `imports/source-vault/<path>`
- `.cache/vendor/5etools/data/...`

5etools moved from `imports/5etools/` to `.cache/vendor/5etools/` — it is a
pinned, re-clonable vendor checkout, not user input. No existing vault note
cited the old path, so there is no live discontinuity; if you ever meet one in
older material, leave it alone (vault notes are user data) and cite the new path
going forward. Prefer `$TTRPG_5ETOOLS_DIR` in commands.

## Handling uncertainty

- Mechanics fact uncertain? Search 5etools/local sources first, then say what
  was or was not found.
- Campaign detail uncertain? Search `notes`; use `archive` only when explicitly
  needed.
- Placement uncertain? Use navigation + vault authoring; choose the best
  semantic folder or ask one focused question if placement changes meaning.
- After writing/updating a vault note, tell the user the path and give a short
  excerpt or summary.

## Don't

- Don't edit `imports/source-vault/`, raw PDFs, 5etools data, or ingested book
  chapters by hand.
- Don't commit user data, qmd indexes, PDFs, 5etools clones, Obsidian state,
  generated images, or vault notes.
- Don't change `.pi/settings.json` `defaultProvider` without instruction.
- Don't hand-edit generated adapters: anything under `.claude/` except
  `settings.local.json`, plus `.pi/prompts/`, `.pi/chains/`, `.pi/agents/`,
  `.mcp.json`, `.codex/config.toml`, and `CLAUDE.md`. Change `.agents/` and run `.agents/harness sync`.
- Don't propose paid-cloud workflows when a local OSS path exists.
- Don't hand-edit `vault/library/books/`; re-ingest instead. Same for
  `vault/transcripts/<session-id>/`; fix `_lexicon.yaml` and re-render instead.
- Don't read raw transcription datasets or rendered transcript chunks directly;
  go through `session-ingest` (`grep`, `record.json`) or `qmd -c transcripts`.
- Don't leave durable notes isolated: add body wikilinks and useful
  connections.

## Harness portability

The project is one set of capabilities; each harness discovers a different
subset. `.agents/` is canonical; `.pi/`, `.claude/`, and `.codex/config.toml`
are generated adapters.

| Capability | pi | Claude Code | Codex |
|---|---|---|---|
| Skills | native from `.agents/skills/` | via generated `.claude/skills/` symlinks | native from `.agents/skills/` |
| This contract | `AGENTS.md` | `CLAUDE.md` → `@AGENTS.md` | `AGENTS.md` |
| Slash prompts | `.pi/prompts/` | `.claude/commands/` | **none** — read `.agents/prompts/<name>.md` |
| MCP | `.mcp.json` + `.pi/mcp.json` | `.mcp.json` + `enabledMcpjsonServers` | generated `.codex/config.toml` |
| Bare `qmd …` works | yes | yes | **no** — shell functions do not cross |
| Parallel subagents | chain runner | `.claude/workflows/*.js` | no |

Two rules follow from this and apply everywhere:

1. **Call tools through `.agents/bin/<tool>`.** That entrypoint sources the
   environment contract itself, so it is correct with zero harness
   configuration. A bare `qmd`/`marker_single` may happen to work under pi and
   Claude, but it is outside the contract and will scatter multi-gigabyte model
   caches into `~/.cache/`. Under Codex it simply fails.
2. **Never assume a harness feature is present.** If something you want exists
   only under pi, say so and do the work inline rather than silently skipping it.

## pi-only capabilities

Available when running under pi, absent elsewhere. None is load-bearing.

- **`subagent(...)`** and the `.pi/agents/` definitions (`researcher`,
  `statblock-converter`). When calling it, omit `output` unless you want a real
  output file path — do **not** pass `output: false`; this runtime may stringify it.
- **The chain runner** (`.pi/chains/*.json`) for parallel multi-stage work.
- **`mcp({...})`** as a runtime meta-tool: inspect server status, list discovered
  tools, reconnect mid-session. Claude and Codex attach MCP at session start
  only — the equivalent of `mcp({connect: ...})` there is restarting the session.
- **`/reload`** to pick up changed project resources without restarting.
- **`.pi/settings.json`** — provider, model routing, packages, compaction,
  `shellCommandPrefix`. Hand-authored, not generated.
- **`.agents/scripts/pi-isolated.sh`** — run pi ignoring the user's global
  `~/.pi/agent/` entirely.
- **Conditional skills** — `ttrpg-wsl-sync` is surfaced by a `resources_discover`
  extension only when `TTRPG_WINDOWS_AGENT_DIR` is set. Other harnesses see it
  only if they read `.agents/conditional-skills/` directly.
