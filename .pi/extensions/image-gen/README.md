# image-gen extension

Project-local Pi extension that exposes one tool:

- `generate_image`

## Purpose

Wrap the OpenAI Images API as a Pi tool: generate one image, save it under
`vault/notes/images/`, and write an adjacent qmd-indexable Markdown asset note
containing the prompt, parameters, and adoption notes.

The richer prompting and adoption workflow lives in
`.pi/skills/ttrpg-create-image-gen/SKILL.md`. This README covers only the tool
surface and tests.

## Requirements

- Node ≥ 18 (uses built-in `fetch` and `node:test`).
- `OPENAI_API_KEY` in the project `.env` or exported in the shell.
- No other npm dependencies. Pi provides `@mariozechner/pi-coding-agent` and
  `typebox` at runtime; the OpenAI call uses built-in `fetch`.

## Tool parameters

`generate_image` parameters:

| Param | Type | Default | Notes |
|---|---|---|---|
| `subject` | string (required) | — | Full image prompt; prefer a complete prompt over a bare phrase. |
| `title` | string | derived from prompt | Title used in the asset note. |
| `slug` | string | derived from title/prompt | Filename slug base. |
| `dest` | string | env `TTRPG_IMAGE_OUTPUT_DIR` or `vault/notes/images` | Must resolve under `vault/notes/images`. |
| `model` | string | env `TTRPG_IMAGE_MODEL` or `gpt-image-1` | OpenAI image model. |
| `size` | string | env `TTRPG_IMAGE_SIZE` or `1024x1024` | e.g. `1024x1024`, `1536x1024`, `1024x1536`. |
| `quality` | string | env `TTRPG_IMAGE_QUALITY` or `auto` | `low`, `auto`, or `high`. |
| `outputFormat` | enum | env `TTRPG_IMAGE_OUTPUT_FORMAT` or `png` | `png`, `jpeg`, or `webp`. |
| `dryRun` | boolean | `false` | Plan paths and metadata without calling OpenAI or writing files. |

The tool returns:

- `content[0].text` — `image: ...\nnote: ...\nembed: ...`
- `details` — `{ dryRun, imagePath, notePath, markdownEmbed, created, request, response }`

## Output

Each generation writes two files:

```text
vault/notes/images/<slug>-<8-hex>.png
vault/notes/images/<slug>-<8-hex>.md
```

The `.md` note contains:

- Obsidian frontmatter (`type: handout`, `source: agent`, `created`, `tags`,
  `status: draft`, `asset_kind: image`, plus model/size/quality/output_format).
- An Obsidian image embed.
- The exact prompt.
- Generation parameters as fenced JSON.
- Sanitized response metadata (`created`, `revised_prompt`, `usage`) if returned.
- `## Adoption Notes`, `## Connections`, `## Sources` sections.

The tool never stores the API key or raw base64 in the Markdown note.

## Tests

Run the unit tests with no extra dependencies:

```bash
node --test .pi/extensions/image-gen/*.test.js
```

The suite covers `parseDotenv`, `slugify`, `titleFromPrompt`,
`ensureUnderImagesDir`, deterministic `planAsset`, `buildAssetNote` shape,
`generateImage` dry-run path/empty-subject/dest-rejection.

## Implementation notes

- `index.ts` registers the tool and TypeBox parameter schema. Mirrors
  `.pi/extensions/query-5etools/index.ts` shape.
- `image-gen.js` is hand-written ESM (no build step, matching
  `.pi/extensions/query-5etools/query-5etools.js`).
- OpenAI call uses `fetch("POST /v1/images/generations")` with
  `response_format: "b64_json"` semantics; falls back to fetching `image.url`
  if the API returns one.
- `findProjectRoot()` honors `TTRPG_ROOT` (used in tests) and otherwise walks
  upward looking for `.pi/extensions/image-gen` or a `.git` + `.env.example`
  pair.

## Don't

- Don't call this tool without an explicit user request; it is metered.
- Don't request real-person likenesses or copyrighted/branded characters.
- Don't put generated assets outside `vault/notes/images/`.
- Don't store secrets or raw base64 in asset notes.
