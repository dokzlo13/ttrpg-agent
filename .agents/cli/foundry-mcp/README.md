# Foundry VTT MCP launcher

Project-local launcher for the unmodified
[`TheStranjer/foundry-vtt-mcp`](https://github.com/TheStranjer/foundry-vtt-mcp)
server. It connects a harness running in Linux/WSL to an active Foundry VTT
world and adapts two upstream assumptions: HTTPS port 443 and publicly trusted
TLS.

This is a project tool under `.agents/cli/`, not a harness extension: it is an
external stdio MCP server launcher, not a pi lifecycle extension or a
replacement for the MCP gateway.

**Always start it through `.agents/bin/foundry-mcp`.** MCP servers are spawned
by the harness itself rather than by its Bash tool, so they never see pi's
`shellCommandPrefix` or Claude's `$CLAUDE_ENV_FILE`. The hermetic entrypoint is
the only thing that applies the environment contract to this server on any
harness. The upstream clone lives at `.cache/vendor/foundry-vtt-mcp`; the
Foundry credential lives at `.agents/state/foundry-credentials.json`, which is
durable state and is never a cleanup target.

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

`.mcp.json` / `.pi/mcp.json` launch `.agents/bin/foundry-mcp`, which applies the environment contract and then execs `run.sh`. On each start it:

1. loads `.env`;
2. resolves the endpoint, including current WSL gateway discovery;
3. installs the pinned upstream revision under `.cache/vendor/foundry-vtt-mcp` if absent;
4. reads `/api/status` and verifies that a world is active;
5. resolves `FOUNDRY_MCP_USER` through Foundry's join data;
6. ensures `.agents/state/foundry-credentials.json` has the exact current
   credentials and mode `0600`, atomically rewriting it only when needed;
7. starts the unmodified upstream MCP server with the targeted compatibility preload.

The launcher constructs one internal HTTPS endpoint from the resolved host and
port. The preload changes TLS/port handling only for that endpoint; other HTTPS
requests in the process retain normal certificate verification.

## Install and test

Installation normally happens automatically. To prepare explicitly:

```bash
.agents/cli/foundry-mcp/install.sh
```

Inspect endpoint resolution without installing dependencies or requiring
credentials:

```bash
.agents/bin/foundry-mcp --print-endpoint
```

Run an end-to-end bootstrap, MCP initialization, tool-list, and read-only world
smoke test:

```bash
.agents/cli/foundry-mcp/smoke-test.sh
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
FOUNDRY_MCP_REF=<commit> .agents/cli/foundry-mcp/install.sh
.agents/cli/foundry-mcp/smoke-test.sh
```

After review, move the authoritative pin and re-sync:

```bash
.agents/harness vendor update foundry-vtt-mcp <commit>
.agents/harness vendor sync foundry-vtt-mcp
```

Then update the fallback `UPSTREAM_REF` in `install.sh` to match, and confirm
with `.agents/harness vendor status`.

## Password rotation

Change the password in Foundry User Management and
`FOUNDRY_MCP_PASSWORD` in `.env`, then restart/reconnect the MCP server. The
credentials cache is regenerated on the next start.
