# /cleanup

You are handling a potentially destructive data cleanup request. Load and follow the `ttrpg-system-data-cleanup` skill.

Do **not** delete anything just because this prompt was invoked.

1. Determine the intended scope from the user's input, if possible. **The skill's
   scope table is authoritative** — read it rather than trusting this summary:

   | Scope | Deletes |
   |---|---|
   | `search-index` | `.cache/xdg/qmd/index.sqlite*`, named files only |
   | `all-index-caches` | the above plus `.cache/index/index.yml` |
   | `active-notes` | `vault/notes/` or selected note markdown |
   | `ingested-books` | generated folders in `vault/library/books/` |
   | `book-ingest-backups` | stale `.<slug>.<timestamp>.bak` artifacts |
   | `vault-content` | active vault content, preserving `vault/.obsidian/` |
   | `imports-books` | source PDFs/EPUBs in `imports/books/` |
   | `imports-source-vault` | legacy archive import |
   | `imports-fvtt-data` | Foundry exports in `imports/fvtt-data/` |
   | `imports-all` | the three `imports/` scopes above |
   | `full-data-reset` | `vault-content` + `imports-all` + `all-index-caches` |
   | `custom-paths` | only explicit allowed paths under the data roots |
   | `vendor-checkouts` | **opt-in only**, one named vendor under `.cache/vendor/` |
   | `tool-environments` | **opt-in only**, `.agents/cli/*/.venv` and `node_modules` |

   Two things this prompt must never get wrong:
   - There is **no** `imports-5etools` scope. The 5etools mirror is a pinned
     vendor checkout, not user imports; removing it is `vendor-checkouts`, and
     it is never implied by `imports-all` or `full-data-reset`.
   - The qmd index lives *beside* the 2.2 GB model store in `.cache/xdg/qmd/`.
     Index scopes delete **named files**, never that directory.
2. If the scope is missing or ambiguous, ask the user to choose one or more scopes. Do not proceed.
3. Inventory the target paths with non-destructive commands and summarize what will be deleted and preserved. Record the size of the expensive class before and after.
4. Ask for an exact confirmation phrase like `CONFIRM CLEANUP <scope>`.
5. Only after exact confirmation, execute the matching destructive command pattern from `ttrpg-system-data-cleanup` as its own shell command. Write the manifest to `/tmp` **first** — a block that deletes without one is a bug; stop and report it.
6. Recreate the expected structure with `source ./.agents/env.sh` before any qmd work; do not hand-write a `mkdir -p` list and do not rely on tracked placeholder files.
7. Refresh qmd in a separate shell command when relevant. If qmd fails, inspect/report; do not rerun destructive cleanup without fresh confirmation.
8. Report the manifest path in `/tmp`, and confirm the expensive class is unchanged.

User input: $@
