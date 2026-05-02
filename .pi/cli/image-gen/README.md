# image-gen

Generate one OpenAI image for TTRPG prep, save it under `vault/notes/images/`, and create an adjacent Markdown asset note so qmd can index the prompt, parameters, and adoption notes.

This tool is intentionally small: it is a CLI wrapper around the OpenAI Images API. The richer workflow lives in `.pi/skills/ttrpg-create-image-gen/SKILL.md`.

## Requirements

- `OPENAI_API_KEY` in the project `.env` or exported in the shell.
- Python deps installed by `uv` from this tool's `pyproject.toml`.

`.env.example` documents the optional defaults:

```dotenv
OPENAI_API_KEY=sk-...
TTRPG_IMAGE_MODEL=gpt-image-1
TTRPG_IMAGE_SIZE=1024x1024
TTRPG_IMAGE_QUALITY=auto
TTRPG_IMAGE_OUTPUT_FORMAT=png
TTRPG_IMAGE_OUTPUT_DIR=vault/notes/images
```

## Usage

Dry-run first. This does not call OpenAI or write files:

```bash
uv run --project .pi/cli/image-gen image-gen \
  --subject "Draw an original fantasy portrait of a tired dwarf cartographer, ink and watercolor, warm lamplight, no text, no watermark." \
  --dry-run
```

Generate a real asset:

```bash
uv run --project .pi/cli/image-gen image-gen \
  --subject "Draw an original fantasy portrait of a tired dwarf cartographer, ink and watercolor, warm lamplight, no text, no watermark."
```

Use a prompt file for longer prompts:

```bash
uv run --project .pi/cli/image-gen image-gen \
  --prompt-file /tmp/cartographer-prompt.md \
  --title "Dwarf Cartographer"
```

Machine-readable output for agent workflows:

```bash
uv run --project .pi/cli/image-gen image-gen \
  --subject "Draw a moonlit ruined chapel in the marsh, cinematic wide shot, no text." \
  --json
```

## Output

Each generation writes two files:

```text
vault/notes/images/<slug>-<hash>.png
vault/notes/images/<slug>-<hash>.md
```

The `.md` note contains:

- Obsidian frontmatter (`type`, `source`, `created`, `tags`, `status`, asset metadata).
- An image embed pointing at the generated image.
- The exact prompt.
- Generation parameters as fenced JSON.
- Sanitized response metadata such as revised prompt or usage if returned.
- Adoption notes and a `## Connections` section.

The tool never stores the API key or raw base64 response in the Markdown note.

## Parameter recipes

Defaults are:

```bash
--model gpt-image-1 --size 1024x1024 --quality auto --output-format png
```

Use CLI flags for one-off deviations:

```bash
# Fast prompt iteration
uv run --project .pi/cli/image-gen image-gen --subject "..." --quality low

# Wide landscape/location art
uv run --project .pi/cli/image-gen image-gen --subject "..." --size 1536x1024

# Tall portrait or poster-style handout
uv run --project .pi/cli/image-gen image-gen --subject "..." --size 1024x1536

# Final high-fidelity generation, if available on the account
uv run --project .pi/cli/image-gen image-gen --subject "..." --model gpt-image-2 --quality high

# Smaller web-friendly output file
uv run --project .pi/cli/image-gen image-gen --subject "..." --output-format webp
```

Rule of thumb: iterate with `quality low` or `auto`, then make one final pass with higher quality/model only when the prompt is stable.

## CLI reference

```text
--subject TEXT       Prompt/subject to send to the image model.
--prompt TEXT        Alias for --subject.
--prompt-file FILE   Read the prompt from a UTF-8 text/markdown file.
--title TEXT         Human-readable title for the Markdown asset note.
--slug TEXT          Filename slug base.
--dest DIR           Output directory. Must be under vault/notes/images.
--model TEXT         Default: TTRPG_IMAGE_MODEL or gpt-image-1.
--size TEXT          Default: TTRPG_IMAGE_SIZE or 1024x1024.
--quality TEXT       Default: TTRPG_IMAGE_QUALITY or auto.
--output-format fmt  png, jpeg, or webp. Default: png.
--dry-run            Plan only; no API call and no writes.
--json               Emit machine-readable JSON.
```

## Adoption into the vault

Generation creates an asset note, but it does not decide campaign placement. When the image becomes attached to an NPC, location, session, or handout:

1. Read/use `ttrpg-vault-authoring`.
2. Link the image asset note from the durable campaign note.
3. Embed the PNG where useful.
4. Add meaningful wikilinks under the asset note's `## Connections` section.
5. Run `qmd update` after significant vault edits if search needs refreshing.

## Prompting guidance

See `.pi/skills/ttrpg-create-image-gen/SKILL.md` for the working prompt recipe. In short: describe intended use, subject, setting, medium/style, composition, lighting/mood, materials/textures, and explicit exclusions such as "no text, no watermark, no logos".

Official references used for the skill guidance:

- OpenAI Image generation guide: https://platform.openai.com/docs/guides/image-generation/
- OpenAI GPT Image prompting guide: https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide
- OpenAI Images API reference: https://platform.openai.com/docs/api-reference/images/create

## Safety and boundaries

- Use only on explicit user request; image generation is metered.
- Prefer original fantasy imagery.
- Do not request real-person likenesses or copyrighted/branded characters.
- Do not put generated assets outside `vault/notes/images/` unless this tool is deliberately redesigned.
