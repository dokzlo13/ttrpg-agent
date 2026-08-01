#!/usr/bin/env bash
# NOT -e: this script sources .agents/env.sh, whose probes exit non-zero as
# normal control flow. See the header of env.sh.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
export TTRPG_ROOT="$PROJECT_ROOT"
# shellcheck disable=SC1091
source "$PROJECT_ROOT/.agents/env.sh"
qmd update "$@"
