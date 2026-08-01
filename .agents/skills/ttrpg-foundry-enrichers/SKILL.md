---
name: ttrpg-foundry-enrichers
description: |
  Reference for Foundry VTT dnd5e system enrichers. Use when writing or editing
  any text that will be pasted into a Foundry actor/item/journal description —
  rolls, saves, checks, references, embedded content. NOT for the
  5e-statblock-importer input (that wants prose, not enrichers).
---

# ttrpg-foundry-enrichers

Agent cheat sheet for Foundry VTT dnd5e enrichers.

Sources:
- Official dnd5e wiki: <https://github.com/foundryvtt/dnd5e/wiki/Enrichers>
- Award details: <https://github.com/foundryvtt/dnd5e/wiki/Awards>
- Source page was marked current for dnd5e `5.3.0` when this skill was updated.

## Golden rule

Use enrichers in Foundry-rendered prose:
- actor biographies and feature descriptions
- item descriptions
- journal entries and handouts
- GM notes intended for Foundry
- post-import cleanup text

Do **not** use enrichers inside the main text block being pasted into
`5e-statblock-importer`. The importer wants plain WotC-style text.

For converted monsters, if Foundry output is requested, produce:

1. `Import Statblock` — plain importer text, no enrichers
2. `Post-Import Foundry Notes` — enriched text for description fields

## Project policy

- Prefer useful, low-risk enrichers over making every rules word clickable.
- Do **not** fabricate Actor/Item/Activity/UUID IDs. Use them only if the user provides them.
- If an enricher depends on an item activity and no activity ID is known, either omit it or use the blank form only when the text will live on that same Foundry item.
- If uncertain, prefer plain text over broken syntax.
- Preserve the user's preference: in monster rules prose, write condition names plainly (`Prone`, `Grappled`, `Frightened`) rather than `&Reference[Prone]`, unless the user explicitly asks for condition links.
- Use `&Reference[...]` mainly for broader rules: `Difficult Terrain`, `Darkness`, `Half Cover`, `Telepathy`, `Cover`, `Hiding`, etc.
- Use `rules=2024` only when it matters, especially skill+tool checks; otherwise let the actor/system context decide.

## General syntax rules

Options use `key=value`:

```text
[[/check ability=dex dc=15]]
[[/damage formula="1d6 + 2" type=fire]]
```

Useful shorthand is allowed where the wiki marks options as inferred:

```text
[[/save dex 15]]
[[/damage 2d6 fire average]]
[[/attack +5]]
```

Quoting:
- Values with spaces need double quotes: `activity="Escape Tentacles"`.
- Formulas with spaces usually need keyed form and quotes: `dc="8 + @prof"`.
- DC formulas cannot include dice.

Do not nest enrichers:

```text
Good: [[/damage 1d6 bludgeoning average]]
Bad:  [[/damage [[/r 1d6]] bludgeoning average]]
```

Common option value types:
- Boolean: `true`, `false`, or an inferred flag like `average`.
- Choice: one allowed ID/name such as `dex`, `fire`, `long`, `extended`.
- Formula: roll/calculation formula, e.g. `2d6 + 3`.
- `@` path: roll-data path such as `@abilities.con.dc`; no spaces/calculation details.
- ID: Foundry document/activity ID. Do not invent it.

## Quick chooser

Use this when writing Foundry prose:

| Need | Use |
|---|---|
| Saving throw | `[[/save dex dc=15]]` |
| Concentration save | `[[/concentration dc=15]]` |
| Ability/skill/tool check | `[[/check wis dc=14]]`, `[[/skill perception 15]]` |
| Passive check | `[[/skill perception 15 passive format=long]]` |
| Damage roll | `[[/damage 2d6 fire average]]` |
| Healing/temp HP | `[[/heal 2d4 + 2]]`, `[[/heal 10 temp]]` |
| Trap attack | `[[/attack +5]]` |
| Use existing item | `[[/item Bite]]` |
| Actor/item dynamic value | `[[lookup @name lowercase]]` |
| Rules tooltip | `&Reference[Difficult Terrain]` |
| XP/currency award | `[[/award 50gp 100xp]]` |

## Attack enrichers

Use for rollable attacks in traps, hazards, journals, or item descriptions.
Use sparingly in monster text; prefer normal statblock attack lines in importer blocks.

```text
[[/attack +5]]
[[/attack formula=5]]
[[/attack formula=5 attackMode=thrown]]
[[/attack activity=jdRTb04FngE1B8cF]]
[[/attack activity=jdRTb04FngE1B8cF format=extended]]
```

Options: `formula`, `activity`, `attackMode`, `format=short|long|extended`, `rules=2014|2024`.

Warnings:
- Fails if neither formula nor attack activity is available.
- Do not explicitly set both formula and activity.
- `extended` formats like NPC statblock attack text and changes with rules version.

## Save and concentration enrichers

Use for saving throws in hazards, auras, features, and journal text.

```text
[[/save dex dc=14]]
[[/save ability=wis dc=15 format=long]]
[[/save str dex dc=20]]
[[/save activity=RLQlsLo5InKHZadn]]
[[/save]]

[[/concentration]]
[[/concentration dc=15]]
[[/concentration ability=cha]]
```

Options: `ability`, `activity`, `dc`, `format=short|long`.

Notes:
- Multiple save abilities are supported: `[[/save str dex dc=20]]`.
- `[[/save]]` only makes sense on an item with a save activity.
- DC can be fixed or an owner-based formula/path, e.g. `dc=@abilities.con.dc`.
- DC formulas cannot contain dice.

Warnings:
- Fails if no ability and no save activity are available.
- Do not explicitly set both ability and activity.

## Check, skill, and tool enrichers

`[[/check]]`, `[[/skill]]`, and `[[/tool]]` are interchangeable entry points; choose the clearest one.

```text
[[/check dex dc=15]]
[[/check ability=dexterity dc=20 format=long]]
[[/skill acrobatics dc=13]]
[[/skill strength intimidation 20]]
[[/check skill=acr/ath dc=15]]
[[/tool ability=dexterity tool=thief]]
[[/skill perception 15 passive format=long]]
[[/check skill=sur tool=navg rules=2024]]
[[/check activity=RLQlsLo5InKHZadn]]
[[/check]]
```

Options: `ability`, `activity`, `dc`, `format=short|long`, `rules=2014|2024`, `skill`, `tool`.

Notes:
- If no ability is provided for a skill/tool, Foundry uses the default ability.
- If an ability is provided, all listed skills/tools use that ability.
- Multiple skills/tools are supported; multiple abilities are not.
- Passive checks use `passive`.
- In 2024 rules, one tool can combine with one or more skills; proficient in both can grant advantage.

Common IDs:
- Abilities: `str`, `dex`, `con`, `int`, `wis`, `cha`.
- Skills: `acr`, `ath`, `arc`, `dec`, `his`, `ins`, `itm`, `inv`, `med`, `nat`, `prc`, `prf`, `per`, `rel`, `slt`, `ste`, `sur`.
- Tools often used: `thief`, `navg`, `herb`, `pois`, `forg`, `disg`, `alchemist`, `smith`, `tinker`.

Warnings:
- Fails if no ability/proficiency and no check activity are available.
- Do not explicitly set both ability/proficiencies and activity.
- Skill/tool IDs must exist in dnd5e config.

## Damage and healing enrichers

Prefer these over raw `[[/r ...]]` rolls when damage type matters.

```text
[[/damage 2d6 fire]]
[[/damage 2d6 fire average]]
[[/damage formula="1d6 + 2" type=piercing & formula=1d4 type=fire average=true]]
[[/damage 1d10 bludgeoning slashing]]
[[/damage 1d10 type=bludgeoning/slashing]]
[[/damage 1d6 + @abilities.dex.mod slashing]]
[[/damage activity=RLQlsLo5InKHZadn]]
[[/damage activity=RLQlsLo5InKHZadn attackMode=twoHanded]]
[[/damage extended]]

[[/heal 2d4 + 2]]
[[/heal 10 temp]]
[[/heal activity=jdRTb04FngE1B8cF]]
[[/heal]]
```

Options: `formula`, `type`, `activity`, `attackMode`, `average`, `format=short|long|extended`, `rules=2014|2024`.

Notes:
- `average` auto-displays average damage, e.g. `7 (2d6) fire`.
- Use `average=5` only when you intentionally need a custom displayed average.
- Multiple alternative types: `fire cold` or `type=fire/cold`.
- Multiple damage packets: separate with `&`.
- Owner-based `@` values resolve from the owner of the item containing the enricher.
- `[[/heal]]` fetches heal activity; `[[/damage]]` fetches attack/damage/save activity with damage.

Common damage/healing types:
`acid`, `bludgeoning`, `cold`, `fire`, `force`, `lightning`, `necrotic`, `piercing`, `poison`, `psychic`, `radiant`, `slashing`, `thunder`, `healing`, `temphp`/`temp`.

Warnings:
- Fails if no formula and no suitable activity are available.
- Do not explicitly set both formula and activity.
- Invalid formulas fail to enrich.

## Item use enrichers

Use only when the item exists in Foundry or the user gave an ID/UUID.

```text
[[/item Bite]]
[[/item Bite activity=Poison]]
[[/item Tentacles activity="Escape Tentacles"]]
[[/item Actor.p26xCjCCTQm5fRN3.Item.amUUCouL69OK1GZU]]
[[/item amUUCouL69OK1GZU]]
[[/item .amUUCouL69OK1GZU]]
```

Modes:
- By item name: uses matching item on selected token or assigned actor.
- By item + activity name: triggers a specific activity; quote activity names with spaces.
- By UUID: targets an exact Actor-owned Item.
- By relative ID/UUID: resolves item from the actor/item/chat context.

Warnings:
- Never invent UUIDs or item IDs.
- Item name mode can fail if no selected/assigned actor has a matching item.

## Lookup enrichers

Use for reusable item/actor text that should adapt to the owning actor or activity.

```text
[[lookup @name]]
[[lookup @name lowercase]]
[[lookup @name uppercase]]
[[lookup @name capitalize]]
[[lookup @name]]{the creature}
[[lookup @details.type.config.label]]
[[lookup @save.dc.value activity=jdRTb04FngE1B8cF]]
```

Options: `path`, `activity`, `style=capitalize|lowercase|uppercase`.

Notes:
- If lookup fails, Foundry displays the original path unless a fallback label is provided: `[[lookup @name]]{the creature}`.
- Use activity lookup only with real activity IDs.

## Reference enrichers

Use for rules tooltips, not as a replacement for every rules word.

```text
&Reference[Difficult Terrain]
&Reference[rule="Difficult Terrain"]
&Reference[Darkness]
&Reference[Half Cover]
&Reference[Telepathy]
&Reference[condition=prone]
&Reference[blinded apply=false]
```

Options:
- `apply=false` prevents a condition reference from showing an apply-condition button.
- Category can be explicit (`condition=prone`, `rule="Difficult Terrain"`) or inferred from name.

Supported reference families include abilities, skills, conditions, creature types, damage types, area shapes, spell components/tags, spell schools, and selected rules.

Common rules worth linking:
`Difficult Terrain`, `Bright Light`, `Dim Light`, `Darkness`, `Lightly Obscured`, `Heavily Obscured`, `Blindsight`, `Darkvision`, `Truesight`, `Surprise`, `Hiding`, `Falling`, `Suffocating`, `Grappling`, `Shoving`, `Half Cover`, `Three-Quarters Cover`, `Total Cover`, `Underwater Combat`, `Attunement`, `Telepathy`.

Project preference:
- In monster action text, prefer plain `Prone`, `Grappled`, etc.
- Use condition references mainly in journal/rules handouts or when the user asks for clickable conditions.

## Award enrichers

Use in journals/GM notes for XP and treasure buttons.

```text
[[/award 50gp 100xp]]
[[/award 100xp each]]
[[/award party 500gp]]
[[/award 2d6sp]]
[[/award 50000gp]]{Give 'em the Gold}
```

Codes:
- Currency: `pp`, `gp`, `ep`, `sp`, `cp`
- Experience: `xp`

Notes:
- Values can be formulas, e.g. `2d6sp`.
- `party` sends to the primary party if configured.
- `each` grants that amount to each recipient instead of splitting it.
- Label text customizes the button label.

## Compact examples

Trap:

```text
A creature that steps on the plate is targeted by [[/attack +6]]. On a hit, it takes [[/damage 2d10 piercing average]].
```

Aura:

```text
A creature that starts its turn in the aura must succeed on [[/save con dc=14]] or take [[/damage 2d6 necrotic average]].
```

Monster post-import note:

```text
Post-Import Foundry Notes
- The chamber floor counts as &Reference[Difficult Terrain].
- A creature entering the spores for the first time on a turn must succeed on [[/save con dc=14]] or become Poisoned until the end of its next turn.
- At the start of each creature's turn while grappled by the maw, it takes [[/damage 2d6 acid average]].
```

## Final checklist

Before returning Foundry-enriched text:
- Is this text meant for Foundry actor/item/journal prose, not statblock importer input?
- Did I avoid invented IDs/UUIDs/activity IDs?
- Are formulas quoted where needed?
- Did I avoid nested enrichers?
- Did I use plain condition names in monster prose unless links were requested?
- If unsure whether Foundry can resolve it, did I choose plain text instead?
