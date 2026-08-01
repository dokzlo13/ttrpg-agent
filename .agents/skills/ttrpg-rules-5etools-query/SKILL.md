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
`.cache/vendor/5etools/` clone. This is the highest-priority path for factual
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

## The tool, on two surfaces

This capability has one implementation and two front ends, both generated from
the same parameter spec (`.agents/cli/query-5etools/spec.mjs`), so their options
are always identical:

| Surface | How to call it | Available in |
|---|---|---|
| `query_5etools` tool | native tool call | pi only |
| `.agents/bin/query-5etools` | shell command | every harness, and a bare shell |

**If your harness exposes a `query_5etools` tool, use it.** Otherwise — Claude
Code, Codex, or a plain terminal — use the CLI. The flag form of a parameter is
its name in kebab-case: `entityType` → `--entity-type`, `preferRuleset` →
`--prefer-ruleset`. Array parameters repeat: `--type fey --type undead`.
Run `.agents/bin/query-5etools --help` for the full list.

Do not use the removed Python wrapper.

### Typical calls

Named creature, rendered as a statblock:

```bash
.agents/bin/query-5etools --entity-type creature --name goblin \
  --output markdown --prefer-ruleset either
```

```text
query_5etools({ entityType: "creature", name: "goblin",
                output: "markdown", preferRuleset: "either" })
```

Filtered candidate list:

```bash
.agents/bin/query-5etools --entity-type creature --cr 5..7 --type fey \
  --output summary --prefer-ruleset either
```

```text
query_5etools({ entityType: "creature", cr: "5..7", type: ["fey"],
                output: "summary", preferRuleset: "either" })
```

Spells:

```bash
.agents/bin/query-5etools --entity-type spell --level 3 \
  --class wizard --school V --output summary
```

```text
query_5etools({ entityType: "spell", level: "3", class: ["wizard"],
                school: ["V"], output: "summary" })
```

Items:

```bash
.agents/bin/query-5etools --entity-type item --rarity rare \
  --kind weapon --output summary
```

```text
query_5etools({ entityType: "item", rarity: ["rare"], kind: ["weapon"],
                output: "summary" })
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
2. Use a tiny Node snippet against `.cache/vendor/5etools/`.
3. If the pattern is clearly reusable, extend `query_5etools` later.

Common escalations:

- class/subclass progression
- feats and backgrounds
- source/schema comparisons between 2014 and 2024 records
- native renderer or helper behavior

## Fallback when 5etools data is absent

The mirror is a pinned vendor checkout, not user content, and is fetched on
first use rather than at clone time. If `$TTRPG_5ETOOLS_DIR`
(`.cache/vendor/5etools/`) is absent, tell the user the local mirror is missing,
offer `.agents/harness vendor sync 5etools` (~190 MB), and meanwhile fall back
to `ttrpg-library-search` against books.
