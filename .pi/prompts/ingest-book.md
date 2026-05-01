---
name: ingest-book
description: Ingest a PDF book into the searchable vault library. Spawns the ingest-worker subagent.
thinking: medium
model: openai-codex/gpt-5.4-mini
skill: ttrpg-import-book-pdf
---

# /ingest-book

You are about to ingest a PDF book into the local vault library. Follow this exactly:

1. **Identify the input PDF.**
   - If the user named one (e.g. `imports/books/old-supplement.pdf`), use that.
   - Otherwise, list `imports/books/*.pdf` and ask which one.

2. **Check it isn't already ingested.**
   - Look at `vault/library/books/` for a matching slug.
   - If `<slug>/.ingest.json` exists with the same `source_hash` and the
     current `schema_version`, ask whether to re-run with `--force` or skip.

3. **Spawn the `ingest-worker` subagent** with this brief:
   ```
   Ingest imports/books/<filename>.pdf into vault/library/books/.
   Run: uv run --project .ttrpg/tools/book-ingest book-ingest --json imports/books/<filename>.pdf
   If the user asks for forced GPU or tuned ingest, add --device cuda and
   sensible batch-size flags.
   Marker LLM selector is --llm no|images-only|text-only|all. Use --llm no
   for fastest local ingest; --llm images-only for searchable image captions;
   --llm text-only for full Marker text/table/header/page LLM cleanup without
   image captions; --llm all for full cleanup plus captions. Any non-no mode
   requires OPENAI_API_KEY and is metered; images-only is roughly one vision
   call per detected Picture/Figure and does not LLM-process the JSON pass.
   The output is JSON; read fields: book_slug, section_count, page_count,
   plan_source, quality_status, warnings.
   Report back: slug, section count, plan source (pdf-outline / marker-json /
   whole-book), quality_status, top warnings (codes + targets), total time.
   ```
   Don't do this in the main context — the logs are too noisy.

4. **If `quality_status` is `review_required` or `failed`**, read
   `vault/library/books/<slug>/.ingest/quality.json` and surface the most
   important warnings to the user before continuing.

5. **After the subagent reports back**, refresh qmd:
   ```
   qmd update
   qmd embed
   ```

6. **Summarize for the user** in 4–6 lines: book slug, section count, plan
   source, system tag (osr/5e/unknown), output path, and whether embedding
   succeeded or failed.

If anything fails (marker missing, hash mismatch, bad PDF, quality_status
== failed), surface the error and stop.

User input: $@
