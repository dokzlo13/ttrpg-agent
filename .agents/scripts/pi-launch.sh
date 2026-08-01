#!/usr/bin/env bash
# pi-launch.sh — start pi with qmd pinned to the project and reusable caches
# under project-local .cache, not the user's global ~/.cache.
#
# Use this by default. The agent's settings (.pi/settings.json) and skills are
# already project-local; this script also localizes qmd for any direct/manual
# qmd use. Inside pi, the bash tool sources .agents/env.sh automatically via
# shellCommandPrefix, so the agent can just use plain `qmd ...`.
#
# For *full* isolation that ignores the user's global ~/.pi/agent/, use
# pi-isolated.sh instead.

# NOT -e: this script sources .agents/env.sh, whose probes exit non-zero as
# normal control flow. See the header of env.sh.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

export TTRPG_ROOT="$PROJECT_ROOT"
# shellcheck disable=SC1091
source "$PROJECT_ROOT/.agents/env.sh"

# Run pi. Args after `--` go straight through.
exec pi "$@"
