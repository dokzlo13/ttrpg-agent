#!/usr/bin/env node

import {fileURLToPath, pathToFileURL} from "node:url";
import path from "node:path";

const cliDir = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(cliDir, "../../..");
const upstreamDir = process.env.FOUNDRY_MCP_UPSTREAM_DIR || path.join(projectRoot, ".cache/vendor/foundry-vtt-mcp");
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

// The SDK's StdioClientTransport.close() aborts its controller and drops the
// child reference WITHOUT killing it, so the server survives the test and stays
// logged into Foundry as a GM user. A lingering MCP session is not harmless: it
// is `isGM`, so Foundry's `game.users.activeGM` election can pick it, and
// socketlib routes every executeAsGM call to `activeGM?.isSelf` alone. Because
// the headless server registers no socketlib handlers, player-initiated
// midi-qol/CPR workflows then hang forever with no error. Reap it explicitly.
// Captured after connect() — the child does not exist until then.
let childPid = null;

async function reapServer() {
  if (!childPid) {
    console.error("[smoke-test] WARNING: could not determine the MCP server pid; check for a stray `node .../build/server.js` holding a GM session.");
    return;
  }
  const alive = () => { try { process.kill(childPid, 0); return true; } catch { return false; } };
  for (const signal of ["SIGTERM", "SIGKILL"]) {
    if (!alive()) return;
    try { process.kill(childPid, signal); } catch { return; }
    for (let i = 0; i < 20 && alive(); i++) await new Promise(r => setTimeout(r, 100));
  }
  if (alive()) console.error(`[smoke-test] WARNING: MCP server pid ${childPid} survived SIGKILL — kill it manually, it is holding a GM session.`);
}

try {
  await client.connect(transport);
  childPid = transport.pid ?? transport._process?.pid ?? null;
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
  await reapServer();
}
