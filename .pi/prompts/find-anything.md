---
name: find-anything
description: General-purpose search across books, notes, and optional archive using qmd hybrid mode.
thinking: low
model: openai-codex/gpt-5.4-mini
skill: ttrpg-library-search
---

# /find-anything

Open-ended search across the full local library. Use when the user's question
isn't structured (otherwise prefer `/find-monster` or a direct `query_5etools`).

1. **Decide the collection scope.**
   - For book/reference questions, start with `-c books`.
   - For campaign-note questions, use `-c notes`.
   - If both are needed, repeat flags: `-c books -c notes` (never `books,notes`).
   - If the user says "in my old notes", add `-c archive`.

2. **Pick the cheapest mode that works.**
   - Proper noun ("Vecna", "Dunemark") → `qmd search` (BM25).
   - Fuzzy / conceptual ("a scene where someone is interrogated") → `qmd query`
     (hybrid expansion/vector/rerank).
   - Last-resort conceptual → `qmd vsearch`.

3. **Run it.** Take the top 3–5 hits, then `qmd get` each to see surroundings
   before quoting. **Never** quote a chunk without reading the full context —
   you'll misattribute or miss a critical preceding sentence.

4. **Report back** with:
   - For each hit: title, source (book name + chapter, or notes path),
     2–3 sentence summary in your own words.
   - Doc IDs so the user can `qmd get` themselves.
   - Brief offer of follow-ups: "Want me to pull the full passage?", "Want me
     to summarize the chapter?", "Want a related search?"

5. **If there are no hits worth showing**, say so plainly. Suggest:
   - Different keywords (the user knows their own books better).
   - Re-running with a different mode (`search` BM25 ↔ `query` hybrid).
   - That the relevant book may not be ingested yet (`/ingest-book`).

**Don't fabricate.** "I don't see this in your library" is a valid answer.

User input: $@
