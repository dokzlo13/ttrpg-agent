#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  sync.sh status
  sync.sh push-vault --dry-run|--apply
  sync.sh pull-notes --dry-run|--apply
  sync.sh pull-books --dry-run|--apply
  sync.sh pull-imports-inbox --dry-run|--apply

Environment:
  TTRPG_WINDOWS_AGENT_DIR   WSL path to the Windows mirror root, e.g. /mnt/c/TTRPG

Safety:
  push-vault mirrors WSL vault to Windows and uses --delete against the Windows mirror.
  pull-notes pulls only vault/notes from Windows to WSL, then qmd update/embed + verifies.
  pull-books explicitly pulls vault/library/books from Windows to WSL, then qmd update/embed + verifies.
  pull-imports-inbox copies only new files from Windows imports/books to WSL imports/books.
EOF
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd "$script_dir/../../../.." && pwd -P)"
cd "$project_root"

# Source the project shell setup so qmd uses the project-local config and .env is
# loaded even when this script is run manually outside Pi's bash tool wrapper.
# shellcheck disable=SC1091
source "$project_root/.pi/scripts/pi-shell.sh"

mode="${1:-}"
shift || true

apply=0
case "${1:-}" in
  --apply)
    apply=1
    shift
    ;;
  --dry-run|"")
    apply=0
    if [ "${1:-}" = "--dry-run" ]; then shift; fi
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    echo "ERROR: unknown option: $1" >&2
    usage >&2
    exit 2
    ;;
esac

if [ "$#" -ne 0 ]; then
  echo "ERROR: unexpected arguments: $*" >&2
  usage >&2
  exit 2
fi

if [ -z "${mode:-}" ] || [ "$mode" = "-h" ] || [ "$mode" = "--help" ]; then
  usage
  exit 0
fi

win_agent="${TTRPG_WINDOWS_AGENT_DIR:-}"
if [ -z "$win_agent" ]; then
  echo "ERROR: TTRPG_WINDOWS_AGENT_DIR is not set. Configure it in .env, e.g. TTRPG_WINDOWS_AGENT_DIR=/mnt/c/TTRPG" >&2
  exit 2
fi

wsl_vault="$project_root/vault"
wsl_imports="$project_root/imports"
win_vault="$win_agent/vault"
win_imports="$win_agent/imports"

require_dir() {
  local label="$1"
  local path="$2"
  if [ ! -d "$path" ]; then
    echo "ERROR: missing $label directory: $path" >&2
    exit 1
  fi
}

backup_dir() {
  local name="$1"
  local stamp
  stamp="$(date +%Y%m%d-%H%M%S)"
  echo "$project_root/.trash/wsl-sync-$name-$stamp"
}

rsync_items_only() {
  # Compact rsync dry-run output without accidentally hiding valid itemized
  # changes. Keep all non-empty lines except rsync summary boilerplate.
  awk 'NF && $0 !~ /^sending incremental file list/ && $0 !~ /^sent / && $0 !~ /^total size is/'
}

run_qmd_refresh() {
  echo
  echo "== qmd refresh =="
  qmd update && qmd embed
}

verify_sync() {
  local label="$1"
  local src="$2"
  local dst="$3"
  shift 3
  echo
  echo "== verify $label =="
  local out
  out="$(rsync -ain "$@" "$src/" "$dst/" | rsync_items_only || true)"
  if [ -z "$out" ]; then
    echo "OK: $label is in sync."
  else
    echo "WARNING: $label still has pending rsync differences:" >&2
    printf '%s\n' "$out" >&2
    return 1
  fi
}

common_vault_excludes=(
  --exclude='.tmp/'
  --exclude='.trash/'
)

case "$mode" in
  status)
    echo "Project root:      $project_root"
    echo "WSL vault:         $wsl_vault"
    echo "WSL imports:       $wsl_imports"
    echo "Windows agent dir: $win_agent"
    echo "Windows vault:     $win_vault"
    echo "Windows imports:   $win_imports"
    echo
    for pair in \
      "WSL vault:$wsl_vault" \
      "WSL imports:$wsl_imports" \
      "Windows agent dir:$win_agent" \
      "Windows vault:$win_vault" \
      "Windows imports:$win_imports"; do
      label="${pair%%:*}"
      path="${pair#*:}"
      if [ -d "$path" ]; then
        echo "OK:      $label exists"
      else
        echo "MISSING: $label -> $path"
      fi
    done
    ;;

  push-vault)
    require_dir "WSL vault" "$wsl_vault"
    mkdir -p "$win_vault"
    if [ "$apply" -eq 1 ]; then
      echo "Applying WSL vault -> Windows vault mirror. This deletes Windows-side files absent from WSL vault."
      mkdir -p "$win_agent/.sync-backups"
      rsync -a --delete --backup --backup-dir="$win_agent/.sync-backups/vault-push-$(date +%Y%m%d-%H%M%S)" \
        "${common_vault_excludes[@]}" \
        "$wsl_vault/" "$win_vault/"
      verify_sync "WSL vault -> Windows vault" "$wsl_vault" "$win_vault" --delete "${common_vault_excludes[@]}"
    else
      echo "Dry-run WSL vault -> Windows vault mirror. Apply with: $0 push-vault --apply"
      rsync -ain --delete "${common_vault_excludes[@]}" "$wsl_vault/" "$win_vault/" | rsync_items_only
    fi
    ;;

  pull-notes)
    require_dir "Windows vault notes" "$win_vault/notes"
    mkdir -p "$wsl_vault/notes"
    if [ "$apply" -eq 1 ]; then
      echo "Applying Windows vault/notes -> WSL vault/notes."
      bdir="$(backup_dir pull-notes)"
      mkdir -p "$bdir"
      rsync -a --backup --backup-dir="$bdir" \
        --exclude='.tmp/' --exclude='.trash/' \
        "$win_vault/notes/" "$wsl_vault/notes/"
      echo "Backups for overwritten WSL files, if any: $bdir"
      run_qmd_refresh
      verify_sync "Windows notes -> WSL notes" "$win_vault/notes" "$wsl_vault/notes" --exclude='.tmp/' --exclude='.trash/'
    else
      echo "Dry-run Windows vault/notes -> WSL vault/notes. Apply with: $0 pull-notes --apply"
      rsync -ain --exclude='.tmp/' --exclude='.trash/' "$win_vault/notes/" "$wsl_vault/notes/" | rsync_items_only
    fi
    ;;

  pull-books)
    require_dir "Windows vault library books" "$win_vault/library/books"
    mkdir -p "$wsl_vault/library/books"
    if [ "$apply" -eq 1 ]; then
      echo "Applying Windows vault/library/books -> WSL vault/library/books. Explicit generated-book edit preservation mode."
      bdir="$(backup_dir pull-books)"
      mkdir -p "$bdir"
      rsync -a --backup --backup-dir="$bdir" \
        --exclude='.tmp/' --exclude='.trash/' \
        "$win_vault/library/books/" "$wsl_vault/library/books/"
      echo "Backups for overwritten WSL files, if any: $bdir"
      run_qmd_refresh
      verify_sync "Windows library books -> WSL library books" "$win_vault/library/books" "$wsl_vault/library/books" --exclude='.tmp/' --exclude='.trash/'
    else
      echo "Dry-run Windows vault/library/books -> WSL vault/library/books. Apply with: $0 pull-books --apply"
      rsync -ain --exclude='.tmp/' --exclude='.trash/' "$win_vault/library/books/" "$wsl_vault/library/books/" | rsync_items_only
    fi
    ;;

  pull-imports-inbox)
    mkdir -p "$wsl_imports/books"
    if [ ! -d "$win_imports/books" ]; then
      echo "Windows imports/books does not exist; nothing to pull: $win_imports/books"
      exit 0
    fi
    if [ "$apply" -eq 1 ]; then
      echo "Applying new-files-only Windows imports/books -> WSL imports/books. Existing files are preserved."
      rsync -a --ignore-existing "$win_imports/books/" "$wsl_imports/books/"
      echo
      echo "New-files-only import sync complete. Current WSL imports/books files:"
      find "$wsl_imports/books" -maxdepth 1 -type f -printf '%f\n' | sort
    else
      echo "Dry-run new-files-only Windows imports/books -> WSL imports/books. Apply with: $0 pull-imports-inbox --apply"
      rsync -ain --ignore-existing "$win_imports/books/" "$wsl_imports/books/" | rsync_items_only
    fi
    ;;

  *)
    echo "ERROR: unknown mode: $mode" >&2
    usage >&2
    exit 2
    ;;
esac
