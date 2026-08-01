#!/usr/bin/env node

import {fileURLToPath, pathToFileURL} from "node:url";
import path from "node:path";

const cliDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(cliDir, "../../..");
const upstreamDir = process.env.FOUNDRY_MCP_UPSTREAM_DIR || path.join(projectRoot, ".cache/foundry-vtt-mcp/upstream");
const sdkRoot = path.join(upstreamDir, "node_modules/@modelcontextprotocol/sdk/dist/client");
const {Client} = await import(pathToFileURL(path.join(sdkRoot, "index.js")));
const {StdioClientTransport} = await import(pathToFileURL(path.join(sdkRoot, "stdio.js")));

const transport = new StdioClientTransport({
  command: path.join(cliDir, "run.sh"),
  stderr: "inherit",
});
const client = new Client(
  {name: "ttrpg-agent-foundry-smoke-test", version: "1.0.0"},
  {capabilities: {}},
);

try {
  await client.connect(transport);
  const listed = await client.listTools();
  const world = await client.callTool({name: "get_world", arguments: {}});
  if (world.isError) throw new Error("Foundry get_world returned an MCP error");
  console.log(JSON.stringify({
    ok: true,
    toolCount: listed.tools.length,
    testedTool: "get_world",
    contentItems: world.content?.length || 0,
  }, null, 2));
} finally {
  await client.close();
}
