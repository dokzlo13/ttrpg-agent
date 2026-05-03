---
name: ttrpg-vault-navigation
description: |
  Core workspace map for reading and writing in this TTRPG project: active
  notes, ingested book artifacts, imports, qmd collections, and source/data
  boundaries. Use before any task that reads from or writes to vault/, imports/,
  vault/library/books/, or qmd-backed collections. This is navigation only;
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
| `vault/library/books/` | yes | only via `book-ingest` | Ingested book/reference artifacts. Don't hand-edit chapters. |
| `imports/books/` | list/read input paths | no | Raw user PDFs/EPUBs. Ingest via `ttrpg-import-book-pdf`. |
| `imports/source-vault/` | yes | **no** | Legacy archive; promote via `ttrpg-import-archive-vault`. |
| `imports/5etools/` | yes | no | Canonical 5e data mirror. Prefer `query_5etools` for creatures/spells/items. |
| `.qmd/` | no | only qmd/system skills | Rebuildable search index/cache. |

Never edit `imports/source-vault/`, raw books, 5etools data, or hand-edit
`vault/library/books/` chapter output.

## Ingested book artifacts

Ingested books live in one directory per slug, with the overview sorted first:

```text
vault/library/books/
└── <slug>/
    ├── __<slug>.md    # book overview / TOC; qmd-indexable and visible first
    ├── 01-…md         # chapter chunks; qmd search/get usually returns these
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

## qmd collection map

- `books` → `vault/library/books/**/*.md` (book overviews + chapters).
- `notes` → `vault/notes/**/*.md` (active authored notes).
- `archive` → legacy vault material; use only when explicitly requested.

Use `qmd search/query/get` for prose retrieval; use `query_5etools` first for
canonical creature/spell/item filters.

## Frontmatter metadata helper

`vault_frontmatter` is a read-only Pi tool for inspecting YAML frontmatter in the
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
- Promoting old-vault content? Use `ttrpg-import-archive-vault`.
- Search/index stale? Use `ttrpg-system-qmd-maintenance`.
