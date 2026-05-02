# ttrpg-agent

**ttrpg-agent is an opinionated, hackable D&D 5e Dungeon Master workstation built on [pi](https://github.com/badlogic/pi-mono).**

It aims to be the “ultimate solution” for DMs who are already comfortable asking coding agents to inspect files, run tools, and maintain a repo. The default stack is:

- **D&D 5e / 5e 2024 prep** as the core use case.
- **Foundry VTT** as the target table platform.
- **pi coding agent** as the agent harness/backbone.
- **ChatGPT Plus/Pro Codex subscription** for pi’s main model access.
- **OpenAI API key** for metered tooling such as image generation and optional Marker LLM cleanup/captions.
- **Local-first data**: your PDFs, notes, 5etools clone, qmd index, generated assets, and Obsidian vault stay out of git.

This repository tracks the machinery: pi skills, slash prompts, subagents, extensions, shell wrappers, and small CLI tools. Your campaign and reference data live in ignored local folders.

---

## What it can do

- Search active notes, ingested books, and an optional legacy archive with **qmd**.
- Query local **5etools** for canonical creatures, spells, and items through a custom pi tool.
- Ingest RPG PDFs into cross-linked Markdown under `vault/library/books/` using **marker-pdf**.
- Write durable Obsidian notes, stubs, canvases, read-alouds, NPCs, mechanics notes, and connections under `vault/notes/`.
- Convert OSR/BX/OSE/AD&D-style monsters and traps into D&D 5e/2024 equivalents.
- Produce **Foundry 5e Statblock Importer** paste text and separate Foundry dnd5e enricher prose.
- Generate OpenAI image assets on explicit request, with adjacent Markdown asset notes for indexing.
- Delegate slow/noisy work to focused subagents for ingest, research, and monster conversion.

## What it is not

- Not a live Foundry module or API integration. Foundry output is copy/paste oriented.
- Not a substitute for DM judgment on encounter balance.
- Not a copyright-clean public dataset. Your local books/PDFs are for your personal prep.
- Not a zero-code app. It assumes you are comfortable letting pi run shell commands in this repo.
- Not fully local when optional OpenAI/web API tooling is enabled.

---

## High-level project structure

```text
ttrpg-agent/
├── README.md                  # this guide
├── AGENTS.md                  # standing instructions loaded by pi
├── .env.example               # non-secret config template
├── .gitignore                 # keeps campaign/import/runtime data out of git
├── .pi/
│   ├── settings.json          # project pi defaults and package list
│   ├── skills/                # task-specific operating procedures
│   ├── prompts/               # slash prompts, e.g. /find-monster
│   ├── agents/                # focused subagent definitions
│   ├── extensions/            # project-local pi extensions, incl. query_5etools
│   ├── scripts/               # launch/qmd environment wrappers
│   └── cli/                   # uv-managed helper CLIs
├── .qmd/                      # ignored qmd config/cache/index/model state
├── imports/                   # ignored raw inputs/reference mirrors
│   ├── books/                 # source PDFs/EPUBs
│   ├── source-vault/          # optional old vault; treated read-only
│   └── 5etools/               # local 5etools-src mirror clone
└── vault/                     # ignored Obsidian workspace
    ├── notes/                 # active authored campaign/table prep
    └── library/books/         # generated book-ingest output
```

`vault/notes/` is the main writing surface. `vault/library/books/` is generated reference material. `imports/` is input/reference data. `.qmd/` is rebuildable qmd state.

---

## Dependencies

### Hard runtime dependencies

| Dependency | Why it is needed | Install notes |
|---|---|---|
| Git | clone/fork this repo, clone 5etools, pi package git sources | <https://git-scm.com/install> |
| Node.js + npm | pi, qmd, pi packages, 5etools JS helpers | Node 22+ for qmd (pi itself needs 20.6+); current working machine uses Node 24. Official download or nvm: <https://nodejs.org/en/download> |
| pi | coding-agent harness | `npm install -g @mariozechner/pi-coding-agent` per pi README |
| qmd | local BM25/vector/hybrid Markdown search | `npm install -g @tobilu/qmd` per qmd npm/GitHub |
| uv | Python tool/project runner and interpreter manager | `curl -LsSf https://astral.sh/uv/install.sh \| sh` or `brew install uv`; docs: <https://docs.astral.sh/uv/getting-started/installation>. Prefer `uv run`/`uv tool` over system Python. |
| marker-pdf / `marker_single` | PDF → Markdown/JSON conversion | `uv tool install --python 3.12 --reinstall marker-pdf --with psutil`; project tools call `marker_single` |
| ripgrep / `rg` | fast repo/vault search used by agents | `sudo apt install ripgrep`, `brew install ripgrep`, or winget; docs: <https://ripgrep.dev> |
| fd | fast file discovery used by humans/agents | `sudo apt install fd-find` on Ubuntu (binary is often `fdfind`), `brew install fd`, or winget; upstream: <https://github.com/sharkdp/fd> |
| jq | inspect JSON ingest reports and qmd/tool output | `sudo apt install jq`, `brew install jq`, or winget |
| Bash + coreutils | shell wrappers in `.pi/scripts/` | Linux/macOS/WSL first-class; native Windows is not the primary target |

### Required accounts/keys for the intended stack

- **ChatGPT Plus/Pro with Codex access** — used by pi through subscription login. Start `pi`, run `/login`, and choose the ChatGPT Plus/Pro / Codex provider. Pi stores OAuth state under your pi config, not in this repo.
- **OpenAI API key** — put `OPENAI_API_KEY=...` in `.env` for image generation and optional Marker LLM modes. Normal fast PDF ingest can run with `TTRPG_MARKER_LLM_MODE=no` and no API key.

### Optional but useful

- **NVIDIA CUDA** for faster qmd/model and Marker workloads. The shell wrapper detects `/usr/local/cuda/bin/nvcc` and sets CUDA-related env vars. CPU fallback is supported.
- **Obsidian** for browsing/editing `vault/` as a graph.
- **Foundry VTT + dnd5e system + 5e Statblock Importer module** for consuming generated monster/importer text.
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

# 3. pi + qmd
npm install -g @mariozechner/pi-coding-agent @tobilu/qmd

# 4. uv + Marker
curl -LsSf https://astral.sh/uv/install.sh | sh
# uv will provision/use the requested Python for Python-based tools.
uv tool install --python 3.12 --reinstall marker-pdf --with psutil

# 5. Clone/fork this workspace
git clone <your-fork-url> ttrpg-agent
cd ttrpg-agent
cp .env.example .env
```

Authenticate pi with your subscription, then run the one-time guided bootstrap prompt:

```bash
./.pi/scripts/pi-launch.sh
# inside pi, if not authenticated yet: /login → choose ChatGPT Plus/Pro (Codex)
# inside pi: /bootstrap
```

`/bootstrap` checks hard dependencies, proposes install fixes, configures `.env`, asks whether to enable OpenAI/Marker/image/web features, optionally imports an old Obsidian vault, optionally clones 5etools, optionally ingests books, and runs smoke tests. Use it for new project initialization only; open a fresh pi session afterward.

First launch may install project pi packages declared in `.pi/settings.json`. If you want a fully isolated run that ignores global pi config, use:

```bash
./.pi/scripts/pi-isolated.sh
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

Project helper CLIs should be run with `uv run --project .pi/cli/<tool> ...`; standalone Python tools such as Marker should be installed with `uv tool install ...`.

### Bootstrap smoke checks

```bash
node --version
npm --version
pi --version
qmd --version
uv --version
rg --version
fd --version
marker_single --help

# From repo root; shell wrapper creates folders and registers qmd collections.
source ./.pi/scripts/pi-shell.sh
qmd collection list
qmd status
```

Expected qmd shape:

- `notes` → `vault/notes/`
- `books` → `vault/library/books/`
- `archive` → `imports/source-vault/` and marked excluded by default

---

## Common workflows

### Add reference data

```bash
mkdir -p imports/books imports/source-vault imports/5etools
cp ~/Downloads/My-Adventure.pdf imports/books/
# optional old vault; this is read-only by project policy
rsync -a ~/Documents/OldVault/ imports/source-vault/
```

### Ingest a PDF book

Inside pi:

```text
/ingest-book imports/books/My-Adventure.pdf
```

Or manually:

```bash
uv run --project .pi/cli/book-ingest book-ingest imports/books/My-Adventure.pdf
qmd update
qmd embed
```

### Search your prep/library

```text
/find-anything haunted charcoal burners in my notes and books
/find-monster goblin boss
```

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
```

Image generation is metered and should only happen on explicit request.

---

## Configuration

### `.pi/settings.json`

Current project defaults:

- `defaultProvider`: `openai-codex`
- `defaultModel`: `gpt-5.5`
- `defaultThinkingLevel`: `medium`
- Project pi packages:
  - `pi-web-access` for web search/fetch tools.
  - `pi-subagents` for delegation workflows.
  - `pi-prompt-template-model` for richer prompt-template support.
  - `pi-mcp-adapter` for MCP gateway support.
- Built-in subagents are disabled except the configured delegate path; project subagents live in `.pi/agents/`.
- `shellCommandPrefix` sources `.pi/scripts/pi-shell.sh`, so pi bash commands automatically use project-local qmd state and project `.env` values.

Do not put secrets in `.pi/settings.json`.

### `.env`

Copy `.env.example` to `.env`. `.env` is ignored. Important keys:

| Key | Used by | Notes |
|---|---|---|
| `OPENAI_API_KEY` | `image-gen`, `book-ingest --llm ...` | Required for image generation and any non-`no` Marker LLM mode |
| `TTRPG_IMAGE_MODEL` | image generation | Default `gpt-image-1` |
| `TTRPG_IMAGE_SIZE` | image generation | `1024x1024`, `1536x1024`, `1024x1536`, etc. |
| `TTRPG_IMAGE_QUALITY` | image generation | `auto`, `low`, `high` depending on model/account |
| `TTRPG_IMAGE_OUTPUT_FORMAT` | image generation | `png`, `jpeg`, or `webp`; default `png` |
| `TTRPG_IMAGE_OUTPUT_DIR` | image generation | Must stay under `vault/notes/images` |
| `TTRPG_MARKER_LLM_MODE` | book ingest | `no`, `images-only`, `text-only`, `all` |
| `TTRPG_MARKER_OPENAI_MODEL` | book ingest | Default `gpt-4o-mini` for Marker LLM calls |
| `TTRPG_MARKER_OPENAI_BASE_URL` | book ingest | OpenAI-compatible base URL; default `https://api.openai.com/v1` |
| `TTRPG_MARKER_LLM_MAX_CONCURRENCY` | book ingest | Parallel Marker LLM calls; default `2`, lower to `1` for rate limits |
| `TTRPG_MARKER_DEVICE` | book ingest | `auto`, `cuda`, `cpu`, `mps` |
| `TTRPG_MARKER_*_BATCH_SIZE` | book ingest | Optional Marker local OCR/layout batch tuning |
| `EXA_API_KEY` | web research | Optional direct Exa key for pi-web-access; Exa MCP may work without it |
| `PERPLEXITY_API_KEY` | web research | Optional Perplexity fallback for pi-web-access |
| `GEMINI_API_KEY` | web/video research | Optional Gemini API fallback for pi-web-access |

### `.pi/scripts/`

- `pi-shell.sh` — shared shell setup. Exports `TTRPG_*` paths, sources project `.env` for optional feature/API keys, sets `QMD_CONFIG_DIR`, `XDG_CACHE_HOME`, CUDA/qmd fallback vars, creates skeleton directories, and wraps `qmd` so it is project-local.
- `pi-launch.sh` — normal launcher; keeps your global pi config but localizes qmd.
- `pi-isolated.sh` — full pi isolation via `PI_CODING_AGENT_DIR=.pi-home`.
- `qmd-init.sh` — thin `qmd update` helper with the project shell setup.

---

## Data and git policy

Tracked by git:

- `README.md`, `AGENTS.md`, `.env.example`, `.gitignore`, empty local-data `.gitkeep` placeholders
- `.pi/settings.json`
- `.pi/skills/`, `.pi/prompts/`, `.pi/agents/`, `.pi/extensions/`
- `.pi/scripts/`
- `.pi/cli/` source, tests, lockfiles, and READMEs

Ignored/local:

- `.env`, `.env.*` except `.env.example`
- `vault/` contents — Obsidian vault, active campaign notes, generated images, ingested book output. Empty skeleton directories are kept with `.gitkeep`.
- `imports/` contents — source PDFs, legacy vault, 5etools clone. Empty import directories are kept with `.gitkeep` except `imports/5etools/`, which must remain clone-friendly.
- `.qmd/` — qmd config/cache/index/model state
- `.cache/`, `.trash/`, test caches, virtualenvs, node_modules
- `.pi/npm/`, `.pi/git/`, `.pi-home/` — project-local pi runtime/package caches

Policy boundaries:

- `imports/source-vault/` is read-only inspiration/archive material.
- `imports/5etools/` is read-only canonical rules data.
- `vault/library/books/` is generated by `book-ingest`; do not hand-edit ingested chapters.
- `vault/notes/` is writable active campaign/table prep.
- Durable notes should have frontmatter, body wikilinks, and a `## Connections` section.
- Destructive cleanup requires dry-run inventory and exact confirmation.

### Fresh clone local inventory

A fresh clone intentionally ships no campaign/reference data. Tracked `.gitkeep` files preserve the empty local-data skeleton where that will not interfere with later imports:

- `imports/books/` — put owned/allowed PDFs or EPUBs here.
- `imports/source-vault/` — optional read-only copy of an older Obsidian vault.
- `imports/5etools/` — optional local 5etools mirror clone (created by bootstrap/shell when enabled; not tracked because `git clone` needs an empty target).
- `vault/notes/` — active authored campaign/table prep.
- `vault/library/books/` — generated Markdown output from book ingestion.

`/bootstrap` can populate any optional inputs you choose, and the shell wrapper/qmd setup tolerates empty optional folders.

---

## Implemented skills

Skills are procedural reference files the agent loads when a task matches. Current project skills cover:

- **Rules/canonical data**
  - `ttrpg-rules-5etools-query` — structured creature/spell/item lookups through `query_5etools`.
  - `ttrpg-rules-5etools-native` — direct JS/native 5etools spelunking for classes, feats, backgrounds, schema details, and unsupported records.
  - `ttrpg-rules-osr-to-5e` — OSR/OSE/BX/AD&D monster/trap/mechanic conversion judgment.
- **Search/research**
  - `ttrpg-library-search` — qmd search over `books`, `notes`, and optional `archive`.
  - `ttrpg-research-web` — outside-web inspiration/research with citations.
- **Vault authoring**
  - `ttrpg-vault-authoring` — placement, file boundaries, stubs, graph/link policy.
  - `ttrpg-vault-rich-notes` — table-ready Obsidian Markdown patterns.
  - `ttrpg-vault-canvas` — Obsidian Canvas JSON creation/validation.
- **Imports**
  - `ttrpg-import-book-pdf` — normal PDF book ingest workflow.
  - `ttrpg-import-raw-pdf` — one-off raw Marker conversion/debugging.
  - `ttrpg-import-archive-vault` — safe promotion of selected old-vault notes.
- **Foundry**
  - `ttrpg-foundry-statblock-importer` — plain WotC-style statblock importer formatting.
  - `ttrpg-foundry-enrichers` — Foundry dnd5e text enrichers for journals/items/actor descriptions.
  - `ttrpg-foundry-dnd5e-wiki` — targeted research against Foundry dnd5e implementation docs.
- **Creative prep**
  - `ttrpg-create-readaloud` — boxed text/read-aloud style.
  - `ttrpg-create-image-gen` — explicit image-generation workflow and asset-note contract.
- **Maintenance**
  - `ttrpg-system-qmd-maintenance` — refresh/rebuild qmd safely.
  - `ttrpg-system-data-cleanup` — destructive data cleanup workflow with safeguards.

---

## Slash prompts

Prompt templates live in `.pi/prompts/` and are invoked inside pi with `/name`:

| Prompt | Purpose |
|---|---|
| `/bootstrap` | One-time first-run setup wizard: dependencies, `.env`, optional imports, smoke tests. |
| `/find-monster` | Canonical monster lookup; 5etools first, qmd fallback. |
| `/find-anything` | General qmd search across books/notes/archive. |
| `/ingest-book` | PDF ingest workflow; delegates to `ingest-worker`. |
| `/convert-monster` | OSR/non-5e monster → 5e + Foundry importer text. |
| `/foundry-monster` | Normalize an existing statblock for Foundry importer paste. |
| `/npc` | Fast NPC sketch, with optional save to vault. |
| `/readaloud` | Boxed text plus DM notes, saved when reusable. |
| `/illustrate` | Generate one OpenAI image asset only on explicit request. |
| `/cleanup` | Safe destructive cleanup flow with scope/dry-run/confirmation. |

---

## Subagents

Subagents live in `.pi/agents/` and are used when a task would otherwise flood the main session with logs/context.

- `ingest-worker` — long-running PDF ingestion through `.pi/cli/book-ingest`; writes only under `vault/library/books/` and `.cache/book-ingest/`.
- `researcher` — read-only broad search across books, notes, archive, 5etools snippets, and web.
- `statblock-converter` — one-monster conversion agent that saves monster notes under `vault/notes/mechanics/monsters/` and emits Foundry importer text.

---

## Custom tools and extensions

### `query_5etools` pi extension

Located in `.pi/extensions/query-5etools/`.

It exposes a project-local pi tool for common 5etools lookups:

- Entities: `creature`, `spell`, `item`
- Creature filters: name, source, CR/range, type, size, alignment, environment
- Spell filters: name, source, level/range, school, class, concentration, ritual
- Item filters: name, source, rarity, kind, attunement
- Output: `summary`, `json`, `markdown`
- Ruleset preference: `2014`, `2024`, or `either`

For unsupported 5etools entity types or renderer weirdness, agents use `ttrpg-rules-5etools-native` and small read-only Node snippets against `imports/5etools/`.

### `.pi/cli/book-ingest`

A uv-managed Python CLI that converts PDFs into sectioned Markdown:

```bash
uv run --project .pi/cli/book-ingest book-ingest imports/books/My-Book.pdf
uv run --project .pi/cli/book-ingest book-ingest validate vault/library/books/my-book
```

Highlights:

- Runs `marker_single` twice: paginated Markdown and JSON block tree.
- Plans sections from PDF outline or Marker `SectionHeader` blocks.
- Writes `_book.md`, section notes, copied images, `.ingest.json`, and quality reports.
- Skips unchanged re-ingests by source hash and schema version.
- Supports `--llm no|images-only|text-only|all`.
- Python deps: `click`, `pypdf`, `pyyaml`; dev deps include `pytest`, `ruff`, `mypy`.

### `.pi/cli/image-gen`

A uv-managed OpenAI Images CLI:

```bash
uv run --project .pi/cli/image-gen image-gen \
  --subject "Original fantasy portrait of a tired dwarf cartographer, no text, no watermark."
```

It writes `vault/notes/images/<slug>-<hash>.png` and an adjacent `.md` asset note containing frontmatter, prompt, params, sanitized response metadata, adoption notes, and connections.

### `.pi/cli/vault-sync`

A deliberately dumb, safe copy/inspect tool for legacy archive notes:

```bash
uv run --project .pi/cli/vault-sync vault-sync inspect imports/source-vault/path/Note.md
uv run --project .pi/cli/vault-sync vault-sync copy imports/source-vault/path/Note.md vault/notes/npcs/note.md
```

It never decides meaning or destination. The LLM chooses placement, then edits the copied note according to vault-authoring policy.

---

## Foundry VTT workflow

The project treats Foundry output as two separate things:

1. **Importer statblock** — plain WotC-style prose for the 5e Statblock Importer. No Foundry enrichers inside the import block.
2. **Post-import prose** — Foundry dnd5e enrichers for actor/item/journal descriptions, clickable saves/checks/damage, references, and notes.

This separation avoids broken imports while still giving you richer Foundry text after the actor/item exists.

---

## Maintenance

Normal refresh after note edits or ingest:

```bash
source ./.pi/scripts/pi-shell.sh
qmd update
qmd embed      # after significant new content; can be slow
qmd status
```

If search looks stale, ask pi:

```text
Use ttrpg-system-qmd-maintenance to verify qmd collections and refresh the index.
```

For destructive cleanup, use `/cleanup` or explicitly ask for `ttrpg-system-data-cleanup`. The expected flow is scope → dry-run inventory → exact confirmation → delete → recreate skeleton → qmd refresh if needed.

---

## Hackability: make it yours

The intended customization loop is simple:

1. Fork the repo.
2. Put your PDFs, vault, and 5etools clone in ignored local folders.
3. Launch pi in the repo.
4. Ask pi to improve the repo machinery when you notice repeated work.

Examples:

```text
Make a new /session-prep prompt that creates a three-scene prep note under vault/notes/sessions/.

Add a skill for my homebrew hexcrawl procedures and make future travel prep use it.

Extend query_5etools to support feats and backgrounds.

Write a small .pi/cli tool that exports selected notes as Foundry journal HTML.
```

Where to change things:

- New agent behavior/routing: `AGENTS.md`
- New repeatable procedure: `.pi/skills/<name>/SKILL.md`
- New slash workflow: `.pi/prompts/<name>.md`
- New custom CLI tool: `.pi/cli/<tool-name>/`
- New pi tool/command/UI: `.pi/extensions/<extension-name>/`
- qmd/shell environment: `.pi/scripts/`

After changing pi resources, run `/reload` inside pi.

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
