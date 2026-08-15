"use strict";

// Compatibility shim for TheStranjer/foundry-vtt-mcp.
// Upstream currently assumes HTTPS port 443 and does not expose TLS options.
// This shim only rewrites requests aimed at the launcher's resolved endpoint
// and allows a self-signed certificate there; all other HTTPS remains strict.

const https = require("node:https");

const endpointValue = process.env.FOUNDRY_MCP_RESOLVED_ENDPOINT;
if (!endpointValue) {
  throw new Error("FOUNDRY_MCP_RESOLVED_ENDPOINT was not prepared by the launcher");
}

const endpoint = new URL(endpointValue);
if (endpoint.protocol !== "https:") {
  throw new Error("Resolved Foundry MCP endpoint must use https:");
}

const endpointPort = Number(endpoint.port || 443);
const allowSelfSigned = process.env.FOUNDRY_MCP_ALLOW_SELF_SIGNED === "true";
const originalRequest = https.request;

function rewriteOptions(input) {
  if (!input || typeof input !== "object" || input instanceof URL) return input;

  let hostname = input.hostname || input.host;
  let port = Number(input.port || 443);

  // Upstream supplies "host:port" as hostname while separately hardcoding 443.
  if (typeof hostname === "string" && hostname === endpoint.host) {
    hostname = endpoint.hostname;
    port = endpointPort;
  }

  // ws supplies an already parsed hostname and port.
  const targetsEndpoint = hostname === endpoint.hostname && port === endpointPort;
  if (!targetsEndpoint) return input;

  const rewritten = {
    ...input,
    hostname: endpoint.hostname,
    port: endpointPort,
    rejectUnauthorized: !allowSelfSigned,
  };
  if ("host" in rewritten) delete rewritten.host;
  return rewritten;
}

// Foundry v14 renamed the POST /join body field `userid` -> `userId`
// (`sessions.authenticateUser`: `const {userId, password} = req.body`). Upstream
// still sends the lowercase form, so v14 looks up `undefined` and answers
// 401 JOIN.ErrorUserDoesNotExist. Add the camelCase key while keeping the legacy
// one, so the payload authenticates on both v13 and v14. Content-Length has to be
// corrected too, since upstream sized it for the original body.
function patchJoinPayload(request) {
  const originalWrite = request.write.bind(request);
  const originalEnd = request.end.bind(request);
  let done = false;

  const fix = chunk => {
    if (done || !chunk || typeof chunk === "function") return chunk;
    try {
      const body = Buffer.isBuffer(chunk) ? chunk.toString("utf8") : String(chunk);
      const parsed = JSON.parse(body);
      if (parsed?.action !== "join" || !parsed.userid || parsed.userId !== undefined) return chunk;
      parsed.userId = parsed.userid;
      const rewritten = JSON.stringify(parsed);
      if (!request.headersSent) request.setHeader("Content-Length", Buffer.byteLength(rewritten));
      done = true;
      return rewritten;
    } catch {
      return chunk;
    }
  };

  request.write = (chunk, ...rest) => originalWrite(fix(chunk), ...rest);
  request.end = (chunk, ...rest) => originalEnd(fix(chunk), ...rest);
  return request;
}

https.request = function patchedRequest(input, ...args) {
  const options = rewriteOptions(input);
  const request = originalRequest.call(this, options, ...args);
  const isJoinPost = options && typeof options === "object" && !(options instanceof URL)
    && options.path === "/join" && String(options.method).toUpperCase() === "POST";
  return isJoinPost ? patchJoinPayload(request) : request;
};

https.get = function patchedGet(input, ...args) {
  const request = https.request(input, ...args);
  request.end();
  return request;
};
