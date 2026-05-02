---
name: bootstrap
description: "First-run interactive project bootstrap: dependency checks, .env config, optional data imports, smoke tests."
thinking: high
---

# /bootstrap

You are running the **first-time bootstrap** for a fresh `ttrpg-agent` clone. Treat this as a one-time initialization wizard. Be proactive and concise, but do not make paid/API/destructive/network choices without explicit user consent.

Hard rules:

- Start with the welcome message in step 1.
- Never print secrets. Do not paste `.env` contents back to the user if keys may be present.
- `.env` is allowed to be created/edited. `imports/`, `vault/`, and `.qmd/` are ignored local data areas.
- Do not edit `imports/source-vault/` source files after they are copied/imported; treat it as read-only archive material.
- Do not ingest books, generate images, run paid LLM Marker modes, clone 5etools, or copy a private vault without user approval.
- Prefer one compact questionnaire over many single-question turns.
- Optional features must fail gracefully: if the user skips archive vault, 5etools, books, web keys, OpenAI, or CUDA, configure the usable subset and clearly say what will be unavailable.

## 1. Welcome

Tell the user, briefly:

> Welcome — you just installed the ultimate D&D 5e + Foundry VTT agent toolchain. This stack can search your campaign notes and ingested books, query a local 5etools mirror for canonical creatures/spells/items, ingest RPG PDFs into an Obsidian-friendly library, write reusable vault notes/read-alouds/NPCs, convert OSR material to 5e, format Foundry statblock importer text/enrichers, and optionally generate image assets.

Then say this bootstrap will check dependencies, configure `.env`, optionally connect local source material, and run smoke tests.

## 2. Inspect current repo and dependencies

Run a read-only check. Use commands like:

```bash
pwd
git rev-parse --show-toplevel 2>/dev/null || true
printf 'node='; node --version 2>/dev/null || true
printf 'npm='; npm --version 2>/dev/null || true
printf 'pi='; pi --version 2>/dev/null || true
printf 'qmd='; qmd --version 2>/dev/null || true
printf 'python3='; python3 --version 2>/dev/null || true
printf 'uv='; uv --version 2>/dev/null || true
printf 'marker_single='; marker_single --help >/dev/null 2>&1 && echo present || echo missing
printf 'git='; git --version 2>/dev/null || true
printf 'rg='; rg --version 2>/dev/null | head -1 || true
printf 'fd='; (fd --version 2>/dev/null || fdfind --version 2>/dev/null) | head -1 || true
printf 'jq='; jq --version 2>/dev/null || true
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null || true
/usr/local/cuda/bin/nvcc --version 2>/dev/null | tail -1 || nvcc --version 2>/dev/null | tail -1 || true
python3 - <<'PY' 2>/dev/null || true
try:
    import torch
    print(f"torch={torch.__version__} cuda_available={torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"torch_cuda_device={torch.cuda.get_device_name(0)} vram_bytes={torch.cuda.get_device_properties(0).total_memory}")
except Exception as e:
    print(f"torch_check_unavailable={type(e).__name__}: {e}")
PY
```

Also check whether `.env` exists and whether key variables are set without printing values:

```bash
python3 - <<'PY'
from pathlib import Path
keys = [
  'OPENAI_API_KEY', 'EXA_API_KEY', 'PERPLEXITY_API_KEY', 'GEMINI_API_KEY',
  'GOOGLE_SEARCH_API_KEY', 'GOOGLE_SEARCH_ENGINE_ID',
  'TTRPG_MARKER_LLM_MODE', 'TTRPG_MARKER_DEVICE',
  'TTRPG_MARKER_LAYOUT_BATCH_SIZE', 'TTRPG_MARKER_DETECTION_BATCH_SIZE',
  'TTRPG_MARKER_RECOGNITION_BATCH_SIZE', 'TTRPG_MARKER_TABLE_REC_BATCH_SIZE',
]
p = Path('.env')
print(f"env_file_exists={p.exists()}")
vals = {}
if p.exists():
    for line in p.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        vals[k.strip()] = v.strip()
for k in keys:
    print(f"{k}={'set' if vals.get(k) else 'empty-or-missing'}")
PY
```

Summarize missing hard dependencies with concrete install suggestions, e.g.:

- Debian/Ubuntu/WSL: `sudo apt install -y git curl build-essential python3 python3-venv jq ripgrep fd-find`
- fd symlink if needed: `command -v fd >/dev/null || sudo ln -s "$(command -v fdfind)" /usr/local/bin/fd`
- Node: install Node 22+ (Node 24 OK) via nvm/official installer.
- pi + qmd: `npm install -g @mariozechner/pi-coding-agent @tobilu/qmd`
- uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Marker: `uv tool install --python 3.12 --reinstall marker-pdf --with psutil`

If hard dependencies are missing, ask whether the user wants you to run safe install commands for this OS or stop while they install manually. Do not continue to expensive/long steps until the basics are present.

## 3. Explain usage concepts

Briefly explain:

- `vault/` is the local Obsidian workspace. Open this folder in Obsidian; active authored prep goes under `vault/notes/`.
- `imports/books/` stores source PDFs/EPUBs. `/ingest-book` or this bootstrap turns them into generated Markdown under `vault/library/books/`.
- `imports/source-vault/` is an optional read-only copy of an existing/old Obsidian vault. The agent searches it only when asked and copies selected notes into `vault/notes/` on demand.
- `imports/5etools/` is an optional local 5etools mirror for canonical creature/spell/item lookups.
- `qmd` indexes notes/books/archive locally. Empty optional folders are OK.

## 4. Ask configuration questionnaire

Ask the user one compact questionnaire. Include these choices:

1. **OpenAI API-backed features:** enable image generation? enable Marker LLM cleanup/captions? If yes, ask whether `OPENAI_API_KEY` is already in the environment or should be pasted/stored in `.env`. Explain metered cost.
2. **Marker LLM mode:** choose `no`, `images-only`, `text-only`, or `all`.
   - Recommend `no` for fastest/free local ingest.
   - Recommend `images-only` if they have an OpenAI key and want searchable figure/map captions at moderate cost.
   - Warn that `text-only`/`all` can make hundreds/thousands of calls on large books.
3. **Marker device/performance:** propose the best reliable config from detected hardware.
   - If CUDA is available: recommend `TTRPG_MARKER_DEVICE=cuda` and batch preset `layout=8`, `detection=8`, `recognition=128`, `table_rec=8` as a stable fast default. If VRAM is under 8 GB, recommend `4/4/64/4` or blank batch sizes. If VRAM is 16+ GB, say the stable default is still `8/8/128/8`, and optionally offer a later benchmark before raising it.
   - If Apple Silicon/MPS appears available: recommend `TTRPG_MARKER_DEVICE=mps` and blank batch sizes.
   - Otherwise recommend `TTRPG_MARKER_DEVICE=auto` or `cpu` and blank batch sizes.
4. **pi-web-access:** explain that `.pi/scripts/pi-shell.sh` sources `.env` before pi starts, so project `.env` can provide web keys to pi-web-access. Ask whether to configure web research keys in `.env`. If system-level env vars already exist, offer to mirror them into `.env` without displaying values. Ask for any of `EXA_API_KEY`, `PERPLEXITY_API_KEY`, `GEMINI_API_KEY`, and/or legacy `GOOGLE_SEARCH_API_KEY` + `GOOGLE_SEARCH_ENGINE_ID`; leaving them blank is OK if Exa MCP, Gemini Web/browser login, or global `~/.pi/web-search.json` handles web access.
5. **Default image settings:** if image generation is enabled, accept defaults unless user wants changes: `gpt-image-1`, `1024x1024`, `auto`, `png`, output under `vault/notes/images`.

After the user answers, create `.env` from `.env.example` if needed and update only the selected keys. Use a small script or precise edits. Preserve unrelated lines and comments. Never display secret values.

Safe script pattern for `.env` updates (adapt keys/values from the user's answers; use values from `os.environ` only with consent):

```bash
python3 - <<'PY'
from pathlib import Path
updates = {
  # 'TTRPG_MARKER_LLM_MODE': 'no',
}
p = Path('.env')
if not p.exists():
    p.write_text(Path('.env.example').read_text() if Path('.env.example').exists() else '')
lines = p.read_text().splitlines()
seen = set()
out = []
for line in lines:
    if line.strip() and not line.lstrip().startswith('#') and '=' in line:
        k = line.split('=', 1)[0].strip()
        if k in updates:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
            continue
    out.append(line)
for k, v in updates.items():
    if k not in seen:
        out.append(f"{k}={v}")
p.write_text('\n'.join(out).rstrip() + '\n')
PY
chmod 600 .env 2>/dev/null || true
```

## 5. Ask/import optional source material

Ask and act on these, with disclaimers:

### Existing Obsidian vault / archive notes

Ask whether the user wants to include an existing Obsidian vault as a **read-only archive**. If yes, ask for its path and whether to copy/sync it into `imports/source-vault/` now. Prefer copying Markdown and attachments; exclude `.obsidian`, `.git`, caches, and trash unless the user explicitly wants them.

Example after approval:

```bash
mkdir -p imports/source-vault
rsync -a --info=progress2 --exclude='.obsidian/' --exclude='.git/' --exclude='.trash/' --exclude='.cache/' "/path/to/old-vault/" imports/source-vault/
```

Then remind: the agent will not edit archive files; it will copy/promote selected notes into `vault/notes/` later.

### 5etools

Ask whether to enable local 5etools content. Give this disclaimer: 5etools mirrors can contain unofficially distributed/copyrighted material depending on source and jurisdiction; use only content you are allowed to use. If approved and `imports/5etools/data` is absent, clone:

```bash
mkdir -p imports
git clone --depth 1 https://github.com/5etools-mirror-3/5etools-src.git imports/5etools
```

If `imports/5etools/` exists but is not empty and not a valid clone, do not overwrite; ask what to do.

### Books

Ask whether the user wants to provide books now or later. Give this disclaimer: prefer books/supplements you own or are allowed to use; do not ingest pirated material. If now, ask for paths to PDFs/EPUBs or tell the user to copy them into `imports/books/`.

If books are present and the user approves ingestion, warn that large batches can take a while. Spawn the `ingest-worker` subagent for each selected PDF (sequentially unless the user explicitly asks for parallel). Brief:

```text
Ingest imports/books/<filename>.pdf into vault/library/books/.
Run: uv run --project .pi/cli/book-ingest book-ingest --json imports/books/<filename>.pdf
Use the .env Marker defaults. If CUDA was configured and the user approved tuned CUDA, add the matching --device/batch-size flags only if needed.
Report slug, page_count, section_count, plan_source, quality_status, warnings, total time.
```

After ingestion, run `qmd update` and `qmd embed`. If ingestion fails due to missing Marker/OpenAI/CUDA, report the graceful fallback or config fix.

## 6. Create local skeleton and run smoke tests

Ensure skeleton directories exist:

```bash
mkdir -p imports/books imports/source-vault imports/5etools vault/notes vault/library/books vault/notes/images .qmd
```

Run smoke tests appropriate to enabled features:

```bash
source ./.pi/scripts/pi-shell.sh
qmd collection list
qmd status
uv run --project .pi/cli/book-ingest book-ingest --help >/dev/null
uv run --project .pi/cli/image-gen image-gen --help >/dev/null 2>&1 || true
```

If 5etools is present, test a tiny query with `query_5etools` (e.g. creature name `goblin`, output `summary`, limit `1`) or a read-only file check under `imports/5etools/data` if the tool is not available in this context.

If books or archive notes were added, run a small qmd search/status check. If no books/archive exist, say those checks were skipped.

If web access was configured or the user says global pi-web-access config should work, ask before running a tiny `web_search` smoke test (e.g. `workflow: "none"`, `numResults: 1`). If skipped or unavailable, report that web research can be tested later.

If OpenAI/image generation is configured, do **not** generate an image automatically; only verify config/help and say `/illustrate ...` is ready.

## 7. Finish

End with:

- A concise checklist of enabled features and skipped optional features.
- Any remaining install/config commands the user needs.
- A reminder to open `vault/` in Obsidian.
- Tell the user to open a new pi session because bootstrap may have used lots of context.
- Give 3–5 example next commands tailored to what is enabled, e.g.:
  - `/find-monster goblin boss`
  - `/find-anything <term from an ingested book>`
  - `/ingest-book imports/books/My-Book.pdf`
  - `/readaloud the party enters a candlelit sinkhole shrine`
  - `/illustrate original token portrait for a mossy undead knight`

User input: $@
