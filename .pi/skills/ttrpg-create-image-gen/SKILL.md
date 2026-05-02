---
name: ttrpg-create-image-gen
description: |
  Generate a single illustration (monster, NPC, location, scene, handout, token
  portrait) with OpenAI image generation, save it to vault/notes/images/, and
  create an adjacent qmd-indexable Markdown asset note with prompt and params.
  Use only when the user explicitly asks for image generation.
---

# ttrpg-create-image-gen

Use this skill only on an explicit user request such as:

- "Make me an image of this NPC."
- "Illustrate the throne room."
- "Generate a token portrait for the boss."
- "Create a visual handout for this ruin."

Never proactively generate images. This is a metered API workflow and the user's
lowest-priority capability.

## Tool

The project tool is:

```bash
uv run --project .pi/cli/image-gen image-gen --subject "<prompt>"
```

Dry-run first if the request is ambiguous or you want to show the planned paths
without spending credits:

```bash
uv run --project .pi/cli/image-gen image-gen --subject "<prompt>" --dry-run
```

The tool reads `OPENAI_API_KEY` from `.env` or the shell. Optional defaults are
in `.env.example`:

```dotenv
TTRPG_IMAGE_MODEL=gpt-image-1
TTRPG_IMAGE_SIZE=1024x1024
TTRPG_IMAGE_QUALITY=auto
TTRPG_IMAGE_OUTPUT_FORMAT=png
TTRPG_IMAGE_OUTPUT_DIR=vault/notes/images
```

## Output contract

Every generation must create both files:

```text
vault/notes/images/<slug>-<hash>.png
vault/notes/images/<slug>-<hash>.md
```

The Markdown note is required so qmd can index the asset. It contains:

- frontmatter with `type: handout`, `source: agent`, `tags: [campaign, image-generation, asset]`, `asset_kind: image`, provider/model/size/quality/output format;
- an Obsidian image embed;
- the exact prompt;
- generation parameters as fenced JSON;
- sanitized response metadata if available;
- `## Adoption Notes`, `## Connections`, and `## Sources` sections.

Never store API keys or base64 image data in the note.

## Prompt recipe

Official OpenAI guidance says GPT image prompts work best when the intent and
constraints are clear, not when the syntax is clever. Prefer a skimmable prompt
in this order:

1. **Action + intended use** — "Draw an original fantasy NPC portrait...", "Create a player-facing handout...", "Illustrate a wide establishing shot...".
2. **Subject** — the main creature, person, object, room, landscape, or scene.
3. **Setting/background** — where it is and what surrounds it.
4. **Medium/style** — ink and watercolor, oil painting, illuminated manuscript, cinematic fantasy concept art, weathered map, photorealistic prop, etc.
5. **Composition/framing** — close-up portrait, waist-up, full body, top-down map, wide shot, low angle, centered subject, negative space for labels.
6. **Lighting/mood/color** — golden hour, candlelit, moonlit, foggy, high contrast, desaturated, sickly green, warm lamplight.
7. **Concrete details** — materials, textures, shapes, heraldry, scars, tools, clothing, environmental clues.
8. **Constraints/exclusions** — no watermark, no logo, no extra text, no modern objects, no branded/copyrighted characters.

Template:

```text
Draw an original fantasy <asset type> for a D&D campaign: <subject>.
Setting/background: <place and surroundings>.
Style/medium: <visual medium and genre>.
Composition: <framing, camera angle, layout>.
Lighting/mood: <lighting, palette, emotion>.
Key details: <3-7 concrete details>.
Constraints: no watermark, no logo, no extra text unless explicitly requested, no copyrighted/branded characters.
```

### CLI parameter tuning

Defaults are intentionally conservative:

```bash
--model gpt-image-1 --size 1024x1024 --quality auto --output-format png
```

Deviate per call when the asset has a clear need:

| Goal | Suggested CLI params | Notes |
|---|---|---|
| Fast/cheap draft | `--quality low --size 1024x1024` | Best for prompt iteration and thumbnails. |
| Normal table asset | `--quality auto --size 1024x1024` | Default balance. Good for portraits, tokens, props, and quick scenes. |
| Landscape scene / splash art | `--size 1536x1024 --quality auto` | Use for locations, battle vistas, travel scenes, wide establishing shots. |
| Portrait / poster / handout | `--size 1024x1536 --quality auto` | Use for NPC full-body art, vertical posters, tall handouts. |
| Final/high fidelity | `--quality high` | Use when retries are more expensive than one better attempt; especially detailed portraits, dense scenes, or image text. |
| Latest/best model if available | `--model gpt-image-2 --quality high` | OpenAI currently recommends newer GPT Image models for best generation/editing. Use when the account supports it and quality matters more than speed/cost. |
| Small web-friendly file | `--output-format webp` | Useful for export/sharing; keep `png` for Obsidian defaults unless size matters. |

Examples:

```bash
# Wide location art
uv run --project .pi/cli/image-gen image-gen \
  --subject "Draw an original fantasy wide establishing shot of a ruined bell tower rising from a misty salt marsh, cinematic concept art, dawn light, no text, no watermark." \
  --size 1536x1024 \
  --quality auto

# Highest-quality final handout, if the model is available on the account
uv run --project .pi/cli/image-gen image-gen \
  --subject "Create a player-facing fantasy handout: a weathered silver locket on black velvet, engraved with an abstract moth sigil, photorealistic prop, no extra text, no watermark." \
  --model gpt-image-2 \
  --quality high \
  --size 1024x1024
```

Avoid changing the `.env` defaults for one-off needs; use CLI flags. Change `.env` only when you want a new long-term default.

### Quality choices

- Use `quality=low` for quick drafts, thumbnails, and iteration when speed/cost matters.
- Use `quality=auto` as the normal default.
- Use `quality=high` when available for final handouts, detailed portraits, dense scenes, or images with literal text.
- Larger images and higher quality generally cost more and/or run slower; prefer small/low while iterating, then make one final higher-quality pass.

### Text inside images

Avoid image text unless the user asks for it. If text is required:

- put the exact text in quotes;
- specify placement, size, color, and typography;
- use higher quality for small or dense text;
- expect to iterate, because image text can still drift.

## TTRPG-specific defaults

Prefer original setting-neutral fantasy art that can be used at the table:

- NPC portrait: waist-up or bust, strong silhouette, readable face, simple background.
- Monster: full body, scale cue, dynamic pose, no gore unless requested.
- Location: wide establishing shot, clear mood, several adventure clues.
- Handout/prop: readable object on plain background, no accidental modern markings.
- Token portrait: centered subject, high contrast, minimal background.

If the user asks for a copyrighted/branded character or real-person likeness,
briefly refuse that exact target and offer an original lookalike alternative
based on traits, archetype, era, mood, and palette.

## Adoption workflow

Generation creates a reusable asset note; it does not automatically decide the
campaign graph placement.

When the user wants the image attached to an NPC, location, faction, session,
scene, or handout:

1. Read/use `ttrpg-vault-authoring`.
2. Inspect nearby `vault/notes/` structure if creating or editing durable notes.
3. Link the image asset note from the campaign note body.
4. Embed the PNG where useful, usually with a relative Obsidian image link.
5. Update the asset note's `## Connections` with wikilinks to the adopting note(s).
6. Run `qmd update` after significant vault edits when search freshness matters.

Do not leave an adopted generated asset isolated. Body wikilinks matter more than
frontmatter-only metadata.

## Official references for this guidance

- OpenAI Image generation guide: https://platform.openai.com/docs/guides/image-generation/
- OpenAI GPT Image prompting guide: https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide
- OpenAI Images API reference: https://platform.openai.com/docs/api-reference/images/create

## Don't

- Don't generate without an explicit user request.
- Don't spend credits when a dry-run or written prompt would satisfy the user.
- Don't generate images of real people.
- Don't generate copyrighted/branded characters from other settings.
- Don't put generated assets outside `vault/notes/images/`.
- Don't store secrets or raw base64 in asset notes.
