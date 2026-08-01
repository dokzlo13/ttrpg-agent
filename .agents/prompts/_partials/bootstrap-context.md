# Bootstrap domain context

Fragment loaded by the `/bootstrap` prompt. Not a command in its own right —
`_partials/` is exempt from prompt generation.

This holds the parts of first-run setup that are about *this project's domain*:
what each `.env` key means, which optional data sources exist, and the
copyright/5etools posture. The mechanical work — probing prerequisites,
installing per-tool environments, materializing the cache layout, reporting
lazily-fetched artifacts — lives in `.agents/harness bootstrap` and is shared
by every harness.

## 3. Explain usage concepts

Briefly explain:

- `vault/` is the local Obsidian workspace. Open this folder in Obsidian; active authored prep goes under `vault/notes/`.
- `imports/books/` stores source PDFs/EPUBs. The `ttrpg-import-book-pdf` skill (or this bootstrap) turns them into generated Markdown under `vault/library/books/`.
- `imports/source-vault/` is an optional read-only copy of an existing/old Obsidian vault. The agent searches it only when asked and copies selected notes into `vault/notes/` on demand.
- `.cache/vendor/5etools/` is an optional local 5etools mirror for canonical creature/spell/item lookups.
- `qmd` indexes notes/books/archive locally. Empty optional folders are OK.

## 4. Configure `.env` one subsection at a time

Do **not** ask all configuration questions at once. Iterate through the subsections below in order. For each subsection:

1. Ask only that subsection's short question(s).
2. Wait for the user's answer.
3. Update `.env` immediately if the user approved a change.
4. Summarize what changed or what was skipped.
5. Then proceed to the next subsection.

Subsections:

### 4A. Marker local performance

Propose the best reliable `TTRPG_MARKER_DEVICE` and batch config from detected hardware, then ask whether to apply it.

- If CUDA is available: recommend `TTRPG_MARKER_DEVICE=cuda` and batch preset `layout=8`, `detection=8`, `recognition=128`, `table_rec=8` as a stable fast default. If VRAM is under 8 GB, recommend `4/4/64/4` or blank batch sizes. If VRAM is 16+ GB, say the stable default is still `8/8/128/8`, and optionally offer a later benchmark before raising it.
- If Apple Silicon/MPS appears available: recommend `TTRPG_MARKER_DEVICE=mps` and blank batch sizes.
- Otherwise recommend `TTRPG_MARKER_DEVICE=auto` or `cpu` and blank batch sizes.

### 4B. OpenAI API-backed features

Ask whether to enable image generation and/or Marker LLM cleanup/captions. Explain that these are metered. If yes, ask whether `OPENAI_API_KEY` is already in the environment or should be pasted/stored in `.env`. Never print the key.

### 4C. Marker LLM mode

Ask for Marker LLM mode only after 4B is settled. Choose `no`, `images-only`, `text-only`, or `all`.

- Recommend `no` for fastest/free local ingest.
- Recommend `images-only` if they have an OpenAI key and want searchable figure/map captions at moderate cost.
- Warn that `text-only`/`all` can make hundreds/thousands of calls on large books.

### 4D. Web research keys

Explain that `.agents/env.sh` sources `.env` before pi starts, so project `.env` can provide web keys to pi-web-access. Ask whether to configure web research keys in `.env`. If system-level env vars already exist, offer to mirror them into `.env` without displaying values. Ask only for keys the user wants to configure now: `EXA_API_KEY`, `PERPLEXITY_API_KEY`, and/or `GEMINI_API_KEY`. Leaving them blank is OK if Exa MCP, Gemini Web/browser login, or global `~/.pi/web-search.json` handles web access.

### 4E. Default image settings

Only if image generation is enabled, ask whether to keep defaults: `gpt-image-1`, `1024x1024`, `auto`, `png`, output under `vault/notes/images`. Apply defaults unless the user wants changes.

When applying any subsection, create `.env` from `.env.example` if needed and update only the selected keys. Use a small script or precise edits. Preserve unrelated lines and comments. Never display secret values.

Safe script pattern for `.env` updates (adapt keys/values from the user's answers; use values from `os.environ` only with consent):

```bash
uv run python - <<'PY'
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

## 5. Ask/import optional source material one source type at a time

Do **not** ask about archive, 5etools, and books all at once. Iterate through the subsections below in order. For each source type: ask the section-specific question, wait for the answer, perform approved action(s), summarize, then continue.

### Existing Obsidian vault / archive notes

Ask whether the user wants to include an existing Obsidian vault as a **read-only archive**. If yes, ask for its path and whether to copy/sync it into `imports/source-vault/` now. Prefer copying Markdown and attachments; exclude `.obsidian`, `.git`, caches, and trash unless the user explicitly wants them.

Example after approval:

```bash
mkdir -p imports/source-vault
rsync -a --info=progress2 --exclude='.obsidian/' --exclude='.git/' --exclude='.trash/' --exclude='.cache/' "/path/to/old-vault/" imports/source-vault/
```

Then remind: the agent will not edit archive files; it will copy/promote selected notes into `vault/notes/` later.

### 5etools

Ask whether to enable local 5etools content. Give this disclaimer: 5etools mirrors can contain unofficially distributed/copyrighted material depending on source and jurisdiction; use only content you are allowed to use. If approved and `.cache/vendor/5etools/data` is absent, clone:

```bash
.agents/harness vendor sync 5etools
```

If `.cache/vendor/5etools/` exists but is not empty and not a valid clone, do not overwrite; ask what to do.

### Books

Ask whether the user wants to provide books now or later. Give this disclaimer: prefer books/supplements you own or are allowed to use; do not ingest pirated material. If now, ask for paths to PDFs/EPUBs or tell the user to copy them into `imports/books/`.

If books are present and the user approves ingestion, warn that large batches can take a while. Load the `ttrpg-import-book-pdf` skill and ingest each selected PDF sequentially (unless the user explicitly asks for parallel):

```bash
.agents/bin/book-ingest --json imports/books/<filename>.pdf
```

Use the `.env` Marker defaults. If CUDA was configured and the user approved tuned CUDA, add the matching `--device`/batch-size flags only if needed. Then run every entry in the returned `next_steps` in order — that covers classify-system, summarize, tag (when `OPENAI_API_KEY` is configured) and the final `.agents/bin/qmd update && .agents/bin/qmd embed`. Report slug, page count, section count, plan source, status, warnings, and total time. If ingestion fails due to missing Marker/OpenAI/CUDA, report the graceful fallback or config fix.

