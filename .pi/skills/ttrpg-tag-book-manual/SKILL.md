---
name: ttrpg-tag-book-manual
description: |
  Agent-driven manual fallback for tagging ingested book chapters when the
  metered `book-ingest tag` path is unavailable (no OPENAI_API_KEY) or when a
  specific chapter needs a one-off override. Load this only when the main
  ingestion skill cannot run metered tagging. The agent reads each chapter and
  chooses tags; the `book-ingest tag-manual` CLI verifies hashes and writes
  frontmatter safely.
---

# ttrpg-tag-book-manual

Fallback when `book-ingest tag <slug>` is unavailable — typically no
`OPENAI_API_KEY`, so the ingest CLI's `next_steps` did not include `tag_book`.
Also useful for one-off per-chapter overrides.

The agent does the semantic work (read chapter → pick 0–3 tags). The CLI does
the writing (verifies `body_hash`, validates tags, writes `tags`/`tags_for`,
preserves `book/*` and `system/*`, refreshes the overview, records observability
in `.ingest/report.json`). Never hand-edit chapter frontmatter directly.

## Procedure per chapter

1. Read the chapter file (skip `__*.md` overview files).
2. Note `body_hash` from frontmatter.
3. Pick 0–3 strongest retrieval tags using the policy below. Prefer missing a
   marginal tag over adding a false positive.
4. Apply via the manual helper:

   ```bash
   uv run --project .pi/cli/book-ingest book-ingest tag-manual <slug> <chapter-file> \
     --body-hash sha256:<hash-from-frontmatter> \
     --tag location --tag monster
   ```

   For an explicit empty tag set:

   ```bash
   uv run --project .pi/cli/book-ingest book-ingest tag-manual <slug> <chapter-file> \
     --body-hash sha256:<hash-from-frontmatter> --empty
   ```

5. After the last chapter, run `qmd update && qmd embed` if the user wants the
   index to reflect the new tags now.

## Tag policy

Pick 0–3 strongest retrieval tags, lowercase, no `#`. Preferred vocabulary:

```text
npc, faction, location, settlement, region, dungeon, room, wilderness, hexcrawl,
encounter, combat, social, exploration, hazard, trap, puzzle, clue, secret,
quest, rumor, readaloud, boxed-text, random-table, roll-table, map, handout,
statblock, monster, item, treasure, spell, ritual, class, feat, background,
rule, mechanic, procedure, subsystem, generator, lore, history, timeline,
calendar, appendix, gm-advice, player-option, horror, mystery, investigation
```

Custom tags allowed when the vocabulary misses a central retrieval concept.
Valid Obsidian tag form: lowercase, no spaces; `_`, `-`, `/` allowed; at least
one non-numeric character.

Calibration:

- Keyed scene/site/creature the GM can run: `encounter`, `location`, `npc`,
  `monster`, `hazard`, `trap`, or `clue` as appropriate.
- Full creature/NPC stat line: `monster` is usually strong.
- Title names a site/room/place and the body describes it: `location`.
- Ordinary loot, pockets, coins, trade goods, or one-sentence rewards do **not**
  justify `item` or `treasure`.
- A single save/roll does **not** justify `mechanic`.
- Generic hooks do **not** justify `quest` unless there is an explicit mission.

If nothing fits, write an empty tag set with `--empty`. Do not invent regex- or
title-based heuristic tags.
