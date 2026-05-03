---
name: ttrpg-import-book-pdf
description: |
  End-to-end ingestion of a PDF book into the searchable vault library: PDF →
  generated chapters → system classification → summaries → tags → qmd index.
  Use when the user adds a PDF to imports/books/ and asks to "ingest", "add this
  book", "process this PDF", or asks for tagging/summaries on an already-ingested
  book. Wraps the .pi/cli/book-ingest CLI.
---

# ttrpg-import-book-pdf

Canonical pipeline doc. Drives the full path from a raw PDF in `imports/books/`
to a tagged, qmd-indexed book under `vault/library/books/<slug>/`.

## When to use

- New PDF appeared in `imports/books/` and the user asked to ingest/add it.
- Re-run on an existing slug — pass `--force` to override the source-hash skip.
- Follow-on enrichment (classify-system, summarize, tag) on an already-ingested
  book.

**Don't use this for:** searching already-ingested content, canonical 5e data
lookups, one-off handout conversion, or archive promotion.

## The flow

The CLI returns ordered `next_steps`. Run them in order. Don't invent omitted
steps; don't second-guess what's emitted.

1. Ingest:
   ```bash
   uv run --project .pi/cli/book-ingest book-ingest --json imports/books/<filename>.pdf
   ```
2. Read the JSON: `book_slug`, `overview_path`, `chapter_dir`, `section_count`,
   `page_count`, `system`, `status`, `report_path`, `next_steps`.
3. Execute every entry in `next_steps` in order. Each is either an informational
   `review_findings` summary or a runnable `command`.
4. Stop only on `status: failed` or unusable output. Continue on `review`; surface
   findings to the user.
5. Final `qmd update && qmd embed` is always emitted last.

## What `next_steps` contains

The CLI's emission rule is binary, by `OPENAI_API_KEY` presence:

| Key present | Steps emitted (in order) |
|---|---|
| ✅ | `classify_system`, `summarize --long-only`, `tag_book`, `qmd update && qmd embed` |
| ❌ | `qmd update && qmd embed` only |

`review_findings` is prepended only when the report has structural findings.
Metered follow-ons are all-or-nothing: with a key, every metered step runs; no
agent decisions in the happy path.

When the key is absent and the user still wants tags, load
**`ttrpg-tag-book-manual`** and best-effort tag chapter-by-chapter via
`book-ingest tag-manual`.

## Output layout

```text
vault/library/books/
└── <book-slug>/
    ├── __<book-slug>.md       # overview / TOC, visible first in Obsidian
    ├── NN-<section>.md        # chapters
    ├── images/
    └── .ingest/
        ├── provenance.json    # source hash, system, run config, quality_status
        └── report.json        # validation + Marker/LLM/follow-on observability
```

Generated chapters use `# Title`, chapter text, then a final `---` nav footer
with full-vault wikilinks. The overview gets deterministic `book-index`/`toc`
metadata and is skipped by chapter summarize/tag follow-ons. Image descriptions
appear as `> [!image] AI description` callouts — AI retrieval aids, not book
prose.

## Ingest LLM modes (Marker SDK)

Resolution order: CLI `--llm` → `TTRPG_MARKER_LLM_MODE` → `no`.

| Mode | When to use | Cost |
|---|---|---|
| `no` | Fastest local ingest, smoke runs | No API calls |
| `images-only` | Searchable image captions | Image-description calls only |
| `text-only` | Poor OCR/layout/tables/headers | Full Marker text cleanup, no captions |
| `all` | Best quality with captions | Slowest; full cleanup + captions |

Anything other than `no` requires `OPENAI_API_KEY`. For partial smokes:
`--llm no --page-range 0-5`.

## The metered follow-ons

All require `OPENAI_API_KEY`; missing key prints a skip message and exits 0.

- **`classify-system <slug>`** — bounded LLM read of front/back matter; writes
  `system`/`systems`/`system_source: llm`/`system_confidence`/`system_rationale`
  to `provenance.json` and `system/<name>` Obsidian tags to chapter and overview
  frontmatter. Refreshes overview.
- **`summarize <slug> --long-only`** — only summarizes chapters too long for
  the tagger's full-text call (default cutoff 18000 body chars,
  `TTRPG_TAG_FULL_CHAPTER_CHARS` overrides). Writes `summary`/`summary_for`.
  This is the form `next_steps` emits; plain `summarize <slug>` summarizes every
  chapter and is rarely needed.
- **`tag <slug>`** — sends complete small chapters as full text; uses the
  detailed `summary` for long chapters. Writes Obsidian-native `tags`/`tags_for`.
  Preserves `book/*` and `system/*`. If the LLM returns no usable tags, writes
  an empty content-tag set — never invents heuristic tags. Refreshes overview.

Frontmatter stamps (`summary_for`, `tags_for`) are body-hash-based, so reruns
skip unchanged chapters and changes correctly invalidate downstream stamps.

## Re-running validation

```bash
uv run --project .pi/cli/book-ingest book-ingest validate \
  vault/library/books/<book-slug>
```

Rewrites `.ingest/report.json` without re-running Marker, preserving follow-on
observability blocks.

## Failure handling

- **Bad PDF / unreadable**: surface the ClickException; suggest the user verify
  the file.
- **OOM / timeout**: try `--page-range` for a smaller slice, or smaller batch
  sizes (`--layout-batch-size 4`, etc.).
- **`status: failed`**: read `.ingest/report.json` and surface top findings.
- **LLM mode requested but no key**: add `OPENAI_API_KEY` or rerun with
  `--llm no`.
- **qmd embed CUDA OOM**: retry with `QMD_LLAMA_GPU=false qmd embed`.

## Reference

Full CLI contract, flags, JSON shape, env variables, and report fields:
`.pi/cli/book-ingest/README.md`.
