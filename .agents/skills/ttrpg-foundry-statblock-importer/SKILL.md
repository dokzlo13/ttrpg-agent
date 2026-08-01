---
name: ttrpg-foundry-statblock-importer
description: |
  Foundry 5e-statblock-importer formatting reference. Use when normalizing,
  converting, or reviewing a monster/NPC statblock that will be pasted into the
  importer. Output should be plain WotC-style text; Foundry enrichers belong in
  a separate post-import section.
---

# ttrpg-foundry-statblock-importer

Updated from the importer's README and parser code.

## What the importer actually wants

A plain-text statblock in standard WotC order. Markdown emphasis is tolerated,
but the safest output is simple prose lines.

The parser recognizes labels like:
- Armor Class / AC
- Hit Points / HP
- Speed
- Saving Throws / Saves
- Skills
- Damage Vulnerabilities
- Damage Resistances / Resistances
- Damage Immunities / Immunities
- Condition Immunities
- Senses
- Languages
- Challenge / CR / Challenge Rating
- Proficiency Bonus
- Initiative
- Source
- Gear

Section headers it recognizes include:
- Actions
- Bonus Actions
- Reactions
- Legendary Actions
- Mythic Actions
- Lair Actions
- Traits / Abilities
- Utility Spells
- Villain Actions

## Best-safe layout to emit

Use this shape unless the user asks otherwise:

```text
<Name>
<Size> <type> (<subtype>), <alignment>
Armor Class <value> (<source>)
Hit Points <avg> (<dice>)
Speed <list>
STR DEX CON INT WIS CHA
10 (+0) 14 (+2) 12 (+1) 8 (-1) 10 (+0) 6 (-2)
Saving Throws Str +4, Con +3
Skills Perception +4, Stealth +6
Damage Resistances cold; bludgeoning, piercing, and slashing from nonmagical attacks
Damage Immunities poison
Condition Immunities poisoned
Senses darkvision 60 ft., passive Perception 14
Languages Common, Goblin
Challenge 3 (700 XP)

Trait Name. Trait text.

Actions
Attack Name. Melee Weapon Attack: +5 to hit, reach 5 ft., one target. Hit: 8 (1d10 + 3) slashing damage.

Bonus Actions
...

Reactions
...

Legendary Actions
The creature can take 3 legendary actions, choosing from the options below.
...
```

## Important parser facts

### Ability scores

The parser accepts both:

```text
STR DEX CON INT WIS CHA
20 (+5) 15 (+2) 21 (+5) 19 (+4) 17 (+3) 16 (+3)
```

and more vertical layouts. The two-line compact form is safest.

### 2024 initiative

The importer recognizes Initiative as its own line, and can also parse it from
an AC line in some formats. Safest form:

```text
Initiative +3 (13)
```

Only include it when intentionally formatting for newer layouts.

### Immunities / resistances

The parser understands both older and newer labels. Still, safest output is:
- `Damage Resistances ...`
- `Damage Immunities ...`
- `Condition Immunities ...`

### Spellcasting

Spellcasting is parsed best when written in ordinary WotC prose, followed by
spell lists such as:

```text
Innate Spellcasting. The creature's spellcasting ability is Wisdom (spell save DC 14). It can innately cast the following spells, requiring no material components:
At will: detect magic, thaumaturgy
1/day each: fear, invisibility
```

### Actions and features

Action/trait titles should look like:

```text
Multiattack. ...
Frost Breath (Recharge 5-6). ...
Legendary Resistance (3/Day). ...
```

That punctuation matters.

## Project rule: plain text only in importer block

Do not put these inside importer text:
- `[[/damage ...]]`
- `[[/save ...]]`
- `[[/check ...]]`
- `@Embed[...]`
- `&Reference[...]`

Those belong in post-import description text, not in the imported statblock.

## Practical monster-conversion output format

When the user asks for conversion + import format, respond as:

```markdown
Reasoning:
- ...

Import Statblock:
```text
<plain importer-ready statblock>
```

Post-Import Foundry Notes:
- optional enrichers for description fields
```

## Don't

- Don't reorder the top matter wildly.
- Don't rename `Actions` to `Powers`, `Moves`, etc.
- Don't try to be clever with markdown tables if plain lines will do.
- Don't mix importer text with enrichers.
