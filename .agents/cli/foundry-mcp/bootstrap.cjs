"use strict";

const fs = require("node:fs");
const https = require("node:https");
const path = require("node:path");

const endpoint = new URL(process.env.FOUNDRY_MCP_RESOLVED_ENDPOINT || "");
const upstreamDir = process.env.FOUNDRY_MCP_UPSTREAM_DIR;
const credentialsPath = process.env.FOUNDRY_CREDENTIALS;
const username = process.env.FOUNDRY_MCP_USER;
const configuredUserId = process.env.FOUNDRY_MCP_USER_ID;
const password = process.env.FOUNDRY_MCP_PASSWORD;
const allowSelfSigned = process.env.FOUNDRY_MCP_ALLOW_SELF_SIGNED === "true";

if (endpoint.protocol !== "https:") throw new Error("Resolved Foundry MCP endpoint must use https:");
if (!upstreamDir) throw new Error("FOUNDRY_MCP_UPSTREAM_DIR is required");
if (!credentialsPath) throw new Error("FOUNDRY_CREDENTIALS is required");
if (!password) throw new Error("FOUNDRY_MCP_PASSWORD is required");
if (!configuredUserId && !username) {
  throw new Error("Set FOUNDRY_MCP_USER (preferred) or FOUNDRY_MCP_USER_ID");
}

const requestOptions = {
  hostname: endpoint.hostname,
  port: Number(endpoint.port || 443),
  rejectUnauthorized: !allowSelfSigned,
};

function get(pathname) {
  return new Promise((resolve, reject) => {
    const request = https.get({...requestOptions, path: pathname}, response => {
      const chunks = [];
      response.on("data", chunk => chunks.push(chunk));
      response.on("end", () => resolve({
        statusCode: response.statusCode,
        headers: response.headers,
        body: Buffer.concat(chunks).toString("utf8"),
      }));
    });
    request.on("error", reject);
  });
}

async function getWorldStatus() {
  const response = await get("/api/status");
  if (response.statusCode !== 200) {
    throw new Error(`Foundry /api/status returned HTTP ${response.statusCode}`);
  }
  const status = JSON.parse(response.body);
  if (!status.world) throw new Error("Foundry has no active world");
  return status;
}

async function resolveUserId() {
  if (configuredUserId) return configuredUserId;

  const join = await get("/join");
  const cookies = join.headers["set-cookie"] || [];
  const match = cookies.join(";").match(/session=([^;]+)/);
  if (!match) throw new Error("Foundry /join did not issue a session cookie");

  const WebSocket = require(path.join(upstreamDir, "node_modules", "ws"));
  const wsProtocol = endpoint.protocol === "https:" ? "wss:" : "ws:";
  const socketUrl = `${wsProtocol}//${endpoint.host}/socket.io/?session=${match[1]}&EIO=4&transport=websocket`;

  const joinData = await new Promise((resolve, reject) => {
    const socket = new WebSocket(socketUrl, {rejectUnauthorized: !allowSelfSigned});
    const timer = setTimeout(() => {
      socket.terminate();
      reject(new Error("Timed out while requesting Foundry join data"));
    }, 10000);

    socket.on("error", reject);
    socket.on("message", buffer => {
      const packet = buffer.toString();
      if (packet.startsWith("0")) socket.send("40");
      else if (packet.startsWith('42["session"')) socket.send('421["getJoinData"]');
      else if (packet.startsWith("431")) {
        clearTimeout(timer);
        socket.close();
        resolve(JSON.parse(packet.slice(3))[0]);
      }
    });
  });

  const matches = joinData.users.filter(user => user.name === username);
  if (matches.length !== 1) {
    const available = joinData.users.map(user => user.name).join(", ");
    throw new Error(`Expected exactly one Foundry user named "${username}"; available users: ${available}`);
  }
  return matches[0]._id || matches[0].id;
}

async function main() {
  const status = await getWorldStatus();
  const userId = await resolveUserId();
  const configuredWorld = process.env.FOUNDRY_MCP_WORLD;
  if (configuredWorld && configuredWorld !== status.world) {
    throw new Error(`Active Foundry world is "${status.world}", expected "${configuredWorld}"`);
  }

  const credentials = [{
    _id: process.env.FOUNDRY_MCP_CONNECTION_ID || status.world,
    hostname: endpoint.host,
    userid: userId,
    password,
  }];

  fs.mkdirSync(path.dirname(credentialsPath), {recursive: true});
  const temporaryPath = `${credentialsPath}.${process.pid}.tmp`;
  fs.writeFileSync(temporaryPath, `${JSON.stringify(credentials, null, 2)}\n`, {mode: 0o600});
  fs.renameSync(temporaryPath, credentialsPath);
  fs.chmodSync(credentialsPath, 0o600);
  console.error(`[foundry-vtt-mcp] Prepared credentials for user "${username || userId}" in world "${status.world}" at ${endpoint.host}`);
}

main().catch(error => {
  console.error(`[foundry-vtt-mcp] Bootstrap failed: ${error.message}`);
  process.exit(1);
});
