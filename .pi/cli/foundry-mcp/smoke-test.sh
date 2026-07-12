#!/usr/bin/env bash
set -euo pipefail

CLI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$CLI_DIR/../../.." && pwd)"
UPSTREAM_DIR="${FOUNDRY_MCP_UPSTREAM_DIR:-$PROJECT_ROOT/.cache/foundry-vtt-mcp/upstream}"

if [[ ! -f "$UPSTREAM_DIR/build/server.js" ]]; then
  "$CLI_DIR/install.sh"
fi

exec node "$CLI_DIR/smoke-test.mjs"
