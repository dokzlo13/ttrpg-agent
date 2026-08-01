// Canonical agent-facing parameter spec for generate_image / generate-image.

export const spec = {
  name: "generate_image",
  cli: "generate-image",
  description:
    "Generate one OpenAI image for TTRPG prep, save it under vault/notes/images/, and write an adjacent qmd-indexable Markdown asset note with prompt and params.",
  promptSnippet:
    "Generate one TTRPG illustration via OpenAI Images and persist a paired PNG + Markdown asset note under vault/notes/images/.",
  promptGuidelines: [
    "Only call generate_image on an explicit user request for image generation; this is metered and the lowest-priority capability.",
    "Pass dryRun=true first when the prompt is uncertain to plan paths without spending credits.",
    "Provide a complete image prompt in `subject` following the recipe in the ttrpg-create-image-gen skill: action+intent, subject, setting, medium/style, composition, lighting/mood, key details, exclusions.",
    "Defaults come from TTRPG_IMAGE_* env vars; only set model/size/quality/outputFormat when deviating per call.",
    "Never request real-person likenesses or copyrighted/branded characters; offer an original archetypal alternative instead.",
  ],
  params: {
    subject: {
      type: "string",
      describe: "Full image prompt. Prefer a complete prompt over a bare subject phrase.",
    },
    title: {
      type: "string",
      optional: true,
      describe:
        "Human-readable title for the adjacent Markdown asset note. Defaults to a title derived from the prompt.",
    },
    slug: {
      type: "string",
      optional: true,
      describe: "Filename slug base. Defaults to a slug derived from the title/prompt.",
    },
    dest: {
      type: "string",
      optional: true,
      describe:
        "Output directory under vault/notes/images. Defaults to env TTRPG_IMAGE_OUTPUT_DIR or vault/notes/images.",
    },
    model: {
      type: "string",
      optional: true,
      describe: "OpenAI image model. Defaults to env TTRPG_IMAGE_MODEL or gpt-image-1.",
    },
    size: {
      type: "string",
      optional: true,
      describe:
        "Image size, e.g. 1024x1024, 1536x1024, 1024x1536. Defaults to env TTRPG_IMAGE_SIZE or 1024x1024.",
    },
    quality: {
      type: "string",
      optional: true,
      describe: "Image quality: low, auto, or high. Defaults to env TTRPG_IMAGE_QUALITY or auto.",
    },
    outputFormat: {
      type: "string",
      optional: true,
      describe: "Output format: png, jpeg, or webp. Defaults to env TTRPG_IMAGE_OUTPUT_FORMAT or png.",
    },
    dryRun: {
      type: "boolean",
      optional: true,
      describe: "Plan paths and metadata without calling OpenAI or writing files.",
    },
  },
};
