# ttrpg-agent

**ttrpg-agent is an opinionated, hackable D&D 5e Dungeon Master workstation that runs under [pi](https://github.com/badlogic/pi-mono), [Claude Code](https://claude.com/claude-code), or [Codex](https://developers.openai.com/codex/cli).**

It aims to be the “ultimate solution” for DMs who are already comfortable asking coding agents to inspect files, run tools, and maintain a repo. The default stack is:

- **D&D 5e / 5e 2024 prep** as the core use case.
- **Foundry VTT** as the target table platform.
- **Any of three agent harnesses** — pick one; you do not need all three.
- **OpenAI API key** for metered tooling such as image generation and optional Marker LLM cleanup/captions.
- **Local-first data**: your PDFs, notes, 5etools clone, qmd index, generated assets, and Obsidian vault stay out of git.

This repository tracks the machinery: skills, slash prompts, tools, shell wrappers, and small CLIs. Your campaign and reference data live in ignored local folders.

Everything is expressed once, harness-neutrally, under **`.agents/`**; each
harness's configuration is *generated* from it. See
[`.agents/README.md`](.agents/README.md) for the technical layout, and
[Harness support](#harness-support) below for what each one can and cannot do.

---

## What it can do

- Search active notes, ingested books, and an optional legacy archive with **qmd**.
- Browse/filter YAML frontmatter facets (`tags`, `type`, `status`, pages, systems) through the read-only `vault_frontmatter` tool / `.agents/bin/vault-frontmatter`.
- Query local **5etools** for canonical creatures, spells, and items through `query_5etools` / `.agents/bin/query-5etools`.
- Ingest RPG PDFs into cross-linked Markdown under `vault/library/books/` using **marker-pdf**.
- Write durable Obsidian notes, stubs, canvases, read-alouds, NPCs, mechanics notes, and connections under `vault/notes/`.
- Convert OSR/BX/OSE/AD&D-style monsters and traps into D&D 5e/2024 equivalents.
- Produce **Foundry 5e Statblock Importer** paste text and separate Foundry dnd5e enricher prose.
- Connect to a running **Foundry VTT** world through MCP for live world inspection and controlled document operations.
- Generate OpenAI image assets on explicit request, with adjacent Markdown asset notes for indexing.
- Run isolated parallel ideation plus curation for open-ended creative design, and (under pi) delegate slow/noisy research and monster conversion to focused subagents.

## What it is not

- Not a Foundry module: live access uses a dedicated Foundry user and an external stdio MCP server; copy/paste importer workflows remain available.
- Not a substitute for DM judgment on encounter balance.
- Not a copyright-clean public dataset. Your local books/PDFs are for your personal prep.
- Not a zero-code app. It assumes you are comfortable letting an agent run shell commands in this repo.
- Not fully local when optional OpenAI/web API tooling is enabled.

---

## High-level project structure

```text
ttrpg-agent/
├── README.md                  # this guide
├── AGENTS.md                  # the project contract (all harnesses)
├── CLAUDE.md                  # generated: @-imports AGENTS.md for Claude Code
├── .mcp.json                  # generated: shared MCP server registration
├── .env.example               # non-secret config template
├── .agents/                   # ← CANONICAL. The only place a human edits.
│   ├── README.md              # technical map of the toolchain
│   ├── env.sh                 # the environment contract
│   ├── manifest.toml          # capability declarations
│   ├── harness                # bootstrap | doctor | sync | verify | vendor
│   ├── bin/                   # hermetic entrypoints — always call tools via these
│   ├── cli/                   # the tools, one isolated environment each
│   ├── skills/                # task-specific operating procedures
│   ├── conditional-skills/    # surfaced only when configured
│   ├── prompts/               # canonical slash-prompt bodies
│   ├── chains/                # multi-agent workflow specs
│   ├── scripts/               # pi launch wrappers
│   └── state/                 # ignored durable state (Foundry credential)
├── .pi/                       # generated adapter + hand-authored pi settings
├── .claude/                   # generated adapter (settings.local.json is yours)
├── .cache/                    # ignored: caches, model weights, vendor checkouts
│   ├── index/                 #   qmd collection config
│   ├── xdg/qmd/               #   qmd index.sqlite + ~2.2 GB of models
│   ├── xdg/datalab/           #   ~3.3 GB of surya/Marker models
│   └── vendor/5etools/        #   pinned 5etools mirror
├── imports/                   # ignored raw user inputs
│   ├── books/                 # source PDFs/EPUBs
│   ├── source-vault/          # optional old vault; treated read-only
│   └── fvtt-data/             # Foundry exports (the one writable subfolder)
└── vault/                     # ignored Obsidian workspace
    ├── notes/                 # active authored campaign/table prep
    └── library/books/         # generated book-ingest output
```

`vault/notes/` is the main writing surface. `vault/library/books/` is generated
reference material. `imports/` is your input data. `.cache/` is a single cache
root holding both rebuildable state (the qmd index) and ~5.5 GB of model weights
plus vendored checkouts that must survive an index reset — which is why
`ttrpg-system-data-cleanup` discriminates by sub-path rather than by directory.

---

## Dependencies

### Hard runtime dependencies

| Dependency | Why it is needed | Install notes |
|---|---|---|
| Git | clone/fork this repo, vendored checkouts, package git sources | <https://git-scm.com/install> |
| Node.js + npm | the harness tooling, qmd, JS tools | Node 22+ for qmd (pi itself needs 20.6+); current working machine uses Node 24. Official download or nvm: <https://nodejs.org/en/download> |
| **one** agent harness | see [Harness support](#harness-support) | pi: `npm install -g @earendil-works/pi-coding-agent` · Claude Code: <https://claude.com/claude-code> · Codex: <https://developers.openai.com/codex/cli> |
| qmd | local BM25/vector/hybrid Markdown search | `npm install -g @tobilu/qmd` per qmd npm/GitHub |
| uv | Python tool/project runner and interpreter manager | `curl -LsSf https://astral.sh/uv/install.sh \| sh` or `brew install uv`; docs: <https://docs.astral.sh/uv/getting-started/installation>. Prefer `uv run`/`uv tool` over system Python. |
| ripgrep / `rg` | fast repo/vault search used by agents | `sudo apt install ripgrep`, `brew install ripgrep`, or winget; docs: <https://ripgrep.dev> |
| fd | fast file discovery used by humans/agents | `sudo apt install fd-find` on Ubuntu (binary is often `fdfind`), `brew install fd`, or winget; upstream: <https://github.com/sharkdp/fd> |
| jq | inspect JSON ingest reports and qmd/tool output | `sudo apt install jq`, `brew install jq`, or winget |
| Bash + coreutils | shell wrappers in `.agents/scripts/` and `.agents/cli/` | Linux/macOS/WSL first-class; native Windows is not the primary target |
| iproute2 (`ip`) | automatic Windows-host discovery for Foundry MCP under WSL | Normally preinstalled on Ubuntu; otherwise `sudo apt install iproute2` |

**`marker-pdf` is deliberately not a global install.** It is a declared
dependency of `book-ingest`, so it lives in that tool's own venv along with its
torch/CUDA wheels. `.agents/bin/marker-single` runs it from there.

### Required accounts/keys

- **Whatever your harness needs.** pi: a ChatGPT Plus/Pro Codex subscription
  (`/login` inside pi). Claude Code: a Claude subscription or API key. Codex: a
  ChatGPT subscription. Auth state lives in your harness's own config, never in
  this repo.
- **OpenAI API key** — put `OPENAI_API_KEY=...` in `.env` for image generation and optional Marker LLM modes. Normal fast PDF ingest can run with `TTRPG_MARKER_LLM_MODE=no` and no API key.

### Optional but useful

- **NVIDIA CUDA** for faster qmd/model and Marker workloads. The shell wrapper detects `/usr/local/cuda/bin/nvcc` and sets CUDA-related env vars. CPU fallback is supported.
- **Obsidian** for browsing/editing `vault/` as a graph.
- **Foundry VTT + dnd5e system** for live MCP access; the **5e Statblock Importer module** remains optional for paste-based monster imports. Live MCP uses the external `TheStranjer/foundry-vtt-mcp` server and does not require a Foundry-side MCP module.
- **Exa / Perplexity / Gemini API keys** or a supported browser login for the `pi-web-access` web research extension. Exa MCP may work with no key.

---

## Install / bootstrap

These commands assume Linux or WSL2. macOS users can replace apt packages with Homebrew equivalents.

```bash
# 1. System packages
sudo apt update
sudo apt install -y git curl build-essential jq ripgrep fd-find

# Ubuntu/Debian names fd as fdfind. Add fd if needed.
command -v fd >/dev/null || sudo ln -s "$(command -v fdfind)" /usr/local/bin/fd

# 2. Node.js + npm, preferably via nvm or the official Node installer
# See https://nodejs.org/en/download if you do not already have node/npm.
node --version
npm --version

# 3. qmd, plus ONE harness (pick whichever you use)
npm install -g @tobilu/qmd
npm install -g @mariozechner/pi-coding-agent    # pi
# ...or install Claude Code / the Codex CLI instead.

# 4. uv (provisions Python for the Python-based tools)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 5. Clone/fork this workspace
git clone <your-fork-url> ttrpg-agent
cd ttrpg-agent
```

Then let the harness script do the mechanical work. It is the same command under
every harness and installs nothing system-wide:

```bash
.agents/harness bootstrap --check     # reports only; installs nothing (it does create the .cache/ and vault/ skeleton)
.agents/harness bootstrap --apply     # scaffold .env, install per-tool envs, sync adapters
```

`--apply` installs the harness's own dependencies, creates each tool's isolated
environment, scaffolds `.env` from `.env.example`, materializes the cache
layout, and generates the adapters for whichever harnesses it finds on `PATH`.
It never installs system packages, and it never pre-downloads the multi-gigabyte
model sets — those arrive on first use.

For the conversational version — which explains each `.env` key, offers to
import an old Obsidian vault, and can fetch the 5etools mirror — start your
harness and run `/bootstrap` (pi, Claude Code) or ask for the bootstrap workflow
(Codex, which has no project slash prompts).

Optional, pi only. `pi-launch.sh` is the documented way to start pi with qmd
pinned to the project; `pi-isolated.sh` additionally ignores your global
`~/.pi/agent/`:

```bash
./.agents/scripts/pi-launch.sh
./.agents/scripts/pi-isolated.sh
```

### Python command policy

Use `uv` for Python execution instead of raw `python`/`python3`, including quick one-off scripts. Add temporary libraries with `--with` rather than installing them globally:

```bash
uv run --with requests python - <<'PY'
import requests
response = requests.get('https://httpbin.org/get')
print(f"Status Code: {response.status_code}")
PY
```

In practice you should not need either: every tool has a hermetic entrypoint at
`.agents/bin/<tool>` that applies the environment contract and then runs the
tool out of its own isolated environment. Use those. A bare `qmd` or
`marker_single` runs *outside* the contract and scatters multi-gigabyte model
caches into `~/.cache/`.

### Bootstrap smoke checks

```bash
.agents/harness doctor      # every prerequisite, with versions and fixes
.agents/harness verify      # structural checks + adapter drift + env smoke tests
```

Or by hand:

```bash
.agents/bin/qmd status
.agents/bin/qmd collection list
.agents/bin/query-5etools --entity-type creature --name goblin --output summary
.agents/bin/vault-frontmatter --action fields
```

Expected qmd shape:

- `notes` → `vault/notes/`
- `books` → `vault/library/books/`
- `archive` → `imports/source-vault/` and marked excluded by default

---

## Common workflows

### Add reference data

```bash
mkdir -p imports/books imports/source-vault
cp ~/Downloads/My-Adventure.pdf imports/books/
# optional old vault; this is read-only by project policy
rsync -a ~/Documents/OldVault/ imports/source-vault/
```

### Ingest a PDF book

Just describe the intent (the agent loads the `ttrpg-import-book-pdf` skill on its own):

```text
ingest imports/books/My-Adventure.pdf
```

Or run the CLI directly. With `OPENAI_API_KEY` set, follow the ordered `next_steps` returned in the JSON output (classify-system, summarize, tag, qmd refresh):

```bash
.agents/bin/book-ingest --json imports/books/My-Adventure.pdf
qmd update && qmd embed
```

### Search your prep/library

```text
/find-anything haunted charcoal burners in my notes and books
/find-monster goblin boss
```

For broad or unclear scoping, agents may use `vault_frontmatter` before qmd to
browse metadata facets such as tags, note types, status, book pages, sections, or
systems. It is read-only and file-backed; missing metadata is not treated as
absence of content.

### Convert and prepare for Foundry

```text
/convert-monster this BX boggart for 5e 2024, party level 5, tough
/foundry-monster format this statblock for importer paste
```

### Create table-facing material

```text
/npc a suspicious river toll collector
/readaloud the party finds a candlelit pit in the forest
/illustrate original token portrait for a moss-covered undead charcoal burner
/run-chain creative-brainstorm -- Create five structurally distinct complications for this alliance; context: ...
```

The creative-brainstorm chain only generates and curates possibilities. Include all relevant context and constraints in its brief; the main agent handles retrieval, validation, and implementation. Image generation is metered and should only happen on explicit request.

---

## Harness support

Pick one. Everything in `AGENTS.md` and every skill applies to all three; the
differences below are about *discovery and plumbing*, not capability, and each
gap has a stated workaround.

| Capability | pi | Claude Code | Codex |
|---|---|---|---|
| Skills from `.agents/skills/` | native | via generated `.claude/skills/` symlinks | native |
| Project contract | `AGENTS.md` | generated `CLAUDE.md` → `@AGENTS.md` | `AGENTS.md` |
| Slash prompts | `.pi/prompts/` | `.claude/commands/` | ❌ user scope only |
| Project MCP config | `.mcp.json` + `.pi/mcp.json` | `.mcp.json` + `enabledMcpjsonServers` | ❌ none |
| Project hooks | extensions | `.claude/settings.json` | ❌ no project scope (user-scope `~/.codex/hooks.json` exists) |
| Bare `qmd …` in a tool shell | ✅ | ✅ | ❌ functions don't cross |
| Tools via `.agents/bin/*` | ✅ | ✅ | ✅ |
| Parallel multi-agent | chain runner | `.claude/workflows/*.js` | ❌ run lenses inline |
| Runtime MCP reconnect | `mcp({...})` | restart session | restart session |

### Known gaps and what to do about them

- **Codex has no project prompts.** Name the workflow instead, or read
  `.agents/prompts/<name>.md` directly.
- **Codex has no project MCP config.** Register the Foundry server once,
  globally — it writes your user config, so it is never done implicitly:

  ```bash
  .agents/harness codex-mcp register foundry-vtt   # undo: ... codex-mcp remove foundry-vtt
  ```

  Then **restart Codex** — it attaches MCP servers only at session start.
  `.agents/harness doctor` reports whether this is done. If a Codex session
  says it has no Foundry tools, this is almost always why: the tool namespace
  does not exist at all, so nothing inside the session can hint at it.

- **Codex gets no shell functions**, only inherited environment variables (via
  the `.agents/bin/codex` launcher). A bare `qmd` will not work there. Use
  `.agents/bin/qmd`, which is what the skills tell you to do anyway.
- **pi needs project trust**, and `-p` (non-interactive) needs `--approve`.
- **pi 0.83 ignores `shellCommandPrefix` in `-p` mode** (verified: even a bare
  `export PROBE=1` prefix does not reach the bash tool). So a *headless* pi run
  gets no Tier 2 at all, and a bare `qmd` there will build an index in
  `~/.cache/qmd` instead of the project. This is precisely why Tier 2 is
  declared non-load-bearing: `.agents/bin/qmd` works in every mode, and every
  skill references it. Interactive pi still applies the prefix.
- **Claude Code drops `.mcp.json` entries without `"type": "stdio"`** — the
  generator always emits it; `verify` asserts it.
- **Native-Windows clones break the `.claude/skills/` symlinks.** Use WSL, or
  enable Developer Mode / `git config core.symlinks true` before cloning.
- **MCP never sees env injection on any harness**, because servers are spawned
  by the harness itself rather than by its Bash tool. That is why the MCP entry
  points at `.agents/bin/foundry-mcp` and not at `run.sh`.

---

## Configuration

### `.pi/settings.json`

Current project defaults:

- `defaultProvider`: `openai-codex`
- `defaultModel`: `gpt-5.6-sol`
- `defaultThinkingLevel`: `high`
- Model routing:
  - `gpt-5.6-sol` for the project default, complex conversions, and quality-first creative work.
  - `gpt-5.6-terra` for research, retrieval, cleanup planning, and lightweight generation.
  - `gpt-5.6-luna` for repeatable, high-volume book-ingest LLM calls.
  - Prompt and agent overrides preserve their prior thinking levels unless the workload requires more.
- Project pi packages:
  - `pi-web-access` for web search/fetch tools.
  - `pi-subagents` for delegation workflows.
  - `pi-prompt-template-model` for richer prompt-template support.
  - `pi-mcp-adapter` for MCP gateway support.
- Built-in subagents are disabled except the configured delegate path; project subagents live in `.pi/agents/`.
- `shellCommandPrefix` sources `.agents/env.sh`, so pi bash commands automatically use project-local qmd state and project `.env` values.

Do not put secrets in `.pi/settings.json`.

### `.env`

Copy `.env.example` to `.env`. `.env` is ignored. Important keys:

| Key | Used by | Notes |
|---|---|---|
| `OPENAI_API_KEY` | `generate_image` tool, `book-ingest --llm ...` | Required for image generation and any non-`no` Marker LLM mode |
| `TTRPG_IMAGE_MODEL` | image generation | Default `gpt-image-1` |
| `TTRPG_IMAGE_SIZE` | image generation | `1024x1024`, `1536x1024`, `1024x1536`, etc. |
| `TTRPG_IMAGE_QUALITY` | image generation | `auto`, `low`, `high` depending on model/account |
| `TTRPG_IMAGE_OUTPUT_FORMAT` | image generation | `png`, `jpeg`, or `webp`; default `png` |
| `TTRPG_IMAGE_OUTPUT_DIR` | image generation | Must stay under `vault/notes/images` |
| `TTRPG_MARKER_LLM_MODE` | book ingest | `no`, `images-only`, `text-only`, `all` |
| `TTRPG_MARKER_OPENAI_MODEL` | book ingest | Default `gpt-5.6-luna` for Marker LLM calls |
| `TTRPG_MARKER_OPENAI_BASE_URL` | book ingest | OpenAI-compatible base URL; default `https://api.openai.com/v1` |
| `TTRPG_MARKER_LLM_MAX_CONCURRENCY` | book ingest | Parallel Marker LLM calls; default `2`, lower to `1` for rate limits |
| `TTRPG_MARKER_LLM_MIN_INTERVAL_SECONDS` | book ingest | Minimum spacing between Marker LLM calls; default `2.0` to reduce TPM bursts |
| `TTRPG_MARKER_DEVICE` | book ingest | `auto`, `cuda`, `cpu`, `mps` |
| `TTRPG_MARKER_*_BATCH_SIZE` | book ingest | Optional Marker local OCR/layout batch tuning |
| `EXA_API_KEY` | web research | Optional direct Exa key for pi-web-access; Exa MCP may work without it |
| `PERPLEXITY_API_KEY` | web research | Optional Perplexity fallback for pi-web-access |
| `GEMINI_API_KEY` | web/video research | Optional Gemini API fallback for pi-web-access |
| `FOUNDRY_MCP_USER` / `FOUNDRY_MCP_PASSWORD` | live Foundry MCP | Existing dedicated user in the active world; keep the password only in `.env` |
| `FOUNDRY_MCP_HOST` / `FOUNDRY_MCP_PORT` | live Foundry MCP | `HOST=auto` discovers the Windows host under WSL; otherwise use a direct hostname/IP and numeric port |
| `FOUNDRY_MCP_ALLOW_SELF_SIGNED` | live Foundry MCP | Set `true` only for a known self-signed Foundry HTTPS endpoint |

### `.agents/env.sh` — the environment contract

The single canonical definition of the project shell environment, shared by
every harness. Exports the `TTRPG_*` paths, sources `.env`, forces every cache
project-local under `.cache/`, sets the CUDA/qmd fallback vars, creates the
directory skeleton, and defines the `qmd`/`qmd-cpu` shell functions with lazy
collection provisioning and a CUDA→CPU retry.

It is applied two ways. **Tier 1** is `.agents/bin/<tool>`, which sources it
itself — that is what makes a tool correct with no harness configuration at all.
**Tier 2** is per-harness convenience so a bare `qmd …` also works: pi's
`shellCommandPrefix`, Claude's `SessionStart` hook writing to `$CLAUDE_ENV_FILE`,
and the `.agents/bin/codex` launcher. Nothing depends on Tier 2.

**Your login shell matters for Tier 2 only.** bash and zsh source
`.agents/env.sh` directly. fish cannot — it is not POSIX — so it sources
`.agents/env.fish`, a thin shim that adopts the same environment from
`TTRPG_ENV_DUMP=1 bash .agents/env.sh` and delegates `qmd` to the Tier 1
entrypoints; Claude's hook picks the right file from `$SHELL`. Tier 1 is
unaffected either way: every `.agents/bin/*` has a bash shebang.

### `.agents/scripts/` (pi only)

- `pi-launch.sh` — normal launcher; keeps your global pi config but localizes qmd.
- `pi-isolated.sh` — full pi isolation via `PI_CODING_AGENT_DIR=.pi-home`.
- `qmd-init.sh` — thin `qmd update` helper with the environment contract applied.

---

## Data and git policy

Tracked by git:

- `README.md`, `AGENTS.md`, `.env.example`, `.gitignore`
- **`.agents/`** — the canonical toolchain: `env.sh`, `manifest.toml`, `harness`,
  `harness-lib/`, `bin/`, `cli/` source + tests + lockfiles + READMEs, `skills/`,
  `conditional-skills/`, `prompts/`, `chains/`, `scripts/`
- Generated adapters, tracked so a clone works before the first `sync`:
  `CLAUDE.md`, `.mcp.json`, `.claude/commands/`, `.claude/settings.json`,
  `.claude/skills/` symlinks, `.claude/workflows/`, `.pi/prompts/`,
  `.pi/agents/`, `.pi/chains/`
- Hand-authored harness config: `.pi/settings.json`, `.pi/mcp.json`, `.pi/extensions/`

Ignored/local:

- `.env`, `.env.*` except `.env.example`
- `vault/` — Obsidian vault, active campaign notes, generated images, ingested book output. This may be a real local directory or a symlink to a Windows-accessible vault.
- `imports/` — source PDFs, legacy vault, Foundry exports, 5etools clone, and other local inputs.
- `.cache/` — the single cache root: qmd index and collection config
  (rebuildable), ~5.5 GB of qmd/surya model weights, HuggingFace/torch/uv/npm
  caches, and pinned vendor checkouts (expensive; must survive an index reset)
- `.agents/state/` — durable agent state, currently the Foundry GM credential.
  Not a cache and never a cleanup target.
- `.agents/node_modules/`, `.agents/cli/*/.venv`, `.agents/cli/*/node_modules`
- `.trash/`, test caches
- `.claude/settings.local.json` — your personal Claude permissions
- `.pi/npm/`, `.pi/git/`, `.pi-home/`, `.pi-subagents/` — pi runtime/package caches

Policy boundaries:

- `imports/source-vault/` is read-only inspiration/archive material.
- `.cache/vendor/5etools/` is a read-only pinned vendor checkout of canonical rules data, reached through `$TTRPG_5ETOOLS_DIR`.
- `vault/library/books/` is generated by `book-ingest`; do not hand-edit ingested chapters.
- `vault/notes/` is writable active campaign/table prep.
- Durable notes should have frontmatter, body wikilinks, and a `## Connections` section.
- Destructive cleanup requires dry-run inventory and exact confirmation.

### Fresh clone local inventory

A fresh clone intentionally ships no campaign/reference data. The local data folders are ignored and may be absent until bootstrap/tooling creates them:

- `imports/books/` — put owned/allowed PDFs or EPUBs here.
- `imports/source-vault/` — optional read-only copy of an older Obsidian vault.
- `imports/fvtt-data/` — targeted Foundry VTT JSON/TXT/ZIP exports.
- `.cache/vendor/5etools/` — optional local 5etools mirror clone.
- `vault/notes/` — active authored campaign/table prep.
- `vault/notes/images/` — generated image assets and adjacent asset notes.
- `vault/library/books/` — generated Markdown output from book ingestion.

The shell wrapper/qmd setup recreates the expected structure with `mkdir -p`, and `/bootstrap` can populate optional inputs you choose.

---

## Implemented skills

Skills are procedural reference files the agent loads when a task matches. Current project skills cover:

- **Rules/canonical data**
  - `ttrpg-rules-5etools-query` — structured creature/spell/item lookups through `query_5etools`.
  - `ttrpg-rules-5etools-native` — direct JS/native 5etools spelunking for classes, feats, backgrounds, schema details, and unsupported records.
  - `ttrpg-rules-osr-to-5e` — OSR/OSE/BX/AD&D monster/trap/mechanic conversion judgment.
- **Search/research**
  - `ttrpg-library-search` — qmd search over `books`, `notes`, and optional `archive`, with optional `vault_frontmatter` metadata scouting.
  - `ttrpg-research-web` — outside-web inspiration/research with citations.
- **Vault authoring**
  - `ttrpg-vault-authoring` — placement, file boundaries, stubs, graph/link policy.
  - `ttrpg-vault-rich-notes` — table-ready Obsidian Markdown patterns.
  - `ttrpg-vault-canvas` — Obsidian Canvas JSON creation/validation.
- **Imports**
  - `ttrpg-import-book-pdf` — canonical end-to-end PDF book ingest pipeline (ingest → classify-system → summarize → tag → qmd).
  - `ttrpg-tag-book-manual` — agent-driven manual tag fallback when `OPENAI_API_KEY` is absent or for per-chapter overrides.
  - `ttrpg-import-raw-pdf` — one-off raw Marker conversion/debugging.
  - `ttrpg-import-archive-vault` — safe promotion of selected old-vault notes.
- **Foundry**
  - `ttrpg-foundry-statblock-importer` — plain WotC-style statblock importer formatting.
  - `ttrpg-foundry-enrichers` — Foundry dnd5e text enrichers for journals/items/actor descriptions.
  - `ttrpg-foundry-dnd5e-wiki` — targeted research against Foundry dnd5e implementation docs.
  - `ttrpg-foundry-mcp` — configure, bootstrap, smoke-test, reconnect, and troubleshoot live Foundry MCP access.
- **Creative prep**
  - `creative-brainstorm` — four isolated creative lenses followed by curation for open-ended design tasks; the main agent supplies a self-contained brief.
  - `ttrpg-create-readaloud` — boxed text/read-aloud style.
  - `ttrpg-create-image-gen` — explicit image-generation workflow and asset-note contract.
- **Campaign design**
  - `ttrpg-campaign-design` — multi-session arcs, quest networks, mysteries, faction conflicts, audited against established campaign truth.
- **Navigation**
  - `ttrpg-vault-navigation` — the workspace map and data boundaries; load before touching local data.
- **Maintenance**
  - `ttrpg-system-qmd-maintenance` — refresh/rebuild qmd safely.
  - `ttrpg-system-data-cleanup` — destructive data cleanup with sub-path-level safeguards over `.cache/`.
  - `ttrpg-harness-engineering` — maintain the toolchain itself: `.agents/` canonical sources, generated adapters, bootstrap/sync/verify. Toolchain only; never touches user data.
- **Conditional** (surfaced only when configured)
  - `ttrpg-wsl-sync` — mirror the vault to a Windows-side copy; appears only when `TTRPG_WINDOWS_AGENT_DIR` is set.

---

## Slash prompts

Canonical prompt bodies live in `.agents/prompts/`. Each harness gets its own
generated form: `.pi/prompts/` (body concatenated + pi frontmatter) and
`.claude/commands/` (`@`-import + a skill-preload preamble). Invoke with `/name`
in pi and Claude Code.

**Codex has no project slash prompts** — user scope only. Under Codex, name the
workflow instead ("run the cleanup workflow") or read
`.agents/prompts/<name>.md` directly; its argument convention is whatever you
type after the request rather than `$ARGUMENTS`.

| Prompt | Purpose |
|---|---|
| `/bootstrap` | One-time first-run setup wizard: dependencies, `.env`, optional imports, smoke tests. |
| `/find-monster` | Canonical monster lookup; 5etools first, qmd fallback. |
| `/find-anything` | General qmd search across books/notes/archive, with optional frontmatter scouting for broad scope. |
| `/convert-monster` | OSR/non-5e monster → 5e + Foundry importer text. |
| `/foundry-monster` | Normalize an existing statblock for Foundry importer paste. |
| `/npc` | Fast NPC sketch, with optional save to vault. |
| `/readaloud` | Boxed text plus DM notes, saved when reusable. |
| `/illustrate` | Generate one OpenAI image asset only on explicit request. |
| `/cleanup` | Safe destructive cleanup flow with scope/dry-run/confirmation. |

---

## Subagents

**Portable.** `creative-brainstorm` runs four isolated ideators concurrently
through different creative lenses, then passes their structured candidates to an
isolated curator. `creative-ideator` and `creative-curator` are internal stages,
not standalone workflows. It is defined once in
`.agents/chains/creative-brainstorm/spec.json` — including both agent
personas — and generated into `.pi/chains/` + `.pi/agents/` for pi and
`.claude/workflows/creative-brainstorm.js` for Claude Code. Codex has no
parallel runner: run the four lenses inline, one at a time, keeping them
genuinely independent.

**pi only.** These are pi subagent definitions in `.pi/agents/` with no Claude
or Codex equivalent; on those harnesses do the work inline using the named skill
instead. Nothing downstream depends on a subagent having run.

- `researcher` — read-only broad search across books, notes, archive, 5etools snippets, and web. Inline: `ttrpg-library-search` + `ttrpg-research-web`.
- `statblock-converter` — one-monster conversion agent that saves monster notes under `vault/notes/mechanics/monsters/` and emits Foundry importer text. Inline: `ttrpg-rules-osr-to-5e`.

---

## Custom tools and extensions

### `query_5etools` pi extension

Located in `.agents/cli/query-5etools/`.

It exposes a project-local pi tool for common 5etools lookups:

- Entities: `creature`, `spell`, `item`
- Creature filters: name, source, CR/range, type, size, alignment, environment
- Spell filters: name, source, level/range, school, class, concentration, ritual
- Item filters: name, source, rarity, kind, attunement
- Output: `summary`, `json`, `markdown`
- Ruleset preference: `2014`, `2024`, or `either`

For unsupported 5etools entity types or renderer weirdness, agents use `ttrpg-rules-5etools-native` and small read-only Node snippets against `.cache/vendor/5etools/`.

### `vault_frontmatter` pi extension

Located in `.agents/cli/vault-frontmatter/`.

It exposes a project-local, read-only pi tool for YAML frontmatter in:

- `vault/notes/**/*.md`
- `vault/library/books/**/*.md`

Actions:

- `fields` — list available frontmatter fields in a scope.
- `values` — list values/counts for one field, such as `tags`, `type`, `status`, or `system`.
- `find` — return files matching simple predicates (`exists`, `missing`, `equals`, `contains`, `matches`, `gte`, `lte`).
- `inspect` — show one file's frontmatter, title, qmd URI, page range, and optional short preview.

It does not search body prose, infer metadata, maintain an index/cache, or write files. Agents use it as an optional metadata/facet scout before qmd/read evidence when broad scope is unclear. The local `yaml` dependency gives full YAML parsing; run `npm install --prefix .agents/cli/vault-frontmatter` after clone or via `/bootstrap`. Full contract and examples are in `.agents/cli/vault-frontmatter/README.md`.

### `.agents/cli/book-ingest`

A uv-managed Python CLI that converts PDFs into sectioned Markdown and drives the full ingestion lifecycle:

```bash
.agents/bin/book-ingest --json imports/books/My-Book.pdf
.agents/bin/book-ingest validate vault/library/books/my-book
```

Highlights:

- Calls Marker through its Python SDK in-process; no subprocess cache/log tree.
- Plans sections from Marker's table of contents, falling back to structural Markdown headings.
- Writes `vault/library/books/<slug>/__<slug>.md` overview, chapter files, copied images, and sidecars under `<slug>/.ingest/` (`provenance.json`, `report.json`).
- Skips unchanged re-ingests by source hash; pinned to Python 3.12 for the known-good Marker/PyTorch/CUDA stack.
- Supports `--llm no|images-only|text-only|all` and emits ordered `next_steps` (classify-system, summarize, tag, qmd refresh) when `OPENAI_API_KEY` is configured.
- Subcommands: `ingest` (default), `classify-system`, `summarize`, `tag`, `tag-manual`, `refresh-overview`, `validate`. Full contract in `.agents/cli/book-ingest/README.md`.

### `.agents/cli/image-gen`

A Pi extension exposing the `generate_image` tool. Called as a typed tool under pi; as `.agents/bin/generate-image` everywhere else:

```text
generate_image({
  subject: "Original fantasy portrait of a tired dwarf cartographer, no text, no watermark.",
})
```

It writes `vault/notes/images/<slug>-<hash>.png` and an adjacent `.md` asset note containing frontmatter, prompt, params, sanitized response metadata, adoption notes, and connections. No npm dependencies; tests run via `node --test .agents/cli/image-gen/*.test.js`.

### `.agents/cli/vault-sync`

A deliberately dumb, safe copy/inspect tool for legacy archive notes:

```bash
.agents/bin/vault-sync inspect imports/source-vault/path/Note.md
.agents/bin/vault-sync copy imports/source-vault/path/Note.md vault/notes/npcs/note.md
```

It never decides meaning or destination. The LLM chooses placement, then edits the copied note according to vault-authoring policy.

---

## Foundry VTT workflow

The project supports three separate Foundry workflows:

1. **Live MCP world access** — `.mcp.json` and `.pi/mcp.json` launch `.agents/bin/foundry-mcp`, which connects as a dedicated Foundry user. See [`.agents/cli/foundry-mcp/README.md`](.agents/cli/foundry-mcp/README.md). Run `.agents/cli/foundry-mcp/smoke-test.sh` for an end-to-end read-only validation.
2. **Importer statblock** — plain WotC-style prose for the 5e Statblock Importer. No Foundry enrichers inside the import block.
3. **Post-import prose** — Foundry dnd5e enrichers for actor/item/journal descriptions, clickable saves/checks/damage, references, and notes.

Keep these concerns separate: MCP performs live world operations, importer text creates actors through the optional module, and enrichers format post-import descriptions.

---

## Maintenance

Normal refresh after note edits or ingest:

```bash
.agents/bin/qmd update
.agents/bin/qmd embed      # after significant new content; can be slow
.agents/bin/qmd status
```

Toolchain health, at any time:

```bash
.agents/harness doctor      # prerequisites, .env gaps, vendor pins
.agents/harness verify      # structural checks, adapter drift, env smoke tests
.agents/harness sync        # regenerate adapters after editing .agents/
```

If search looks stale, ask your agent:

```text
Use ttrpg-system-qmd-maintenance to verify qmd collections and refresh the index.
```

For destructive cleanup, use `/cleanup` or explicitly ask for
`ttrpg-system-data-cleanup`. The expected flow is scope → dry-run inventory →
exact confirmation → delete → recreate skeleton → qmd refresh if needed.

Note that the qmd index (`.cache/xdg/qmd/index.sqlite`) sits *beside* the 2.2 GB
model store (`.cache/xdg/qmd/models/`). Index resets therefore target named
files, never the directory — the cleanup skill enforces this, and refuses to
delete the expensive caches even when they are named explicitly.

To update a pinned upstream checkout:

```bash
.agents/harness vendor status
.agents/harness vendor update 5etools v2.29.0
.agents/harness vendor sync 5etools
```

A 5etools bump changes rules data underneath your campaign notes, so it is
always explicit and never automatic.

---

## Hackability: make it yours

The intended customization loop is simple:

1. Fork the repo.
2. Put your PDFs and vault in ignored local folders; fetch 5etools with `.agents/harness vendor sync 5etools`.
3. Launch your harness in the repo.
4. Ask it to improve the repo machinery when you notice repeated work — the
   `ttrpg-harness-engineering` skill exists to keep those changes coherent.

Examples:

```text
Make a new /session-prep prompt that creates a three-scene prep note under vault/notes/sessions/.

Add a skill for my homebrew hexcrawl procedures and make future travel prep use it.

Extend query_5etools to support feats and backgrounds.

Write a small .agents/cli tool that exports selected notes as Foundry journal HTML.
```

Where to change things:

- New agent behavior/routing: `AGENTS.md`
- New repeatable procedure: `.agents/skills/<name>/SKILL.md`
- New slash workflow: `.agents/prompts/<name>.md` + a `[[prompt]]` block in `.agents/manifest.toml`
- New custom CLI tool: `.agents/cli/<tool-name>/` + `[[tool]]` + a `.agents/bin/<tool>` entrypoint
- New pi tool registration: `.pi/extensions/<name>/` (parameters come from the tool's `spec.mjs`)
- Shell environment: `.agents/env.sh`
- Model choice: the `[tiers.*]` table in `.agents/manifest.toml` — never a model ID in a prompt

**Never hand-edit a generated adapter.** After changing anything under
`.agents/`, run:

```bash
.agents/harness sync
.agents/harness verify
```

Then `/reload` inside pi, or restart your Claude Code / Codex session.

---

## Security and privacy notes

- Pi packages and project extensions run code with your user permissions. Review third-party packages before adding them.
- `.env` is ignored; do not commit API keys.
- `vault/` and `imports/` are ignored; do not commit private campaign notes or copyrighted PDFs.
- Web/image/LLM features can send prompts or extracted content to external services when enabled.
- The default local-first path is: qmd + 5etools + Marker without LLM mode.

---

## Source links for external installs

- pi coding agent: <https://github.com/badlogic/pi-mono>
- pi npm package: <https://www.npmjs.com/package/@mariozechner/pi-coding-agent>
- qmd: <https://github.com/tobi/qmd>
- qmd npm package: <https://www.npmjs.com/package/@tobilu/qmd>
- uv install docs: <https://docs.astral.sh/uv/getting-started/installation>
- marker-pdf: <https://github.com/datalab-to/marker>
- ripgrep: <https://ripgrep.dev>
- fd: <https://github.com/sharkdp/fd>
- Git installs: <https://git-scm.com/install>
- Node.js downloads: <https://nodejs.org/en/download>
