---
name: npc
description: Generate a quick NPC sketch — name, look, voice, hook. Cheaper model, low thinking.
thinking: low
model: openai-codex/gpt-5.4-mini
skill: ttrpg-vault-authoring
---

# /npc

The user wants a quick NPC. This is a low-cost, fast prompt — don't overthink.

1. **Get the brief.** If the user said "an innkeeper", that's enough. If they
   said nothing, ask one question: role + setting? (e.g. "innkeeper, in a
   coastal town").

2. **Write the sketch.** Format:

   ```markdown
   ## <Name>
   *<one-line tagline>*

   **Look:** 2–3 concrete physical details + one telling object/habit.
   **Voice:** how they speak (accent, pacing, verbal tic). One example line in quotes.
   **Wants:** what they want from the players (or the world).
   **Hook:** one reason the players would come back to them.
   **Secret:** one thing they're hiding (optional but usually good).
   ```

   Keep it under 150 words total. The user wants something usable, not an
   entire backstory.

3. **Check for collisions.** Quick `qmd search` on the proposed name in `notes`
   to make sure it's not already used. If it is, change the name.

4. **Offer to save.** If the user wants to keep them, use `ttrpg-vault-authoring`:
   usually `vault/notes/npcs/<slug>.md`, but prefer an existing better local folder.
   Add body wikilinks and `## Connections` for any known places/factions/NPCs.
   Otherwise just hand them the sketch.

5. **Don't:**
   - Don't generate a full statblock unless asked. Most NPCs don't need one.
   - Don't write read-aloud here. That's `/readaloud`.
   - Don't write three NPCs when the user asked for one.

If the user wants 3-5 NPC ideas to pick from, that's fine — but keep each one
to a tight 4–5 lines. Quantity dilutes specificity.

User input: $@
