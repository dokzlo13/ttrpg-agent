---
name: illustrate
description: Generate one TTRPG visual asset with OpenAI image generation.
thinking: medium
model: openai-codex/gpt-5.5
skill: ttrpg-create-image-gen
---

# /illustrate

Generate one image only when the user's request is explicit. Follow
`ttrpg-create-image-gen`.

1. Turn the user's idea into a complete image prompt using the skill's recipe:
   intended use, subject, setting, style/medium, composition, lighting/mood,
   concrete details, and exclusions.
2. Avoid real-person likenesses and copyrighted/branded characters. Offer an
   original archetypal alternative if needed.
3. If the request is underspecified but still actionable, choose sensible TTRPG
   defaults instead of asking many questions. Use a dry-run when uncertain.
4. Run `.ttrpg/tools/image-gen` to create the PNG and adjacent Markdown asset
   note in `vault/notes/images/`.
5. Report the image path, asset note path, and markdown embed.
6. If the user wants it attached to an NPC/location/session/etc., then read/use
   `ttrpg-vault-authoring` and adopt it into the relevant durable note with
   body wikilinks.

User input: $@
