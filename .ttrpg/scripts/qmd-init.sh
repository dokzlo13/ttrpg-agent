#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/.ttrpg/scripts/pi-shell.sh"
qmd update "$@"
