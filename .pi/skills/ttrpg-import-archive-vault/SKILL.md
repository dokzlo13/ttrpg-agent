---
name: ttrpg-import-archive-vault
description: |
  Promote selected legacy notes from read-only imports/source-vault/ into the
  active vault. Use after search/inspection when the user wants to pull in,
  migrate, copy, or reuse old-vault material. The LLM chooses meaning and
  destination; vault-sync only inspects and safely copies explicit files.
---

# ttrpg-import-archive-vault

Wraps `.pi/cli/vault-sync`. This is **not** an rsync, classifier, or auto-router.
The old vault may be messy and semantically unreliable, so the LLM makes all decisions.
The tool only reports factual note metadata and safely copies a chosen file.

Core rule:

> LLM decides meaning and destination. `vault-sync` only reports facts and copies safely.

## When to use

- "Pull `cities/Dunemark.md` from my old vault."
- "I had notes on the Blackthorne family somewhere in imports/source-vault — find them
  and migrate."
- "Look through the old vault and reuse anything about the Red Chapel."

**Don't use this for:** general long-form searching by itself (use `ttrpg-library-search` with
`-c archive` or `rg` first), editing existing `vault/notes/` files, book ingests, or bulk archive
mirroring.

## Procedure

### Find-then-promote, the normal path

1. Locate candidates with qmd or text search:
   ```bash
   qmd query "blackthorne" -c archive
   # or
   rg -n "Blackthorne" imports/source-vault
   ```
2. Read promising source notes directly. Inspect for quick facts if useful:
   ```bash
   uv run --project .pi/cli/vault-sync vault-sync inspect \
     "imports/source-vault/random/lord blackthorne.md"
   ```
3. Decide semantically what to do:
   - ignore irrelevant archive noise
   - merge useful facts into an existing `vault/notes/` note
   - summarize/split into a fresh note
   - copy raw material only after choosing a semantic destination under `vault/notes/`
   - ask one focused question or summarize in chat if placement is genuinely uncertain
4. Use `ttrpg-vault-authoring` to choose the destination. The script does not choose.
5. Copy only after choosing an explicit destination under `vault/notes/`:
   ```bash
   uv run --project .pi/cli/vault-sync vault-sync copy \
     "imports/source-vault/random/lord blackthorne.md" \
     "vault/notes/npcs/lord-blackthorne.md"
   ```
   Add `--copy-attachments` if `inspect` shows local images/embeds worth preserving.
6. Open the copied note and perform semantic cleanup manually:
   - normalize frontmatter (`type`, `source`, `created`, `tags`, `status`)
   - preserve old metadata only if it is useful, usually under `legacy:`
   - add/repair important wikilinks
   - add `## Connections`
   - create stubs or migrate linked notes only when they matter
7. Run `qmd update` after meaningful active-note changes.

### Direct single-note copy

Use this when the user names a specific archive note and the destination is obvious:

```bash
uv run --project .pi/cli/vault-sync vault-sync inspect \
  "imports/source-vault/cities/Dunemark.md"
uv run --project .pi/cli/vault-sync vault-sync copy \
  "imports/source-vault/cities/Dunemark.md" \
  "vault/notes/locations/dunemark.md"
```

Then edit the copied note. Do not leave it as unprocessed archive junk; normalize, merge, or delete it after extracting the useful material.

## What `vault-sync` does

- `list --filter "*.md"` lists markdown candidates with path, line count, size, and title.
- `inspect <source>` reports factual metadata: frontmatter, headings, wikilinks, embeds, local image
  links, and which embeds resolve to files.
- `copy <source> <dest>` copies one markdown file unchanged to an explicit destination under `vault/notes/`.
- `copy --copy-attachments` also copies resolvable local non-`.md` embeds/images beside the
  destination, preserving relative paths and leaving markdown text unchanged.
- `copy --dry-run` validates and prints the plan without writing.

## What `vault-sync` deliberately does not do

- No type detection.
- No destination proposal.
- No frontmatter normalization.
- No wikilink rewriting.
- No merge logic.
- No bulk `pull-all`.
- No overwrite of existing active notes.

## After a copy

Tell the user briefly:

> Copied `<source>` → `<dest>`. Markdown was left unchanged. I then normalized/merged/linked it as
> follows: ...

If there are unresolved old-vault links or skipped attachments, mention them as possible follow-up
migration candidates.

## Reference

`.pi/cli/vault-sync/README.md`.
