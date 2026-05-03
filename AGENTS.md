# AGENTS.md

You are running inside **ttrpg-agent**, a Pi-powered workspace for D&D/TTRPG
session prep, local reference search, and book ingestion. Be a creative
collaborator by default, but treat this file as the project contract for source
priority, skill routing, and data boundaries.

## Non-negotiables

- **Do the work.** Ask only when placement, scope, or destructive action would
  materially change meaning.
- **Local sources beat memory.** For rules facts, book references, and campaign
  details, search local data first.
- **Do not edit imports.** `imports/source-vault/`, `imports/books/`, and
  `imports/5etools/` are read-only.
- **Do not hand-edit ingested books.** Use `book-ingest` /
  `ttrpg-import-book-pdf` for `vault/library/books/` changes.
- **Do not commit.** The user reviews all repo and data changes before commit.
- **Use project Python conventions.** Never run raw `python` / `python3`; use
  `uv run python ...` or `uv run --project .pi/cli/<tool> ...`.
- **No heuristic smart decisions in tools.** If a pipeline/tool/plugin must
  classify, infer, tag, summarize, or choose semantic metadata, use an LLM or
  leave the field empty/unknown. Do not add regex/text-heuristic fallback guesses.
- **No backward-compatibility shims for generated ingests.** Book-ingest output
  may be regenerated; do not preserve readers/writers for obsolete ingested-book
  layouts or old generated frontmatter fields.

## Core navigation first

Use **`ttrpg-vault-navigation`** before any task that reads from or writes to
`vault/`, `imports/`, `vault/library/books/`, or qmd collections. It is the
single source for:

- active notes vs ingested book artifacts vs raw imports;
- qmd collection mapping (`books`, `notes`, `archive`);
- current book-ingest layout;
- what may be written directly and what must go through a tool.

This navigation skill does **not** replace `ttrpg-vault-authoring`; it only
answers "where is it and what are the boundaries?" Use authoring/rich-note/canvas
skills when creating durable active-vault content.

### Minimal workspace map

| Path | Read? | Write? | Purpose |
|---|---:|---:|---|
| `.pi/` | yes | sparingly | Project machinery: skills, prompts, agents, CLI tools. |
| `.qmd/` | no | qmd/system skills | Rebuildable qmd index/cache/config state. |
| `imports/books/` | file list/input only | no | Raw books supplied by the user. |
| `imports/source-vault/` | yes | **no** | Legacy archive, read-only. |
| `imports/5etools/` | yes | no | Local canonical 5e data mirror. |
| `vault/notes/` | yes | yes | Active authored campaign notes and table prep. |
| `vault/library/books/` | yes | only via `book-ingest` | Ingested book/reference artifacts. |

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

> navigation/source → rules/search/import → conversion/format → vault placement → rich output/canvas → index/cleanup

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
campaign notes. For canonical creature/spell/item filters, use 5etools first.

| Task trigger | Use |
|---|---|
| Find prose/lore/statblock mentions in ingested books or active notes | `ttrpg-vault-navigation` → `ttrpg-library-search` |
| “Did I already write about X?” | `ttrpg-vault-navigation` → `ttrpg-library-search -c notes` |
| “Where is X discussed in my books?” | `ttrpg-vault-navigation` → `ttrpg-library-search -c books` |
| Old/legacy vault material explicitly requested | `ttrpg-vault-navigation` → `ttrpg-library-search -c archive` and/or `ttrpg-import-archive-vault` |
| qmd results stale/missing/duplicated/wrong collection | `ttrpg-system-qmd-maintenance` |

Collection defaults:

- `books` → `vault/library/books/**/*.md`.
- `notes` → `vault/notes/**/*.md`.
- `archive` → legacy vault; use only when explicitly requested.

Always `qmd get <doc-id>` before quoting or summarizing a search hit.

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

### 5. Foundry VTT tooling

Keep importer format, clickable prose, and system implementation separate.

| Task trigger | Use |
|---|---|
| Foundry 5e-statblock-importer paste text | `ttrpg-foundry-statblock-importer` |
| Foundry actor/item/journal prose with clickable rolls, saves, checks, references | `ttrpg-foundry-enrichers` |
| Foundry dnd5e system implementation: activities, effects, advancements, hooks, formulas | `ttrpg-foundry-dnd5e-wiki` |

Importer rule: main statblock importer text must be plain WotC-style prose.
Foundry enrichers belong only in a separate post-import section.

### 6. Creative prep and outside inspiration

Use these to produce table-facing material, not to answer local canonical facts.

| Task trigger | Use |
|---|---|
| Read-aloud, boxed text, scene description | `ttrpg-create-readaloud` |
| Web inspiration, naming, mythology, current rulings beyond local data | `ttrpg-research-web` |
| Explicit image-generation request | `ttrpg-create-image-gen` |
| Quick NPC sketch | `/npc`; save only if useful via vault authoring |

Image generation is metered; only call it on explicit user request.

### 7. System maintenance and destructive cleanup

| Task trigger | Use |
|---|---|
| qmd refresh/reindex/rebuild/collection verification | `ttrpg-system-qmd-maintenance` |
| Destructive cleanup/reset/purge/remove of vault/import/index data | `ttrpg-system-data-cleanup` |

Destructive cleanup always requires exact scope, dry-run inventory, and explicit
confirmation before deletion.

## Common chains

- **Canonical monster/spell/item:** `ttrpg-rules-5etools-query`; if absent and
  user wants prose, then navigation → qmd book search.
- **Book/campaign prose lookup:** navigation → `ttrpg-library-search` →
  `qmd get` before quoting/summarizing.
- **PDF ingest:** navigation → `ttrpg-import-book-pdf` (CLI returns ordered
  `next_steps` covering classify/summarize/tag/qmd; run them).
- **OSR monster to Foundry:** source lookup → `ttrpg-rules-osr-to-5e` →
  `ttrpg-foundry-statblock-importer` → optional Foundry enrichers → vault
  authoring if saved.
- **Foundry item/actor setup:** `ttrpg-foundry-dnd5e-wiki` for system behavior,
  `ttrpg-foundry-enrichers` for description syntax.
- **Save durable prep:** navigation → `ttrpg-vault-authoring` → optional
  rich-note/canvas skill → write under `vault/notes/` → qmd refresh if needed.
- **Promote old-vault material:** navigation → archive search/read →
  `ttrpg-import-archive-vault` → vault authoring → optional rich note.
- **Cleanup/reset:** `ttrpg-system-data-cleanup`; never delete before exact
  confirmation.

## Subagents and prompt shortcuts

Use subagents when context/log volume would balloon:

- One non-5e statblock conversion → `statblock-converter`.
- Broad fact-finding across books/notes/archive/web → `researcher`.

When using `subagent(...)`, omit `output` unless you want a real output file
path. Do **not** pass `output: false`; this runtime may stringify it.

Prompt shortcuts include:

| Prompt | Use |
|---|---|
| `/find-monster` | canonical monster lookup: 5etools first, qmd fallback |
| `/find-anything` | qmd/library search across books/notes/archive |
| `/convert-monster` | OSR/non-5e monster → 5e + Foundry importer text |
| `/foundry-monster` | normalize existing monster for Foundry importer |
| `/cleanup` | destructive cleanup workflow with confirmation |
| `/npc` | quick NPC sketch |
| `/readaloud` | boxed text / scene opener |
| `/illustrate` | explicit image-generation request |

Prompt shortcuts do not replace source-backed lookup.

## Citations and copyright posture

The PDFs are the user's local, legitimately purchased materials. You may quote
or restructure for personal prep, but avoid long verbatim passages in chat.
Prefer paraphrase with citations like:

- `vault/library/books/<slug>/<chapter>.md:<line>`
- `imports/source-vault/<path>`
- `imports/5etools/data/...`

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
- Don't propose paid-cloud workflows when a local OSS path exists.
- Don't hand-edit `vault/library/books/`; re-ingest instead.
- Don't leave durable notes isolated: add body wikilinks and useful
  connections.
