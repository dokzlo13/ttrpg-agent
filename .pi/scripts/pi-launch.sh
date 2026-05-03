#!/usr/bin/env bash
# pi-launch.sh — start pi with qmd pinned to the project and reusable caches
# under project-local .cache, not the user's global ~/.cache.
#
# Use this by default. The agent's settings (.pi/settings.json) and skills are
# already project-local; this script also localizes qmd for any direct/manual
# qmd use. Inside pi, the bash tool sources .pi/scripts/pi-shell.sh
# automatically, so the agent can just use plain `qmd ...`.
#
# For *full* isolation that ignores the user's global ~/.pi/agent/, use
# pi-isolated.sh instead.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

source "$PROJECT_ROOT/.pi/scripts/pi-shell.sh"

# Run pi. Args after `--` go straight through.
exec pi "$@"
