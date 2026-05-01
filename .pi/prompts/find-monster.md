---
name: find-monster
description: Find a monster across canonical 5e data and ingested books. Tries query_5etools first, falls back to qmd.
thinking: low
model: openai-codex/gpt-5.4-mini
skill: ttrpg-rules-5etools-query
---

# /find-monster

The user is looking for a monster. Resolve it efficiently:

1. **Try `query_5etools` first.**
   ```
   query_5etools({
     entityType: "creature",
     name: "<name>",
     output: "markdown"
   })
   ```
   If 2024 matters, set `preferRuleset: "2024"`; otherwise use `"either"`
   and offer to compare if both lines of material matter.

2. **If that returns nothing**, broaden to a partial-name pass with `output: "summary"`.

3. **Still nothing? Fall back to qmd** across the books collection:
   ```
   qmd search "<name>" -c books
   ```
   If hits, `qmd get <id>` the most likely one and present it.

4. **For "where is X discussed across all my books"** — that's a prose lookup.
   Skip step 1 entirely and go to qmd. Use this prompt only when the user
   wants the actual statblock or canonical record.

5. **Output**:
   - Name, source book(s), CR, type, alignment.
   - Compact statblock if `query_5etools` was the hit.
   - List of book mentions if `qmd` was the hit.
   - **Offer next steps**: "Want me to make this Foundry-importable?"
     (`/foundry-monster` or `/convert-monster`), "Want me to find
     similar creatures at the same CR?" (re-query with filters).

Keep the response under one screen unless the user asks for more.

User input: $@
