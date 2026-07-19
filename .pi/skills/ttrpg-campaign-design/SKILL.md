---
name: ttrpg-campaign-design
description: |
  Develop, expand, connect, or audit GM-facing campaign material — arcs,
  storylines, quest networks, mysteries, factions, and consequences — that
  must stay consistent with established campaign facts across multiple
  sessions. Turns requested plots into non-prescriptive world situations.
  Not for context-free ideation, one-session agendas, post-session
  reconciliation, encounter mechanics, prose, or Foundry implementation.
---

# TTRPG campaign design

Build a **playable world state**, not a predicted story. The result should remain
useful for however many sessions play takes.

The active vault is GM-facing by default. Do not create player-safe variants or
visibility metadata unless the user explicitly requests a separate handoff.

## Compose with the current stack

Before using local campaign data:

1. Read `ttrpg-vault-navigation`.
2. Use `ttrpg-library-search` for campaign, book, and explicitly requested
   archive prose. Read or `qmd get` actual hits before relying on them.
3. Before durable vault writes, read `ttrpg-vault-authoring`; add
   `ttrpg-vault-rich-notes` for table-usable or hub notes.
4. Use `creative-brainstorm` only after retrieval when structurally different
   options would help. Brainstormed material remains proposals.
5. Use `ttrpg-vault-canvas` only when requested or when a visual network would
   materially improve reasoning.
6. Use `ttrpg-research-web` only when the user requests outside material or
   local sources are insufficient. A web summary never substitutes for exact
   module/book text when that text is available locally.

### Foundry boundary

Campaign design never creates, edits, deletes, imports, configures, connects to,
or scans a Foundry world. When the user explicitly names a live Foundry document
or a specific source-register entry as campaign evidence, and read-only access is
already available, inspect only that named document through read-only operations.
Classify it as a played record, approved preparation, future preparation, or
unresolved/stale material; follow the campaign's stated source precedence.

Do not inspect Foundry solely to generate an implementation inventory.

## Use and exclusions

Use this skill to:

- develop or revise an arc, quest network, mystery, faction conflict, location
  situation, or long-running threat;
- connect an adventure/module to established campaign truth;
- turn loose ideas or a requested plot/session sequence into cohesive,
  non-mandatory world situations;
- audit prep for railroading, causal gaps, brittle information, inert factions,
  weak consequences, outcome convergence that erases consequential differences,
  or premature canon.

Do not use it as the primary workflow for:

- a one-session agenda, run sheet, or predicted scene order;
- post-session capture or played-state/timeline reconciliation;
- encounter composition, statblocks, treasure mechanics, or rules lookup;
- Foundry scene/actor/item/journal implementation;
- read-aloud, dialogue polish, or handouts;
- generic invention that does not depend on established campaign state.

## Load only relevant references

- Always read `references/world-state.md`.
- Read `references/situations.md` for arcs, quests, locations, nodes, or module
  integration.
- Read `references/mysteries-and-information.md` when secrets, investigation,
  foreshadowing, knowledge, or access matter.
- Read `references/factions-and-consequences.md` when autonomous forces or
  materially different outcome states matter.
- `references/sources.md` records methodology provenance; it is not required for
  routine execution.

## Workflow

### 1. Frame the design question

Determine the target, desired play, protected truth, design horizon, and
non-goals. Ask one focused question only when the answer materially changes the
campaign; otherwise proceed with clearly labeled proposals.

### 2. Retrieve the smallest sufficient state

Prefer, when present:

1. the current-state or played-boundary note;
2. the relevant arc/index/workbench note;
3. involved quest, NPC, faction, location, and mystery notes;
4. loose-thread or future-tie registers only when the design reaches them;
5. exact book, archive, or explicitly named read-only Foundry evidence.

Frontmatter is only a scout. Do not assume prepared material was played.

### 3. Establish authority and present causality

Follow `references/world-state.md`. Protect played facts, direct user decisions,
established hidden causes, and explicit negatives. Report source contradictions
instead of blending them.

Model the present situation before future developments: visible instability,
hidden cause, active forces, knowledge limits, usable leverage, current action,
and what naturally changes without intervention. A major development needs a
causal actor/process, capability, trigger, and consequence; otherwise leave it
open.

### 4. Design only what the task needs

- Use `references/situations.md` for reusable situations, ways in, useful leads
  or resources, and changes if engaged or ignored. Never require a scene order.
- Use `references/mysteries-and-information.md` for revelation resilience,
  causal chronology, actor knowledge, fair information, and open promises.
- Use `references/factions-and-consequences.md` for independent world motion,
  fair pressure, and the two or three materially distinct outcome states worth
  preparing now.

For distant material, record function, constraints, and open questions—not full
scenes or endings. Make one natural campaign connection when useful; do not force
callbacks across every thread.

### 5. Run the compact quality gate

Before presenting substantial work or writing:

- played truth and explicit negatives remain intact;
- no mandatory order or predicted PC action is required;
- no necessary conclusion depends on one fragile source;
- PCs can obtain enough truthful information for consequential choices;
- active forces have a causal present action and next visible move;
- failure, delay, and refusal still produce playable world states;
- authoritative facts remain in owning notes rather than a second story bible;
- for audits: claims are source-backed and cite exact owning note paths and
  headings; use line numbers only after verifying the current file; compare exact
  scope, conditions, and authority before calling a contradiction; separate
  presentation bias from a real dependency;
- for audits: proposed repairs preserve what already works and add only the
  missing element;
- the result stops before mechanics, prose, and Foundry implementation.

### 6. Present and write

Return only the useful subset: protected facts or tensions, current situation,
situations/connections, information, faction motion, consequences, and a few
material decisions.

`Develop`, `design`, or `audit` alone defaults to chat output. A direct request to
**write, save, update a note, or create a draft** authorizes a clearly marked
`status: draft` write without another permission question.

Require explicit approval before promoting generated material to `reviewed` or
`canon`, replacing established truth, or making an unrequested major causal
change.

When writing:

- inspect the natural owner's status before editing;
- update the owning note directly when it is already `draft` or the user has
  explicitly approved the exact merge into a `reviewed`/`canon` owner;
- never downgrade an authoritative note or append unapproved proposals beneath
  its `reviewed`/`canon` status;
- when the natural owner is authoritative, use an existing linked draft
  workbench/quest note or create a small linked bespoke draft, then merge only
  after approval;
- link to authoritative facts instead of copying a parallel state ledger;
- preserve the surrounding campaign language;
- add useful body links, `## Connections`, and actual sources;
- never advance current state or the played timeline during design;
- do not impose a new campaign schema.

### Optional implementation-neutral inventory

Only after the design is accepted and the user explicitly requests an inventory,
provide a short, non-executable list of campaign needs: situation/location, NPC
or creature role, prop/information handout, GM reference, and mechanics requiring
later design. It may name an already-known Foundry target only as a verification
target.

Do not prescribe document types, IDs/UUIDs, map placement, token configuration,
activities, enrichers, automation, paste-ready text, or mutations. Do not read or
write Foundry for this inventory. End by naming the appropriate later explicit
Foundry workflow.

## Stop condition

Stop when the world state is causally coherent, important situations remain
reachable without prescribed order, necessary information is resilient, active
forces can move from motives and tools, consequences create future play, and
unresolved decisions remain visible.
