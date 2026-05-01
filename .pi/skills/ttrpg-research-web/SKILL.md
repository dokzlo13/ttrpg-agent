---
name: ttrpg-research-web
description: |
  Web research for material outside the local vault/library: inspiration,
  real-world references, mythology, naming, community resources, or current
  rulings. Use after local sources are insufficient, or when the user explicitly
  asks for outside material. Do not use for local 5e records or Foundry docs.
---

# ttrpg-research-web

Wraps the `pi-web-access` extension. The user has Exa and Google API keys
configured (see README prereqs).

## When to use

✅ **Use** for:

- Real-world inspiration: "Slavic folklore creatures with hooves", "Edo-period
  inn culture", "real cave systems with bioluminescence".
- Naming: "Welsh-coded place names", "medieval Iberian noble surnames".
- System rulings beyond local data: "official Sage Advice on grappling +
  invisibility", "Crawford tweets about counterspell".
- 5e community resources not in our books: blog posts on encounter design,
  homebrew wikis.

❌ **Don't use** for things that should be local:

- Statblocks of canonical 5e creatures → `ttrpg-rules-5etools-query` / `query_5etools`.
- Content from the user's own books → `ttrpg-library-search`.
- Foundry enricher syntax → `ttrpg-foundry-enrichers` (already documented).
- Foundry dnd5e system implementation/docs → `ttrpg-foundry-dnd5e-wiki`.

The instinct is: **try local first**. Web is the fallback when local has
nothing or when the question is genuinely outside-of-game-system.

## Citation convention

When pulling material from the web into the vault, **always cite**:

```markdown
## Inspiration
The "wraiths bound to bells" motif draws on Japanese *kane no rei* folklore.
- https://example.com/article — accessed 2026-04-30
- https://example.com/other — accessed 2026-04-30
```

URL + access date, in the body or in the Sources footer. The user trusts
material with a citation; uncited "from the web" content is a red flag.

## Don't

- Don't paste large chunks of copyrighted articles into the vault. Paraphrase
  and cite. The vault is a working document, not a content cache.
- Don't trust a single source for a system ruling. Cross-check Sage Advice +
  the official errata before claiming "the rule says X".
- Don't burn web calls on trivial questions. Each search costs API budget;
  prefer the local library.

## Patterns

```
# Inspiration mining
search: "real-world examples of plague-doctor rituals"
→ summarize 3–5 sources, cite, suggest 2–3 ways to use this in the campaign

# Naming
search: "Welsh place name elements meaning hill / fortress"
→ list elements, give 5 example combinations the user can pick from

# Rules clarification
search: 'site:dnd.wizards.com "sage advice" grappling invisibility'
→ paraphrase the ruling, link the page, give the page's date
```
