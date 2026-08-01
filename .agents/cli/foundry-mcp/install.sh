#!/usr/bin/env bash
set -euo pipefail

CLI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$CLI_DIR/../../.." && pwd)"

# `npm ci` below must not leak into the user's ~/.npm. When this script is run
# outside the env contract (i.e. not via .agents/bin/foundry-mcp), apply it.
if [ -z "${TTRPG_ROOT:-}" ]; then
  export TTRPG_ROOT="$PROJECT_ROOT"
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.agents/env.sh"
fi

UPSTREAM_DIR="${FOUNDRY_MCP_UPSTREAM_DIR:-$PROJECT_ROOT/.cache/vendor/foundry-vtt-mcp}"
# Fallback pin, used only when this script is run directly. The AUTHORITATIVE
# pin is the [[vendor]] block for foundry-vtt-mcp in .agents/manifest.toml —
# move it with `.agents/harness vendor update foundry-vtt-mcp <ref>` and keep
# the two in sync (`.agents/harness vendor status` compares them).
UPSTREAM_REF="${FOUNDRY_MCP_REF:-f52ee5bc2329db0dd5007aab55b46ec0a9710d58}"
REPOSITORY="https://github.com/TheStranjer/foundry-vtt-mcp.git"

if [[ ! -d "$UPSTREAM_DIR/.git" ]]; then
  rm -rf "$UPSTREAM_DIR"
  mkdir -p "$(dirname "$UPSTREAM_DIR")"
  git clone "$REPOSITORY" "$UPSTREAM_DIR"
fi

git -C "$UPSTREAM_DIR" fetch --quiet origin "$UPSTREAM_REF"
git -C "$UPSTREAM_DIR" checkout --quiet --detach "$UPSTREAM_REF"

(
  cd "$UPSTREAM_DIR"
  npm ci
  npm run build
  # Build tools are not needed by the running stdio server.
  npm prune --omit=dev --audit=false --package-lock=false
)

printf '[foundry-vtt-mcp] Installed upstream revision %s\n' "$(git -C "$UPSTREAM_DIR" rev-parse HEAD)" >&2
