---
name: ttrpg-foundry-mcp
description: Configure, bootstrap, smoke-test, reconnect, or troubleshoot the live Foundry VTT MCP integration. Use when the user asks to connect Pi to a running Foundry world, set or rotate Foundry MCP credentials, diagnose WSL/host/TLS connectivity, verify live MCP tools, or update TheStranjer/foundry-vtt-mcp. Do not load for ordinary use of already-connected Foundry MCP tools.
compatibility: Requires Node.js/npm, Git, Foundry VTT with an active world, and iproute2 for automatic WSL host discovery.
---

# Live Foundry VTT MCP

This project runs the unmodified `TheStranjer/foundry-vtt-mcp` server through
`.pi/cli/foundry-mcp/run.sh`. Read
[`../../cli/foundry-mcp/README.md`](../../cli/foundry-mcp/README.md) before
changing setup or launcher behavior.

## Boundaries

- Do not install a Foundry module; this adapter logs in as a normal Foundry user.
- Do not ask the user to paste a durable password into chat. Have them update
  the gitignored `.env` locally. A temporary test password explicitly supplied
  by the user may be used for the requested test.
- Do not commit `.env`, `.cache/foundry-vtt-mcp/credentials.json`, the upstream
  clone, or world data.
- Keep upstream source unmodified. Put compatibility behavior in this project's
  launcher/preload, and pin reviewed upstream commits in `install.sh`.
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

4. Verify `.pi/mcp.json` points `foundry-vtt` to
   `.pi/cli/foundry-mcp/run.sh`.
5. Run the end-to-end smoke test:

   ```bash
   .pi/cli/foundry-mcp/smoke-test.sh
   ```

6. Reload Pi after changing `.pi/mcp.json`; reconnecting is sufficient for
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
bash -n .pi/cli/foundry-mcp/run.sh .pi/cli/foundry-mcp/install.sh
node --check .pi/cli/foundry-mcp/bootstrap.cjs
node --check .pi/cli/foundry-mcp/preload.cjs
.pi/cli/foundry-mcp/run.sh --print-endpoint
.pi/cli/foundry-mcp/smoke-test.sh
```

Then inspect the Pi MCP gateway:

1. `mcp({})` for server status.
2. `mcp({server: "foundry-vtt"})` for discovered tools.
3. `mcp({connect: "foundry-vtt"})` if disconnected.
4. Call a read-only tool such as `foundry_vtt_get_world`.

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
FOUNDRY_MCP_REF=<commit> .pi/cli/foundry-mcp/install.sh
.pi/cli/foundry-mcp/smoke-test.sh
```

Only change the default pin in `install.sh` after the compatibility shim and all
smoke tests pass.
