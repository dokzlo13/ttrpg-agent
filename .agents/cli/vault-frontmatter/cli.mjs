#!/usr/bin/env node
// CLI surface for vault_frontmatter.
import { runCli } from "../_lib/spec-args.mjs";
import { spec } from "./spec.mjs";
import { runVaultFrontmatter } from "./vault-frontmatter.js";

// runVaultFrontmatter resolves vault/library paths relative to the cwd it is
// handed. Pass the repository root explicitly — never process.cwd(), which
// would silently rescope the search when the tool is invoked from a subdirectory.
const root = process.env.TTRPG_ROOT || process.cwd();

await runCli(spec, process.argv.slice(2), async (values) => {
  const result = await runVaultFrontmatter(values, root);
  if (!result.truncated) return result.text;
  // Same rule as query-5etools: a cap the caller cannot see is a cap that gets
  // mistaken for a complete answer.
  // The total lives under a different key per action: find -> matchedCount,
  // fields -> fieldCount, values -> uniqueValueCount. Printing "of undefined"
  // is worse than printing nothing.
  const total = result.matchedCount ?? result.fieldCount ?? result.uniqueValueCount;
  const of = total === undefined ? "" : ` of ${total}`;
  return (
    `${result.text}\n\n` +
    `NOTE: truncated — showing ${result.returnedCount}${of} matches. ` +
    `Raise --limit to see the rest.`
  );
});
