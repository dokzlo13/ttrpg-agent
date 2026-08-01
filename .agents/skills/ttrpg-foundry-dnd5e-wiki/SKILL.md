---
name: ttrpg-foundry-dnd5e-wiki
description: |
  Research workflow for current Foundry VTT dnd5e system implementation docs.
  Use for questions about dnd5e activities, active effects, advancements,
  roll formulas, item/actor setup, hooks, module development, system settings,
  or other Foundry dnd5e automation behavior. Fetch targeted wiki pages on
  demand; do not use for canonical tabletop 5e rules or routine enricher syntax.
---

# ttrpg-foundry-dnd5e-wiki

Use this skill to answer questions about **Foundry VTT's dnd5e system implementation**.
It is a targeted web-docs workflow, not a local reference dump.

Primary source:
- Wiki UI: <https://github.com/foundryvtt/dnd5e/wiki>
- Raw wiki Markdown: `https://raw.githubusercontent.com/wiki/foundryvtt/dnd5e/<Page-Name>.md`
- Start page: <https://raw.githubusercontent.com/wiki/foundryvtt/dnd5e/Home.md>

## Use when

Use for Foundry dnd5e behavior, configuration, or implementation questions:

- activities: attack, cast, check, damage, heal, save, summon, transform, etc.
- active effects and effects syntax
- advancements and custom class/race setup
- item, actor, NPC, vehicle, and sheet behavior
- roll formulas and roll data paths
- hooks, APIs, module registration, system HTML/theming
- compendium browser, journals, awards, transformations, enchantments
- "how do I set this up in Foundry dnd5e?"

Do **not** use for:

- canonical D&D 5e rules, monsters, spells, or items → use local 5etools/qmd skills
- routine enricher syntax → use `ttrpg-foundry-enrichers` first
- statblock importer paste format → use `ttrpg-foundry-statblock-importer`
- generic Foundry core docs unless the dnd5e wiki points there or the user asks

## Workflow

1. Classify the question and pick likely pages from the topic map below.
2. If page choice is uncertain, fetch `Home.md` first and inspect links.
3. Fetch **raw Markdown** pages with `fetch_content`; avoid GitHub HTML when possible.
4. Follow only directly relevant links. Do not crawl the whole wiki.
5. Answer from the fetched docs, citing page URLs.
6. If docs are incomplete for API/source behavior, then use `code_search` or
   `vercel_grep_searchGitHub` against `foundryvtt/dnd5e` for concrete source/examples.
7. State when behavior may depend on the installed dnd5e system version.

Preferred fetch pattern:

```text
fetch_content url="https://raw.githubusercontent.com/wiki/foundryvtt/dnd5e/Home.md"
fetch_content url="https://raw.githubusercontent.com/wiki/foundryvtt/dnd5e/Activities.md"
fetch_content url="https://raw.githubusercontent.com/wiki/foundryvtt/dnd5e/Activity-Type-Save.md"
```

If a raw page fails, try:
- the GitHub wiki UI URL: `https://github.com/foundryvtt/dnd5e/wiki/<Page-Name>`
- `web_search` scoped to `github.com/foundryvtt/dnd5e/wiki`
- the repository/source search tools for implementation details

## Topic map

Start/discovery:
- `Home.md` — current wiki index
- `FAQ.md` — common system questions

Activities:
- `Activities.md`
- `Activity-Type-Attack.md`
- `Activity-Type-Cast.md`
- `Activity-Type-Check.md`
- `Activity-Type-Damage.md`
- `Activity-Type-Enchant.md`
- `Activity-Type-Forward.md`
- `Activity-Type-Heal.md`
- `Activity-Type-Save.md`
- `Activity-Type-Summon.md`
- `Activity-Type-Transform.md`
- `Activity-Type-Utility.md`

Character/item construction:
- `Advancement.md`
- `Advancement-User-Guide.md`
- `Advancement-Type-Ability-Score-Improvement.md`
- `Advancement-Type-Hit-Points.md`
- `Advancement-Type-Item-Choice.md`
- `Advancement-Type-Item-Grant.md`
- `Advancement-Type-Scale-Value.md`
- `Advancement-Type-Size.md`
- `Advancement-Type-Subclass.md`
- `Advancement-Type-Trait.md`
- `Custom-Class-Advancement.md`
- `Custom-Race-Tutorial.md`
- `Items.md` if present/reachable from the wiki

Automation and formulas:
- `Active-Effect-Guide.md`
- `Roll-Formulas.md`
- `Enchantment.md`
- `Transformation.md`
- `Awards.md`

Text/journals/enrichers:
- `Enrichers.md` only for current verification; otherwise prefer `ttrpg-foundry-enrichers`
- `Embeds.md`
- `Journal-Pages.md`

Development/API:
- `Hooks.md`
- `Module-Registration.md`
- `System-HTML.md`
- `Table-of-Contents.md`
- `Modifying-Your-Game-with-Scripts.md`

Other UI/system topics:
- `Calendar.md`
- `Compendium-Browser.md`
- `Dynamic-Module-Art.md`

## Answering rules

- Separate **Foundry dnd5e implementation behavior** from **tabletop 5e rules**.
- Do not claim a tabletop rule changed just because Foundry automates something a certain way.
- Prefer exact setup steps and field names when the docs provide them.
- Include short citations, e.g.:
  - `https://raw.githubusercontent.com/wiki/foundryvtt/dnd5e/Activities.md`
- Do not paste large chunks of docs; summarize and quote only tiny necessary snippets.
- If the wiki is stale/ambiguous, say so and cross-check the source repository.

## Common compositions

- Foundry item with rollable save/damage text:
  `ttrpg-foundry-dnd5e-wiki` for activities → `ttrpg-foundry-enrichers` for description syntax.

- Converting a monster for Foundry:
  `ttrpg-rules-osr-to-5e` or source lookup → `ttrpg-foundry-statblock-importer` for import block →
  `ttrpg-foundry-enrichers` for post-import notes → this skill only if dnd5e system setup is unclear.

- Custom class/subclass/species/background setup:
  this skill for advancement docs → local 5e lookup only for canonical progression facts.
