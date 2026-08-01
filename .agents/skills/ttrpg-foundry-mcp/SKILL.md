---
name: ttrpg-foundry-mcp
description: Configure, bootstrap, smoke-test, reconnect, or troubleshoot the live Foundry VTT MCP integration. Use when the user asks to connect the agent to a running Foundry world, set or rotate Foundry MCP credentials, diagnose WSL/host/TLS connectivity, verify live MCP tools, or update TheStranjer/foundry-vtt-mcp. Do not load for ordinary use of already-connected Foundry MCP tools.
compatibility: Requires Node.js/npm, Git, Foundry VTT with an active world, and iproute2 for automatic WSL host discovery.
---

# Live Foundry VTT MCP

This project runs the unmodified `TheStranjer/foundry-vtt-mcp` server through
the hermetic entrypoint `.agents/bin/foundry-mcp` (which applies the environment
contract, then execs `.agents/cli/foundry-mcp/run.sh`). Read
[`../../cli/foundry-mcp/README.md`](../../cli/foundry-mcp/README.md) before
changing setup or launcher behavior.

**Always launch via `.agents/bin/foundry-mcp`.** MCP servers are spawned by the
harness itself, not by its Bash tool, so they never see pi's
`shellCommandPrefix` or Claude's `$CLAUDE_ENV_FILE`. On every harness, MCP is a
Tier-1-only path — the hermetic entrypoint is the sole thing that gives the
server the right vendor and credential paths.

### How each harness is configured

| Harness | MCP config | Notes |
|---|---|---|
| pi | root `.mcp.json`, plus `.pi/mcp.json` for lifecycle overrides | supports lazy start and idle timeout |
| Claude Code | root `.mcp.json` + `enabledMcpjsonServers` in `.claude/settings.json` | entries **must** carry `"type": "stdio"` or Claude silently drops them |
| Codex | **no project scope** — register once, globally: `.agents/harness codex-mcp register foundry-vtt` | writes the user's global config, so only do this when asked |

**If a Codex session reports no Foundry tools at all, this registration is the
first thing to check** — it is the single most common cause, and it is invisible
from inside the session because Codex simply has no such tool namespace:

```bash
codex mcp get foundry-vtt                        # "No MCP server named ..." => not registered
.agents/harness doctor                           # reports the state and the fix
.agents/harness codex-mcp register foundry-vtt   # explicit, user-initiated
.agents/harness codex-mcp remove foundry-vtt     # undo
```

Codex attaches MCP servers at session start, so **restart Codex** afterwards.

## Boundaries

- Do not install a Foundry module; this adapter logs in as a normal Foundry user.
- Do not ask the user to paste a durable password into chat. Have them update
  the gitignored `.env` locally. A temporary test password explicitly supplied
  by the user may be used for the requested test.
- Do not commit `.env`, `.agents/state/foundry-credentials.json`, the upstream
  clone at `.cache/vendor/foundry-vtt-mcp`, or world data. The credential lives
  under `.agents/state/` (gitignored, and never a cleanup target) precisely so
  no cache-purge scope can destroy it.
- Keep upstream source unmodified. Put compatibility behavior in this project's
  launcher/preload. The upstream pin lives in `.agents/manifest.toml` (`[[vendor]]` foundry-vtt-mcp); `install.sh` carries a matching fallback for direct invocation.
- Prefer read-only MCP calls for smoke tests. Ask before destructive or broad
  write operations in the live world.

## Initial setup

1. Confirm Foundry is running with the target world active.
2. Have the user create a dedicated Foundry account. Gamemaster is required for
   unrestricted MCP writes; lesser roles intentionally restrict capabilities.
3. Configure the gitignored project `.env`:

   ```dotenv
   FOUNDRY_MCP_USER=MCP
   FOUNDRY_MCP_PASSWORD='replace-locally'
   FOUNDRY_MCP_HOST=auto
   FOUNDRY_MCP_PORT=30000
   FOUNDRY_MCP_ALLOW_SELF_SIGNED=true
   ```

   Host forms:
   - `auto`: under WSL, detect the Windows host from the default route.
   - `localhost`: direct local/forwarded connection.
   - a hostname or IP: direct connection.

   Keep the numeric port in `FOUNDRY_MCP_PORT`. HTTPS/WSS is fixed by the
   upstream adapter.

4. Verify the MCP registration for your harness points `foundry-vtt` at
   `.agents/bin/foundry-mcp` (see the table above), not at `run.sh` directly.
5. Run the end-to-end smoke test:

   ```bash
   .agents/cli/foundry-mcp/smoke-test.sh
   ```

6. Under pi, `/reload` after changing `.mcp.json` or `.pi/mcp.json`; under
   Claude Code or Codex, restart the session. Reconnecting is sufficient for
   `.env` or launcher-only changes.

The runtime bootstrap discovers the active world, resolves the exact configured
username to Foundry's internal user document ID, writes upstream credentials
with mode `0600`, and then starts MCP. It does not create Foundry users.

## Diagnostics

Work from the network inward:

```bash
# Resolved WSL Windows-host gateway
ip route show default

# Foundry reachability; -k is appropriate only for the configured self-signed cert
curl -ksS https://$(ip route show default | awk 'NR==1 {print $3}'):30000/api/status

# Syntax and independent end-to-end validation
bash -n .agents/cli/foundry-mcp/run.sh .agents/cli/foundry-mcp/install.sh
node --check .agents/cli/foundry-mcp/bootstrap.cjs
node --check .agents/cli/foundry-mcp/preload.cjs
.agents/bin/foundry-mcp --print-endpoint
.agents/cli/foundry-mcp/smoke-test.sh
```

Then inspect the server from inside the session. **This step is harness-specific
and is the one place where the harnesses genuinely differ**, because pi exposes
MCP lifecycle as a runtime meta-tool and the others do not:

- **pi** — a live gateway you can drive mid-session:
  1. `mcp({})` for server status.
  2. `mcp({server: "foundry-vtt"})` for discovered tools.
  3. `mcp({connect: "foundry-vtt"})` if disconnected.
- **Claude Code / Codex** — MCP servers are attached at session start and there
  is no reconnect meta-tool. If the server is missing or wedged, **restart the
  session**; that is the equivalent of pi's `mcp({connect: ...})`. Confirm the
  config first with `.agents/bin/foundry-mcp --print-endpoint`, which exercises
  the same launcher path without touching the session.

On every harness, finish by calling a read-only tool such as
`foundry_vtt_get_world`.

Common failures:

- **No active world:** launch the world, not only Foundry's setup screen.
- **Username not found:** exact-match `FOUNDRY_MCP_USER` against the active
  world's users, or set `FOUNDRY_MCP_USER_ID` explicitly.
- **Authentication failed:** synchronize the Foundry password and `.env`.
- **Certificate failure:** use `FOUNDRY_MCP_ALLOW_SELF_SIGNED=true` only for a
  known self-signed endpoint.
- **Wrong WSL address:** use `FOUNDRY_MCP_HOST=auto`; avoid pinning a transient
  WSL gateway address.
- **Direct/non-WSL environment:** set `FOUNDRY_MCP_HOST` to a hostname or IP and
  set `FOUNDRY_MCP_PORT` separately.

## Updating upstream

Do not track `main` implicitly. Test a specific commit:

```bash
FOUNDRY_MCP_REF=<commit> .agents/cli/foundry-mcp/install.sh
.agents/cli/foundry-mcp/smoke-test.sh
```

Only move the pin after the compatibility shim and all smoke tests pass, and
move it in **both** places:

```bash
.agents/harness vendor update foundry-vtt-mcp <commit>   # manifest (authoritative)
.agents/harness vendor sync foundry-vtt-mcp              # re-checkout + rebuild
```

Then update the fallback `UPSTREAM_REF` in `install.sh` to match, and confirm
with `.agents/harness vendor status`.
