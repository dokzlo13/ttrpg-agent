#!/usr/bin/env bash
# Shared shell setup for pi bash tool commands in this project.
# Loaded via .pi/settings.json shellCommandPrefix.

PROJECT_ROOT="$(pwd)"
if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  PROJECT_ROOT="$git_root"
fi

export TTRPG_ROOT="$PROJECT_ROOT"
export TTRPG_CLI_DIR="$PROJECT_ROOT/.pi/cli"
export TTRPG_TOOLS_DIR="$TTRPG_CLI_DIR"
export TTRPG_IMPORTS_DIR="$PROJECT_ROOT/imports"
export TTRPG_BOOKS_DIR="$PROJECT_ROOT/imports/books"
export TTRPG_SOURCE_VAULT_DIR="$PROJECT_ROOT/imports/source-vault"
export TTRPG_5ETOOLS_DIR="$PROJECT_ROOT/imports/5etools"
export TTRPG_VAULT_DIR="$PROJECT_ROOT/vault"
export TTRPG_NOTES_DIR="$PROJECT_ROOT/vault/notes"
export TTRPG_LIBRARY_DIR="$PROJECT_ROOT/vault/library/books"

# Load project-local optional feature/API settings for pi itself, pi-web-access,
# and shell tools. .env is gitignored; keep it simple KEY=value dotenv syntax.
if [ -f "$PROJECT_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/.env"
  set +a
fi

export QMD_CONFIG_DIR="$PROJECT_ROOT/.qmd"
export XDG_CACHE_HOME="$PROJECT_ROOT/.qmd"
# Prefer NVIDIA's WSL CUDA toolkit over Ubuntu's old nvidia-cuda-toolkit package
# when node-llama-cpp has to compile a local CUDA backend.
if [ -x /usr/local/cuda/bin/nvcc ]; then
  export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}"
  export CUDAToolkit_ROOT="${CUDAToolkit_ROOT:-/usr/local/cuda}"
  export CUDACXX="${CUDACXX:-/usr/local/cuda/bin/nvcc}"
  case ":$PATH:" in
    *:/usr/local/cuda/bin:*) ;;
    *) export PATH="/usr/local/cuda/bin:$PATH" ;;
  esac
fi
# qmd/node-llama-cpp treats QMD_LLAMA_GPU as an on/off switch, not a backend selector:
# false/off/none/0 force CPU; any other value lets node-llama-cpp auto-pick CUDA/Vulkan/CPU.
export QMD_LLAMA_GPU="${QMD_LLAMA_GPU:-auto}"
# Work around llama.cpp CUDA VMM pool failures on WSL2/RTX 50xx
# (cuMemAddressReserve(CUDA_POOL_VMM_MAX_SIZE) can abort despite enough VRAM).
export GGML_CUDA_NO_VMM="${GGML_CUDA_NO_VMM:-1}"
# Do not set NODE_LLAMA_CPP_CMAKE_OPTION_* here: those force node-llama-cpp
# away from its prebuilt backend and into local source builds, which are less
# stable on this WSL2/RTX 5090 setup. If a manual rebuild is needed, pass those
# options only for that one rebuild command.
# Marker auto-selects CUDA when TORCH_DEVICE is unset. Do not export a
# TTRPG_MARKER_DEVICE default here: book-ingest reads project .env for that,
# and an exported shell default would otherwise mask .env changes.

mkdir -p \
  "$QMD_CONFIG_DIR" \
  "$TTRPG_BOOKS_DIR" \
  "$TTRPG_SOURCE_VAULT_DIR" \
  "$TTRPG_5ETOOLS_DIR" \
  "$TTRPG_NOTES_DIR" \
  "$TTRPG_LIBRARY_DIR"

_pi_qmd_collection_path() {
  command qmd collection show "$1" 2>/dev/null | awk '/Path:/ {print $2; exit}'
}

_pi_qmd_collection_exists() {
  command qmd collection show "$1" >/dev/null 2>&1
}

_pi_qmd_remove_collection_if_exists() {
  local name="$1"
  if _pi_qmd_collection_exists "$name"; then
    command qmd collection remove "$name" >/dev/null 2>&1 || true
  fi
}

_pi_qmd_ensure_collection() {
  local name="$1"
  local path="$2"
  local mask="$3"
  local existing
  existing="$(_pi_qmd_collection_path "$name")"

  if [ "$existing" != "$path" ]; then
    if [ -n "$existing" ]; then
      command qmd collection remove "$name" >/dev/null 2>&1 || true
    fi
    command qmd collection add "$path" --name "$name" --mask "$mask" >/dev/null 2>&1 || true
  fi
}

_pi_qmd_ensure_config() {
  # Legacy collection names from the earlier mirror-based layout. Remove them so
  # the active qmd collections are non-overlapping and directly point at source
  # directories: notes, books, and optional archive.
  _pi_qmd_remove_collection_if_exists vault
  _pi_qmd_remove_collection_if_exists source

  _pi_qmd_ensure_collection notes "$TTRPG_NOTES_DIR" "**/*.md"
  _pi_qmd_ensure_collection books "$TTRPG_LIBRARY_DIR" "**/*.md"
  _pi_qmd_ensure_collection archive "$TTRPG_SOURCE_VAULT_DIR" "**/*.md"

  command qmd collection exclude archive >/dev/null 2>&1 || true

  command qmd context add qmd://notes "Active campaign notes and table prep under vault/notes." >/dev/null 2>&1 || true
  command qmd context add qmd://books "Ingested RPG books and supplements under vault/library/books." >/dev/null 2>&1 || true
  command qmd context add qmd://archive "Optional legacy notes under imports/source-vault; search only when explicitly requested." >/dev/null 2>&1 || true
}

_pi_qmd_ensure_index_if_missing() {
  if [ ! -f "$QMD_CONFIG_DIR/qmd/index.sqlite" ]; then
    command qmd update >&2
  fi
}

_pi_qmd_cpu_forced() {
  case "${QMD_LLAMA_GPU,,}" in
    false|off|none|disable|disabled|0) return 0 ;;
    *) return 1 ;;
  esac
}

_qmd_run() {
  local cmd="${1:-}"
  _pi_qmd_ensure_config
  case "$cmd" in
    query|search|vsearch|get|ls|status)
      _pi_qmd_ensure_index_if_missing
      ;;
  esac
  command qmd "$@"
}

qmd() {
  local cmd="${1:-}"

  # CUDA/node-llama-cpp can hard-abort on some WSL2/RTX 50xx setups. Keep GPU
  # as the default fast path, but retry LLM-backed qmd commands once on CPU.
  case "$cmd" in
    embed|vsearch|query|status)
      if ! _pi_qmd_cpu_forced && [ "${QMD_CPU_FALLBACK:-1}" != "0" ]; then
        _qmd_run "$@"
        local rc=$?
        if [ "$rc" -ne 0 ]; then
          echo "qmd $cmd failed with exit code $rc; retrying once on CPU (QMD_LLAMA_GPU=false)." >&2
          QMD_LLAMA_GPU=false NODE_LLAMA_CPP_GPU=false _qmd_run "$@"
          return $?
        fi
        return 0
      fi
      ;;
  esac

  _qmd_run "$@"
}

qmd-cpu() {
  QMD_LLAMA_GPU=false NODE_LLAMA_CPP_GPU=false _qmd_run "$@"
}
