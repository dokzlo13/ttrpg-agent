// pi tool registration only. See .agents/cli/vault-frontmatter/spec.mjs for the
// canonical description, prompt guidelines and parameter set.
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { paramsToTypebox, type ToolSpec } from "../_lib/spec-typebox.js";
// @ts-expect-error — canonical spec is plain ESM JavaScript, resolved at runtime.
import { spec as rawSpec } from "../../../.agents/cli/vault-frontmatter/spec.mjs";
// @ts-expect-error — canonical implementation is plain ESM JavaScript.
import { runVaultFrontmatter } from "../../../.agents/cli/vault-frontmatter/vault-frontmatter.js";

const spec = rawSpec as ToolSpec;

export default function vaultFrontmatterExtension(pi: ExtensionAPI) {
  pi.registerTool({
    name: spec.name,
    label: "Vault Frontmatter",
    description: spec.description,
    promptSnippet: spec.promptSnippet,
    promptGuidelines: spec.promptGuidelines,
    parameters: paramsToTypebox(spec),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      // Anchor scanning at the repository root, never a session cwd that may
      // have drifted into a subdirectory.
      const root = process.env.TTRPG_ROOT || ctx.cwd;
      const result = await runVaultFrontmatter(params as Record<string, unknown>, root);
      return {
        content: [{ type: "text", text: result.text }],
        details: {
          action: result.action,
          scope: result.scope,
          scannedCount: result.scannedCount,
          frontmatterCount: result.frontmatterCount,
          parseErrorCount: result.parseErrorCount,
          matchedCount: result.matchedCount,
          returnedCount: result.returnedCount,
          truncated: result.truncated,
          results: result.results ?? result.result ?? [],
        },
      };
    },
  });
}
