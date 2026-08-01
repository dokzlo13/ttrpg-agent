# query-5etools

Project tool exposing one capability on two surfaces:

- `query_5etools` — the pi tool, registered by `.pi/extensions/query-5etools/`
- `.agents/bin/query-5etools` — the hermetic CLI, usable from any harness or a
  bare shell

Both are built from the same `spec.mjs`, so their parameters cannot drift.

## Purpose

Provide the **common-case structured query path** into the pinned 5etools clone
at `$TTRPG_5ETOOLS_DIR` (`.cache/vendor/5etools`) without forcing the agent to
keep re-deriving 5etools' JS bootstrap.

This tool is intentionally small. It handles:

- `entityType: creature | spell | item`
- common structured filters
- output modes: `summary | json | markdown`
- simple ruleset preference: `2014 | 2024 | either`

## Non-goals

- Full 5etools API coverage
- Every entity type in the repo
- Reimplementing 5etools formatting in custom code when a native renderer exists

For anything stranger, the agent should read `ttrpg-rules-5etools-native` and use a
small Node snippet against the real 5etools JS source.

## Output guidance

- `summary` → candidate lists
- `json` → raw-ish records for reasoning or downstream transforms
- `markdown` → native rendered output when available

## Implementation notes

- Loads 5etools JS modules once per session.
- Uses `node/util.js` `patchLoadJson()` so Node can read the repo's JSON files.
- Creatures use `RendererMarkdown.exporting.pGetMarkdownDoc(...prop:'monster')`.
- Spells use `RendererMarkdown.spell.getCompactRenderedString(...)`.
- Items use `RendererMarkdown.item.getCompactRenderedString(...)` with summary fallback.
