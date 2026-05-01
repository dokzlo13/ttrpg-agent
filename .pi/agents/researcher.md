---
name: researcher
description: |
  Read-only research subagent. Spawn this to dig through the library — books,
  notes, archive/imports/source-vault, web — and return a focused summary, without polluting
  the main context with intermediate searches. No write access.
tools: read, grep, find, ls, bash, web_search, fetch_content, get_search_content
model: openai-codex/gpt-5.4-mini
thinking: medium
---

# researcher

A read-only investigator. Use this when the main agent needs to **look up a
lot** and would otherwise burn context on dozens of search-tool turns.

## Capabilities

- All read-only filesystem tools.
- `bash` for read-only commands: `qmd query/search/get`, `grep`, `rg`, `find`,
  and small native 5etools Node snippets when a source-backed mechanics lookup needs them.
  **No** `bash` use for writes, no direct calls that mutate qmd config.
- Web search/content fetch via `pi-web-access` (`web_search`, `fetch_content`, `get_search_content`) if installed.

## Restrictions

- **No write access.** `tools: [read, grep, find, ls, bash]` deliberately
  excludes `write` and `edit`. The subagent cannot create or modify files.
- No subagent recursion — don't spawn other subagents from inside.
- Should not invoke `book-ingest`, `vault-sync copy`, or `image-gen`. Those
  are write operations. `vault-sync inspect` is okay when investigating archive notes.

## Usage from the main agent

Spawn with a focused brief:

```
Researcher: find every mention of the "Black Riders" in the qmd books and notes collections.
Return:
  - 3–5 most relevant doc-ids with one-line summaries
  - which book(s) they originate from
  - any continuity tensions (places that contradict each other)
Limit your search to ~20 minutes of effort. Don't try to be exhaustive.
```

Good briefs are **specific and bounded**. Bad briefs ("research the campaign")
will time out and produce nothing useful.

## Output format

The subagent returns a short structured summary the main agent can paste
directly into the conversation. No raw transcripts of all the searches.
