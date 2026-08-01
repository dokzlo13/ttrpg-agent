// pi tool registration only. See .agents/cli/image-gen/spec.mjs for the
// canonical description, prompt guidelines and parameter set.
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { paramsToTypebox, type ToolSpec } from "../_lib/spec-typebox.js";
// @ts-expect-error — canonical spec is plain ESM JavaScript, resolved at runtime.
import { spec as rawSpec } from "../../../.agents/cli/image-gen/spec.mjs";
// @ts-expect-error — canonical implementation is plain ESM JavaScript.
import { generateImage } from "../../../.agents/cli/image-gen/image-gen.js";

const spec = rawSpec as ToolSpec;

export default function imageGenExtension(pi: ExtensionAPI) {
  pi.registerTool({
    name: spec.name,
    label: "Generate Image",
    description: spec.description,
    promptSnippet: spec.promptSnippet,
    promptGuidelines: spec.promptGuidelines,
    parameters: paramsToTypebox(spec),
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
