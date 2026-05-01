---
name: ttrpg-create-readaloud
description: |
  Write boxed-text "read aloud" passages for the user to read at the table —
  scene openings, NPC introductions, environmental moments, dramatic reveals.
  Use whenever the user asks for "read-aloud", "boxed text", "scene description",
  "describe the room/forest/inn for me to read".
---

# ttrpg-create-readaloud

> **This skill is the most opinionated in the set. The voice below is the
> author's first guess at what feels good at the table. Refine it ruthlessly
> as you learn what your players respond to. Edit this file directly — it's
> meant to be hacked.**

## When to use

Direct triggers: "read-aloud for...", "boxed text", "describe X for the table",
"give me a scene opener", "intro the inn".

Indirect: any time the user is prepping a session and just produced a location,
encounter, or NPC. Offer one proactively if it'd help (don't always write one).

## The voice

**Concrete over abstract.** "Pine resin and woodsmoke" beats "smells like a forest".
Three senses in the first paragraph. Sight is free; pick two more (sound, smell,
temperature, texture, taste).

**Present tense, second person.** "You step through the doorway. The air is
cooler in here." Not "they would step" or "the party can see".

**Length: 60–120 words for normal scenes.** Up to 200 for dramatic reveals.
A boxed text longer than that is a soliloquy and players tune out.

**End on a hook, not a period.** The last beat should imply choice or threat:
a door at the back, a figure noticing you, footsteps from below. Don't
narrate the players' reactions — that's their job.

**No mechanical content inside.** No DCs, no "you make a Perception check",
no monster names that haven't been introduced fictionally. Save mechanics for
the DM-facing block beneath.

## Format on the page

```markdown
> *The trail breaks out of the pines and you find yourselves on the lip of a
> ravine. Far below, a black river coils through the rocks, and a single
> rope-and-plank bridge spans the gap — its boards greyed by weather, its
> ropes furred with moss. The wind off the water carries the smell of wet
> stone and something older, like turned earth. Halfway across, a raven
> watches you. It does not move.*

**DM notes**
- Bridge: DEX save DC 12 if rushing, otherwise safe.
- Raven: scout for the witch in §3. Doesn't attack; flies off if approached.
- Hook: the raven's stillness is the first cue she's been watching.
```

The boxed text is the blockquote in italics. The DM notes live underneath,
plain. The user copies the boxed text as-is into Foundry's journal or reads
it aloud.

## Writing patterns to lean on

- **The single uncanny detail.** Everything in the scene is normal except one
  thing: the candles burn blue. The raven doesn't move. The innkeeper has the
  same accent as the dead noble. Players latch onto the wrong note.
- **The negative space.** What's *missing* is often louder than what's there:
  no birdsong in the woods, no fire in the hearth on a cold night, no priest
  at the morning service.
- **The implied recent past.** Wet footprints. A still-warm cup. A door
  swinging on its hinges. Players feel they just missed something.

## Patterns to avoid

- **"You see a [adjective] [adjective] [noun]."** Pile-of-adjectives is filler.
  One precise noun beats three vague modifiers.
- **Naming things players couldn't know.** "A githyanki warrior approaches" —
  no. Describe the angular face, yellow skin, silver sword. They earn the name.
- **Telling them how they feel.** "A chill of dread runs down your spine."
  Show the dread (dimming light, faltering torches, animals fleeing); let them
  feel it.
- **Closing the scene.** "And then you arrive at the inn." Stop one beat earlier.

## Length-by-purpose cheat

| Purpose | Words | Notes |
|---|---|---|
| Quick room/transition | 30–60 | Sight + one other sense |
| Standard scene opener | 60–120 | Three senses, one uncanny detail |
| Dramatic reveal / boss | 120–200 | Slower pacing; build to the hook |
| NPC first appearance | 40–80 | Posture, voice, one telling object |
| Combat round narration | 20–40 | Just the sensory crunch, no rules |

## Where to save

Use `ttrpg-vault-authoring` before saving. Reusable boxed texts usually go to
`vault/notes/readalouds/<slug>.md` with frontmatter `type: readaloud`, `scene: <description>`,
`tags: [...]`. Session-specific ones live inside the relevant session prep note instead.
When saved, include wikilinks to the related location/NPC/faction and add a short
`## Connections` section if the note will be reused.

## Don't

- Don't generate read-aloud for combat tactics or rules. That's not boxed text.
- Don't reuse exact phrasing across multiple scenes — players notice.
- Don't write the players' actions. Stop at the hook.
