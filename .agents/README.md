# `.agents/` — the canonical toolchain

Everything a session needs, expressed once, harness-neutrally. `.pi/`,
`.claude/`, and `.codex/config.toml` are **adapters generated from here**. Codex
reads `.agents/skills/` natively and gets the environment contract through a
launcher.

If you are changing anything in this directory, read the
`ttrpg-harness-engineering` skill first — it holds the invariants and the
diagnosis order. This file is the map.

## Why it is polyglot

Three languages, each doing the one job it is actually good at. This is not
accretion; it is the smallest set that works:

| Language | Where | Why not something else |
|---|---|---|
| **bash** | `env.sh`, `bin/*`, `scripts/*` | The environment contract must be *sourced into* a harness's shell, and a shell function (`qmd()`) is the only thing that can wrap a command transparently. Nothing else can do this. |
| **Node** | `harness`, `harness-lib/*`, three CLI tools | The generator must emit JS workflow files containing prose full of `{braces}` — a quoting minefield in bash. The agent-facing JS tools were already Node (pi extensions are TypeScript). |
| **Python** | `cli/book-ingest`, `cli/vault-sync`, `cli/craig-stt`, `cli/session-ingest` | Marker/surya/torch are Python. `vault-sync` is Python because it sits next to them in the same problem domain. `craig-stt` pins a Python upstream (faster-whisper); `session-ingest` reads its datasets through that upstream's SDK. |

The language is an implementation detail of each tool. Callers only ever touch
`bin/<tool>`, which is uniformly bash regardless of what it execs.

## Layout

```
.agents/
├── env.sh                  THE environment contract (bash). Read this first.
├── manifest.toml           capability declarations — input to `harness sync`
├── harness                 lifecycle CLI (node)
├── harness-lib/            its implementation
│   ├── toml.mjs              thin wrapper over smol-toml
│   ├── exec.mjs              argv-based process helpers (never shell strings)
│   ├── sync.mjs              all adapter generators
│   ├── bootstrap.mjs         Tier G probe / Tier T install / Tier L report
│   ├── vendor.mjs            pinned upstream checkouts
│   └── verify.mjs            structural asserts + regenerate-and-diff
├── package.json            the harness IS an npm package (dep: smol-toml)
├── bin/                    hermetic Tier 1 entrypoints + harness launchers
├── cli/                    the tools, one isolated environment each
│   ├── _lib/spec-args.mjs    shared argv parser built from a tool's spec
│   ├── book-ingest/          python  (marker + torch + CUDA live in ITS venv)
│   ├── vault-sync/           python
│   ├── craig-stt/            python  (deps only, no source — pins the upstream GPU transcriber)
│   ├── session-ingest/       python  (post-processing; portable, no CUDA)
│   ├── query-5etools/        node
│   ├── vault-frontmatter/    node    (owns its own `yaml` dep + lockfile)
│   ├── image-gen/            node
│   └── foundry-mcp/          bash + node launcher for a vendored MCP server
├── skills/                 25 skills — discovered natively by pi and Codex
├── conditional-skills/     ttrpg-wsl-sync, surfaced only when configured
├── prompts/                9 canonical bodies, NO frontmatter, + _partials/
├── chains/                 multi-agent workflow specs
├── scripts/                pi-launch.sh, pi-isolated.sh, qmd-init.sh
└── state/                  durable gitignored state (Foundry credential)
```

## The three things that are load-bearing

### 1. `env.sh` — the environment contract

~40 variables, of which only 19 are static `.env` keys. The rest are *derived*:
paths computed from the repo root, caches forced project-local, CUDA
autodetection. Plus side effects (the directory skeleton) and a `qmd()` shell
**function** carrying collection auto-provisioning and a CUDA→CPU retry. A
static key/value map in a harness config cannot express any of that — which is
why this is a shell file and not a JSON blob.

It is consumed in **two tiers**:

- **Tier 1 — correctness.** `bin/<tool>` resolves the repo root from
  `BASH_SOURCE`, exports `TTRPG_ROOT`, sources `env.sh`, execs. Correct with
  zero harness configuration, from any cwd, under any harness or a bare shell.
- **Tier 2 — ergonomics only.** So a bare `qmd …` also works: pi's
  `shellCommandPrefix`, Claude's `SessionStart` → `$CLAUDE_ENV_FILE` hook, and
  the `bin/codex` launcher. **Nothing may depend on Tier 2.**

Two rules that look like style but are not:

- **Never add `set -e`.** Several probes exit non-zero as normal control flow (a
  missing qmd collection is the state the code repairs; the PATH walk finds
  nothing when a binary is absent). Under errexit those abort the *calling*
  shell silently, on the fresh-clone path. Use `set -uo pipefail`.
  `harness verify` asserts this.
- **It is sourced by the user's login shell, not necessarily bash.** Keep it
  working in bash *and* zsh. Two traps, both hit for real here:
  - `${var,,}` and `type -aP` are bash-only; use `tr`, and walk `PATH` by hand
    (`for dir in $PATH` does not split under zsh).
  - **Never `local path` / `fpath` / `cdpath` / `manpath` / `status`.** In zsh
    the lowercase `path` is tied to `PATH`, so `local path=…` replaces `PATH`
    with one directory for the whole dynamic extent of the call, and every
    nested command dies with "command not found".

  `harness verify` runs both shells.

### fish

fish is not POSIX and cannot source `env.sh` at all. It gets `.agents/env.fish`,
which contains **no logic of its own**:

- environment: `TTRPG_ENV_DUMP=1 bash .agents/env.sh` prints every managed
  variable as `KEY=VALUE`; the fish file just transcribes that (splitting `PATH`
  into a fish list).
- the `qmd` / `qmd-cpu` wrappers: POSIX shell functions cannot cross into fish,
  so they are defined as fish functions delegating to `.agents/bin/qmd`, which
  applies the real wrapper in bash.

So there is one implementation, not two. Claude's `SessionStart` hook picks the
right file from `$SHELL`. And Tier 1 needs nothing: every `bin/*` carries a bash
shebang, so tools already work under fish with zero setup.

### 2. `bin/` — hermetic entrypoints

Every one is the same six lines: resolve root, export `TTRPG_ROOT`, source
`env.sh`, exec the payload. `qmd` and `qmd-cpu` call the shell *function* rather
than a binary, so they carry the collection provisioning and CPU fallback.

`env.sh` resolves the real `qmd`/`pi`/`claude`/`codex` binary while skipping
`.agents/bin`, so putting this directory on `PATH` cannot cause a fork bomb.

Skills reference these paths and never a bare tool name. A bare `qmd` or
`marker_single` runs outside the contract and writes gigabytes into `~/.cache/`.

### 3. `manifest.toml` — one place to declare capabilities

Tools, MCP servers, vendored pins, prompts, prerequisites, lazily-fetched
artifacts, and **model tiers**. Prompts declare `tier = "fast"`, never a model
ID: `openai-codex/gpt-5.6-sol` is pi routing syntax and is actively harmful in a
Claude command. A model migration is a one-line edit here.

## The tool contract: one spec, two surfaces

`query-5etools`, `vault-frontmatter` and `image-gen` are each callable as a pi
tool *and* as a CLI. Rather than hand-mirroring parameters — a guaranteed drift
source — each has a `spec.mjs`:

```
.agents/cli/<tool>/spec.mjs
        │
        ├──► cli.mjs  + _lib/spec-args.mjs   →  argv parser  (every harness)
        └──► .pi/extensions/<tool>/index.ts  →  typebox schema  (pi only)
```

Nothing is generated; both sides just import the same object. Flag names are the
parameter name in kebab-case (`entityType` → `--entity-type`). `filters` is
`Array<{field,op,value}>` and so cannot be a flat flag — it is declared
`objectList`, exposed as repeatable `--filters 'field:op:value'` (alias `--filter`) plus
`--filters-json`.

## Per-tool isolation

Each tool owns its environment; there is no shared venv.

```
cli/<tool>/
├── pyproject.toml | package.json     language-native manifest
├── .venv/ | node_modules/            ISOLATED, gitignored
├── spec.mjs                          agent-facing params, where applicable
└── src/ …
```

`book-ingest` and `vault-sync` stay separate despite both being Python: wildly
different dependency weights (marker + torch + CUDA vs three small packages) and
different constraints (`>=3.12,<3.13` vs `>=3.11`). A broken or heavy tool
cannot affect any other.

This is also why there is no global `uv tool install marker-pdf`: `marker-pdf`
is already a declared dependency of `book-ingest`, so `bin/marker-single` just
runs it out of that venv.

## Lifecycle

```bash
.agents/harness doctor                  # = bootstrap --check; installs nothing
.agents/harness bootstrap --apply       # install harness deps + per-tool envs
.agents/harness sync                    # regenerate every adapter; idempotent
.agents/harness verify                  # asserts + regenerate-and-diff + smoke
.agents/harness vendor status
.agents/harness vendor sync 5etools
.agents/harness vendor update 5etools v2.29.0
```

Dependency tiers, and who installs what:

| Tier | What | Bootstrap behaviour |
|---|---|---|
| **G — global** | NVIDIA driver, Node ≥22, npm, uv, git, ripgrep, fd, jq, qmd, the harness binaries | **check and report only, never installs** |
| **T — per-tool** | each tool's own isolated environment | installs on `--apply` |
| **L — lazy** | qmd GGUFs (~2.2 GB), surya models (~3.3 GB), 5etools (~190 MB), the foundry-mcp clone | reports "will download on first use", **never pre-fetches** |

Tier L is deliberate: a fresh clone must be usable without a multi-gigabyte
download, and every one of those artifacts already self-fetches.

## Generated artifacts — do not edit

`harness sync` writes these; each carries a do-not-edit header and `verify`
regenerates them into memory and diffs:

| Output | From |
|---|---|
| `.claude/skills/<n>` (25 symlinks) | `.agents/skills/` scan |
| `.claude/commands/*.md` | canonical prompt body + manifest |
| `.claude/settings.json` | manifest (env hook, MCP allowlist, permissions) |
| `.claude/workflows/*.js` | `.agents/chains/*/spec.json` |
| `.pi/prompts/*.md` | canonical body **concatenated** + pi frontmatter |
| `.pi/chains/*.json`, `.pi/agents/creative-*.md` | same chain spec |
| `.mcp.json`, `.codex/config.toml`, `CLAUDE.md` | manifest |

Hand-authored and staying that way: `.pi/settings.json`, `.pi/mcp.json`,
`.pi/extensions/*`, and `.claude/settings.local.json` (user-owned; the generator
and the verifier both skip it).

**Why pi's prompts are concatenated rather than a pointer:** a "read and follow
`.agents/prompts/x.md`" stub would downgrade pi from a *guaranteed inlined body*
to an instruction the model may under-execute — a reliability regression on the
harness that works today, on prompts including the destructive `/cleanup`.
Claude keeps its guarantee via `@`-import; pi keeps its via concatenation.

## Cache layout

One root, forced project-local (unconditional, not `${VAR:-…}` — an inherited
value would break isolation silently). Escape hatch:
`TTRPG_ALLOW_HOST_CACHES=1`.

```
.cache/
├── index/          QMD_CONFIG_DIR   index.yml            rebuildable
├── xdg/            XDG_CACHE_HOME
│   ├── qmd/
│   │   ├── index.sqlite               the search index    rebuildable
│   │   └── models/    ~2.2 GB         qmd GGUFs           EXPENSIVE
│   └── datalab/       ~3.3 GB         surya/Marker        EXPENSIVE
├── huggingface/ torch/ uv/ npm/                           expensive
└── vendor/         pinned upstream checkouts              re-clonable
```

> **The index and the 2.2 GB model store are siblings inside
> `.cache/xdg/qmd/`.** Never delete that directory or sweep its contents; target
> `index.sqlite*` as named files. This is the single most expensive mistake
> available in this repo, and it is why `ttrpg-system-data-cleanup`
> discriminates by sub-path rather than by root.

## Harness differences worth knowing

| | pi | Claude Code | Codex |
|---|---|---|---|
| `.agents/skills/` | native | via generated symlinks | native |
| `AGENTS.md` | yes | needs `CLAUDE.md` → `@AGENTS.md` | yes |
| project slash prompts | yes | yes | **none — user scope only** |
| project MCP | yes | yes (needs `"type": "stdio"`) | generated `.codex/config.toml` |
| shell functions cross into tools | yes | yes | **no** |

So a bare `qmd` works under pi and Claude but not Codex — which is exactly why
`bin/` exists and why skills reference it. MCP servers are spawned by the
harness itself and never see env injection on *any* harness; MCP is Tier-1-only
by construction.
