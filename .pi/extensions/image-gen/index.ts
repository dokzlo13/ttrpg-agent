import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "typebox";
import { generateImage } from "./image-gen.js";

export default function imageGenExtension(pi: ExtensionAPI) {
  pi.registerTool({
    name: "generate_image",
    label: "Generate Image",
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
    parameters: Type.Object({
      subject: Type.String({
        description: "Full image prompt. Prefer a complete prompt over a bare subject phrase.",
      }),
      title: Type.Optional(
        Type.String({ description: "Human-readable title for the adjacent Markdown asset note. Defaults to a title derived from the prompt." }),
      ),
      slug: Type.Optional(
        Type.String({ description: "Filename slug base. Defaults to a slug derived from the title/prompt." }),
      ),
      dest: Type.Optional(
        Type.String({ description: "Output directory under vault/notes/images. Defaults to env TTRPG_IMAGE_OUTPUT_DIR or vault/notes/images." }),
      ),
      model: Type.Optional(
        Type.String({ description: "OpenAI image model. Defaults to env TTRPG_IMAGE_MODEL or gpt-image-1." }),
      ),
      size: Type.Optional(
        Type.String({ description: "Image size, e.g. 1024x1024, 1536x1024, 1024x1536. Defaults to env TTRPG_IMAGE_SIZE or 1024x1024." }),
      ),
      quality: Type.Optional(
        Type.String({ description: "Image quality: low, auto, or high. Defaults to env TTRPG_IMAGE_QUALITY or auto." }),
      ),
      outputFormat: Type.Optional(
        Type.String({ description: "Output format: png, jpeg, or webp. Defaults to env TTRPG_IMAGE_OUTPUT_FORMAT or png." }),
      ),
      dryRun: Type.Optional(
        Type.Boolean({ description: "Plan paths and metadata without calling OpenAI or writing files." }),
      ),
    }),
    async execute(_toolCallId, params) {
      const result = await generateImage(params as Record<string, unknown>);
      const lines = [
        `${result.dryRun ? "dry-run: " : ""}image: ${result.imagePath}`,
        `${result.dryRun ? "dry-run: " : ""}note:  ${result.notePath}`,
        `${result.dryRun ? "dry-run: " : ""}embed: ${result.markdownEmbed}`,
      ];
      return {
        content: [{ type: "text", text: lines.join("\n") }],
        details: {
          dryRun: result.dryRun,
          imagePath: result.imagePath,
          notePath: result.notePath,
          markdownEmbed: result.markdownEmbed,
          created: result.created,
          request: result.request,
          response: result.response,
        },
      };
    },
  });
}
