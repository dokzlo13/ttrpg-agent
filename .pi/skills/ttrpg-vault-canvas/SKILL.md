---
name: ttrpg-vault-canvas
description: |
  Create, edit, and validate Obsidian Canvas .canvas files inside vault/notes/.
  Use when the user asks for Obsidian canvases, visual maps, relationship maps,
  clue boards, faction maps, encounter flows, timelines, or JSON Canvas output.
  Compose with ttrpg-vault-authoring for placement and ttrpg-vault-rich-notes
  when companion Markdown notes are useful.
---

# ttrpg-vault-canvas

Use this skill to build durable Obsidian Canvas files for the active vault. A
canvas is JSON, not Markdown frontmatter; keep it valid, connected, and placed
under `vault/notes/` unless the user explicitly requests another vault path.

This skill adapts JSON Canvas 1.0 and Obsidian Canvas practices for this TTRPG
workspace. When a canvas is durable campaign prep, pair it with the placement
rules in `ttrpg-vault-authoring`.

## When to use

Use for:

- relationship maps between NPCs, factions, locations, secrets, clues, and events
- encounter flowcharts, dungeon-room dependency maps, or heist timelines
- visual session prep boards with linked notes and callout-style text nodes
- editing an existing `.canvas` file
- validating JSON Canvas structure before handing a file to the user

Do **not** use a canvas when a regular Markdown note, table, or Mermaid diagram
would be simpler. Prefer `ttrpg-vault-rich-notes` for prose-first notes.

## Vault placement

1. Read/use `ttrpg-vault-authoring` before creating a durable canvas.
2. Prefer `vault/notes/canvases/<slug>.canvas` if no better local folder exists.
3. Canvas-internal file paths are **vault-root-relative** and must not include the
   leading `vault/` prefix. Example: use `notes/npcs/mara-vale.md`, not
   `vault/notes/npcs/mara-vale.md`.
4. If the canvas needs to be discoverable by qmd or carry sources/frontmatter,
   create a companion Markdown note at `vault/notes/canvases/<slug>.md` and link
   it to the canvas or its major notes. Canvas files themselves do not have YAML.
5. Use file nodes for important existing notes; use text nodes for summaries,
   unanswered questions, clocks, and table-facing reminders.
6. If the canvas introduces important new entities, create or request proper
   note stubs rather than leaving the entity only inside a canvas text node.

## JSON Canvas structure

A `.canvas` file is JSON with two top-level arrays:

```json
{
  "nodes": [],
  "edges": []
}
```

Top-level `nodes` and `edges` are optional in the spec, but for this vault write
both arrays for clarity.

### Node requirements

Every node needs:

- `id`: unique string; use 16 lowercase hexadecimal characters
- `type`: one of `text`, `file`, `link`, `group`
- `x`, `y`: integer top-left position in pixels
- `width`, `height`: integer dimensions in pixels
- optional `color`: preset string `"1"`-`"6"` or a hex color such as `"#8844cc"`

Node order is z-index: earlier nodes are behind later nodes. Put `group` nodes
before the nodes they visually contain.

### Text nodes

Text nodes store Markdown-flavored plain text. Use real newline escapes in JSON
strings (`\n` in the JSON source, rendered as line breaks after parsing). Do not
write double-escaped literal `\\n` unless you intentionally want backslash-n text.

Good uses:

- short headings and summaries
- clue cards
- clocks and open questions
- Obsidian wikilinks such as `[[mara-vale|Mara Vale]]` when helpful

Example:

```json
{
  "id": "2f4a9c1e7b8d0a33",
  "type": "text",
  "x": 0,
  "y": 0,
  "width": 360,
  "height": 180,
  "color": "5",
  "text": "# Session Threats\n\n- [[red-chapel]] cult pressure\n- Missing caravan clue"
}
```

### File nodes

File nodes reference notes and attachments. In this vault, use vault-root-relative
paths.

```json
{
  "id": "2ef16cf39d981d8b",
  "type": "file",
  "x": 440,
  "y": 0,
  "width": 420,
  "height": 260,
  "file": "notes/npcs/mara-vale.md",
  "subpath": "#Secrets"
}
```

`subpath` is optional and starts with `#` for headings or block IDs.

### Link nodes

Use link nodes for external URLs only when they are genuinely useful at the
table or during prep. Prefer local source citations in Markdown notes for book or
campaign sources.

```json
{
  "id": "a7ee8f4c02db7c61",
  "type": "link",
  "x": 920,
  "y": 0,
  "width": 320,
  "height": 160,
  "url": "https://jsoncanvas.org/spec/1.0/"
}
```

### Group nodes

Groups are visual containers. Place contained nodes within the group's rectangle
with 20-50px padding.

```json
{
  "id": "0d44ef8dd17a5223",
  "type": "group",
  "x": -40,
  "y": -60,
  "width": 980,
  "height": 420,
  "label": "Faction Pressure",
  "color": "6"
}
```

## Edges

Every edge needs:

- `id`: unique string; use 16 lowercase hexadecimal characters
- `fromNode`: source node id
- `toNode`: target node id

Optional fields:

- `fromSide` / `toSide`: `top`, `right`, `bottom`, or `left`
- `fromEnd` / `toEnd`: `none` or `arrow` (`toEnd` defaults to arrow in readers)
- `label`: concise edge text
- `color`: preset string `"1"`-`"6"` or hex

Example:

```json
{
  "id": "b8fbce22c308a173",
  "fromNode": "2f4a9c1e7b8d0a33",
  "fromSide": "right",
  "toNode": "2ef16cf39d981d8b",
  "toSide": "left",
  "toEnd": "arrow",
  "label": "points at"
}
```

## Color presets

JSON Canvas defines six portable preset strings and leaves exact theme colors to
the app:

| Preset | Meaningful use in prep |
|---|---|
| `"1"` red | danger, antagonist, violence, urgent problem |
| `"2"` orange | active clock, complication, heat |
| `"3"` yellow | clue, treasure, opportunity |
| `"4"` green | ally, safety, resource |
| `"5"` cyan | location, travel, information |
| `"6"` purple | magic, mystery, faction, weirdness |

Hex colors are allowed, but presets usually adapt better to Obsidian themes.

## Layout guidelines

- Canvas coordinates can be negative; `x` grows right and `y` grows down.
- Align positions to 20px increments for readable diffs and tidy layouts.
- Leave 60-100px between separate cards; leave 20-50px padding inside groups.
- Use left-to-right flow for causality and top-to-bottom flow for time.
- Size hints:
  - label card: 220x100
  - clue or NPC summary: 320-420x160-260
  - file preview: 360-500x240-360
  - group: enough to contain children plus padding

## TTRPG canvas patterns

### Relationship map

- Use file nodes for NPCs/factions/locations that already have notes.
- Use edge labels like `owes`, `blackmails`, `protects`, `hunts`, `funds`.
- Use direction consistently: subject/action -> object.
- Add a legend text node if colors carry meaning.

### Session prep board

- One group per scene, location, or beat.
- Use text nodes for read-aloud reminders, stakes, secrets, and likely checks.
- Link to session note, encounter notes, monster notes, and read-alouds via file
  nodes.

### Mystery/clue board

- Put the revelation or question in the center.
- Connect clues to the revelation with labels explaining what the clue proves.
- Mark missing/optional clues with color or text, not only with position.

## Safe editing workflow

1. Read and parse the existing `.canvas` file.
2. Preserve unrelated nodes, edges, coordinates, labels, and colors.
3. Generate IDs that do not collide with any existing node or edge ID.
4. Avoid overlapping nodes unless deliberately layering inside a group.
5. Validate JSON and edge references before finishing.
6. Tell the user the path and summarize the canvas structure.

## Validation

Use the bundled validator whenever practical:

```bash
node .pi/skills/ttrpg-vault-canvas/scripts/validate-canvas.mjs vault/notes/canvases/example.canvas
```

Validation checklist:

- JSON parses successfully.
- Top level is an object; `nodes` and `edges` are arrays when present.
- Every node and edge `id` is unique.
- Node `type` is one of `text`, `file`, `link`, `group`.
- Every node has integer `x`, `y`, `width`, and `height`.
- Required per-type fields exist: `text`, `file`, or `url`.
- File `subpath`, when present, starts with `#`.
- Every edge references existing `fromNode` and `toNode` IDs.
- Side/end/color values are valid.

## References and attribution

- JSON Canvas Spec 1.0: https://jsoncanvas.org/spec/1.0/
- Obsidian JSON Canvas repository: https://github.com/obsidianmd/jsoncanvas
- Adapted in part from Steph Ango's MIT-licensed Obsidian skills:
  https://github.com/kepano/obsidian-skills/tree/main/skills/json-canvas
