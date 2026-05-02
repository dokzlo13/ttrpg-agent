# vault-sync

`vault-sync` is a deliberately dumb safety tool for promoting selected markdown notes from the
read-only archive vault (`imports/source-vault/`) into the active notes tree (`vault/notes/`).

It does **not** classify notes, choose folders, normalize frontmatter, merge notes, or bulk-import a
messy archive. Those are semantic decisions for the LLM using `ttrpg-vault-authoring` and the
current campaign context.

Core rule:

> LLM decides meaning and destination. `vault-sync` only reports facts and copies safely.

## Intended workflow

1. Search or browse `imports/source-vault/` for candidate old notes.
2. Inspect a candidate for factual metadata, links, embeds, headings, and source path.
3. The LLM reads the note and decides whether to copy, summarize, split, merge, ignore, or ask one focused placement question.
4. If copying is useful, the LLM chooses an explicit semantic destination under `vault/notes/`.
5. Run `vault-sync copy <source> <chosen-destination>`.
6. The LLM opens the new note and performs semantic cleanup: frontmatter, wikilinks,
   `## Connections`, stubs, or merge edits.

## Commands

```bash
# List archive markdown files: path, body lines, file size, title.
uv run --project .pi/cli/vault-sync vault-sync list --filter "*.md"

# Inspect one archive note. This makes no type or destination guesses.
uv run --project .pi/cli/vault-sync vault-sync inspect \
  "imports/source-vault/messy/Lord Blackthorne.md"

# Copy one archive note to an explicit LLM-chosen active-notes path.
uv run --project .pi/cli/vault-sync vault-sync copy \
  "imports/source-vault/messy/Lord Blackthorne.md" \
  "vault/notes/npcs/lord-blackthorne.md"

# Also copy resolvable local non-md embeds/images without rewriting the markdown.
uv run --project .pi/cli/vault-sync vault-sync copy \
  "imports/source-vault/messy/Lord Blackthorne.md" \
  "vault/notes/npcs/lord-blackthorne.md" \
  --copy-attachments

# Validate a copy plan without writing anything.
uv run --project .pi/cli/vault-sync vault-sync copy \
  "imports/source-vault/messy/Lord Blackthorne.md" \
  "vault/notes/npcs/lord-blackthorne.md" \
  --copy-attachments --dry-run
```

## What the tool guarantees

- Source must be inside `imports/source-vault/`.
- Destination must be an explicit `.md` file inside `vault/notes/`.
- Destination must not already exist.
- Source note is never modified.
- Copied markdown is byte-for-byte text content as read/written UTF-8: no type detection, no
  destination routing, no frontmatter rewriting, no wikilink rewriting.
- `inspect` reports facts only:
  - source path
  - title from first H1 or filename
  - file size and body line count
  - frontmatter and frontmatter keys
  - headings
  - wikilinks
  - embeds and whether they resolve to local files
  - local markdown images
- `--copy-attachments` copies resolvable local non-`.md` embeds/images beside the destination using
  the same relative path. It does not rewrite note text.

## What the LLM must do after copy

The copied note is just raw archive material. Before treating it as a durable active note, the
LLM should edit it according to `ttrpg-vault-authoring`:

```yaml
---
type: npc | location | faction | session | monster | item | spell | rules | readaloud | handout | meta | draft
source: imports/source-vault/<path>
created: YYYY-MM-DD
tags: [campaign]
status: draft
---
```

Then add useful body wikilinks and a `## Connections` section. If the old note is better merged into
an existing active note, do that instead of copying it.

## Testing

```bash
uv run --project .pi/cli/vault-sync pytest .pi/cli/vault-sync/tests
```
