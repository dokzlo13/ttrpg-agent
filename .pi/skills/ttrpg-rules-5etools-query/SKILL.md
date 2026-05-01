---
name: ttrpg-rules-5etools-query
description: |
  Structured canonical D&D 5e queries over the local 5etools mirror. Use first
  for creature, spell, and item records: statblocks, spell/item text, and
  filterable CR/level/source/type/school/rarity lists. If query_5etools cannot
  express the record type, escalate to ttrpg-rules-5etools-native.
---

# ttrpg-rules-5etools-query

## When to use this skill

Use this skill for **structured canonical data queries** against the local
`imports/5etools/` clone. This is the highest-priority path for factual
creature, spell, and item mechanics.

Examples this skill handles well:

- "Every CR 5–7 fey in MM and supplements."
- "All 3rd-level wizard evocation spells."
- "Rare weapons that require attunement."
- "Show me the goblin statblock from MM."

For canonical rules answers, prefer source-backed answers over memory. If the
fact is outside creature/spell/item coverage, hand off to `ttrpg-rules-5etools-native`.

Examples that should go elsewhere:

- "Where is the goblin described across my books?" → `ttrpg-library-search`
- "I need a weird one-off 5etools renderer/schema inspection." → `ttrpg-rules-5etools-native`

## Default tool: `query_5etools`

Use the project-local Pi tool, not the removed Python wrapper.

### Typical calls

```text
query_5etools({
  entityType: "creature",
  name: "goblin",
  output: "markdown",
  preferRuleset: "either"
})
```

```text
query_5etools({
  entityType: "creature",
  cr: "5..7",
  type: ["fey"],
  output: "summary",
  preferRuleset: "either"
})
```

```text
query_5etools({
  entityType: "spell",
  level: "3",
  class: ["wizard"],
  school: ["V"],
  output: "summary"
})
```

```text
query_5etools({
  entityType: "item",
  rarity: ["rare"],
  kind: ["weapon"],
  output: "summary"
})
```

## Output modes

- `summary` — best default for candidate lists.
- `json` — raw-ish 5etools records for follow-on reasoning.
- `markdown` — native 5etools markdown rendering when available.

## Ruleset preference

Use `preferRuleset` deliberately:

- `"2024"` when the user explicitly wants 2024 material.
- `"2014"` when adapting older content or comparing legacy canon.
- `"either"` when the user just wants the best match and source labels are enough.

## If the simple tool is not enough

When you need something outside creature/spell/item or a stranger filter/rendering path:

1. Read `ttrpg-rules-5etools-native`.
2. Use a tiny Node snippet against `imports/5etools/`.
3. If the pattern is clearly reusable, extend `query_5etools` later.

Common escalations:

- class/subclass progression
- feats and backgrounds
- source/schema comparisons between 2014 and 2024 records
- native renderer or helper behavior

## Fallback when 5etools data is absent

If `imports/5etools/` is not cloned yet, fall back to `ttrpg-library-search` against
books and tell the user the local mirror is missing.
