---
name: ttrpg-import-book-pdf
description: |
  Ingest a PDF book into the searchable vault library. Use when the user adds
  a new PDF to imports/books/ and asks to "ingest", "add this book", "process
  this PDF", or similar. Wraps .pi/cli/book-ingest.
---

# ttrpg-import-book-pdf

## When to use

- User dropped a new PDF in `imports/books/` and says "ingest it" / "add this".
- A previously-ingested book needs re-processing. Force a re-run with `--force`.

**Don't use this for:** searching already-ingested content (use `ttrpg-library-search`),
querying canonical 5e data (use `ttrpg-rules-5etools-query` / `query_5etools`),
one-off handout conversion (use `ttrpg-import-raw-pdf`), or copying notes from the old vault
(use `ttrpg-import-archive-vault`).

## Procedure

1. Confirm the PDF exists at `imports/books/<filename>.pdf`.
2. Check `vault/library/books/` for a matching slug. If `<slug>/.ingest.json`
   exists with the same `source_hash` and `schema_version`, the tool will
   skip unless `--force` is passed.
3. Spawn the **ingest-worker** subagent with the PDF path. That subagent
   runs:

   ```bash
   uv run --project .pi/cli/book-ingest book-ingest imports/books/<filename>.pdf
   # On a CUDA machine, force/tune if needed:
   # uv run --project .pi/cli/book-ingest book-ingest imports/books/<filename>.pdf --device cuda
   # LLM modes: --llm no|images-only|text-only|all
   # For agent automation that prefers structured output:
   # uv run --project .pi/cli/book-ingest book-ingest --json imports/books/<filename>.pdf
   ```

4. The tool writes generated reference output under
   `vault/library/books/<book-slug>/`, separate from active notes in
   `vault/notes/`:

   - `_book.md` — book index with section TOC and frontmatter.
   - `NN-<slug>.md` — one per planned section.
   - `images/` — referenced images, with link paths rewritten in section bodies.
   - `.ingest.json` — provenance (hash, schema, page count, plan source, system tag).
   - `.ingest/manifest.json`, `quality.json`, `marker.json`, `agent-next.txt` —
     sidecar metadata (qmd skips dot-directories).

   Raw Marker artifacts and per-run logs go to `.cache/book-ingest/<hash>/`
   (project-local, gitignored). The `logs/marker-<format>.log` files contain
   subprocess stdout/stderr with the redacted command, return code, and
   duration — useful when debugging poor extraction.

5. After completion, read `.ingest/quality.json` if `quality_status` is
   `review_required` or `failed`. Common warnings to surface to the user:
   `empty_section_body`, `tiny_section`, `oversized_section`,
   `broken_image_target`, `title_looks_like_ocr_noise`.
6. Run `qmd update` and then `qmd embed` so the new content is searchable
   and semantically retrievable. The ingester intentionally does not run
   these itself; the calling agent does, after reviewing quality.
7. Briefly confirm to the user: book title, section count, plan source
   (`pdf-outline` / `marker-json` / `whole-book`), system tag (osr/5e/unknown),
   output path, and whether embedding succeeded.

## Re-running validation

If the user manually edits a book directory or a stale ingest needs a
fresh quality report:

```bash
uv run --project .pi/cli/book-ingest book-ingest validate \
  vault/library/books/<book-slug>
```

This rewrites `.ingest/quality.json` without re-running Marker.

## `--llm` modes

Preferred flag: `--llm no|images-only|text-only|all`. Resolution order:
CLI `--llm` → `TTRPG_MARKER_LLM_MODE` → legacy `--use-llm`/`--describe-images`
flags/env → `no`.

| Mode | Use when | Cost/behavior |
|---|---|---|
| `no` | fastest local ingest | no API calls; image files still copied |
| `images-only` | user wants images searchable | normal Marker extraction plus `LLMImageDescriptionProcessor` only on the Markdown pass; roughly one vision call per detected Picture/Figure |
| `text-only` | OCR/layout/table/header quality is poor | full Marker LLM cleanup, no image captions; can be hundreds/thousands of calls |
| `all` | quality-first and image captions are both wanted | slowest; full LLM cleanup plus image captions |

For image discoverability ("find the bronze medal", "where's the dungeon
map"), prefer:

```bash
uv run --project .pi/cli/book-ingest book-ingest \
  --llm images-only imports/books/<filename>.pdf
```

The image file is still saved; only a description paragraph is added to the
section body. `--describe-images` remains as a deprecated alias and maps to
`images-only` unless combined with `--use-llm`, which maps to `all`.

When any LLM mode other than `no` is on, ensure `OPENAI_API_KEY` is set in
`.env`. The wrapper writes a 0600-mode temp config rather than putting the key
on the command line. Check provenance with:

```bash
cat <book>/.ingest/marker.json | jq '.runs[].llm'
```

## Why links matter

Section frontmatter is useful for filtering, but Obsidian graph quality
comes from body links. Don't remove the generated `_book.md` ↔ section
links or the previous/next chain in section files; they are intentional.

When active campaign notes link back to an ingested book, the book index file is
`_book.md`, not `<book-slug>.md`. To avoid broken or ambiguous links, use a
path-qualified Obsidian link from the vault root:

```markdown
[[library/books/<book-slug>/_book|Readable Book Title]]
```

Do not invent links like `[[the-pit-in-the-forest]]` unless an active stub with
that filename actually exists.

## GPU / performance

Marker auto-selects CUDA when its PyTorch install sees a CUDA GPU. If you
need to force or tune:

```bash
uv run --project .pi/cli/book-ingest book-ingest imports/books/<file>.pdf \
  --device cuda \
  --layout-batch-size 8 \
  --detection-batch-size 8 \
  --recognition-batch-size 128
```

If marker runs out of VRAM, lower or omit the batch sizes.

## Failure modes

- **Marker missing / wrong version**: tell the user to install marker per
  `.pi/cli/book-ingest/README.md`.
- **Hash + schema match, no `--force`**: report the skip; this is normal idempotence.
- **`schema_version` mismatch**: tool auto-forces re-ingest with a warning.
- **Quality status `failed`**: a section file or `_book.md` is missing.
  Inspect `.ingest/quality.json` and consider re-running with `--force-ocr`
  or `--llm text-only` / `--llm all` only when the user accepts the slow,
  metered LLM pass.
- **OSR detected** (`system: osr`): don't auto-convert. Flag it so the user
  can decide to run `/convert-monster`.

## Common chain

`ttrpg-import-book-pdf` → `ingest-worker` → inspect `.ingest/quality.json` if needed → `qmd update` → `qmd embed`.

## Reference

CLI contract: `.pi/cli/book-ingest/README.md`.
