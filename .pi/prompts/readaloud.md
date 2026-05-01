---
name: readaloud
description: Write a "read aloud" boxed-text passage for the table.
thinking: medium
model: openai-codex/gpt-5.5
skill: ttrpg-create-readaloud
---

# /readaloud

Write boxed text for the user to read at the table. Follow the voice and
length conventions in the `ttrpg-create-readaloud` skill (concrete sensory detail,
present tense second person, ends on a hook, no mechanics inside the boxed
text).

1. **Get the scene.** If the user gave you context ("the players approach
   the witch's hut at dusk"), use it. If not, ask one focused question — what
   is the scene, and what's the dramatic purpose?

2. **Check the local library** for relevant material before writing:
   - If the scene is from a known location, `qmd search` the location's slug
     in `vault/notes/locations/`. Reuse details (the inn's specific
     fireplace, the river's colour) so the world stays consistent.
   - If it's an NPC introduction, pull their bio from `vault/notes/npcs/`.

3. **Write the passage.** 60–120 words for normal scenes, up to 200 for
   dramatic reveals. Three senses minimum. End on a hook.

4. **Add DM notes underneath.** Mechanics, hooks, tactical notes — anything
   the user needs but can't read aloud.

5. **Save it** if it's reusable. Read/use `ttrpg-vault-authoring` first:
   - Reusable scene → usually `vault/notes/readalouds/<slug>.md`, unless the existing vault suggests a better folder.
   - Session-specific → write it in the session prep doc the user is working on, or `vault/notes/sessions/` if creating one.
   - Throwaway / one-off → just paste it back to the user, no save.

   Default to saving reusable scene prompts unless the user explicitly says not to. Add wikilinks to related locations/NPCs and a `## Connections` section when saved.

6. **Offer one revision pass.** "Want it longer / shorter / more uncanny / less
   purple?" Don't iterate forever — one offer, then move on.

User input: $@
