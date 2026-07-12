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

https.request = function patchedRequest(input, ...args) {
  return originalRequest.call(this, rewriteOptions(input), ...args);
};

https.get = function patchedGet(input, ...args) {
  const request = https.request(input, ...args);
  request.end();
  return request;
};
