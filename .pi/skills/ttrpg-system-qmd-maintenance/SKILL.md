---
name: ttrpg-system-qmd-maintenance
description: |
  Maintain the local qmd index after major vault/import moves, deletions, book
  ingests, or qmd oddities. Use when the user asks to reindex, refresh search,
  rebuild the database, fix stale qmd results, or verify collections.
---

# ttrpg-system-qmd-maintenance

Use this skill when search results are stale, documents were moved/deleted, book ingests changed,
or the user asks to "reindex", "refresh qmd", "rebuild search", or "fix the database".

## Project qmd shape

The shell wrapper `.pi/scripts/pi-shell.sh` makes qmd project-local and registers three
non-overlapping collections:

| Collection | Source |
|---|---|
| `notes` | `vault/notes/` active authored campaign notes and prep |
| `books` | `vault/library/books/` generated book-ingest output |
| `archive` | `imports/source-vault/` optional legacy notes, excluded from default queries |

There is no generated qmd vault mirror. `.qmd/` is rebuildable qmd config/index state. Reusable qmd/Marker/HuggingFace/torch/uv model caches live under project-local `.cache/` and should survive normal qmd/index wipeouts.

## Normal refresh

Use after ordinary note edits, moves, deletions, or migrations:

```bash
qmd update
qmd status
```

This should remove deleted docs and index new/changed files.

## Refresh with embeddings

Use after significant new content, especially book ingests or large batches of notes:

```bash
qmd update
qmd embed
qmd status
```

If embeddings are slow, tell the user before running `qmd embed` unless they already asked for a full refresh.

## Verification checks

After structural changes or suspected duplication:

```bash
qmd collection list
qmd collection show notes
qmd collection show books
qmd collection show archive
qmd ls notes | head
qmd ls books | head
qmd ls archive | head
```

Expected:

- `notes` path is `.../vault/notes`.
- `books` path is `.../vault/library/books`.
- `archive` path is `.../imports/source-vault` and is marked `[excluded]` in `qmd collection list`.
- `qmd ls notes` should not show `vault/library/books/...` files.
- `qmd ls books` should show ingested book chunks when books have been ingested.

## Full rebuild

Use only when normal `qmd update` leaves stale/deleted docs, collection paths are wrong, qmd errors,
or after major folder migrations:

```bash
rm -rf .qmd/qmd
source ./.pi/scripts/pi-shell.sh
qmd update
qmd embed
qmd status
```

Notes:

- This preserves project-local `.cache/` model/cache data.
- It should not re-download/rebuild qmd or Marker models unless `.cache/` was manually removed outside normal maintenance.
- If semantic search is not needed immediately, skip `qmd embed` and mention that vectors are stale/missing.

## Index cleanup for broader data cleanup

If the user asks to "clean up", "wipe", "purge", or "reset" vault/import/index data, use
`ttrpg-system-data-cleanup` first. That skill defines destructive scopes, confirmation requirements, and
protected paths. This skill is for qmd health and rebuilds, not broad deletion.

For qmd specifically:

- **Search-index cleanup** deletes only `.qmd/qmd/`.
  This is the preferred destructive qmd reset and preserves `.cache/` uv/datalab/qmd model caches.
- **All-index-caches cleanup** deletes the contents of `.qmd/`.
  This removes rebuildable qmd config/index state but still preserves `.cache/`; it should not force model re-downloads. It still must not touch `.pi/cli/`, `.pi/scripts/`, or `.cache/`.
- After deleting vault notes, ingested books, or archive imports, run `qmd update` so deleted docs
  disappear from search results.
- After deleting ingested books without re-ingesting, `qmd embed` is usually unnecessary.
- Never delete `.pi/` as qmd maintenance; qmd maintenance is limited to `.qmd/`.

## Smoke test pattern

To verify indexing and cleanup behavior without leaving content behind:

```bash
printf '# QMD Smoke\n\nTemporary Dunemark smoke note.\n' > vault/notes/_qmd-smoke.md
qmd update
qmd search "Dunemark smoke" -c notes -n 3
rm vault/notes/_qmd-smoke.md
qmd update
qmd search "Dunemark smoke" -c notes -n 3
```

The first search should find the note; the final search should not.

## Common fixes

- **`Collection not found: books,notes`**: collection names were comma-joined. Use repeated flags:
  `qmd query "term" -c books -c notes`.
- **Book chunks appear in `notes` results**: verify `qmd collection show notes` points at `vault/notes`,
  not `vault`, then do a full rebuild.
- **Archive results appear unexpectedly**: run `qmd collection exclude archive` and search archive only with `-c archive`.
- **No new notes appear**: run `qmd update`; verify `qmd collection show notes` points at `vault/notes`.
- **Semantic results are poor after ingest**: run `qmd embed` after `qmd update`.
