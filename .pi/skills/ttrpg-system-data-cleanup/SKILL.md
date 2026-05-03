---
name: ttrpg-system-data-cleanup
description: |
  Destructive cleanup/reset procedures for the gitignored TTRPG workspace data: qmd indexes, vault notes, ingested book output, and imports. Use when the user asks to clean up, wipe, reset, purge, or remove vault/import/index data. Requires explicit scope selection, dry-run inventory, and confirmation before deleting anything. Never touches .pi, .pi/cli, .pi/scripts, repo files, or Obsidian settings.
---

# ttrpg-system-data-cleanup

Use this skill for destructive cleanup of **local data** in this workspace. The goal is to remove user/imported/generated content while preserving the project machinery and the expected local data structure.

This skill is intentionally conservative. If the user says only "clean up the vault" or "reset data", **do not delete yet**: ask which scope they mean.

## Non-negotiable safety rules

1. **No deletion without explicit confirmation.** First present the chosen scope, exact paths, and a dry-run inventory. Then ask for confirmation using a phrase such as:
   - `CONFIRM CLEANUP search-index`
   - `CONFIRM CLEANUP ingested-books:the-pit-in-the-forest-v1-2-basic-bx`
   - `CONFIRM CLEANUP full-data-reset`
2. **Stay in the project root.** Verify `pwd` is `/path/to/ttrpg-agent` before running destructive commands.
3. **Only delete inside the allowed data roots** listed in this skill. Never use an unbounded `rm -rf`.
4. **Preserve container directories and settings.** Delete contents, not backbone folders. After destructive cleanup, recreate the expected folder structure with `mkdir -p`; do not rely on tracked placeholder files.
5. **Never touch protected paths** under this skill:
   - `.git/`
   - `.pi/` including skills, prompts, settings, extensions, npm cache
   - `.pi/cli/`
   - `.pi/scripts/`
   - `AGENTS.md`, `.gitignore`, README/package/config files
   - `vault/.obsidian/` unless the user explicitly asks outside this skill; default is always preserve it
6. **Prefer manifests over surprises.** Save a deletion manifest in `/tmp/` before deleting so the user can see what was targeted.
7. **Use `find ... -mindepth 1` for directory contents.** This avoids deleting required parent folders.
8. **Split destructive cleanup from qmd maintenance.** Do not run `rm`/`find -delete` and `qmd update` in the same shell command. First complete deletion, recreate/verify the structure, then run qmd refresh as a separate command. If qmd fails, do not repeat deletion blindly; inspect the current state first.

## Expected local data structure

The repo does not track local data folders. They may be absent, real directories, or symlinks. Tooling should tolerate this and recreate structure with:

```bash
mkdir -p \
  vault/notes \
  vault/notes/images \
  vault/notes/mechanics \
  vault/notes/readalouds \
  vault/library/books \
  imports/books \
  imports/source-vault \
  imports/5etools \
  imports/fvtt-data \
  .qmd/qmd \
  .cache/datalab/models \
  .cache/qmd/models \
  .cache/uv \
  .cache/huggingface \
  .cache/torch
```

## Cleanup scopes

When the user has not chosen a precise scope, offer these options:

| Scope | Deletes | Preserves / notes |
|---|---|---|
| `search-index` | `.qmd/qmd/` contents | Keeps qmd container structure. Rebuild with `qmd update`; run `qmd embed` only if semantic search is needed. Preserves `.cache/` model caches. |
| `all-index-caches` | Contents of `.qmd/` | Keeps `.qmd/` directory. This removes rebuildable qmd config/index state only. It **must preserve `.cache/`** so qmd/Marker/HuggingFace/torch/uv models are not re-downloaded. Does **not** touch `.pi/cli/`. |
| `active-notes` | Markdown/content under `vault/notes/` or a selected subfolder/file | Keeps `vault/`, `vault/notes/`, `vault/.obsidian/`. |
| `ingested-books` | Generated book folders under `vault/library/books/`, either all or selected slugs | Keeps `vault/library/books/` directory. Does not delete source PDFs in `imports/books/`. |
| `book-ingest-backups` | Stale `.<slug>.<timestamp>.bak` directories and `.<slug>.<timestamp>.bak.md` overview backups under `vault/library/books/` | Keeps current ingested content. These appear when book-ingest runs with `--keep-backup`. |
| `vault-content` | Active vault content: `vault/notes/`, `vault/library/books/`, and generated data folders such as `vault/images/` if present | Keeps `vault/`, `vault/.obsidian/`, `vault/notes/`, `vault/library/books/`; ask before deleting any unusual top-level vault folder. |
| `imports-books` | Source PDFs/EPUBs/etc. in `imports/books/`, either all or selected files | Keeps `imports/books/` directory. Does not remove ingested markdown; pair with `ingested-books` if desired. |
| `imports-source-vault` | Contents of `imports/source-vault/` | Keeps `imports/source-vault/` directory. This removes legacy archive material used for migrations. |
| `imports-5etools` | Contents of `imports/5etools/` | Keeps `imports/5etools/` directory if possible. Warn that canonical 5e lookup tools may stop working until the mirror is restored. |
| `imports-fvtt-data` | Foundry export JSON/TXT/ZIP files in `imports/fvtt-data/` | Keeps `imports/fvtt-data/` directory. |
| `imports-all` | Contents of `imports/books/`, `imports/source-vault/`, `imports/5etools/`, and `imports/fvtt-data/` | Keeps `imports/` and child directories. Warn about losing PDFs/archive/exports and disabling local 5etools. |
| `full-data-reset` | `vault-content` + `imports-all` + `all-index-caches` | Preserves all backbone folders/settings. Requires especially clear confirmation. |
| `custom-paths` | Only explicitly listed paths under allowed data roots | Refuse paths outside `vault/`, `imports/`, or `.qmd/`. |

Allowed data roots for deletion are only:

```text
vault/notes/
vault/library/books/
vault/images/                 # if present, generated images only
imports/books/
imports/source-vault/
imports/5etools/
imports/fvtt-data/
.qmd/
```

`.cache/` is intentionally not an allowed cleanup root in this skill. It holds reusable project-local qmd/Marker/HuggingFace/torch/uv model caches and should survive normal index, ingest, vault, and full-data resets.

Do not assume other `vault/*` folders are disposable. Inventory them and ask before touching them.

## Confirmation workflow

1. **Clarify scope.** If the user has not selected one of the scopes above, ask them to choose. Mention that multiple scopes can be combined.
2. **Inventory.** Run non-destructive commands only:

   ```bash
   pwd
   du -sh vault imports .qmd 2>/dev/null || true
   find <target> -mindepth 1 -maxdepth 2 -print 2>/dev/null | sort | head -200
   ```

   For selected book/import files, list the exact paths.
3. **Present plan.** Summarize:
   - scope(s)
   - paths to delete
   - paths explicitly preserved
   - expected follow-up (`qmd update`, `qmd embed`, or no index work)
4. **Ask for exact confirmation phrase.** Do not proceed on vague approval like "ok" if the paths are broad (`vault-content`, `imports-all`, `full-data-reset`).
5. **Execute only the confirmed scope.** Save a manifest in `/tmp/` immediately before deletion. Run the destructive phase as its own shell command.
6. **Recreate structure.** Ensure expected empty folders exist before doing any qmd work.
7. **Verify cleanup state.** List the remaining folder structure and confirm protected paths/imports still exist.
8. **Refresh or clear qmd as a separate step.** See [Post-cleanup qmd handling](#post-cleanup-qmd-handling).
9. **Report results.** Include deleted scope, manifest path, remaining structure paths, and any qmd command outcome.

## Command patterns

Use these patterns as templates. Adjust only after the user confirms the exact scope.

Important execution rule: **run cleanup commands and qmd commands separately**. A qmd error should not obscure whether deletion succeeded or leave the agent tempted to rerun a destructive block.

After every destructive block, run a separate verification command such as:

```bash
cd /path/to/ttrpg-agent
find vault -maxdepth 3 -mindepth 1 -type d -print 2>/dev/null | sort
find .qmd -maxdepth 3 -mindepth 1 -type d -print 2>/dev/null | sort
find .cache -maxdepth 3 -mindepth 1 -type d -print 2>/dev/null | sort
find imports -maxdepth 2 -mindepth 1 -type d -print 2>/dev/null | sort
```

To recreate the standard structure:

```bash
mkdir -p \
  vault/notes vault/notes/images vault/notes/mechanics vault/notes/readalouds \
  vault/library/books \
  imports/books imports/source-vault imports/5etools imports/fvtt-data \
  .qmd/qmd \
  .cache/datalab/models .cache/qmd/models .cache/uv .cache/huggingface .cache/torch
```

### Search index only

```bash
set -euo pipefail
cd /path/to/ttrpg-agent
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-search-index.txt"
find .qmd/qmd -mindepth 1 -print 2>/dev/null | sort > "$manifest" || true
if [ -d .qmd/qmd ]; then
  find .qmd/qmd -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
fi
mkdir -p .qmd/qmd .cache/datalab/models .cache/qmd/models .cache/uv .cache/huggingface .cache/torch
printf 'Manifest: %s\n' "$manifest"
```

Then, in a **separate** command, rebuild if the user wants search usable immediately:

```bash
cd /path/to/ttrpg-agent
source ./.pi/scripts/pi-shell.sh
qmd update
# qmd embed   # only when semantic search/vectors are needed
qmd status
```

### All index caches

```bash
set -euo pipefail
cd /path/to/ttrpg-agent
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-all-index-caches.txt"
find .qmd -mindepth 1 -print 2>/dev/null | sort > "$manifest" || true
find .qmd -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
mkdir -p .qmd/qmd .cache/datalab/models .cache/qmd/models .cache/uv .cache/huggingface .cache/torch
printf 'Manifest: %s\n' "$manifest"
```

### Active notes, selected subtree, or selected markdown files

For all active notes:

```bash
set -euo pipefail
cd /path/to/ttrpg-agent
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-active-notes.txt"
find vault/notes -mindepth 1 -print 2>/dev/null | sort > "$manifest" || true
find vault/notes -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
mkdir -p vault/notes vault/notes/images vault/notes/mechanics vault/notes/readalouds
printf 'Manifest: %s\n' "$manifest"
```

For selected markdown files, avoid broad globs in `rm`. Build and review the list first, then delete that exact list:

```bash
find vault/notes/<subfolder> -type f -name '*.md' -print | sort
# after confirmation:
find vault/notes/<subfolder> -type f -name '*.md' -delete
find vault/notes/<subfolder> -type d -empty -delete
mkdir -p vault/notes
```

### Ingested books

For all ingested books:

```bash
set -euo pipefail
cd /path/to/ttrpg-agent
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-ingested-books.txt"
find vault/library/books -mindepth 1 -print 2>/dev/null | sort > "$manifest" || true
find vault/library/books -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
mkdir -p vault/library/books
printf 'Manifest: %s\n' "$manifest"
```

For selected slugs, delete only exact directories that exist under `vault/library/books/`:

```bash
rm -rf -- vault/library/books/<confirmed-slug-1> vault/library/books/<confirmed-slug-2>
mkdir -p vault/library/books
```

### book-ingest backups

Stale `.<slug>.<timestamp>.bak` directories and
`.<slug>.<timestamp>.bak.md` overview files appear under `vault/library/books/`
when book-ingest is run with `--keep-backup` (or when backups from a failed/manual
run were preserved). The leading dot keeps qmd from indexing them, but they
still use disk.

Inventory first:

```bash
find vault/library/books -mindepth 1 -maxdepth 1 \( -type d -name '.*.bak' -o -type f -name '.*.bak.md' \) | sort
```

Drop all of them:

```bash
set -euo pipefail
cd /path/to/ttrpg-agent
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-book-ingest-backups.txt"
find vault/library/books -mindepth 1 -maxdepth 1 \( -type d -name '.*.bak' -o -type f -name '.*.bak.md' \) \
  -print 2>/dev/null | sort > "$manifest"
find vault/library/books -mindepth 1 -maxdepth 1 \( -type d -name '.*.bak' -o -type f -name '.*.bak.md' \) \
  -exec rm -rf -- {} +
printf 'Manifest: %s\n' "$manifest"
```

Drop a specific timestamped backup:

```bash
target="vault/library/books/.<slug>.<timestamp>.bak"
test -e "$target" || { echo "no such backup"; exit 1; }
rm -rf -- "$target"
```

No qmd refresh needed — the leading dot makes qmd's `**/*.md` glob
skip these directories.

### Vault content reset

Use this only after the user confirms `vault-content` or as part of `full-data-reset`.

When possible, recreate the existing empty folder structure under `vault/` so the workspace layout remains recognizable.

```bash
set -euo pipefail
cd /path/to/ttrpg-agent
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-vault-content.txt"
{
  find vault/notes -mindepth 1 -print 2>/dev/null || true
  find vault/library/books -mindepth 1 -print 2>/dev/null || true
  find vault/images -mindepth 1 -print 2>/dev/null || true
} | sort > "$manifest"
for dir in vault/notes vault/library/books vault/images; do
  if [ -d "$dir" ]; then
    find "$dir" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  fi
done
mkdir -p vault/notes vault/notes/images vault/notes/mechanics vault/notes/readalouds vault/library/books
printf 'Manifest: %s\n' "$manifest"
```

This intentionally preserves `vault/.obsidian/` and any unlisted top-level `vault/*` folder.

### Imports cleanup

Use the same content-only pattern. Examples:

```bash
# imports-books
find imports/books -mindepth 1 -print 2>/dev/null | sort > "$manifest" || true
find imports/books -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
mkdir -p imports/books

# imports-source-vault
find imports/source-vault -mindepth 1 -print 2>/dev/null | sort > "$manifest" || true
find imports/source-vault -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
mkdir -p imports/source-vault

# imports-fvtt-data
find imports/fvtt-data -mindepth 1 -print 2>/dev/null | sort > "$manifest" || true
find imports/fvtt-data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
mkdir -p imports/fvtt-data

# imports-5etools (warn first: canonical lookup may stop working)
find imports/5etools -mindepth 1 -print 2>/dev/null | sort > "$manifest" || true
find imports/5etools -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
mkdir -p imports/5etools
```

For `imports-all`, run all confirmed import cleanup blocks and then:

```bash
mkdir -p imports/books imports/source-vault imports/5etools imports/fvtt-data
```

### Full data reset

A full reset is just the confirmed combination of:

1. `vault-content`
2. `imports-all`
3. `all-index-caches`

Run the individual blocks, not a single `rm -rf vault imports .qmd`. Recreate the structure afterwards:

```bash
mkdir -p vault/notes vault/notes/images vault/notes/mechanics vault/notes/readalouds vault/library/books
mkdir -p imports/books imports/source-vault imports/5etools imports/fvtt-data
mkdir -p .qmd/qmd .cache/datalab/models .cache/qmd/models .cache/uv .cache/huggingface .cache/torch
```

## Post-cleanup qmd handling

Run qmd handling only after the destructive block has completed and the structure has been verified. Use a separate shell command:

```bash
cd /path/to/ttrpg-agent
source ./.pi/scripts/pi-shell.sh
qmd update
qmd status
```

- If only `search-index` or `all-index-caches` was cleaned, run `qmd update` to recreate index state if the user wants search available immediately.
- If vault notes or ingested books were deleted, run `qmd update` so deleted docs disappear from search.
- If ingested books were deleted and not immediately re-ingested, `qmd embed` is usually unnecessary.
- If `imports/source-vault` was deleted, run `qmd update` so the `archive` collection stops returning stale docs.
- If `imports-5etools` was deleted, do **not** try to fix with qmd; tell the user local 5etools-backed creature/spell/item lookup may fail until the mirror is restored.
- If `qmd update` or `qmd status` fails after cleanup, report that qmd refresh failed, show the error, verify the structure again, and stop. Do not rerun the destructive cleanup block unless the user reconfirms it.

See also `ttrpg-system-qmd-maintenance` for normal qmd rebuild and verification commands. Use that skill instead when the request is only "refresh/rebuild search" rather than destructive cleanup.

## Refusals and escalation

Refuse or re-clarify if:

- The user asks to delete `.pi/`, `.pi/cli/`, `.pi/scripts/`, or repo configuration as part of "cleanup".
- A custom path resolves outside the allowed data roots.
- The user gives a broad destructive request but will not provide an exact confirmation phrase.
- The requested cleanup would remove `vault/.obsidian/`; explain that this skill preserves Obsidian settings and ask for a separate explicit maintenance task if truly needed.
