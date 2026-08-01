#!/usr/bin/env node
// CLI surface for generate_image. Metered: only run on an explicit request.
import { runCli } from "../_lib/spec-args.mjs";
import { spec } from "./spec.mjs";
import { generateImage } from "./image-gen.js";

await runCli(spec, process.argv.slice(2), async (values) => {
  const result = await generateImage(values);
  const prefix = result.dryRun ? "dry-run: " : "";
  return [
    `${prefix}image: ${result.imagePath}`,
    `${prefix}note:  ${result.notePath}`,
    `${prefix}embed: ${result.markdownEmbed}`,
  ].join("\n");
});
