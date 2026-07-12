#!/usr/bin/env bash
set -euo pipefail

CLI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$CLI_DIR/../../.." && pwd)"

# Follow the project's existing simple KEY=value dotenv convention while
# preserving explicit process-level overrides.
declare -A INHERITED_FOUNDRY_ENV=()
for key in \
  FOUNDRY_MCP_HOST FOUNDRY_MCP_PORT \
  FOUNDRY_MCP_USER FOUNDRY_MCP_USER_ID FOUNDRY_MCP_PASSWORD \
  FOUNDRY_MCP_WORLD FOUNDRY_MCP_CONNECTION_ID \
  FOUNDRY_MCP_ALLOW_SELF_SIGNED FOUNDRY_MCP_UPSTREAM_DIR FOUNDRY_CREDENTIALS; do
  if [[ -v "$key" ]]; then INHERITED_FOUNDRY_ENV["$key"]="${!key}"; fi
done
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi
for key in "${!INHERITED_FOUNDRY_ENV[@]}"; do
  printf -v "$key" '%s' "${INHERITED_FOUNDRY_ENV[$key]}"
  export "$key"
done
unset INHERITED_FOUNDRY_ENV key

UPSTREAM_DIR="${FOUNDRY_MCP_UPSTREAM_DIR:-$PROJECT_ROOT/.cache/foundry-vtt-mcp/upstream}"
CREDENTIALS_PATH="${FOUNDRY_CREDENTIALS:-$PROJECT_ROOT/.cache/foundry-vtt-mcp/credentials.json}"
PRELOAD="$CLI_DIR/preload.cjs"
BOOTSTRAP="$CLI_DIR/bootstrap.cjs"
SERVER="$UPSTREAM_DIR/build/server.js"

resolve_endpoint() {
  local host="${FOUNDRY_MCP_HOST:-auto}"
  local port="${FOUNDRY_MCP_PORT:-30000}"

  if [[ ! "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
    printf '[foundry-vtt-mcp] FOUNDRY_MCP_PORT must be a number from 1 to 65535.\n' >&2
    return 1
  fi

  if [[ "$host" == "auto" ]]; then
    if ! grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
      printf '[foundry-vtt-mcp] FOUNDRY_MCP_HOST=auto is only supported under WSL. Set a hostname or IP address.\n' >&2
      return 1
    fi
    host="$(ip route show default | awk 'NR == 1 {print $3}')"
    if [[ -z "$host" ]]; then
      printf '[foundry-vtt-mcp] Could not detect the Windows host from WSL. Set FOUNDRY_MCP_HOST explicitly.\n' >&2
      return 1
    fi
  elif [[ "$host" == *://* || "$host" == *:* || "$host" == */* ]]; then
    printf '[foundry-vtt-mcp] FOUNDRY_MCP_HOST must be auto, a hostname, or an IP address; configure the port separately.\n' >&2
    return 1
  fi

  # The upstream adapter itself supports HTTPS/WSS only.
  printf 'https://%s:%s\n' "$host" "$port"
}

FOUNDRY_MCP_RESOLVED_ENDPOINT="$(resolve_endpoint)"
export FOUNDRY_MCP_RESOLVED_ENDPOINT

if [[ "${1:-}" == "--print-endpoint" ]]; then
  printf '%s\n' "$FOUNDRY_MCP_RESOLVED_ENDPOINT"
  exit 0
fi
export FOUNDRY_MCP_UPSTREAM_DIR="$UPSTREAM_DIR"
export FOUNDRY_CREDENTIALS="$CREDENTIALS_PATH"
export FOUNDRY_MCP_ALLOW_SELF_SIGNED="${FOUNDRY_MCP_ALLOW_SELF_SIGNED:-false}"

if [[ ! -f "$SERVER" ]]; then
  "$CLI_DIR/install.sh"
fi

# Resolve the configured Foundry username to its stable document ID and write
# the exact credentials schema expected by the unmodified upstream server.
node "$BOOTSTRAP"

export NODE_OPTIONS="--require=$PRELOAD${NODE_OPTIONS:+ $NODE_OPTIONS}"
exec node "$SERVER"
