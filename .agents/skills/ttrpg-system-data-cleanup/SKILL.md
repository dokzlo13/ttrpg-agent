---
name: ttrpg-system-data-cleanup
description: |
  Destructive cleanup/reset procedures for the gitignored TTRPG workspace data: qmd indexes, vault notes, ingested book output, imports, and the session corpus (recordings, datasets, rendered transcripts). Use when the user asks to clean up, wipe, reset, purge, or remove vault/import/index/session data. Requires explicit scope selection, dry-run inventory, and confirmation before deleting anything. Discriminates by sub-path inside .cache: the qmd index is deletable, but the 5.5 GB of model weights beside it, the pinned vendor checkouts, .agents/state, and the irreplaceable Craig source recordings are not. Never touches .agents (the canonical project toolchain), .pi, .claude, repo files, or Obsidian settings.
---

# ttrpg-system-data-cleanup

Use this skill for destructive cleanup of **local data** in this workspace. The goal is to remove user/imported/generated content while preserving the project machinery and the expected local data structure.

This skill is intentionally conservative. If the user says only "clean up the vault" or "reset data", **do not delete yet**: ask which scope they mean.

## Non-negotiable safety rules

1. **No deletion without explicit confirmation.** First present the chosen scope, exact paths, and a dry-run inventory. Then ask for confirmation using a phrase such as:
   - `CONFIRM CLEANUP search-index`
   - `CONFIRM CLEANUP ingested-books:the-pit-in-the-forest-v1-2-basic-bx`
   - `CONFIRM CLEANUP full-data-reset`
   - `CONFIRM CLEANUP session-corpus:2026-08-08` — and this one needs a **second**
     acknowledgement; see [Retiring a whole session](#retiring-a-whole-session-session-corpusid)
2. **Stay in the project root.** Verify `pwd` is `/path/to/ttrpg-agent` before running destructive commands.
3. **Only delete inside the allowed data roots** listed in this skill. Never use an unbounded `rm -rf`.
4. **Preserve container directories and settings.** Delete contents, not backbone folders. After destructive cleanup, recreate the expected folder structure with `mkdir -p`; do not rely on tracked placeholder files.
5. **Never touch protected paths** under this skill:
   - `.git/`
   - `.agents/` — the canonical project toolchain: `env.sh`, `bin/`, `cli/`, `skills/`,
     `prompts/`, `chains/`, `harness`, `manifest.toml`, and `state/`
   - `.pi/` and `.claude/` — harness adapters, settings, extensions, npm cache
   - `AGENTS.md`, `CLAUDE.md`, `.gitignore`, README/package/config files
   - `vault/.obsidian/` unless the user explicitly asks outside this skill; default is always preserve it
   - `.cache/sessions/datasets/*/source/` and the dataset's own record files
     (`dataset.json`, `segments.jsonl`, `meta.json`, `speech.jsonl`) — the
     original Craig recording and its verbatim transcription. Craig expires
     recordings, so these are **not re-downloadable**
   - `vault/transcripts/_speakers.yaml` and `vault/transcripts/_lexicon.yaml` —
     hand-maintained user data that grows session over session
6. **Prefer manifests over surprises.** Save a deletion manifest in `/tmp/` before deleting so the user can see what was targeted.
7. **Use `find ... -mindepth 1` for directory contents.** This avoids deleting required parent folders.
8. **Split destructive cleanup from qmd maintenance.** Do not run `rm`/`find -delete` and `.agents/bin/qmd update` in the same shell command. First complete deletion, recreate/verify the structure, then run qmd refresh as a separate command. If qmd fails, do not repeat deletion blindly; inspect the current state first.
9. **Never `rm` inside a dataset directory.** Reclaiming the ~2 GB of decoded
   audio and STT cache per recording is `.agents/bin/session-ingest prune`,
   which drives the SDK's `prune()`, keeps the dataset's own record files
   consistent, and refuses while the session chronicle is still unwritten. A
   hand-rolled `rm` there is one slip away from the irreplaceable class.

## Expected local data structure

The repo does not track local data folders. They may be absent, real directories, or symlinks. Tooling should tolerate this and recreate structure with:

The reliable way to recreate it is to source the environment contract, which
owns the canonical skeleton and creates exactly the right directories:

```bash
source ./.agents/env.sh
```

For reference, that produces:

```text
vault/notes{,/images,/mechanics,/readalouds}
vault/library/books
vault/transcripts     # rendered session transcripts + the two _*.yaml files
imports/books  imports/source-vault  imports/fvtt-data
.cache/index          # QMD_CONFIG_DIR  — qmd index.yml
.cache/xdg            # XDG_CACHE_HOME  — qmd index.sqlite + model stores
.cache/sessions       # session corpus root
.cache/sessions/datasets   # CRAIG_STT_WORK_DIR — recordings and datasets
.cache/craig-stt      # whisper checkpoints (~3 GB, expensive class)
.cache/uv  .cache/npm  .cache/huggingface  .cache/torch
.cache/vendor         # pinned upstream checkouts
.agents/state         # durable agent state (never a cleanup target)
```

Do **not** hand-write a `mkdir -p` list here: it drifts from `env.sh` and the
old one recreated pre-migration paths (`.qmd/qmd`, `.cache/qmd/models`) that no
longer exist. `.cache/vendor/*` is deliberately not created empty — those are
git checkouts managed by `.agents/harness vendor sync`.

## Cleanup scopes

When the user has not chosen a precise scope, offer these options:

| Scope | Deletes | Preserves / notes |
|---|---|---|
| `search-index` | `.cache/xdg/qmd/index.sqlite*` — the **files only** | Keeps `.cache/index/index.yml` (collection config) and every model store. Rebuild with `.agents/bin/qmd update`; run `.agents/bin/qmd embed` only if semantic search is needed. |
| `all-index-caches` | `.cache/xdg/qmd/index.sqlite*` **plus** `.cache/index/index.yml` | Removes rebuildable qmd index *and* collection config. `env.sh` re-registers the collections on the next `.agents/bin/qmd` call. Must preserve every model store. Does **not** touch `.agents/`, `.pi/`, or `.claude/`. |
| `active-notes` | Markdown/content under `vault/notes/` or a selected subfolder/file | Keeps `vault/`, `vault/notes/`, `vault/.obsidian/`. |
| `ingested-books` | Generated book folders under `vault/library/books/`, either all or selected slugs | Keeps `vault/library/books/` directory. Does not delete source PDFs in `imports/books/`. |
| `book-ingest-backups` | Stale `.<slug>.<timestamp>.bak` directories and `.<slug>.<timestamp>.bak.md` overview backups under `vault/library/books/` | Keeps current ingested content. These appear when book-ingest runs with `--keep-backup`. |
| `vault-content` | Active vault content: `vault/notes/`, `vault/library/books/`, and generated data folders such as `vault/images/` if present | Keeps `vault/`, `vault/.obsidian/`, `vault/notes/`, `vault/library/books/`, and **`vault/transcripts/`** — rendered transcripts are session-corpus output with their own scope, not general vault content. Ask before deleting any unusual top-level vault folder. |
| `imports-books` | Source PDFs/EPUBs/etc. in `imports/books/`, either all or selected files | Keeps `imports/books/` directory. Does not remove ingested markdown; pair with `ingested-books` if desired. |
| `imports-source-vault` | Contents of `imports/source-vault/` | Keeps `imports/source-vault/` directory. This removes legacy archive material used for migrations. |
| `imports-fvtt-data` | Foundry export JSON/TXT/ZIP files in `imports/fvtt-data/` | Keeps `imports/fvtt-data/` directory. |
| `imports-all` | Contents of `imports/books/`, `imports/source-vault/`, and `imports/fvtt-data/` | Keeps `imports/` and child directories. Warn about losing PDFs/archive/exports. Does **not** include the 5etools mirror — that is a vendored dependency, not user imports. |
| `transcripts-rendered` | Rendered transcript folders `vault/transcripts/<session-id>/`, all or selected sessions | Keeps `vault/transcripts/` and the two hand-maintained `_speakers.yaml` / `_lexicon.yaml` files — those are refused under **every** scope. Rebuild with `.agents/bin/session-ingest render <session-id>`; the dataset it renders from must still exist. Follow with `.agents/bin/qmd update`. |
| `session-derived` | API-regenerable session sidecars: the contents of `.cache/sessions/<id>/` (`provenance.json`, `inputs/`, `runs/`, `turns.class.jsonl`, `anchors.json`, `extraction.json`, `record.json`, `recap.draft.md`, `index.sqlite`) plus `.cache/sessions/scratch/` | Never touches `.cache/sessions/datasets/`. Regenerating costs OpenAI spend for the metered stages (`segment`, `extract`, `recap`, `glossary`). Deleting `anchors.json` invalidates every evidence link already cited in the vault until `render` re-runs, so prefer a named session over "all". |
| `session-audio-prune` | GPU-regenerable dataset internals: `datasets/<recording_id>/pcm/`, `stt/`, `source/extracted/` — ~2 GB per session | **Not an `rm` scope.** Run `.agents/bin/session-ingest prune [--dry-run]`, which drives the SDK's `prune()` and refuses while the session chronicle is unwritten. This skill never hand-deletes inside a dataset directory. Re-deriving costs one GPU pass (~10 min per 3 h session), no API spend. |
| `session-corpus:<id>` | **Everything** for one session: its dataset including `source/` (the original Craig archive), `.cache/sessions/<id>/`, and `vault/transcripts/<id>/` | **Nuclear, double confirmation.** See [Retiring a whole session](#retiring-a-whole-session-session-corpusid). Never implied by any other scope, never part of `full-data-reset`, one session id at a time. |
| `full-data-reset` | `vault-content` + `imports-all` + `all-index-caches` | Preserves all backbone folders/settings, every model store, all vendored checkouts, and the **entire session corpus** (`.cache/sessions/`, `.cache/craig-stt/`, `vault/transcripts/`). Requires especially clear confirmation. |
| `vendor-checkouts` | `.cache/vendor/<name>/` for an explicitly named vendor | **Separate opt-in scope. Never part of an index, imports, or full-data reset.** Re-clonable with `.agents/harness vendor sync <name>`, but 5etools is ~190 MB and foundry-vtt-mcp needs an `npm ci` rebuild. Warn that canonical 5e lookup stops working until restored. |
| `tool-environments` | `.agents/cli/*/.venv` and `.agents/cli/*/node_modules` | Rebuildable with `.agents/harness bootstrap --apply`. Warn that book-ingest's venv pulls multi-GB torch/CUDA wheels back down. Never part of any data reset. |
| `custom-paths` | Only explicitly listed paths under allowed data roots | Refuse anything outside the allowed roots below, and refuse the expensive and irreplaceable classes **even when explicitly listed**. |

### What lives under `.cache/`, and what may be deleted

Moving the qmd index under `.cache/` means directory-level rules are no longer
safe. Classify by sub-path, never by root:

| Class | Paths | Cleanup policy |
|---|---|---|
| **Rebuildable — safe to delete** | `.cache/xdg/qmd/index.sqlite*`, `.cache/index/index.yml` | The only things `search-index` / `all-index-caches` may touch. |
| **Expensive — must survive every scope** | `.cache/xdg/qmd/models/` (~2.2 GB), `.cache/xdg/datalab/` (~3.3 GB), `.cache/craig-stt/` (whisper checkpoints, ~3 GB), `.cache/huggingface/`, `.cache/torch/`, `.cache/uv/`, `.cache/npm/` | Never deletable by any named scope. **Refuse even under `custom-paths`.** |
| **Vendored — costly to restore** | `.cache/vendor/5etools/`, `.cache/vendor/foundry-vtt-mcp/` | `vendor-checkouts` scope only, one named vendor at a time. |
| **Durable state — never a target** | `.agents/state/` (holds the live Foundry GM credential) | Not deletable by this skill under any scope, including `custom-paths`. |
| **Session corpus — not one class** | `.cache/sessions/**` | Splits three ways, from irreplaceable to freely regenerable. Classify with the table in [The session corpus](#the-session-corpus) below before touching anything under it. |

> **The single most dangerous mistake available in this repo.**
> `.cache/xdg/qmd/` contains **both** the rebuildable `index.sqlite` **and** the
> 2.2 GB `models/` store. Never delete that *directory* or its contents
> wholesale. Always target `index.sqlite*` as named files:
>
> ```bash
> rm -f .cache/xdg/qmd/index.sqlite .cache/xdg/qmd/index.sqlite-shm .cache/xdg/qmd/index.sqlite-wal
> ```
>
> A `find .cache/xdg/qmd -mindepth 1 -delete`-shaped command costs hours of
> re-downloading. Likewise, "allow `.cache/` as a cleanup root" is **not** an
> acceptable repair for any scope: it would let a routine index reset destroy
> 5.5 GB of model weights.

### The session corpus

A recorded session spreads across three places — the dataset under
`.cache/sessions/datasets/`, the derived sidecars under `.cache/sessions/<id>/`,
and the rendered markdown under `vault/transcripts/`. They look alike on disk
and are worth wildly different amounts. Classify by sub-path here too:

| Class | Paths | Cleanup policy |
|---|---|---|
| **Irreplaceable user data** | `.cache/sessions/datasets/*/source/` (the original Craig archive) and the dataset's own records: `*/dataset.json`, `*/segments.jsonl`, `*/meta.json`, `*/speech.jsonl` | **Refused under every scope, including `custom-paths`.** Craig expires recordings; once the archive is gone the session cannot be re-transcribed at any price. The only exception is the nuclear `session-corpus:<id>`, which exists precisely so this can never happen by accident. |
| **Irreplaceable user data** | `vault/transcripts/_speakers.yaml`, `vault/transcripts/_lexicon.yaml` | **Refused under every scope, no exception** — not even `session-corpus:<id>`, which is per-session while these are cumulative. Hand-maintained, grown session over session, and the lexicon is also the correction dictionary every future render depends on. |
| **Regenerable at GPU cost** | `.cache/sessions/datasets/*/pcm/` (~1.9 GB), `*/stt/`, `*/source/extracted/` | Only via `.agents/bin/session-ingest prune` (the SDK's `prune()`), **never `rm`**. Scope name: `session-audio-prune`. |
| **Regenerable at API cost** | `.cache/sessions/<id>/` sidecars, `<id>/index.sqlite`, `.cache/sessions/scratch/` | Scope `session-derived`. Costs OpenAI spend to rebuild, nothing else. |
| **Rendered output** | `vault/transcripts/<session-id>/` | Scope `transcripts-rendered`; rebuild with `.agents/bin/session-ingest render <session-id>`. |

> **Deleting inside WSL does not give the disk back.** The ext4 VHDX grows and
> never shrinks on its own, so `du` will drop by 2 GB and Windows will report no
> change. Say this when reporting a large session cleanup, so nobody deletes
> more looking for the space. Reclaiming it is a separate, deliberate
> `wsl --shutdown` + `Optimize-VHD` / `diskpart compact vdisk` operation on the
> Windows side — outside this skill.

Allowed data roots for deletion are only:

```text
vault/notes/
vault/library/books/
vault/images/                       # if present, generated images only
vault/transcripts/<session-id>/     # rendered chunks only; never the _*.yaml files
imports/books/
imports/source-vault/
imports/fvtt-data/
.cache/xdg/qmd/index.sqlite*        # named files only, never the directory
.cache/index/index.yml              # named file only
.cache/sessions/<session-id>/       # derived sidecars only
.cache/sessions/scratch/            # disposable A/B re-transcription work dirs
```

Note what is **not** in that list: `.cache/sessions/datasets/` in any form.
Dataset contents are reached only through `session-ingest prune`, or through the
`session-corpus:<id>` scope.

Everything else under `.cache/` — and all of `.agents/` — is out of scope
except through the explicit `vendor-checkouts`, `tool-environments` and
`session-corpus:<id>` scopes, each of which must be requested by name.

Do not assume other `vault/*` folders are disposable. Inventory them and ask before touching them.

## Confirmation workflow

1. **Clarify scope.** If the user has not selected one of the scopes above, ask them to choose. Mention that multiple scopes can be combined.
2. **Inventory.** Run non-destructive commands only:

   ```bash
   pwd
   du -sh vault imports .cache/index .cache/xdg/qmd 2>/dev/null || true
   find <target> -mindepth 1 -maxdepth 2 -print 2>/dev/null | sort | head -200
   ```

   Record the size of the expensive class before and after, so a regression is
   visible rather than silent:

   ```bash
   du -sh .cache/xdg/qmd/models .cache/xdg/datalab .cache/craig-stt .cache/vendor 2>/dev/null
   ```

   For any session scope, inventory the corpus by class as well — the numbers
   are what make "2 GB of prunable audio" and "1.2 GB you can never get back"
   distinguishable to the user:

   ```bash
   du -sh .cache/sessions .cache/sessions/datasets vault/transcripts 2>/dev/null
   du -sh .cache/sessions/datasets/*/source .cache/sessions/datasets/*/pcm 2>/dev/null
   .agents/bin/session-ingest doctor --json      # roots, disk by class, per-session state
   ```

   For selected book/import files, list the exact paths.
3. **Present plan.** Summarize:
   - scope(s)
   - paths to delete
   - paths explicitly preserved
   - expected follow-up (`.agents/bin/qmd update`, `.agents/bin/qmd embed`, or no index work)
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
find .cache -maxdepth 3 -mindepth 1 -type d -print 2>/dev/null | sort
find imports -maxdepth 2 -mindepth 1 -type d -print 2>/dev/null | sort
# The expensive class must be untouched by every scope:
du -sh .cache/xdg/qmd/models .cache/xdg/datalab .cache/craig-stt .cache/vendor 2>/dev/null
# And so must the irreplaceable class, whatever the scope was:
ls .cache/sessions/datasets/*/source vault/transcripts/_*.yaml 2>/dev/null
```

To recreate the standard structure, source the environment contract — it owns
the canonical skeleton, so there is no second list to keep in sync:

```bash
source ./.agents/env.sh
```

### Search index only

```bash
set -euo pipefail
cd /path/to/ttrpg-agent
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-search-index.txt"
# Named files only. .cache/xdg/qmd/ also holds models/ (~2.2 GB) — never delete
# the directory or sweep its contents.
find .cache/xdg/qmd -maxdepth 1 -type f -name 'index.sqlite*' -print | sort > "$manifest"
rm -f .cache/xdg/qmd/index.sqlite .cache/xdg/qmd/index.sqlite-shm .cache/xdg/qmd/index.sqlite-wal
printf 'Manifest: %s\n' "$manifest"
```

Then, in a **separate** command, rebuild if the user wants search usable immediately:

```bash
cd /path/to/ttrpg-agent
source ./.agents/env.sh
.agents/bin/qmd update
# .agents/bin/qmd embed   # only when semantic search/vectors are needed
.agents/bin/qmd status
```

### All index caches

```bash
set -euo pipefail
cd /path/to/ttrpg-agent
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-all-index-caches.txt"
# Index sqlite + collection config, as named files. Nothing else under .cache/
# is in scope: .cache/xdg/qmd/models, .cache/xdg/datalab, huggingface, torch,
# uv, npm and vendor/ all stay.
{
  find .cache/xdg/qmd -maxdepth 1 -type f -name 'index.sqlite*' -print 2>/dev/null
  find .cache/index -maxdepth 1 -type f -name 'index.yml' -print 2>/dev/null
} | sort > "$manifest" || true
rm -f .cache/xdg/qmd/index.sqlite .cache/xdg/qmd/index.sqlite-shm .cache/xdg/qmd/index.sqlite-wal
rm -f .cache/index/index.yml
printf 'Manifest: %s\n' "$manifest"
```

`env.sh` re-registers the `notes` / `books` / `archive` collections on the next
`.agents/bin/qmd` call, so removing `index.yml` is recoverable. Confirm the
expensive class is intact before rebuilding:

```bash
du -sh .cache/xdg/qmd/models .cache/xdg/datalab
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
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-ingested-books-selected.txt"
printf '%s\n' vault/library/books/<confirmed-slug-1> vault/library/books/<confirmed-slug-2> > "$manifest"
rm -rf -- vault/library/books/<confirmed-slug-1> vault/library/books/<confirmed-slug-2>
mkdir -p vault/library/books
printf 'Manifest: %s\n' "$manifest"
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
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-book-ingest-backup-single.txt"
printf '%s\n' "$target" > "$manifest"
rm -rf -- "$target"
printf 'Manifest: %s\n' "$manifest"
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

This intentionally preserves `vault/.obsidian/`, `vault/transcripts/`, and any
unlisted top-level `vault/*` folder. Rendered transcripts are the session
corpus's output, not general vault content — they have their own
`transcripts-rendered` scope and must be asked for by name.

### Imports cleanup

Use the same content-only pattern. Examples:

Unlike the blocks above, these previously referenced `$manifest` without ever
setting it — which wrote no manifest and *still deleted*. Every destructive
block must define its own `stamp`/`manifest`. Run one scope at a time:

```bash
set -uo pipefail
cd /path/to/ttrpg-agent
scope="imports-books"          # or imports-source-vault | imports-fvtt-data
case "$scope" in
  imports-books)        target="imports/books" ;;
  imports-source-vault) target="imports/source-vault" ;;
  imports-fvtt-data)    target="imports/fvtt-data" ;;
  *) echo "unknown scope: $scope" >&2; exit 2 ;;
esac
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-${scope}.txt"
find "$target" -mindepth 1 -print 2>/dev/null | sort > "$manifest" || true
test -s "$manifest" || { echo "nothing to delete in $target"; exit 0; }
find "$target" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
mkdir -p "$target"
printf 'Manifest: %s\n' "$manifest"
```

For `imports-all`, run all confirmed import cleanup blocks and then:

```bash
mkdir -p imports/books imports/source-vault imports/fvtt-data
```

The 5etools mirror is **not** part of any imports scope. It is a pinned vendor
checkout under `.cache/vendor/5etools`, not user-supplied material.

### Rendered transcripts

`transcripts-rendered` deletes whole `vault/transcripts/<session-id>/` folders
and nothing else. The `_*.yaml` files sit one level up and are excluded by the
`-mindepth 1 -maxdepth 1 -type d` shape below — keep it, do not "simplify" it
into a glob that would sweep them in.

```bash
set -uo pipefail
cd /path/to/ttrpg-agent
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-transcripts-rendered.txt"
# Directories only: _speakers.yaml and _lexicon.yaml are files and stay.
find vault/transcripts -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null | sort > "$manifest" || true
test -s "$manifest" || { echo "no rendered transcripts"; exit 0; }
find vault/transcripts -mindepth 1 -maxdepth 1 -type d -exec rm -rf -- {} +
mkdir -p vault/transcripts
printf 'Manifest: %s\n' "$manifest"
```

For selected sessions, name the exact directories instead:

```bash
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-transcripts-rendered-selected.txt"
printf '%s\n' vault/transcripts/<confirmed-session-id> > "$manifest"
rm -rf -- vault/transcripts/<confirmed-session-id>
printf 'Manifest: %s\n' "$manifest"
```

Then, as a separate command, refresh the index so the `transcripts` collection
stops returning deleted chunks:

```bash
cd /path/to/ttrpg-agent
.agents/bin/qmd update
```

Verify the hand-maintained files survived:

```bash
ls -l vault/transcripts/_speakers.yaml vault/transcripts/_lexicon.yaml
```

### Derived session data

`session-derived` removes the sidecars under `.cache/sessions/<id>/` — what
`segment`, `extract`, `recap` and `record` produced, plus the disposable
`index.sqlite` and the `scratch/` work dirs. `.cache/sessions/datasets/` is not
in scope and must not appear in the manifest.

Prefer one named session. Deleting `anchors.json` breaks every evidence link
already cited in the vault until `render` runs again, so a blanket wipe across
all sessions is rarely what the user means — confirm that explicitly.

```bash
set -uo pipefail
cd /path/to/ttrpg-agent
id="<confirmed-session-id>"
test -d ".cache/sessions/$id" || { echo "no such session: $id"; exit 1; }
case "$id" in datasets|scratch) echo "refusing: '$id' is not a session id" >&2; exit 2 ;; esac
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-session-derived-${id}.txt"
find ".cache/sessions/$id" -mindepth 1 -print 2>/dev/null | sort > "$manifest" || true
find ".cache/sessions/$id" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
printf 'Manifest: %s\n' "$manifest"
```

The `scratch/` root is disposable in its entirety:

```bash
find .cache/sessions/scratch -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
mkdir -p .cache/sessions/scratch
```

Confirm the datasets root is untouched:

```bash
du -sh .cache/sessions/datasets 2>/dev/null
```

### Pruning session audio (`session-audio-prune`)

This is not a `rm` recipe and must not be turned into one. The ~2 GB of decoded
`pcm/` and cached `stt/` output per recording comes off through the tool that
owns the dataset format:

```bash
cd /path/to/ttrpg-agent
.agents/bin/session-ingest prune --dry-run          # inventory, deletes nothing
.agents/bin/session-ingest prune                    # after confirmation
```

`prune` refuses while the session chronicle is still unwritten, keeps the
dataset's own record files consistent, and never touches `source/`. If it
refuses, that is the answer — report it rather than reaching for `find`.
Re-deriving what it removed costs one GPU pass (~10 min for a 3 h session) and
no API spend, so this is the cheapest large reclaim available.

Remember the VHDX caveat when reporting: under WSL the space will not show up
on the Windows side without a separate compaction step.

### Retiring a whole session (`session-corpus:<id>`)

The nuclear scope. It removes the dataset **including `source/`** — the original
Craig archive — alongside the derived sidecars and the rendered transcript.
Craig expires recordings, so after this the session is gone: not re-renderable,
not re-transcribable, not recoverable at any price.

Two confirmations, not one:

1. `CONFIRM CLEANUP session-corpus:<session-id>`
2. A separate acknowledgement, in the user's own words, that **the source
   recording is no longer downloadable from Craig and they accept losing it.**

Do not accept both in the same message, and do not offer this scope
unprompted — if a user asks for "space back", offer `session-audio-prune` and
`session-derived` first and show what each would reclaim. Never combine it with
another scope, and never run it for more than one session id at a time.

```bash
set -uo pipefail
cd /path/to/ttrpg-agent
id="<confirmed-session-id>"
rec="<confirmed-recording-id>"       # from session.json / `session-ingest doctor`
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-session-corpus-${id}.txt"
{
  find ".cache/sessions/$id" -mindepth 1 -print 2>/dev/null
  find ".cache/sessions/datasets/$rec" -mindepth 1 -maxdepth 2 -print 2>/dev/null
  find "vault/transcripts/$id" -mindepth 1 -print 2>/dev/null
} | sort > "$manifest" || true
rm -rf -- ".cache/sessions/$id" ".cache/sessions/datasets/$rec" "vault/transcripts/$id"
mkdir -p .cache/sessions/datasets vault/transcripts
printf 'Manifest: %s\n' "$manifest"
```

Afterwards: `.agents/bin/qmd update` as a separate command, and check that the
cumulative files and every *other* session survived:

```bash
ls -l vault/transcripts/_speakers.yaml vault/transcripts/_lexicon.yaml
ls .cache/sessions/datasets
```

### Vendored checkouts (separate opt-in scope)

Only on an explicit `vendor-checkouts` request naming the vendor. Warn first:
5etools is ~190 MB to re-clone and canonical 5e lookup stops working until it
is restored; foundry-vtt-mcp needs an `npm ci` rebuild.

```bash
set -euo pipefail
cd /path/to/ttrpg-agent
name="5etools"                       # or foundry-vtt-mcp — one named vendor only
test -d ".cache/vendor/$name" || { echo "no such vendor: $name"; exit 1; }
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-vendor-${name}.txt"
find ".cache/vendor/$name" -mindepth 1 -maxdepth 2 -print 2>/dev/null | sort > "$manifest" || true
rm -rf -- ".cache/vendor/$name"
printf 'Manifest: %s\nRestore with: .agents/harness vendor sync %s\n' "$manifest" "$name"
```

### Tool environments (separate opt-in scope)

Only on an explicit `tool-environments` request. Rebuildable, but book-ingest's
venv pulls multi-GB torch/CUDA wheels back down.

```bash
set -uo pipefail
cd /path/to/ttrpg-agent
stamp=$(date +%Y%m%d-%H%M%S)
manifest="/tmp/ttrpg-agent-cleanup-${stamp}-tool-environments.txt"
find .agents/cli -mindepth 2 -maxdepth 2 \( -name .venv -o -name node_modules \) -print \
  | sort > "$manifest" || true
find .agents/cli -mindepth 2 -maxdepth 2 \( -name .venv -o -name node_modules \) \
  -exec rm -rf -- {} +
printf 'Manifest: %s\nRestore with: .agents/harness bootstrap --apply\n' "$manifest"
```

### Full data reset

A full reset is just the confirmed combination of:

1. `vault-content`
2. `imports-all`
3. `all-index-caches`

It does **not** include `vendor-checkouts`, `tool-environments`, or any session
scope; none of those is implied by "reset the data". In particular the session
corpus survives a full reset untouched — `.cache/sessions/`, `.cache/craig-stt/`
and `vault/transcripts/` all stay, because the recordings inside are
irreplaceable and a "reset" is by definition something the user expects to be
able to recover from.

Run the individual blocks, not a single `rm -rf vault imports .cache`. Recreate
the structure afterwards by sourcing the environment contract, then confirm the
expensive and irreplaceable classes survived:

```bash
source ./.agents/env.sh
du -sh .cache/xdg/qmd/models .cache/xdg/datalab .cache/craig-stt .cache/vendor 2>/dev/null
du -sh .cache/sessions vault/transcripts 2>/dev/null
```

## Post-cleanup qmd handling

Run qmd handling only after the destructive block has completed and the structure has been verified. Use a separate shell command:

```bash
cd /path/to/ttrpg-agent
source ./.agents/env.sh
.agents/bin/qmd update
.agents/bin/qmd status
```

- If only `search-index` or `all-index-caches` was cleaned, run `.agents/bin/qmd update` to recreate index state if the user wants search available immediately.
- If vault notes or ingested books were deleted, run `.agents/bin/qmd update` so deleted docs disappear from search.
- If ingested books were deleted and not immediately re-ingested, `.agents/bin/qmd embed` is usually unnecessary.
- If `imports/source-vault` was deleted, run `.agents/bin/qmd update` so the `archive` collection stops returning stale docs.
- If rendered transcripts were deleted, run `.agents/bin/qmd update` so the `transcripts` collection stops returning deleted chunks. That collection is excluded from default search, so a stale entry there is easy to miss — refresh anyway. `.agents/bin/qmd embed` is unnecessary unless transcripts are being re-rendered immediately.
- Deleting only `.cache/sessions/` sidecars needs no qmd work at all: nothing under `.cache/` is indexed.
- If the 5etools vendor checkout was deleted, do **not** try to fix with qmd; tell the user local 5etools-backed creature/spell/item lookup will fail until `.agents/harness vendor sync 5etools` restores it.
- If `.agents/bin/qmd update` or `.agents/bin/qmd status` fails after cleanup, report that qmd refresh failed, show the error, verify the structure again, and stop. Do not rerun the destructive cleanup block unless the user reconfirms it.

See also `ttrpg-system-qmd-maintenance` for normal qmd rebuild and verification commands. Use that skill instead when the request is only "refresh/rebuild search" rather than destructive cleanup.

## Refusals and escalation

Refuse or re-clarify if:

- The user asks to delete `.agents/`, `.pi/`, `.claude/`, or repo configuration as part of "cleanup".
- A custom path resolves outside the allowed data roots.
- A path resolves into the **expensive class** — `.cache/xdg/qmd/models`,
  `.cache/xdg/datalab`, `.cache/huggingface`, `.cache/torch`, `.cache/uv`,
  `.cache/npm`. Refuse even under `custom-paths`, even when named explicitly.
  Deleting these costs a 5.5 GB re-download and is never what "reset the index"
  means. If the user genuinely wants the model caches gone, say plainly that it
  is outside this skill and ask for a separate, explicitly-scoped task.
- A path resolves into `.agents/state/` — durable state holding the live Foundry
  credential; it is never a cleanup target.
- A path resolves into the **irreplaceable class** — `.cache/sessions/datasets/*/source/`,
  or a dataset's `dataset.json` / `segments.jsonl` / `meta.json` / `speech.jsonl`,
  or `vault/transcripts/_speakers.yaml` / `_lexicon.yaml`. Refuse even under
  `custom-paths`, even when named explicitly. Craig expires recordings, so this
  is the one class in the repo that no amount of time or money restores. Deleting
  a whole session's recording is the separate `session-corpus:<id>` scope with
  its own double confirmation; the two `_*.yaml` files have no deletion path at
  all under this skill.
- A request would `rm` inside `.cache/sessions/datasets/` to reclaim space —
  including `pcm/`, `stt/` or `source/extracted/`, which really are regenerable.
  That is `.agents/bin/session-ingest prune`, and going around the SDK is how a
  neighbouring `source/` gets caught in a glob. If `prune` refuses, report the
  refusal; do not work around it.
- A request would delete a vendor checkout or a tool environment as part of a
  data reset. Those are the separate `vendor-checkouts` / `tool-environments`
  scopes and must be requested by name.
- The user gives a broad destructive request but will not provide an exact confirmation phrase.
- The requested cleanup would remove `vault/.obsidian/`; explain that this skill preserves Obsidian settings and ask for a separate explicit maintenance task if truly needed.
