---
name: cleanup
description: Destructive cleanup/reset for vault, imports, ingested books, and qmd indexes.
argument-hint: "[scope or paths]"
thinking: medium
model: openai-codex/gpt-5.4-mini
skill: ttrpg-system-data-cleanup
---

# /cleanup

You are handling a potentially destructive data cleanup request. Load and follow the `ttrpg-system-data-cleanup` skill.

Do **not** delete anything just because this prompt was invoked.

1. Determine the intended scope from the user's input, if possible:
   - `search-index` — qmd search index only
   - `all-index-caches` — all `.qmd/` caches
   - `active-notes` — `vault/notes/` or selected note markdown
   - `ingested-books` — generated folders in `vault/library/books/`
   - `vault-content` — active vault content, preserving `vault/.obsidian/`
   - `imports-books` — source PDFs/EPUBs in `imports/books/`
   - `imports-source-vault` — legacy archive import
   - `imports-5etools` — local 5etools mirror; warn that canonical lookup may break
   - `imports-all` — all import roots
   - `full-data-reset` — vault content + imports + indexes
   - `custom-paths` — only explicit allowed paths under data roots
2. If the scope is missing or ambiguous, ask the user to choose one or more scopes. Do not proceed.
3. Inventory the target paths with non-destructive commands and summarize what will be deleted and preserved.
4. Ask for an exact confirmation phrase like `CONFIRM CLEANUP <scope>`.
5. Only after exact confirmation, execute the matching destructive command pattern from `ttrpg-system-data-cleanup` as its own shell command.
6. Preserve tracked `.gitkeep` placeholders; do not delete them, and recreate/touch them with the skeleton before any qmd work.
7. Refresh qmd in a separate shell command when relevant. If qmd fails, inspect/report; do not rerun destructive cleanup without fresh confirmation.
8. Report the manifest path in `/tmp/`.

User input: $@
