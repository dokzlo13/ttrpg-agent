# book-ingest

Convert a TTRPG PDF into clean, sectioned, cross-linked Markdown under
`vault/library/books/<book-slug>/` for retrieval, citation, and Obsidian
browsing. Generated reference material — authored campaign notes live
separately under `vault/notes/`.

## Quick usage

```bash
uv sync

# Single book. Marker auto-selects CUDA when its PyTorch install sees a GPU.
uv run book-ingest imports/books/PHB-2024.pdf

# Folder of PDFs.
uv run book-ingest imports/books/

# Re-ingest after the PDF changed (or to override hash skip).
uv run book-ingest imports/books/PHB-2024.pdf --force

# Fastest: no LLM/API calls.
uv run book-ingest imports/books/PHB-2024.pdf --llm no

# Image captions only (needs OPENAI_API_KEY). Good default for searchable art/maps.
uv run book-ingest imports/books/PHB-2024.pdf --llm images-only

# Full Marker LLM text/table/header/page cleanup; slow + metered.
uv run book-ingest imports/books/PHB-2024.pdf --llm text-only

# Full cleanup plus image captions; slowest.
uv run book-ingest imports/books/PHB-2024.pdf --llm all

# Re-run quality checks against an already-ingested book.
uv run book-ingest validate vault/library/books/phb-2024

# Machine-readable summary for an agent caller.
uv run book-ingest --json imports/books/PHB-2024.pdf
```

## CLI surface

Two commands. `ingest` is the default — `book-ingest <pdf>` is shorthand
for `book-ingest ingest <pdf>`.

```text
book-ingest ingest <pdf-or-dir> [options]
book-ingest validate <book-dir> [--json] [--write/--no-write]
```

### `ingest` options

| Option | Default | Notes |
|---|---|---|
| `--output PATH` | `vault/library/books` | Where book directories land. |
| `--cache PATH` | `<project>/.cache/book-ingest` | Raw Marker artifacts + logs. |
| `--force` | off | Re-run even when the source hash matches. |
| `--dry-run` | off | Show what would be written; no Marker call. |
| `--keep-cache / --no-keep-cache` | `--no-keep-cache` | Keep markdown/json artifacts in the cache. Logs are kept regardless. |
| `--keep-backup / --no-keep-backup` | `--no-keep-backup` | Retain `.<slug>.<timestamp>.bak` after a replacing install. |
| `--llm {no\|images-only\|text-only\|all}` | resolved | Preferred Marker LLM mode. CLI > `TTRPG_MARKER_LLM_MODE` > legacy bool env > `no`. |
| `--use-llm / --no-use-llm` | resolved | Deprecated compatibility flag. Prefer `--llm`. Alone maps to `text-only`; with `--describe-images` maps to `all`. |
| `--describe-images / --no-describe-images` | resolved | Deprecated compatibility flag. Prefer `--llm images-only` or `--llm all`. Alone maps to `images-only`. |
| `--openai-model TEXT` | `gpt-4o-mini` | Used only when an LLM mode other than `no` resolves. |
| `--openai-base-url TEXT` | `https://api.openai.com/v1` | OpenAI-compatible endpoint. |
| `--page-range TEXT` | none | Marker's `--page_range`, e.g. `0,5-10,20`. |
| `--force-ocr` | off | Marker's `--force_ocr`. |
| `--device {auto\|cuda\|cpu\|mps}` | `auto` (or env) | |
| `--layout-batch-size`, `--detection-batch-size`, `--recognition-batch-size`, `--table-rec-batch-size` | env / Marker default | Tune for GPU. |
| `--json` | off | Machine-readable summary on stdout. |

## What it does

1. Hash the PDF; **skip** if `<slug>/.ingest.json` already records the same
   `source_hash` and matching `schema_version` (unless `--force`). See
   [Re-ingestion semantics](#re-ingestion-semantics).
2. Run `marker_single` twice into a scratch directory: once for paginated
   Markdown (`--paginate_output`) and once for JSON (block tree). Marker's
   model load is cached so the second call is fast.
3. Plan sections deterministically:
   - **PDF outline** if the PDF has `≥3` depth-0 outline entries with
     resolvable page indices.
   - **Marker JSON `SectionHeader` blocks** otherwise. Splitting prefers a
     numbered-prefix pattern (`^[A-Z]?\d+\s\w`) when ≥3 headers match,
     else `<h1>`, else the most common of `<h1>/<h2>`.
   - Whole-book fallback as a last resort.
4. Clean titles, drop noise (Front Cover / Title / Endpaper / Contents /
   Copyright …), uniquify slugs, and assign per-section page ranges.
5. Slice the paginated Markdown by `{N}-----` page markers, rewrite
   `![](_page_N_*.jpeg)` to `![](images/_page_N_*.jpeg)`, copy referenced
   images, and write `<NN>-<slug>.md` notes plus `_book.md`.
6. Validate (warn-only) and write `.ingest/quality.json`.
7. Move the staged tree to its final location atomically. If a previous
   ingest exists, it is renamed to `.<slug>.<timestamp>.bak` (leading
   dot — qmd skips it during indexing) and dropped on success, or kept
   with `--keep-backup`.

## Output layout

```text
vault/library/books/<book-slug>/
  _book.md                      # book index with section TOC + frontmatter
  01-<slug>.md                  # one per planned section
  02-<slug>.md
  ...
  images/                       # only images referenced by section bodies
  .ingest.json                  # provenance with schema_version
  .ingest/                      # sidecar metadata (qmd skips dot-dirs)
    manifest.json               # planned sections, page ranges, slugs
    quality.json                # validation report
    marker.json                 # redacted Marker invocation record
    agent-next.txt              # plain-text next steps for the caller
```

## Cache policy

The cache lives at `<project>/.cache/book-ingest/<source-hash>/` (project-
local, gitignored). It exists for *debugging*; the canonical content is
in `vault/library/books/<slug>/`.

| Mode | Cache contents after success |
|---|---|
| default (`--no-keep-cache`) | `logs/marker-markdown.log`, `logs/marker-json.log` only |
| `--keep-cache` | `logs/`, `markdown/`, `json/`, `marker-cmd.json` |

Logs are *always* persisted (even with `--no-keep-cache`) because they're
KB-size and high-value: redacted command line, returncode, duration,
Marker version, full stdout/stderr.

If the cache grows too large, run the `ttrpg-system-data-cleanup` skill with the
`book-ingest-cache` scope, or delete `.cache/book-ingest/` manually.

## Re-ingestion semantics

When you run `book-ingest` against a PDF that has previously been ingested,
the tool decides what to do based on the source hash, the schema version,
and `--force`. There is no error path here; the choices are predictable:

| Existing `<slug>/.ingest.json` | `--force`? | Behavior |
|---|---|---|
| same hash, same `schema_version` | no | **skip**: no Marker call, return the recorded summary, exit 0 |
| same hash, same `schema_version` | yes | full re-ingest, replace, drop backup |
| same hash, schema mismatch | either | log a warning, full re-ingest, replace, drop backup |
| different hash (PDF updated) | either | full re-ingest, replace, drop backup |
| missing `.ingest.json`, target dir present | no | **error**: `target … already exists; pass --force to replace` |
| missing `.ingest.json`, target dir present | yes | full re-ingest, replace, drop backup |

The replacement path always writes the new tree to a scratch directory,
moves the old output to `.<slug>.<timestamp>.bak`, then atomically renames
the new tree into place. After a successful install the backup is
removed unless you pass `--keep-backup`. On install failure the backup
is restored.

So **multiple runs do not duplicate work** — the hash check short-
circuits before anything expensive runs. Re-ingesting an identical PDF
is essentially a no-op.

## LLM modes and image descriptions

Research note: Marker maintainers recommend `--use_llm --processors
"marker.processors.llm.llm_image_description.LLMImageDescriptionProcessor"`
when you want image descriptions without the full LLM pipeline. The wrapper's
`images-only` mode uses that approach, but preserves Marker's normal non-LLM
cleanup processors so text output stays comparable to fast/no-LLM mode.

`--llm` modes:

| Mode | Marker behavior | JSON planning pass |
|---|---|---|
| `no` | No API calls. Normal local extraction; image files are still copied. | no LLM |
| `images-only` | Normal local extraction plus `LLMImageDescriptionProcessor` only; no table/page/header/text LLM processors. | no LLM |
| `text-only` | Full Marker LLM cleanup for text/tables/forms/headers/pages, but no image captions. | LLM on |
| `all` | `text-only` plus image captions. | LLM on, image captions still markdown-only |

When image captions are enabled, Marker's `LLMImageDescriptionProcessor` writes a one-paragraph
description next to each extracted image, so the section body looks like:

```markdown
Treasure — Modest bronze campaign medal

Image /page/6/Picture/2 description: The image depicts a cluster of wooden
structures resembling cabins, set among trees…

![](images/_page_6_Picture_2.jpeg)
```

This makes images discoverable through qmd's BM25 even when nearby body
text doesn't reference them. The image file is still saved to disk; only
the description is added.

Resolution order, highest first:

1. `--llm no|images-only|text-only|all` on the command line.
2. `TTRPG_MARKER_LLM_MODE=no|images-only|text-only|all` in `.env` or process env.
3. Legacy CLI flags `--use-llm` / `--describe-images`.
4. Legacy env booleans `TTRPG_MARKER_USE_LLM` and `TTRPG_MARKER_DESCRIBE_IMAGES`.
5. `no` by default.

Legacy mapping: `--use-llm` alone means `text-only`; `--describe-images`
alone means `images-only`; both together mean `all`.

Every run prints the resolved values and their sources on stderr, e.g.:

```text
book-ingest: llm_mode=images-only (source=env), use_llm=True, describe_images=True, device=cuda, model=gpt-4o-mini
```

The same is recorded in `.ingest.json → options.llm` and in
`.ingest/marker.json`. To check without re-running:

```bash
cat <book>/.ingest/marker.json | jq '.runs[0].llm'
```

When an LLM mode other than `no` resolves, the wrapper:

1. Loads project-root `.env` into the process env (without overriding).
2. Resolves `OPENAI_API_KEY`, `TTRPG_MARKER_OPENAI_MODEL`, and
   `TTRPG_MARKER_OPENAI_BASE_URL` (CLI flags win).
3. Writes a `0600`-mode `marker-config-<format>.json` in scratch with the key.
4. Calls Marker with `--use_llm --llm_service marker.services.openai.OpenAIService`.
5. For `images-only`, adds `--processors <normal non-LLM processors +
   LLMImageDescriptionProcessor>` and only does that on the Markdown run.

If `OPENAI_API_KEY` is not set when LLM mode resolves on, the run aborts
before invoking Marker. The key never appears on the command line.
Provenance records only `openai_api_key_present: true`.

`.env` keys read by this tool:

```dotenv
TTRPG_MARKER_LLM_MODE=images-only                   # no | images-only | text-only | all
# Legacy fallback if TTRPG_MARKER_LLM_MODE is unset:
# TTRPG_MARKER_USE_LLM=false
# TTRPG_MARKER_DESCRIBE_IMAGES=false
OPENAI_API_KEY=sk-...
TTRPG_MARKER_OPENAI_MODEL=gpt-4o-mini
TTRPG_MARKER_OPENAI_BASE_URL=https://api.openai.com/v1

TTRPG_MARKER_DEVICE=cuda
TTRPG_MARKER_LAYOUT_BATCH_SIZE=8
TTRPG_MARKER_DETECTION_BATCH_SIZE=8
TTRPG_MARKER_RECOGNITION_BATCH_SIZE=128
TTRPG_MARKER_TABLE_REC_BATCH_SIZE=8
```

`text-only` and `all` invoke per-block LLM calls (table, header, math,
page-correction, etc.). For a 200-page book this can be hundreds to thousands
of calls — slow and metered. `images-only` is usually much cheaper: roughly one
vision-model call per detected Picture/Figure block, and no LLM pass over the
JSON planning run.

## Quality validation

Validation is detection-only; output is always written. Findings live in
`.ingest/quality.json`:

| Code | Meaning |
|---|---|
| `empty_section_body` | Sliced page range produced no body text. |
| `tiny_section`, `oversized_section` | Body size out of bounds (`<200B` / `>200KB`). |
| `title_looks_like_ocr_noise` | Section title looks like garbage. |
| `duplicate_slug` | Two sections collapsed to the same slug; suffixed with `-2`. |
| `non_monotonic_pages` | A section starts before the previous one ended. |
| `broken_image_target` | Body references an image not in `images/`. |
| `missing_source_image` | Marker did not emit a referenced image. |
| `marker_llm_requested_but_skipped` | `--use-llm` was set without an API key. |
| `book_index_missing`, `section_file_missing`, `manifest_missing` | Errors. |

`status` is `failed` if any errors, else `review_required` if any
warnings, else `ok`. Re-run validation any time with
`book-ingest validate <book-dir>`.

## GPU / performance

Marker uses PyTorch and auto-selects CUDA when available. Force a device
and tune batch sizes:

```bash
uv run book-ingest imports/books/PHB-2024.pdf --device cuda \
  --layout-batch-size 8 --detection-batch-size 8 \
  --recognition-batch-size 128
```

Supported devices: `auto`, `cuda`, `cpu`, `mps`. Lower or omit batch
sizes if you hit OOM.

## Schema version

Current output schema is `2`. A schema mismatch in `.ingest.json`
triggers forced re-ingest on the next run with a console warning. Bump
the version in `book_ingest/__init__.py` (`SCHEMA_VERSION`) when the
output layout changes in a way that requires re-running existing
ingests.

## Scope boundary

This tool is a library ingester only. It does **not**:

- extract statblocks or emit sidecar JSON for monsters/NPCs;
- summarize chapters or whole books;
- refresh `qmd`;
- enrich images with anything beyond Marker's own LLM processor.

Those decisions happen later, from the section Markdown itself.

## Dependencies

- System: `marker_single` from
  [`marker-pdf`](https://github.com/datalab-to/marker), e.g.
  `uv tool install marker-pdf`.
- Python runtime deps (`pyproject.toml`): `click`, `pypdf`, `pyyaml`.
- Dev deps (`pyproject.toml [dependency-groups.dev]`): `pytest`,
  `pytest-cov`, `ruff`, `mypy`, `types-PyYAML`.

## Quality gates

Run all four before merging changes. They are all clean on the current
source tree:

```bash
cd .pi/cli/book-ingest

uv run ruff check .            # lint
uv run ruff format --check .   # formatter check
uv run mypy .                  # types
uv run pytest                  # unit tests
```

Or in one shell:

```bash
uv run ruff check . && uv run ruff format --check . \
  && uv run mypy . && uv run pytest
```

Auto-fix lint and apply formatting:

```bash
uv run ruff check --fix .
uv run ruff format .
```

Configuration lives in `pyproject.toml` (`[tool.ruff]`, `[tool.mypy]`,
`[tool.pytest.ini_options]`). Unit tests cover:

- `clean_title`, `slugify`, `is_noise_title`, `looks_like_ocr_noise`,
  `book_title_from`;
- page-marker slicing, `<span>` stripping, image-link rewriting,
  heading deduplication;
- `render_section_note`, `render_book_index`;
- the marker-JSON planner path with synthetic fixtures (no Marker run);
- `validate_book_dir` rule firing;
- `parse_dotenv`, `parse_bool_env`, `resolve_tristate`,
  `resolve_llm_config`;
- `_build_command` / `_write_llm_config` for `--use-llm` and
  `--describe-images` wiring.

End-to-end smoke runs against `imports/books/*.pdf` are documented but
not in CI (Marker requires ~500 MB of model weights and a working
torch install).

## Plan history

- [`REDESIGN_PLAN.md`](REDESIGN_PLAN.md) — initial v1 proposal.
- [`REDESIGN_PLAN_v2.md`](REDESIGN_PLAN_v2.md) — current architecture.
