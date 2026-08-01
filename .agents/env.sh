#!/usr/bin/env bash
# .agents/env.sh — the project environment contract.
#
# This is the single canonical definition of the ttrpg-agent shell environment.
# It is harness-neutral and is consumed three ways:
#
#   Tier 1 (correctness)  .agents/bin/<tool> sources this file, so every tool is
#                         correct with zero harness configuration, from any cwd.
#   Tier 2 (ergonomics)   pi     -> .pi/settings.json shellCommandPrefix
#                         Claude -> SessionStart hook writes a `source` line to
#                                   $CLAUDE_ENV_FILE
#                         Codex  -> .agents/bin/codex sources this, then execs codex
#                                   (default inherit="all" carries env vars in;
#                                   shell functions do NOT cross, by design)
#
# Nothing in any skill may depend on Tier 2. If a harness breaks its injection
# mechanism, Tier 1 still works and only ergonomics regress.
#
# ---------------------------------------------------------------------------
# DO NOT ADD `set -e` / `set -euo pipefail` TO THIS FILE.
# ---------------------------------------------------------------------------
# It is written for a no-errexit context and is *sourced* into the caller's
# shell. Several probes here exit non-zero as normal control flow (a missing qmd
# collection is the state the code repairs; `type -aP` exits 1 when a binary is
# absent). Under errexit those would abort the calling shell silently — on the
# fresh-clone path this migration exists to fix. Scripts that source this file
# use `set -uo pipefail`, never `-e`.

# --- root resolution --------------------------------------------------------
# A Tier 1 launcher resolves the repo root from BASH_SOURCE and exports
# TTRPG_ROOT before sourcing this file. Honour it. Only fall back to cwd/git
# discovery when nothing set it — otherwise running a tool from inside another
# git repo would silently repoint the cache vars, the vault path and the qmd
# collections at that repo.
if [ -n "${TTRPG_ROOT:-}" ]; then
  PROJECT_ROOT="$TTRPG_ROOT"
else
  PROJECT_ROOT="$(pwd)"
  if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    PROJECT_ROOT="$git_root"
  fi
  unset git_root
fi

export TTRPG_ROOT="$PROJECT_ROOT"
export TTRPG_AGENTS_DIR="$PROJECT_ROOT/.agents"
export TTRPG_BIN_DIR="$PROJECT_ROOT/.agents/bin"
# Canonical, symlink-resolved form of the above. The binary resolver compares
# against THIS, never the raw string — see _ttrpg_resolve_real_bin.
TTRPG_BIN_REAL="$(cd -- "$TTRPG_BIN_DIR" 2>/dev/null && pwd -P)" || TTRPG_BIN_REAL="$TTRPG_BIN_DIR"
export TTRPG_BIN_REAL
export TTRPG_CLI_DIR="$PROJECT_ROOT/.agents/cli"
export TTRPG_TOOLS_DIR="$TTRPG_CLI_DIR"
# Small, durable, gitignored agent state that is NOT a cache and must never be
# a cleanup target (e.g. the Foundry MCP credential).
export TTRPG_STATE_DIR="$PROJECT_ROOT/.agents/state"

export TTRPG_IMPORTS_DIR="$PROJECT_ROOT/imports"
export TTRPG_BOOKS_DIR="$PROJECT_ROOT/imports/books"
export TTRPG_SOURCE_VAULT_DIR="$PROJECT_ROOT/imports/source-vault"
export TTRPG_VAULT_DIR="$PROJECT_ROOT/vault"
export TTRPG_NOTES_DIR="$PROJECT_ROOT/vault/notes"
export TTRPG_LIBRARY_DIR="$PROJECT_ROOT/vault/library/books"

# Load project-local optional feature/API settings. .env is gitignored.
#
# It is read *before* the cache block below, and the cache variables there are
# set unconditionally — so setting XDG_CACHE_HOME / HF_HOME / UV_CACHE_DIR /
# QMD_CONFIG_DIR in .env has no effect. The single supported escape hatch is
# TTRPG_ALLOW_HOST_CACHES=1 (settable in .env or the parent shell).
#
# .env is PARSED, not sourced. `.env.example` documents "simple KEY=value dotenv
# syntax", and sourcing it turns that promise into three real failures:
#   * `FOUNDRY_MCP_PASSWORD=my pass` runs `pass` — under the `set -e` used by
#     pi-launch.sh and sync.sh that kills the script (rc=127).
#   * a password containing `$(...)` or backticks EXECUTES on every shell start.
#   * `HF_TOKEN=${SOMETHING_UNSET}` aborts every `.agents/bin/*` (they use -u).
# Parsing also makes the ENV DUMP for fish exactly agree with bash.
# Not supported, by design: shell expansion, command substitution, multi-line
# values. Use a wrapper script if you need those.
if [ -f "$PROJECT_ROOT/.env" ]; then
  while IFS= read -r _ttrpg_line || [ -n "$_ttrpg_line" ]; do
    _ttrpg_line="${_ttrpg_line%$'\r'}"          # CRLF from a Windows editor
    case "$_ttrpg_line" in ''|'#'*) continue ;; esac
    _ttrpg_line="${_ttrpg_line#export }"        # tolerate `export KEY=value`
    case "$_ttrpg_line" in *=*) ;; *) continue ;; esac

    _ttrpg_k="${_ttrpg_line%%=*}"
    _ttrpg_v="${_ttrpg_line#*=}"
    _ttrpg_k="${_ttrpg_k#"${_ttrpg_k%%[![:space:]]*}"}"
    _ttrpg_k="${_ttrpg_k%"${_ttrpg_k##*[![:space:]]}"}"
    case "$_ttrpg_k" in ''|*[!A-Za-z0-9_]*) continue ;; esac

    case "$_ttrpg_v" in
      \"*\") _ttrpg_v="${_ttrpg_v#\"}"; _ttrpg_v="${_ttrpg_v%\"}" ;;
      \'*\') _ttrpg_v="${_ttrpg_v#\'}"; _ttrpg_v="${_ttrpg_v%\'}" ;;
      *)
        # Unquoted: drop a trailing ` # comment`, as `source` used to.
        case "$_ttrpg_v" in
          *[[:space:]]#*) _ttrpg_v="${_ttrpg_v%%[[:space:]]#*}" ;;
        esac
        _ttrpg_v="${_ttrpg_v%"${_ttrpg_v##*[![:space:]]}"}"
        ;;
    esac
    export "$_ttrpg_k=$_ttrpg_v"
  done < "$PROJECT_ROOT/.env"
  unset _ttrpg_line _ttrpg_k _ttrpg_v
fi

# --- one cache root, forced project-local -----------------------------------
# .cache/
# |-- index/     QMD_CONFIG_DIR   qmd index.yml + index.sqlite (rebuildable)
# |-- xdg/       XDG_CACHE_HOME
# |   |-- qmd/models/   qmd GGUFs            ~2.2 GB  (expensive)
# |   `-- datalab/      surya/marker models  ~3.3 GB  (expensive)
# |-- huggingface/  HF_HOME
# |-- torch/        TORCH_HOME
# |-- uv/           UV_CACHE_DIR
# |-- npm/          npm_config_cache  (npm defaults to ~/.npm, outside ~/.cache)
# `-- vendor/       pinned upstream checkouts (5etools, foundry-vtt-mcp)
#
# These are set unconditionally, not `${VAR:-...}`. A value inherited from the
# user's shell would otherwise silently win and break isolation without a word.
export TTRPG_CACHE_DIR="$PROJECT_ROOT/.cache"
export TTRPG_VENDOR_DIR="$TTRPG_CACHE_DIR/vendor"

if [ "${TTRPG_ALLOW_HOST_CACHES:-0}" = "1" ]; then
  # Escape hatch: honour host/user cache locations when explicitly requested.
  export QMD_CONFIG_DIR="${QMD_CONFIG_DIR:-$TTRPG_CACHE_DIR/index}"
  export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$TTRPG_CACHE_DIR/xdg}"
  export UV_CACHE_DIR="${UV_CACHE_DIR:-$TTRPG_CACHE_DIR/uv}"
  export HF_HOME="${HF_HOME:-$TTRPG_CACHE_DIR/huggingface}"
  export TORCH_HOME="${TORCH_HOME:-$TTRPG_CACHE_DIR/torch}"
  export npm_config_cache="${npm_config_cache:-$TTRPG_CACHE_DIR/npm}"
else
  export QMD_CONFIG_DIR="$TTRPG_CACHE_DIR/index"
  export XDG_CACHE_HOME="$TTRPG_CACHE_DIR/xdg"
  export UV_CACHE_DIR="$TTRPG_CACHE_DIR/uv"
  export HF_HOME="$TTRPG_CACHE_DIR/huggingface"
  export TORCH_HOME="$TTRPG_CACHE_DIR/torch"
  export npm_config_cache="$TTRPG_CACHE_DIR/npm"
fi

# --- vendored upstream checkouts (see .agents/manifest.toml [[vendor]]) ------
# 5etools is a pinned git clone, not user content: re-clonable, read-only by
# policy. It lives under the vendor root, and everything reaches it through
# this variable rather than a hardcoded path.
export TTRPG_5ETOOLS_DIR="$TTRPG_VENDOR_DIR/5etools"
# Unconditional, like every other path var. With `:-` these leaked between
# checkouts: source repo A's env.sh then repo B's, and B kept pointing at A's
# vendor dir — after which install.sh's `rm -rf "$UPSTREAM_DIR"` would delete
# A's checkout. They also ride into every Codex tool shell via inherit="all".
export FOUNDRY_MCP_UPSTREAM_DIR="$TTRPG_VENDOR_DIR/foundry-vtt-mcp"
# The Foundry GM credential is durable state, not a cache: it must survive every
# cleanup scope, so it lives in .agents/state/ rather than under .cache/.
export FOUNDRY_CREDENTIALS="$TTRPG_STATE_DIR/foundry-credentials.json"

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
# qmd 2.5+: leave QMD_LLAMA_GPU unset for automatic selection; explicit values are
# metal, vulkan, cuda, or false/off/0. Older configs may still contain "auto".
if [ "${QMD_LLAMA_GPU:-}" = "auto" ]; then
  unset QMD_LLAMA_GPU
fi
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

# --- directory skeleton -----------------------------------------------------
# No symlinks. Pointing XDG_CACHE_HOME at .cache/xdg (instead of at .qmd, as the
# pre-migration layout did) removes the need for the two cache symlinks that
# used to be recreated here.
#
# Deliberately NOT created: $TTRPG_5ETOOLS_DIR and $FOUNDRY_MCP_UPSTREAM_DIR are
# vendored checkouts managed by `.agents/harness vendor sync`; an empty
# directory there would look like a broken checkout. The model stores under
# $XDG_CACHE_HOME are likewise populated on first use by qmd/marker themselves.
mkdir -p \
  "$QMD_CONFIG_DIR" \
  "$XDG_CACHE_HOME" \
  "$TTRPG_CACHE_DIR" \
  "$TTRPG_VENDOR_DIR" \
  "$UV_CACHE_DIR" \
  "$HF_HOME" \
  "$TORCH_HOME" \
  "$npm_config_cache" \
  "$TTRPG_STATE_DIR" \
  "$TTRPG_IMPORTS_DIR/fvtt-data" \
  "$TTRPG_BOOKS_DIR" \
  "$TTRPG_SOURCE_VAULT_DIR" \
  "$TTRPG_NOTES_DIR" \
  "$TTRPG_NOTES_DIR/images" \
  "$PROJECT_ROOT/vault/notes/mechanics" \
  "$PROJECT_ROOT/vault/notes/readalouds" \
  "$TTRPG_LIBRARY_DIR" \
  2>/dev/null

# --- real-binary resolution -------------------------------------------------
# Print the first PATH match for $1 that does NOT live in .agents/bin, so a
# hermetic launcher can never recurse into itself. `command qmd` would bypass
# the shell *function* below but still search PATH, so "keep .agents/bin off
# PATH" was only a convention; this makes it an interlock.
#
# Must not abort a caller running under errexit: `type -aP` exits 1 when the
# binary is absent, which is exactly the case the callers handle explicitly.
_ttrpg_resolve_real_bin() {
  local name="$1"
  local dir found="" real_dir real_target
  # Walk PATH by hand rather than using `type -aP`: that is a bash builtin, and
  # this file is sourced by whatever shell the harness's tool uses — zsh on many
  # machines. Splitting via tr + `read` behaves identically in bash and zsh,
  # whereas `for dir in $PATH` does not (zsh leaves $PATH unsplit by default).
  #
  # The self-exclusion MUST compare resolved paths, not raw strings. A plain
  # string test is defeated by a trailing slash ($ROOT/.agents/bin/), a doubled
  # separator ($ROOT/.agents//bin), an empty PATH component while cwd is bin/,
  # a symlinked repo root, or a ~/.local/bin/qmd symlink pointing back here —
  # and any of those turns `.agents/bin/qmd` into an exponential fork bomb.
  while IFS= read -r dir; do
    [ -n "$dir" ] || dir="."
    [ -x "$dir/$name" ] || continue
    [ ! -d "$dir/$name" ] || continue

    # Skip our own directory, however it happens to be spelled.
    real_dir="$(cd -- "$dir" 2>/dev/null && pwd -P)" || real_dir=""
    [ "$real_dir" != "$TTRPG_BIN_REAL" ] || continue

    # Skip anything that is (or symlinks to) a file inside our own bin/.
    real_target="$(cd -- "$(dirname -- "$dir/$name")" 2>/dev/null && pwd -P)" || real_target=""
    if [ -L "$dir/$name" ]; then
      local link_dest
      link_dest="$dir/$name"
      # Follow one hop at a time; readlink -f is not portable to every platform.
      while [ -L "$link_dest" ]; do
        local hop
        hop="$(readlink "$link_dest")" || break
        case "$hop" in
          /*) link_dest="$hop" ;;
          *) link_dest="$(dirname -- "$link_dest")/$hop" ;;
        esac
      done
      real_target="$(cd -- "$(dirname -- "$link_dest")" 2>/dev/null && pwd -P)" || real_target=""
    fi
    [ "$real_target" != "$TTRPG_BIN_REAL" ] || continue

    found="$dir/$name"
    break
  done <<EOF
$(printf '%s' "$PATH" | tr ':' '\n')
EOF
  [ -n "$found" ] || return 1
  printf '%s\n' "$found"
}

TTRPG_QMD_BIN="$(_ttrpg_resolve_real_bin qmd || true)"
export TTRPG_QMD_BIN

_ttrpg_qmd_exec() {
  if [ -z "${TTRPG_QMD_BIN:-}" ]; then
    printf 'qmd: not found on PATH (excluding %s). Install qmd, or add the real binary to PATH.\n' \
      "$TTRPG_BIN_DIR" >&2
    return 127
  fi
  "$TTRPG_QMD_BIN" "$@"
}

_ttrpg_qmd_collection_path() {
  _ttrpg_qmd_exec collection show "$1" 2>/dev/null | awk '/Path:/ {print $2; exit}' || true
}

_ttrpg_qmd_collection_exists() {
  _ttrpg_qmd_exec collection show "$1" >/dev/null 2>&1
}

_ttrpg_qmd_remove_collection_if_exists() {
  local name="$1"
  if _ttrpg_qmd_collection_exists "$name"; then
    _ttrpg_qmd_exec collection remove "$name" >/dev/null 2>&1 || true
  fi
}

_ttrpg_qmd_ensure_collection() {
  local name="$1"
  # NOT `local path` — in zsh the lowercase `path` is tied to `PATH`, so
  # declaring it local replaces PATH with this one directory for the whole
  # dynamic extent of the function. Every nested call then loses awk, tr, and
  # the qmd binary itself. Same trap applies to cdpath/fpath/manpath/status.
  local collection_dir="$2"
  local mask="$3"
  local existing
  existing="$(_ttrpg_qmd_collection_path "$name")" || true

  if [ "$existing" != "$collection_dir" ]; then
    if [ -n "$existing" ]; then
      _ttrpg_qmd_exec collection remove "$name" >/dev/null 2>&1 || true
    fi
    _ttrpg_qmd_exec collection add "$collection_dir" --name "$name" --mask "$mask" >/dev/null 2>&1 || true
  fi
}

_ttrpg_qmd_ensure_config() {
  # Legacy collection names from the earlier mirror-based layout. Remove them so
  # the active qmd collections are non-overlapping and directly point at source
  # directories: notes, books, and optional archive.
  _ttrpg_qmd_remove_collection_if_exists vault
  _ttrpg_qmd_remove_collection_if_exists source

  _ttrpg_qmd_ensure_collection notes "$TTRPG_NOTES_DIR" "**/*.md"
  _ttrpg_qmd_ensure_collection books "$TTRPG_LIBRARY_DIR" "**/*.md"
  _ttrpg_qmd_ensure_collection archive "$TTRPG_SOURCE_VAULT_DIR" "**/*.md"

  _ttrpg_qmd_exec collection exclude archive >/dev/null 2>&1 || true

  _ttrpg_qmd_exec context add qmd://notes "Active campaign notes and table prep under vault/notes." >/dev/null 2>&1 || true
  _ttrpg_qmd_exec context add qmd://books "Ingested RPG books and supplements under vault/library/books." >/dev/null 2>&1 || true
  _ttrpg_qmd_exec context add qmd://archive "Optional legacy notes under imports/source-vault; search only when explicitly requested." >/dev/null 2>&1 || true
}

_ttrpg_qmd_ensure_index_if_missing() {
  # qmd resolves the index path with legacy-compat behaviour: it may sit at
  # $QMD_CONFIG_DIR/index.sqlite or at $XDG_CACHE_HOME/qmd/index.sqlite
  # depending on how the config was created. Testing only the first would make
  # this probe permanently true once the two roots differ, firing a full
  # re-index on every single qmd call. Test both.
  if [ -f "$QMD_CONFIG_DIR/index.sqlite" ] || [ -f "$XDG_CACHE_HOME/qmd/index.sqlite" ]; then
    return 0
  fi
  _ttrpg_qmd_exec update >&2
}

_ttrpg_qmd_cpu_forced() {
  # `${gpu,,}` is bash-only and blows up under zsh with "bad substitution".
  # This file is sourced into the harness's shell, which is zsh on many
  # machines, so lowercase with tr instead.
  local gpu
  gpu="$(printf '%s' "${QMD_LLAMA_GPU:-}" | tr '[:upper:]' '[:lower:]')"
  case "$gpu" in
    false|off|none|disable|disabled|0) return 0 ;;
    *) return 1 ;;
  esac
}

_qmd_run() {
  local cmd="${1:-}"
  # Provisioning spawns ~12 qmd processes (~2-4 s of Node startup). Running it
  # on every call made it the dominant cost of every search. The config file is
  # the marker that it has already been done; force a re-run with
  # TTRPG_QMD_FORCE_CONFIG=1, or use ttrpg-system-qmd-maintenance to repair.
  if [ "${TTRPG_QMD_FORCE_CONFIG:-0}" = "1" ] || [ ! -f "$QMD_CONFIG_DIR/index.yml" ]; then
    _ttrpg_qmd_ensure_config
  fi
  case "$cmd" in
    query|search|vsearch|get|ls|status)
      _ttrpg_qmd_ensure_index_if_missing
      ;;
  esac
  _ttrpg_qmd_exec "$@"
}

qmd() {
  local cmd="${1:-}"

  # CUDA/node-llama-cpp can hard-abort on some WSL2/RTX 50xx setups. Keep GPU
  # as the default fast path, but retry LLM-backed qmd commands once on CPU.
  case "$cmd" in
    embed|vsearch|query|status)
      if ! _ttrpg_qmd_cpu_forced && [ "${QMD_CPU_FALLBACK:-1}" != "0" ]; then
        _qmd_run "$@"
        local rc=$?
        # 127 means qmd itself is absent — retrying cannot help and the
        # "retrying on CPU" message is actively misleading.
        if [ "$rc" -ne 0 ] && [ "$rc" -ne 127 ]; then
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

# --- export mode, for shells that cannot source this file -------------------
# `TTRPG_ENV_DUMP=1 bash .agents/env.sh` prints every variable this contract
# manages as KEY=VALUE lines, so a non-POSIX shell (fish) can adopt the same
# environment without a second implementation of any of the logic above —
# .agents/env.fish is a thin consumer of this output. See also .agents/bin/*,
# which is how those shells get the qmd wrapper.
#
# Triggered by the env var only, never by a positional argument: this file is
# *sourced* by scripts that pass their own "$@" through (pi-launch.sh et al.),
# so keying on $1 would make `pi-launch.sh --dump` spew KEY=VALUE lines.
#
# Values are filesystem paths and dotenv values; embedded newlines are not
# supported and would corrupt the stream.
#
# If you add an exported variable above, ADD IT HERE TOO or fish silently
# misses it. `.agents/harness verify` diffs the two and fails on a gap.
if [ "${TTRPG_ENV_DUMP:-0}" = "1" ]; then
  _ttrpg_dump_keys="TTRPG_ROOT TTRPG_AGENTS_DIR TTRPG_BIN_DIR TTRPG_BIN_REAL
TTRPG_CLI_DIR TTRPG_TOOLS_DIR TTRPG_STATE_DIR TTRPG_IMPORTS_DIR TTRPG_BOOKS_DIR
TTRPG_SOURCE_VAULT_DIR TTRPG_VAULT_DIR TTRPG_NOTES_DIR TTRPG_LIBRARY_DIR
TTRPG_CACHE_DIR TTRPG_VENDOR_DIR TTRPG_5ETOOLS_DIR TTRPG_QMD_BIN
QMD_CONFIG_DIR XDG_CACHE_HOME UV_CACHE_DIR HF_HOME TORCH_HOME npm_config_cache
FOUNDRY_MCP_UPSTREAM_DIR FOUNDRY_CREDENTIALS
CUDA_HOME CUDAToolkit_ROOT CUDACXX GGML_CUDA_NO_VMM QMD_LLAMA_GPU PATH"

  # Everything the project's .env defines is part of the contract too — that is
  # where the API keys and feature flags live. Read the key names back out of
  # the file rather than maintaining a second list. The `export ` prefix is
  # optional here exactly as it is in the parser above; omitting it from this
  # pattern used to make fish silently miss those keys.
  if [ -f "$PROJECT_ROOT/.env" ]; then
    _ttrpg_dump_keys="$_ttrpg_dump_keys
$(sed -n 's/^[[:space:]]*\(export[[:space:]]\{1,\}\)\{0,1\}\([A-Za-z_][A-Za-z0-9_]*\)[[:space:]]*=.*/\2/p' "$PROJECT_ROOT/.env")"
  fi

  for _ttrpg_k in $_ttrpg_dump_keys; do
    eval "_ttrpg_isset=\${$_ttrpg_k+set}"
    [ "${_ttrpg_isset:-}" = "set" ] || continue
    eval "_ttrpg_v=\$$_ttrpg_k"
    printf '%s=%s\n' "$_ttrpg_k" "$_ttrpg_v"
  done
  unset _ttrpg_dump_keys _ttrpg_k _ttrpg_isset _ttrpg_v
  # Must not be inherited by children: .agents/bin/* all source this file, and a
  # leaked TTRPG_ENV_DUMP=1 would prepend 30 KEY=VALUE lines to every tool's
  # stdout — which corrupts the MCP server's JSON-RPC stream outright.
  unset TTRPG_ENV_DUMP
fi
