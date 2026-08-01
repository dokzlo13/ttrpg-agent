// pi tool registration only. Description, prompt guidelines and the parameter
// set all come from the canonical spec in .agents/cli/query-5etools/spec.mjs,
// which also drives the CLI. Do not restate them here.
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { paramsToTypebox, type ToolSpec } from "../_lib/spec-typebox.js";
// @ts-expect-error — canonical spec is plain ESM JavaScript, resolved at runtime.
import { spec as rawSpec } from "../../../.agents/cli/query-5etools/spec.mjs";
// @ts-expect-error — canonical implementation is plain ESM JavaScript.
import { query5etools } from "../../../.agents/cli/query-5etools/query-5etools.js";

const spec = rawSpec as ToolSpec;

export default function query5etoolsExtension(pi: ExtensionAPI) {
  pi.registerTool({
    name: spec.name,
    label: "Query 5etools",
    description: spec.description,
    promptSnippet: spec.promptSnippet,
    promptGuidelines: spec.promptGuidelines,
    parameters: paramsToTypebox(spec),
    async execute(_toolCallId, params) {
      const result = await query5etools(params as Record<string, unknown>);
      // `details` is not sent to the model — only `content` is. Putting the
      // truncation notice only in details meant pi's model read "Found 10
      // creatures" when 19 matched. Same rule as the CLI: no silent caps.
      const text = result.truncated
        ? `${result.text}\n\nNOTE: truncated — showing ${result.returnedCount} of ${result.totalMatches} matches. Raise \`limit\` to see the rest.`
        : result.text;
      return {
        content: [{ type: "text", text }],
        details: {
          query: result.query,
          totalMatches: result.totalMatches,
          returnedCount: result.returnedCount,
          truncated: result.truncated,
          results: result.results,
        },
      };
    },
  });
}
