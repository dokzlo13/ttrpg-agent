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

The environment contract `.agents/env.sh` makes qmd project-local and registers three
non-overlapping collections:

| Collection | Source |
|---|---|
| `notes` | `vault/notes/` active authored campaign notes and prep |
| `books` | `vault/library/books/` generated book-ingest output |
| `archive` | `imports/source-vault/` optional legacy notes, excluded from default queries |

There is no generated qmd vault mirror. Everything lives under the single
project-local cache root, and the distinction that matters is **sub-path, not
directory**:

| Path | What it is | Rebuildable? |
|---|---|---|
| `.cache/xdg/qmd/index.sqlite*` | the search index | ✅ yes — `qmd update` / `qmd embed` |
| `.cache/index/index.yml` | collection config | ✅ yes — `env.sh` re-registers collections |
| `.cache/xdg/qmd/models/` | qmd GGUFs, ~2.2 GB | ❌ **re-download only** |
| `.cache/xdg/datalab/` | surya/Marker models, ~3.3 GB | ❌ **re-download only** |
| `.cache/huggingface`, `.cache/torch`, `.cache/uv`, `.cache/npm` | tool caches | ❌ expensive |
| `.cache/vendor/` | pinned upstream checkouts | ❌ re-clone + rebuild |

> The index and the 2.2 GB model store are **siblings** inside
> `.cache/xdg/qmd/`. Never delete that directory or sweep its contents — always
> name `index.sqlite*` explicitly.

There is no longer a `.qmd/` directory at the repo root, and no cache symlinks:
`XDG_CACHE_HOME` points at `.cache/xdg` directly.

## Normal refresh

Use after ordinary note edits, moves, deletions, or migrations:

```bash
.agents/bin/qmd update
.agents/bin/qmd status
```

This should remove deleted docs and index new/changed files.

## Refresh with embeddings

Use after significant new content, especially book ingests or large batches of notes:

```bash
.agents/bin/qmd update
.agents/bin/qmd embed
.agents/bin/qmd status
```

If embeddings are slow, tell the user before running `.agents/bin/qmd embed` unless they already asked for a full refresh.

## Verification checks

After structural changes or suspected duplication:

```bash
.agents/bin/qmd collection list
.agents/bin/qmd collection show notes
.agents/bin/qmd collection show books
.agents/bin/qmd collection show archive
.agents/bin/qmd ls notes | head
.agents/bin/qmd ls books | head
.agents/bin/qmd ls archive | head
```

Expected:

- `notes` path is `.../vault/notes`.
- `books` path is `.../vault/library/books`.
- `archive` path is `.../imports/source-vault` and is marked `[excluded]` in `.agents/bin/qmd collection list`.
- `.agents/bin/qmd ls notes` should not show `vault/library/books/...` files.
- `.agents/bin/qmd ls books` should show ingested book chunks when books have been ingested.

## Full rebuild

Use only when normal `.agents/bin/qmd update` leaves stale/deleted docs, collection paths are wrong, qmd errors,
or after major folder migrations:

```bash
# Named files only — models/ is a sibling in this directory.
rm -f .cache/xdg/qmd/index.sqlite .cache/xdg/qmd/index.sqlite-shm .cache/xdg/qmd/index.sqlite-wal
source ./.agents/env.sh
.agents/bin/qmd update
.agents/bin/qmd embed
.agents/bin/qmd status
```

This preserves `.cache/index/index.yml` configuration and every model store. Skip
`.agents/bin/qmd embed` when semantic search is not needed immediately.

## Index cleanup for broader data cleanup

If the user asks to "clean up", "wipe", "purge", or "reset" vault/import/index data, use
`ttrpg-system-data-cleanup` first. That skill defines destructive scopes, confirmation requirements, and
protected paths. This skill is for qmd health and rebuilds, not broad deletion.

For qmd specifically:

- **Search-index cleanup** deletes only `.cache/xdg/qmd/index.sqlite*` and preserves collection config and every model store.
- **All-index-caches cleanup** additionally deletes `.cache/index/index.yml`.
  This removes rebuildable qmd index/config state only. Everything else under `.cache/` — the model stores, the tool caches and `.cache/vendor/` — must survive, and `.agents/`, `.pi/` and `.claude/` are never touched. Discriminate by sub-path, not by root.
- After deleting vault notes, ingested books, or archive imports, run `.agents/bin/qmd update` so deleted docs
  disappear from search results.
- After deleting ingested books without re-ingesting, `.agents/bin/qmd embed` is usually unnecessary.
- Never delete `.agents/`, `.pi/`, or `.claude/` as qmd maintenance; qmd maintenance is limited to
  rebuildable qmd index/config state.

## Smoke test pattern

To verify indexing and cleanup behavior without leaving content behind:

```bash
printf '# QMD Smoke\n\nTemporary Dunemark smoke note.\n' > vault/notes/_qmd-smoke.md
.agents/bin/qmd update
.agents/bin/qmd search "Dunemark smoke" -c notes -n 3
rm vault/notes/_qmd-smoke.md
.agents/bin/qmd update
.agents/bin/qmd search "Dunemark smoke" -c notes -n 3
```

The first search should find the note; the final search should not.

## Common fixes

- **`Collection not found: books,notes`**: collection names were comma-joined. Use repeated flags:
  `.agents/bin/qmd query "term" -c books -c notes`.
- **Book chunks appear in `notes` results**: verify `.agents/bin/qmd collection show notes` points at `vault/notes`,
  not `vault`, then do a full rebuild.
- **Archive results appear unexpectedly**: run `.agents/bin/qmd collection exclude archive` and search archive only with `-c archive`.
- **No new notes appear**: run `.agents/bin/qmd update`; verify `.agents/bin/qmd collection show notes` points at `vault/notes`.
- **Semantic results are poor after ingest**: run `.agents/bin/qmd embed` after `.agents/bin/qmd update`.
