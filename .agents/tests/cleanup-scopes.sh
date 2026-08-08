#!/usr/bin/env bash
# §4.6 dedicated safety test.
#
# Seeds a scratch TTRPG_ROOT with dummy files in every .cache/ class — including
# the session corpus, whose source recordings are irreplaceable — runs each named
# cleanup scope's command block VERBATIM as written in ttrpg-system-data-cleanup,
# and asserts the expensive and irreplaceable classes are untouched by every one
# of them.
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
  # session corpus: two sessions, so a per-session scope cannot take the other one
  mkdir -p .cache/craig-stt/models .cache/sessions/scratch
  mkdir -p .cache/sessions/datasets/x/source .cache/sessions/datasets/x/pcm
  mkdir -p .cache/sessions/datasets/y/source
  mkdir -p .cache/sessions/2026-08-08/inputs .cache/sessions/2026-07-01
  mkdir -p vault/transcripts/2026-08-08 vault/transcripts/2026-07-01

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
  echo ckpt > .cache/craig-stt/models/large-v3.bin
  # class 2b: IRREPLACEABLE — Craig expires recordings, and the two _*.yaml files
  # are hand-maintained user data. No scope below may take any of these.
  echo zip  > .cache/sessions/datasets/x/source/rec.flac.zip
  echo ds   > .cache/sessions/datasets/x/dataset.json
  echo zip  > .cache/sessions/datasets/y/source/rec.flac.zip
  echo lex  > vault/transcripts/_lexicon.yaml
  echo spk  > vault/transcripts/_speakers.yaml
  # GPU-regenerable dataset internals: only `session-ingest prune` may take these,
  # never an rm scope.
  echo pcm  > .cache/sessions/datasets/x/pcm/track1.pcm
  # session-derived + rendered output: deletable, but only by their own scopes
  echo rec  > .cache/sessions/2026-08-08/record.json
  echo anch > .cache/sessions/2026-08-08/anchors.json
  echo snap > .cache/sessions/2026-08-08/inputs/lexicon.yaml
  echo rec2 > .cache/sessions/2026-07-01/record.json
  echo tmp  > .cache/sessions/scratch/run2.log
  echo chunk> vault/transcripts/2026-08-08/01-000-015.md
  echo chnk2> vault/transcripts/2026-07-01/01-000-015.md
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
  .cache/craig-stt/models/large-v3.bin
)
# The one class no amount of time or money restores. Every scope below asserts
# these, including the nuclear one — `session-corpus:<id>` may take a session's
# own recording, but never another session's and never the cumulative _*.yaml.
IRREPLACEABLE=(
  .cache/sessions/datasets/x/source/rec.flac.zip
  .cache/sessions/datasets/x/dataset.json
  vault/transcripts/_lexicon.yaml
  vault/transcripts/_speakers.yaml
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
assert_survives  search-index "${EXPENSIVE[@]}" "${IRREPLACEABLE[@]}" "${PROTECTED[@]}" .cache/index/index.yml vault/notes/a.md
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
assert_survives  all-index-caches "${EXPENSIVE[@]}" "${IRREPLACEABLE[@]}" "${PROTECTED[@]}" vault/notes/a.md
echo "  manifest: $(wc -l < "$manifest") entries"

echo "== scope: active-notes =="
seed
find vault/notes -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
mkdir -p vault/notes
assert_gone      active-notes vault/notes/a.md
assert_survives  active-notes "${EXPENSIVE[@]}" "${IRREPLACEABLE[@]}" "${PROTECTED[@]}" vault/library/books/a-book/01.md

echo "== scope: ingested-books =="
seed
find vault/library/books -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
assert_gone      ingested-books vault/library/books/a-book/01.md
assert_survives  ingested-books "${EXPENSIVE[@]}" "${IRREPLACEABLE[@]}" "${PROTECTED[@]}" imports/books/x.pdf

echo "== scope: imports-all =="
seed
for d in imports/books imports/source-vault imports/fvtt-data; do
  find "$d" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
done
mkdir -p imports/books imports/source-vault imports/fvtt-data
assert_gone      imports-all imports/books/x.pdf imports/source-vault/old.md
assert_survives  imports-all "${EXPENSIVE[@]}" "${IRREPLACEABLE[@]}" "${PROTECTED[@]}"
# 5etools must NOT be part of imports-all any more
assert_survives  imports-all .cache/vendor/5etools/data.json

echo "== scope: transcripts-rendered =="
seed
# Directories only. The `-type d` is what keeps _speakers.yaml and _lexicon.yaml
# out of the sweep — a glob "simplification" here is the bug this asserts.
find vault/transcripts -mindepth 1 -maxdepth 1 -type d -print 2>/dev/null | sort > "$manifest" || true
find vault/transcripts -mindepth 1 -maxdepth 1 -type d -exec rm -rf -- {} +
mkdir -p vault/transcripts
assert_gone      transcripts-rendered vault/transcripts/2026-08-08/01-000-015.md
assert_survives  transcripts-rendered "${EXPENSIVE[@]}" "${IRREPLACEABLE[@]}" "${PROTECTED[@]}" \
                 .cache/sessions/2026-08-08/record.json
echo "  manifest: $(wc -l < "$manifest") entries"

echo "== scope: session-derived (one named session) =="
seed
id="2026-08-08"
test -d ".cache/sessions/$id" || { echo "no such session: $id"; exit 1; }
# Verbatim from the skill: `datasets` and `scratch` are not session ids. It is a
# no-op for a real id, and an exit 2 (test failure) if that guard ever inverts.
case "$id" in datasets|scratch) echo "refusing: '$id' is not a session id" >&2; exit 2 ;; esac
find ".cache/sessions/$id" -mindepth 1 -print 2>/dev/null | sort > "$manifest" || true
find ".cache/sessions/$id" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
find .cache/sessions/scratch -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
mkdir -p .cache/sessions/scratch
assert_gone      session-derived .cache/sessions/2026-08-08/record.json \
                 .cache/sessions/2026-08-08/anchors.json .cache/sessions/scratch/run2.log
# The datasets root and the other session are out of scope entirely.
assert_survives  session-derived "${EXPENSIVE[@]}" "${IRREPLACEABLE[@]}" "${PROTECTED[@]}" \
                 .cache/sessions/datasets/x/pcm/track1.pcm .cache/sessions/2026-07-01/record.json \
                 vault/transcripts/2026-08-08/01-000-015.md
echo "  manifest: $(wc -l < "$manifest") entries"

echo "== scope: session-corpus:<id> (nuclear, one session only) =="
seed
id="2026-08-08"; rec="x"
rm -rf -- ".cache/sessions/$id" ".cache/sessions/datasets/$rec" "vault/transcripts/$id"
mkdir -p .cache/sessions/datasets vault/transcripts
assert_gone      session-corpus .cache/sessions/2026-08-08/record.json \
                 .cache/sessions/datasets/x/source/rec.flac.zip \
                 vault/transcripts/2026-08-08/01-000-015.md
# Even the nuclear scope is per-session: the cumulative _*.yaml files and every
# OTHER session's recording survive it.
assert_survives  session-corpus "${EXPENSIVE[@]}" "${PROTECTED[@]}" \
                 vault/transcripts/_lexicon.yaml vault/transcripts/_speakers.yaml \
                 .cache/sessions/datasets/y/source/rec.flac.zip \
                 .cache/sessions/2026-07-01/record.json \
                 vault/transcripts/2026-07-01/01-000-015.md

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
assert_survives  full-data-reset "${EXPENSIVE[@]}" "${IRREPLACEABLE[@]}" "${PROTECTED[@]}"
# No session scope is part of a data reset: the whole corpus survives it.
assert_survives  full-data-reset .cache/sessions/2026-08-08/record.json \
                 .cache/sessions/datasets/x/pcm/track1.pcm \
                 vault/transcripts/2026-08-08/01-000-015.md

echo "== scope: vendor-checkouts (opt-in, one named vendor) =="
seed
name="5etools"
rm -rf -- ".cache/vendor/$name"
assert_gone      vendor-checkouts .cache/vendor/5etools/data.json
assert_survives  vendor-checkouts "${EXPENSIVE[@]}" "${IRREPLACEABLE[@]}" \
                 .cache/vendor/foundry-vtt-mcp/server.js .agents/state/foundry-credentials.json

echo "== scope: tool-environments (opt-in) =="
seed
rm -rf .agents/cli/*/.venv .agents/cli/*/node_modules
assert_survives  tool-environments "${EXPENSIVE[@]}" "${IRREPLACEABLE[@]}" "${PROTECTED[@]}" vault/notes/a.md
[ -d .agents/cli/book-ingest/.venv ] && { echo "  ✗ FAIL venv survived"; fails=$((fails+1)); }

echo
if [ "$fails" -eq 0 ]; then
  echo "RESULT: PASS — the expensive and irreplaceable classes survived every named scope."
else
  echo "RESULT: FAIL — $fails assertion(s) failed."
fi
exit "$fails"
