---
name: ttrpg-library-search
description: |
  Hybrid qmd search across ingested books, active vault notes, and optional
  legacy archive prose. Use for "find/remember/where is this discussed" tasks
  whose answer is a passage, scene, lore note, or campaign note. For canonical
  creature/spell/item/class mechanics, query 5etools first.
---

# ttrpg-library-search

## When to use this skill

Use it for **prose lookup** — anywhere the answer is a passage, scene, statblock,
or note rather than a structured record. Examples:

- "Where is the *mind flayer* discussed in my supplements?"
- "Find every read-aloud about a forest at night."
- "Which adventures have a haunted lighthouse?"
- "Did I write notes about the Blackthorne family already?"

**Use `ttrpg-rules-5etools-query` + `query_5etools` first** when the question is a structured canonical filter:
"every CR 5–7 fey in MM", "all 3rd-level evocation spells", "rare weapons".
Use `ttrpg-rules-5etools-native` for class/subclass/feat/background records. qmd is bad at structured filtering.

## Three collections, one tool

| Collection | What's in it | When to query |
|---|---|---|
| `books` | Ingested PDFs under `vault/library/books/` | "find … in my books" |
| `notes` | Active authored notes under `vault/notes/` | "did I already write …" |
| `archive` | Optional read-only legacy vault under `imports/source-vault/` | only when user explicitly asks for old notes |

Default: query `books` first for book/library questions, and `notes` for campaign-note questions. If you need more than one collection, repeat `-c`; **do not comma-join collection names**. Add `archive` only on explicit ask.

## Modes

qmd supports three search modes. Pick the cheapest one that answers the question:

1. **`qmd search <terms>`** — BM25 keyword search. Fast and cheap.
   First choice for proper-noun lookups ("Vecna", "Dunemark", "Blackthorne").
2. **`qmd query "<question>"`** — hybrid search with query expansion + vector + rerank.
   Use for fuzzy semantic questions where keywords aren't obvious, or after BM25 misses.
3. **`qmd vsearch "<phrase>"`** — pure vector. Rarely the best choice; reach
   for it only when keyword search misses and the question is conceptual.

After a hit, **always `qmd get <doc-id>`** to pull the full surrounding chunk
before quoting or summarizing. The list output gives titles, not bodies.

## Concrete invocations

```bash
# Cheap keyword pass first
qmd search "mind flayer ceremorphosis" -c books

# Hybrid/semantic pass when keywords are vague
qmd query "scene where players are interrogated by guards" -c books

# Combine collections by repeating -c; comma-joined names are invalid
qmd query "Dunemark" -c books -c notes

# Pull full chunk
qmd get <doc-id>
```

If nothing relevant is returned, **say so** rather than fabricating. Suggest the
user might need to ingest the relevant book.

## Index location & freshness

The index lives at `.qmd/`. Inside pi, plain `qmd` commands are made
project-local automatically via `shellCommandPrefix` loading `.pi/scripts/pi-shell.sh`.
After ingesting a book or making large note edits, run `qmd update` to refresh
the index.

For major moves/deletions, stale results, collection path problems, duplicated book chunks,
or full rebuilds, read `ttrpg-system-qmd-maintenance`.

## Don't

- Don't paste a 10-page chunk into the response. Summarize, cite the doc-id,
  and offer to pull more if needed.
- Don't claim something "isn't in the books" without trying both `query` and
  `search`. They surface different things.
