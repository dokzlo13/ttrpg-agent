# book-ingest

Convert a TTRPG PDF into clean, sectioned, cross-linked Markdown under
`vault/library/books/` for retrieval, citation, and Obsidian browsing.
Generated reference material lives separately from authored campaign notes in
`vault/notes/`.

## Quick usage

```bash
uv sync

# Single book. Marker uses the pinned Python 3.12 project venv and CUDA when available.
uv run book-ingest imports/books/PHB-2024.pdf

# Fastest: no LLM/API calls.
uv run book-ingest imports/books/PHB-2024.pdf --llm no

# Image captions only (needs OPENAI_API_KEY). Good default for searchable art/maps.
uv run book-ingest imports/books/PHB-2024.pdf --llm images-only

# Machine-readable summary for an agent caller.
uv run book-ingest --json imports/books/PHB-2024.pdf

# Re-run quality checks against an already-ingested book.
uv run book-ingest validate vault/library/books/phb-2024

# Classify the rules system from front/back matter evidence (needs OPENAI_API_KEY).
uv run book-ingest classify-system phb-2024

# Add metered detailed chapter summaries, then refresh overview suffixes.
uv run book-ingest summarize phb-2024

# Main tagging prep: summarize only chapters too long for full-text tag calls.
uv run book-ingest summarize phb-2024 --long-only
# Optional custom size threshold:
uv run book-ingest summarize phb-2024 --long-only --long-threshold 30000

# Add metered Obsidian tags. Small chapters are tagged from full text; long ones use summaries.
uv run book-ingest tag phb-2024
# Optional matching full-text threshold:
uv run book-ingest tag phb-2024 --full-text-chars 30000

# Manual fallback for one agent-reviewed chapter; CLI verifies hash and writes safely.
uv run book-ingest tag-manual phb-2024 03-haunted-inn.md --body-hash sha256:abc --tag location --tag monster

# Refresh an overview after summaries/tags change chapter frontmatter.
uv run book-ingest refresh-overview phb-2024
```

This project is pinned to Python 3.12 (`.python-version`) because the current
Marker/PyTorch CUDA stack is known-good there. On the user's WSL Ubuntu setup,
`marker-pdf==1.10.2` resolves to `torch==2.11.0+cu130`, which sees the CUDA GPU.

## CLI surface

`ingest` is the default — `book-ingest <pdf>` is shorthand for
`book-ingest ingest <pdf>`.

```text
book-ingest ingest <pdf-or-dir> [options]
book-ingest classify-system <slug> [--force] [--json]
book-ingest summarize <slug> [--force] [--long-only] [--long-threshold N] [--json]
book-ingest tag <slug> [--force] [--full-text-chars N] [--json]
book-ingest tag-manual <slug> <chapter> --body-hash <hash> (--tag <tag>... | --empty) [--force] [--json]
book-ingest refresh-overview <slug>
book-ingest validate <book-dir> [--json] [--write/--no-write]
```

### `ingest` options

| Option | Default | Notes |
|---|---|---|
| `--output PATH` | `vault/library/books` | Where book outputs land. |
| `--force` | off | Re-run even when the source hash matches. |
| `--dry-run` | off | Show what would be written; no Marker call. |
| `--keep-backup / --no-keep-backup` | `--no-keep-backup` | Retain dot-prefixed replacement backups. |
| `--llm {no\|images-only\|text-only\|all}` | resolved | CLI > `TTRPG_MARKER_LLM_MODE` > `no`. |
| `--openai-model TEXT` | `gpt-4o-mini` | Used only when LLM mode is not `no`. |
| `--openai-base-url TEXT` | `https://api.openai.com/v1` | OpenAI-compatible endpoint. |
| `--page-range TEXT` | none | Marker `page_range`, e.g. `0,5-10,20`. |
| `--force-ocr` | off | Marker `force_ocr`. |
| `--device {auto\|cuda\|cpu\|mps}` | env / `auto` | Sets `TORCH_DEVICE` before importing Marker. |
| batch-size flags | env / Marker default | `--layout-batch-size`, `--detection-batch-size`, `--recognition-batch-size`, `--table-rec-batch-size`. |
| `--json` | off | Machine-readable agent feedback on stdout. |

Removed flags: `--cache`, `--keep-cache`, `--workers`, `--use-llm`, and
`--describe-images`. There is no subprocess cache/log tree anymore; Marker is
called in-process through the SDK.

## What it does

1. Hash the PDF; **skip** if `<slug>/.ingest/provenance.json` records the same
   `source_hash` (unless `--force`).
2. Call Marker's Python SDK once (`PdfConverter`) with paginated Markdown
   output. The returned object provides Markdown, images, table of contents,
   page stats, and metadata in-process.
3. Plan sections from `rendered.metadata["table_of_contents"]`; fall back to
   structural Markdown headings (`plan_source: marker-markdown`) when Marker
   does not produce a usable ToC, and finally to one whole-book section only
   when neither source is usable.
4. Slice paginated Markdown by heading character offsets when available, else by
   `{N}-----` page markers; rewrite image links to `images/<file>`. Empty
   planned sections are omitted rather than written as placeholder notes.
5. Write the overview at `vault/library/books/<slug>/__<slug>.md` and chapter
   files in the same book directory.
6. Validate structural invariants and write one sidecar:
   `<slug>/.ingest/report.json`.
7. Install the book directory atomically. On replacement, previous output is
   moved to a dot-prefixed backup and removed after success unless
   `--keep-backup` is passed.

## Output layout

```text
vault/library/books/
└── <slug>/
    ├── __<slug>.md            # overview / TOC; visible first in Obsidian
    ├── 01-<section>.md        # chapter chunks; what qmd search/get returns
    ├── 02-<section>.md
    ├── images/
    └── .ingest/
        ├── provenance.json    # hash, page count, LLM/run config, system
        └── report.json        # validation + Marker/LLM/follow-on observability
```

Chapter files have minimal frontmatter:

```yaml
---
book: <slug>
section: The Home of Ezekiel Duncaster
section_index: 5
page_start: 11
page_end: 12
body_hash: sha256:abc123…
ingested_at: '2026-05-01T20:10:27Z'
---
```

Chapter bodies use this shape; generated links use full vault paths to avoid
ambiguous same-name chapters in other book folders:

```markdown
# <section title>

<chapter text>

---

Previous: [[library/books/<slug>/NN-prev|Previous Title]]
Next: [[library/books/<slug>/NN-next|Next Title]]
Pages: 11–12
```

The overview is a normal Markdown file sorted first in the same directory. It is
not sent to summarize/tag LLM follow-ons; it receives deterministic TOC metadata
(`summary: Book <title> table of contents.`, `tags: [..., toc]`) when rendered.

After follow-on enrichment, chapter frontmatter may also include:

```yaml
summary: Detailed retrieval summary for long chapters.
summary_for: sha256:abc123…
tags: [location, random-table]
tags_for: sha256:def456…
```

`summary_for` stamps the body hash summarized. `tags_for` stamps the body hash
plus current summary, so summaries changing correctly invalidate tags. The
`tags` field is the Obsidian-native tag property, so these are visible in Tags
view and searchable with Obsidian's `tag:` operator.

## Image descriptions

When Marker LLM image descriptions are enabled, the generated descriptions are
rewritten into Obsidian callouts so they are visually distinct from book prose:

```markdown
> [!image] AI description
> The image depicts a cluster of wooden structures resembling cabins…

![](images/_page_6_Picture_2.jpeg)
```

These callouts are AI-generated retrieval aids. Do not quote them as source
text from the book.

## Report shape

Provenance lives in `.ingest/provenance.json` and stores redacted LLM config,
source of resolved CLI/env/default settings, batch sizes, requested device, and
observed Torch CUDA availability/device. Validation lives in `.ingest/report.json`:

```json
{
  "status": "ok|review|failed",
  "marker": {
    "duration_seconds": 380.4,
    "exception": null,
    "warnings": [],
    "llm": { "mode": "images-only", "requested": 33, "succeeded": 19, "calls": [] }
  },
  "findings": [],
  "stats": {
    "sections": 15,
    "pages": 66,
    "images_extracted": 33,
    "sections_omitted_empty": 0,
    "omitted_empty_sections": [],
    "chars_total": 158234
  }
}
```

Retained structural finding codes include `empty_section_body`, `tiny_section`,
`oversized_section`, `title_looks_like_ocr_noise`, `duplicate_slug`,
`non_monotonic_pages`, `broken_image_target`, `missing_source_image`,
`book_index_missing`, and `section_file_missing`.

SDK-derived finding codes are `marker_exception`, `marker_warnings`, and
`llm_calls_failed`. There is no log parsing and no `manifest_missing` check.

## Agent stdout contract

Every ingest prints one structural status line to stderr before Marker starts,
including resolved sources for important runtime settings, for example:

```text
book-ingest: llm.mode=images-only[cli] llm.model=gpt-4o-mini[env] llm.concurrency=1[env] device=cuda[env] torch.cuda=true torch.gpu="NVIDIA GeForce RTX 5090" page_range=0-1[cli]
book-ingest: llm.mode=no[default] device=cuda[env] torch.cuda=true torch.gpu="NVIDIA GeForce RTX 5090"
```

`--json` emits one JSON object with paths, counts, status, report path,
`finding_summary`, non-duplicated `findings`, and ordered `next_steps`.
Human stdout prints the same content. Agents should run returned `next_steps` in
order. Metered follow-ons (`classify-system`, `summarize`, `tag`) are omitted
when no `OPENAI_API_KEY` is configured; do not invent omitted steps. The final
step is normally `qmd update && qmd embed`.

## Follow-on subcommands

Ingest does **not** guess rules systems from text heuristics. It writes
`system: unknown` and `systems: []` until `classify-system` runs. All metered
follow-ons require `OPENAI_API_KEY`; missing key prints a skip message and exits
0. Each command refreshes the overview and records observability in
`.ingest/report.json`.

| Command | Writes to | Behavior |
|---|---|---|
| `classify-system <slug>` | `.ingest/provenance.json` (`system`, `systems`, `system_source: llm`, `system_confidence`, `system_rationale`) and `system/<name>` chapter/overview tags | Bounded LLM read of first/last chapters; emits all plausible system tags. |
| `summarize <slug>` | `summary` / `summary_for` per chapter | Sends each chapter body to OpenAI in bounded parallel; skips chapters whose `summary_for` matches `body_hash` unless `--force`. |
| `summarize <slug> --long-only` | same | Skips chapters small enough for the tagger's full-text call. This is what `next_steps` emits. |
| `tag <slug>` | Obsidian-native `tags` / `tags_for` per chapter | One LLM call per chapter with evidence + confidence; keeps 0–3 strong tags. Small chapters use full text; long chapters use `summary` (or bounded head/tail excerpt). Empty tag set when LLM returns nothing — never invents heuristic tags. |
| `tag-manual <slug> <chapter>` | same | Manual fallback writer. Agent reads chapter, picks 0–3 tags, passes `--body-hash`. Helper verifies hash, validates tags, preserves `book/*` and `system/*`. `--empty` writes explicit empty tag set. |

Env vars for follow-ons:

- `OPENAI_API_KEY` — required for metered commands; absent → skip+exit 0.
- `TTRPG_MARKER_OPENAI_MODEL` — default `gpt-4o-mini`.
- `TTRPG_MARKER_OPENAI_BASE_URL` — default `https://api.openai.com/v1`.
- `TTRPG_SUMMARIZE_MAX_CONCURRENCY` — default `4`.
- `TTRPG_TAG_FULL_CHAPTER_CHARS` — default `18000`. Shared by
  `summarize --long-only` and `tag`. Overridable per command via
  `--long-threshold N` and `--full-text-chars N`.

`tags_for` stamps `body_hash + summary`, so changing summaries invalidates tags
correctly. `summary_for` stamps `body_hash` alone.

## LLM modes

| Mode | Marker behavior |
|---|---|
| `no` | No API calls. Local extraction; image files still copied. |
| `images-only` | Local extraction plus `LLMImageDescriptionProcessor`; no table/page/header/text LLM cleanup. |
| `text-only` | Full Marker LLM cleanup for text/tables/forms/headers/pages, but no image captions. |
| `all` | Full cleanup plus image captions. |

When an LLM mode other than `no` resolves, `OPENAI_API_KEY` must be set. The
key is passed in-memory through Marker SDK config and never written to a log.
`TTRPG_MARKER_LLM_MAX_CONCURRENCY` controls LLM request concurrency, and
`TTRPG_MARKER_LLM_MIN_INTERVAL_SECONDS` (default `2.0`) spaces requests to reduce
image-caption TPM rate-limit bursts.

## Quality gates

```bash
cd .pi/cli/book-ingest
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest
```
