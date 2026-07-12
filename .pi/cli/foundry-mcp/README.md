# Foundry VTT MCP launcher

Project-local launcher for the unmodified
[`TheStranjer/foundry-vtt-mcp`](https://github.com/TheStranjer/foundry-vtt-mcp)
server. It connects Pi running in Linux/WSL to an active Foundry VTT world and
adapts two upstream assumptions: HTTPS port 443 and publicly trusted TLS.

This belongs under `.pi/cli/`, not `.pi/extensions/`: it is an external stdio
MCP server launcher used by `pi-mcp-adapter`, not a Pi lifecycle extension or a
replacement for the MCP gateway.

## Requirements

- Node.js and npm (the project currently runs Node.js 24)
- Git
- `ip` from `iproute2` when using WSL gateway discovery
- Network access to GitHub during the first installation
- Foundry VTT running with an active world
- A dedicated Foundry user created in that world

No Foundry-side module is required. The installer removes build-only
dependencies after compilation.

## Configuration

Put secrets and local connection settings in the gitignored project `.env`:

```dotenv
FOUNDRY_MCP_USER=MCP
FOUNDRY_MCP_PASSWORD='replace-me'
FOUNDRY_MCP_HOST=auto
FOUNDRY_MCP_PORT=30000
FOUNDRY_MCP_ALLOW_SELF_SIGNED=true
```

`FOUNDRY_MCP_HOST` is deliberately only a host selector:

| Value | Behavior |
|---|---|
| `auto` | Under WSL, discover the Windows host from the current default route |
| `localhost` | Connect directly to localhost |
| `foundry.example.test` | Connect directly to a named host |
| `172.21.64.1` | Connect directly to an IP address |

Configure the numeric port separately with `FOUNDRY_MCP_PORT`. HTTPS/WSS is
fixed because that is what the upstream adapter supports; there is no separate
protocol or full-endpoint setting.

Optional identity settings:

```dotenv
FOUNDRY_MCP_WORLD=breathing-earth-5e
FOUNDRY_MCP_USER_ID=FoundryDocumentId
```

Normally use a username, not `FOUNDRY_MCP_USER_ID`: the bootstrap resolves the
exact username to the current world's internal user ID each time the MCP server
starts.

The Foundry user must already exist. The launcher does not create users or
change their roles/passwords. Use a dedicated Gamemaster user when Pi needs all
read/write tools; use a less privileged user when only limited access is wanted.

## Lifecycle

`.pi/mcp.json` launches `run.sh`. On each start it:

1. loads `.env`;
2. resolves the endpoint, including current WSL gateway discovery;
3. installs the pinned upstream revision under `.cache/` if absent;
4. reads `/api/status` and verifies that a world is active;
5. resolves `FOUNDRY_MCP_USER` through Foundry's join data;
6. atomically writes `.cache/foundry-vtt-mcp/credentials.json` with mode `0600`;
7. starts the unmodified upstream MCP server with the targeted compatibility preload.

The launcher constructs one internal HTTPS endpoint from the resolved host and
port. The preload changes TLS/port handling only for that endpoint; other HTTPS
requests in the process retain normal certificate verification.

## Install and test

Installation normally happens automatically. To prepare explicitly:

```bash
.pi/cli/foundry-mcp/install.sh
```

Inspect endpoint resolution without installing dependencies or requiring
credentials:

```bash
.pi/cli/foundry-mcp/run.sh --print-endpoint
```

Run an end-to-end bootstrap, MCP initialization, tool-list, and read-only world
smoke test:

```bash
.pi/cli/foundry-mcp/smoke-test.sh
```

Expected summary:

```json
{
  "ok": true,
  "toolCount": 36,
  "testedTool": "get_world",
  "contentItems": 1
}
```

## Upstream updates

`install.sh` pins a known-good upstream commit rather than silently tracking
`main`. This keeps MCP startup reproducible without maintaining a fork. To test
a newer revision:

```bash
FOUNDRY_MCP_REF=<commit> .pi/cli/foundry-mcp/install.sh
.pi/cli/foundry-mcp/smoke-test.sh
```

After review, update the default commit in `install.sh`.

## Password rotation

Change the password in Foundry User Management and
`FOUNDRY_MCP_PASSWORD` in `.env`, then restart/reconnect the MCP server. The
credentials cache is regenerated on the next start.
