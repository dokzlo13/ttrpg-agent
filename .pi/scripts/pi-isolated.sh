#!/usr/bin/env bash
# pi-isolated.sh — start pi in fully isolated mode.
#
# Use this when you want pi to ignore your global ~/.pi/agent/ entirely:
# no inherited skills, no inherited AGENTS.md, no inherited settings.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

source "$PROJECT_ROOT/.pi/scripts/pi-shell.sh"

# Fully isolate pi: PI_CODING_AGENT_DIR replaces ~/.pi/agent/ as the search
# root for inherited config. Pointing it at ./.pi-home (an empty dir we create
# on demand) means nothing is inherited — pi sees only the project's .pi/.
ISOLATED_HOME="$PROJECT_ROOT/.pi-home"
mkdir -p "$ISOLATED_HOME"
export PI_CODING_AGENT_DIR="$ISOLATED_HOME"

echo "→ Running pi in isolated mode" >&2
echo "  QMD_CONFIG_DIR      = $QMD_CONFIG_DIR" >&2
echo "  TTRPG_CACHE_DIR     = $TTRPG_CACHE_DIR" >&2
echo "  PI_CODING_AGENT_DIR = $PI_CODING_AGENT_DIR" >&2

exec pi "$@"
