#!/usr/bin/env node
// CLI surface for query_5etools. Argv parsing is derived from spec.params, the
// same object pi's extension turns into a typebox schema.
import { runCli } from "../_lib/spec-args.mjs";
import { spec } from "./spec.mjs";
import { query5etools } from "./query-5etools.js";

await runCli(spec, process.argv.slice(2), async (values) => {
  const result = await query5etools(values);
  if (!result.truncated) return result.text;
  // Never cap silently. pi's tool surfaces `truncated` in its details payload;
  // the CLI has only stdout, so say it in words — otherwise "Found 10" reads as
  // "there are 10" when there are 19.
  return (
    `${result.text}\n\n` +
    `NOTE: truncated — showing ${result.returnedCount} of ${result.totalMatches} matches. ` +
    `Raise --limit to see the rest.`
  );
});
