---
name: foundry-monster
description: Format an existing monster or conversion as importer-ready text plus optional post-import Foundry enrichers.
thinking: medium
model: openai-codex/gpt-5.5
skill: ttrpg-foundry-statblock-importer
---

# /foundry-monster

The user already has a monster concept or statblock and wants a Foundry-ready result.
Consult `ttrpg-foundry-enrichers` too if post-import notes would help.

1. If they provide a vault note path, read it.
2. If they provide raw statblock text, use that.
3. Output:

```markdown
Import Statblock:
```text
<plain importer-ready statblock>
```

Post-Import Foundry Notes:
- optional enriched text for actor/item/journal descriptions
```

4. Keep enrichers **out** of the import block.
5. If the source text is malformed, normalize it into the safest importer format.
6. If useful, save the result back into the monster note. If creating a new note, use `ttrpg-vault-authoring` and include wikilinks/`## Connections` where relevant.

User input: $@
