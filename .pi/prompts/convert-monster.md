---
name: convert-monster
description: Convert an OSR or non-5e monster into a 5e 2014/2024 statblock, ready for Foundry importer paste.
thinking: high
model: openai-codex/gpt-5.5
skill: ttrpg-rules-osr-to-5e
---

# /convert-monster

Convert the user's monster using this order of operations:

1. **Read the source statblock carefully.** Also consult `ttrpg-foundry-statblock-importer`
   for the import block shape and `ttrpg-foundry-enrichers` for any optional
   post-import notes.
   - pasted text beats memory
   - if the user points to a vault/book file, read that exact section first

2. **Prefer an existing 5e creature.**
   Try in order:
   - direct replacement
   - close analogue with changes
   - full rebuild only if needed

3. **Ask only if missing:**
   - ruleset: 2014 or 2024
   - party level
   - difficulty: chump / standard / tough / boss

4. **Write the result to the vault.** Use `ttrpg-vault-authoring` first; usually this is `vault/notes/mechanics/monsters/<slug>.md`. Include wikilinks to the source book/chapter, related monsters, locations, or factions when known.

5. **Respond in this shape:**

```markdown
Reasoning:
- direct replacement / analogue / rebuild
- target CR and why
- major changes

Import Statblock:
```text
<plain WotC-style importer-ready statblock>
```

Post-Import Foundry Notes:
- only if useful
- use enrichers here, not inside the import block
```

6. **Inside the main import block:**
   - use plain text only
   - no Foundry enrichers
   - follow `ttrpg-foundry-statblock-importer`

7. **After writing the note**, mention where it was saved.

If the source is already basically a 5e creature, push back briefly and suggest
using the canonical statblock instead.

User input: $@
