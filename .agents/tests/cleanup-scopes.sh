#!/usr/bin/env bash
# §4.6 dedicated safety test.
#
# Seeds a scratch TTRPG_ROOT with dummy files in all three .cache/ classes, runs
# each named cleanup scope's command block VERBATIM as written in
# ttrpg-system-data-cleanup, and asserts the expensive class is untouched by
# every one of them.
set -uo pipefail

# Every path below this point is RELATIVE and several are `rm -rf`. If mktemp
# failed we would silently run the whole suite — deleting vault/, imports/ and
# .cache/ — in the invoking directory, which is normally the real repo root, and
# still exit 0. `cd ""` returns success in bash, so the cd alone does not save
# us. Refuse to continue unless we are provably inside a fresh scratch dir.
SCRATCH="$(mktemp -d 2>/dev/null)" || SCRATCH=""
case "$SCRATCH" in
  /*/*) ;;
  *) echo "FATAL: mktemp -d did not return an absolute scratch path; refusing to run." >&2; exit 2 ;;
esac
[ -d "$SCRATCH" ] || { echo "FATAL: scratch dir '$SCRATCH' does not exist." >&2; exit 2; }
[ -z "$(ls -A "$SCRATCH" 2>/dev/null)" ] || { echo "FATAL: scratch dir '$SCRATCH' is not empty." >&2; exit 2; }
[ ! -e "$SCRATCH/.git" ] || { echo "FATAL: refusing to operate inside a git repo." >&2; exit 2; }

trap 'rm -rf "$SCRATCH"' EXIT
cd "$SCRATCH" || { echo "FATAL: cannot cd into '$SCRATCH'." >&2; exit 2; }
# Belt and braces: the destructive blocks below are relative to $PWD.
[ "$PWD" = "$SCRATCH" ] || { echo "FATAL: cwd '$PWD' != scratch '$SCRATCH'." >&2; exit 2; }

seed() {
  rm -rf "$SCRATCH"/{vault,imports,.cache,.agents}
  mkdir -p vault/notes vault/library/books/a-book imports/books imports/source-vault imports/fvtt-data
  mkdir -p .cache/index .cache/xdg/qmd/models .cache/xdg/datalab/models
  mkdir -p .cache/huggingface .cache/torch .cache/uv .cache/npm
  mkdir -p .cache/vendor/5etools .cache/vendor/foundry-vtt-mcp
  mkdir -p .agents/state .agents/cli/book-ingest/.venv

  # class 1: rebuildable
  echo idx  > .cache/xdg/qmd/index.sqlite
  echo shm  > .cache/xdg/qmd/index.sqlite-shm
  echo wal  > .cache/xdg/qmd/index.sqlite-wal
  echo cfg  > .cache/index/index.yml
  # class 2: EXPENSIVE — must survive every scope
  echo gguf > .cache/xdg/qmd/models/embed.gguf
  echo surya> .cache/xdg/datalab/models/layout.bin
  echo hf   > .cache/huggingface/blob
  echo torch> .cache/torch/blob
  echo uv   > .cache/uv/blob
  echo npm  > .cache/npm/blob
  # class 3: vendored
  echo v1   > .cache/vendor/5etools/data.json
  echo v2   > .cache/vendor/foundry-vtt-mcp/server.js
  # durable state
  echo cred > .agents/state/foundry-credentials.json
  # user data
  echo note > vault/notes/a.md
  echo book > vault/library/books/a-book/01.md
  echo pdf  > imports/books/x.pdf
  echo arc  > imports/source-vault/old.md
  echo fv   > imports/fvtt-data/e.json
}

EXPENSIVE=(
  .cache/xdg/qmd/models/embed.gguf
  .cache/xdg/datalab/models/layout.bin
  .cache/huggingface/blob
  .cache/torch/blob
  .cache/uv/blob
  .cache/npm/blob
)
PROTECTED=(
  .cache/vendor/5etools/data.json
  .cache/vendor/foundry-vtt-mcp/server.js
  .agents/state/foundry-credentials.json
)

fails=0
assert_survives() {
  local scope="$1"; shift
  for f in "$@"; do
    if [ ! -f "$f" ]; then
      echo "  ✗ FAIL  scope '$scope' destroyed $f"
      fails=$((fails + 1))
    fi
  done
}
assert_gone() {
  local scope="$1"; shift
  for f in "$@"; do
    if [ -f "$f" ]; then
      echo "  ✗ FAIL  scope '$scope' left $f behind"
      fails=$((fails + 1))
    fi
  done
}

manifest="$SCRATCH/manifest.txt"

echo "== scope: search-index =="
seed
find .cache/xdg/qmd -maxdepth 1 -type f -name 'index.sqlite*' -print | sort > "$manifest"
rm -f .cache/xdg/qmd/index.sqlite .cache/xdg/qmd/index.sqlite-shm .cache/xdg/qmd/index.sqlite-wal
assert_gone      search-index .cache/xdg/qmd/index.sqlite
assert_survives  search-index "${EXPENSIVE[@]}" "${PROTECTED[@]}" .cache/index/index.yml vault/notes/a.md
echo "  manifest: $(wc -l < "$manifest") entries"

echo "== scope: all-index-caches =="
seed
{
  find .cache/xdg/qmd -maxdepth 1 -type f -name 'index.sqlite*' -print 2>/dev/null
  find .cache/index -maxdepth 1 -type f -name 'index.yml' -print 2>/dev/null
} | sort > "$manifest" || true
rm -f .cache/xdg/qmd/index.sqlite .cache/xdg/qmd/index.sqlite-shm .cache/xdg/qmd/index.sqlite-wal
rm -f .cache/index/index.yml
assert_gone      all-index-caches .cache/xdg/qmd/index.sqlite .cache/index/index.yml
assert_survives  all-index-caches "${EXPENSIVE[@]}" "${PROTECTED[@]}" vault/notes/a.md
echo "  manifest: $(wc -l < "$manifest") entries"

echo "== scope: active-notes =="
seed
find vault/notes -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
mkdir -p vault/notes
assert_gone      active-notes vault/notes/a.md
assert_survives  active-notes "${EXPENSIVE[@]}" "${PROTECTED[@]}" vault/library/books/a-book/01.md

echo "== scope: ingested-books =="
seed
find vault/library/books -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
assert_gone      ingested-books vault/library/books/a-book/01.md
assert_survives  ingested-books "${EXPENSIVE[@]}" "${PROTECTED[@]}" imports/books/x.pdf

echo "== scope: imports-all =="
seed
for d in imports/books imports/source-vault imports/fvtt-data; do
  find "$d" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
done
mkdir -p imports/books imports/source-vault imports/fvtt-data
assert_gone      imports-all imports/books/x.pdf imports/source-vault/old.md
assert_survives  imports-all "${EXPENSIVE[@]}" "${PROTECTED[@]}"
# 5etools must NOT be part of imports-all any more
assert_survives  imports-all .cache/vendor/5etools/data.json

echo "== scope: full-data-reset (vault-content + imports-all + all-index-caches) =="
seed
for dir in vault/notes vault/library/books vault/images; do
  [ -d "$dir" ] && find "$dir" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
done
for d in imports/books imports/source-vault imports/fvtt-data; do
  find "$d" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
done
rm -f .cache/xdg/qmd/index.sqlite .cache/xdg/qmd/index.sqlite-shm .cache/xdg/qmd/index.sqlite-wal
rm -f .cache/index/index.yml
assert_gone      full-data-reset vault/notes/a.md imports/books/x.pdf .cache/xdg/qmd/index.sqlite
assert_survives  full-data-reset "${EXPENSIVE[@]}" "${PROTECTED[@]}"

echo "== scope: vendor-checkouts (opt-in, one named vendor) =="
seed
name="5etools"
rm -rf -- ".cache/vendor/$name"
assert_gone      vendor-checkouts .cache/vendor/5etools/data.json
assert_survives  vendor-checkouts "${EXPENSIVE[@]}" .cache/vendor/foundry-vtt-mcp/server.js \
                 .agents/state/foundry-credentials.json

echo "== scope: tool-environments (opt-in) =="
seed
rm -rf .agents/cli/*/.venv .agents/cli/*/node_modules
assert_survives  tool-environments "${EXPENSIVE[@]}" "${PROTECTED[@]}" vault/notes/a.md
[ -d .agents/cli/book-ingest/.venv ] && { echo "  ✗ FAIL venv survived"; fails=$((fails+1)); }

echo
if [ "$fails" -eq 0 ]; then
  echo "RESULT: PASS — the expensive class survived every named scope."
else
  echo "RESULT: FAIL — $fails assertion(s) failed."
fi
exit "$fails"
