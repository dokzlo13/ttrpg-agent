# craig-stt — pinned upstream transcription CLI

A dependency-only uv project. There is no source here: it exists so that
`uv run --project .agents/cli/craig-stt craig-stt …` resolves a locked
environment for the upstream CLI
([dokzlo13/craig-stt](https://github.com/dokzlo13/craig-stt)). Call it through
`.agents/bin/craig-stt`, never bare — the launcher is what sources the
environment contract (`CRAIG_STT_WORK_DIR`, `CRAIG_STT_CACHE_DIR`, `HF_HOME`)
and injects `.agents/state/craig-stt.toml` when it exists.

`[tool.uv] package = false` makes this a *virtual* project, so uv builds an
environment without trying to build a distribution from a directory that has no
package in it. The committed `uv.lock` is what makes another machine
reproducible — uv resolves the workspace member `craig-stt-dataset` (same
commit, `packages/craig-stt-dataset`) transitively, so `session-ingest` and this
project agree on the SDK by construction as long as both pins match.

**Updating the pin:** edit `rev` in `pyproject.toml` → `uv lock` here → update
the matching `craig-stt-dataset` pin in `.agents/cli/session-ingest` in the same
change set → relock there → run the consumer contract test. A pin bump that
breaks an SDK field we depend on must fail in that test, not in production.

**Why `install = false` in `manifest.toml`:** the CUDA extras
(`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) plus `ctranslate2`/`faster-whisper`
are roughly 2 GB of wheels, and the whisper checkpoint another ~3 GB. A fresh
clone must be usable without that download, so bootstrap skips this project and
uv builds `.venv` on the first `.agents/bin/craig-stt` run.
