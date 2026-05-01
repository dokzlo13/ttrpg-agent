---
name: ttrpg-rules-osr-to-5e
description: |
  Convert OSR (B/X, OSE, OSRIC, AD&D, BFRPG, etc.) statblocks and mechanics to
  D&D 5e (2014 or 2024) with appropriate CR scaling for a given party level.
  Use whenever the user is adapting an OSR adventure or asks to "5e-ify" a
  monster, trap, or mechanic.
---

# ttrpg-rules-osr-to-5e

Use this for **monster conversion judgment**, not just math.

## First principle: prefer replacement over invention

Always try these in order:

1. **Direct replacement.** If a good 5e 2024 creature already exists, say:
   `Use statblock of <monster> from <source>.`
2. **Close analogue with edits.** Start from a nearby 5e creature, then adjust
   damage types, movement, senses, signature abilities, and flavor.
3. **New build.** Only build from scratch when no close analogue exists or the
   monster's identity depends on a unique gimmick.

The best conversion usually starts from an existing 5e chassis.

## Workflow

### 1) Identify the monster's job

Before converting numbers, decide what the monster is **for**:

- brute / blocker
- skirmisher / ambusher
- artillery / caster
- controller / debuffer
- solo boss / elite lieutenant / mook

Preserve the **table role** first. Old statblocks are often sparse; their job in
play matters more than literal number transfer.

### 2) Determine the target

If the user didn't specify, ask only for what's necessary:

- ruleset: **2014** or **2024**
- party level
- intended difficulty: **chump / standard / tough / boss**

Default assumptions if forced:
- ruleset: 2024
- difficulty: standard

### 3) Translate by intent, not by formula worship

Use formulas only as starting estimates.

#### HD → HP

Rough first pass:

| Old HD | Starting 5e HP feel |
|---|---|
| 1 HD | 7–12 |
| 2 HD | 13–24 |
| 3–4 HD | 20–40 |
| 5–6 HD | 35–60 |
| 7–9 HD | 55–95 |
| 10–12 HD | 85–140 |

Then adjust to the target CR band.

#### AC

- descending AC → start with `19 - old AC`
- ascending AC → use as a clue, not gospel
- natural armor in old editions usually means AC should land a bit high for its CR

#### Attacks

Convert to normal 5e attack lines:

`Bite. Melee Weapon Attack: +X to hit, reach 5 ft., one target. Hit: Y (dice) piercing damage.`

Use **attack bonus, DPR, and save DC** from the target CR band, not from old THAC0 logic.

#### Saves / special defenses

Map old save categories by effect:

| Old-school effect | Usually becomes |
|---|---|
| poison / death | CON save |
| breath / blast | DEX save |
| petrify / paralysis | CON save |
| charm / fear / magic compulsion | WIS save |
| mind-breaking sorcery / alien logic | INT or WIS save |

Prefer the save that matches the fiction, not a rigid lookup table.

### 4) Rebuild special abilities in 5e language

Prefer existing 5e building blocks:

- Pack Tactics
- Flyby
- Magic Resistance
- Spider Climb
- Siege Monster
- Rampage
- Swallow
- grapple / restrained riders
- Recharge 5–6
- 1/Day, 3/Day, At Will
- existing 5e spells instead of bespoke spell-like text

If you invent a new ability, write it in clean 5e rules language and keep it to
**one gimmick per monster** unless it's a boss.

## CR tuning

Use the DMG monster-by-CR logic as the balancing anchor.

### Practical target bands

| CR | AC | Attack | Save DC | DPR |
|---|---:|---:|---:|---:|
| 1 | 13 | +3 | 13 | 9–14 |
| 3 | 13 | +4 | 13 | 21–26 |
| 5 | 15 | +6 | 15 | 33–38 |
| 8 | 16 | +7 | 16 | 51–56 |
| 10 | 17 | +7 | 16 | 63–68 |
| 15 | 18 | +8 | 18 | 93–98 |
| 20 | 19 | +10 | 19 | 123–140 |

HP should also sit near the chosen CR band; use this table as a sanity check,
not a prison.

### Heuristic adjustments

Raise effective CR if the monster has:
- strong control riders every round
- repeatable invisibility / mobility denial
- reliable AoE damage
- Magic Resistance
- strong regeneration
- Legendary Resistance / legendary actions

Lower effective CR if:
- damage is highly conditional
- it has poor action economy
- signature tricks are easy to shut down
- it is mostly flavor with weak numbers

## Output policy

For conversion requests, produce:

1. **Reasoning**
   - direct replacement / analogue / from-scratch
   - what you changed
   - what CR band you targeted
2. **Import Statblock**
   - plain WotC-style text for `5e-statblock-importer`
   - no Foundry enrichers inside the import block
3. **Post-import notes** (optional)
   - only if the user wants Foundry description enrichers afterward

## Foundry rule

The importer block must stay plain text.

If the user wants Foundry-clickable extras, put them in a separate section after
conversion, using `ttrpg-foundry-enrichers`. Do **not** mix enrichers into the
main importer block.

## Save destinations

Use `ttrpg-vault-authoring` before saving. Persistent monster conversions usually go to:
`vault/notes/mechanics/monsters/<slug>.md`

Include frontmatter such as:

```yaml
---
type: monster
source: agent
created: 2026-04-30
tags: [monster, converted, osr]
status: draft
source_system: osr
original_book: <book-slug>
cr_target: <n>
party_level_assumed: <n>
---
```

Then include:
- short reasoning section
- wikilinks to the source book/chapter and related locations/factions/creatures when known
- importer-ready statblock
- `## Conversion Notes`

## Don't

- Don't preserve old mechanics that are just system residue.
- Don't overfit to exact old HP or AC if it breaks the intended encounter role.
- Don't invent bespoke spells when an existing 5e spell does the job.
- Don't put Foundry enrichers inside importer text.
