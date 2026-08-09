---
name: ttrpg-session-chronicle
description: |
  Turn a processed session (record.json + recap.draft.md) into the durable
  campaign record: an append-only chronicle under vault/notes/sessions/, an
  in-chat owner review, and regenerated state projections. Use after
  `session-ingest record` succeeds, when the user asks to "write up the
  session", «занеси сессию в базу», or asks what changed in the campaign after
  play. Also the skill for retcons, clock ticks, and promoting an entity to its
  own note.
---

# ttrpg-session-chronicle

Layer 2 of the session pipeline. Layer 1 (`ttrpg-session-ingest`) ends with a
validated `record.json`; this skill is everything after it.

```text
record.json + recap.draft.md
  └─► 1. draft chronicle    vault/notes/sessions/sNNN-YYYY-MM-DD-<slug>.md
                            (status: draft; owner questions inside it, in
                             «## Вопросы владельцу»)
      2. reconcile          against state/, entity notes, prep, clocks
      3. ask IN CHAT        the same questions, same conversation ← the review
      4. apply decisions    answers → ## Реконсиляция (^sN-dNN), retcons,
                            canon markers; delete the questions section
      5. rebuild + freeze   state/ projections, status: canon,
                            chronicle --check → chronicle --freeze, qmd update
```

**The governing rule:** session records are the append-only ledger; everything
the DM reads to run the game is a projection of that ledger plus hand-authored
prep; every mutable field has exactly one writing process; **canon changes only
through decisions the owner made.**

**One session = one transcription adoption, one chronicle, one review, one
freeze** — all in the same conversation when the owner answers, and safely
paused when they do not. The whole review lives in chat: there is no separate
proposals file and no inbox folder. An unanswered question is not lost — it
stays in the draft note's «Вопросы владельцу» section, and the note stays
`draft`, out of `story-state.md`. Neglect degrades to *less coverage*, never
*wrong canon*.

## Before you write anything

1. **Read the record compactly.** `record.json` is ~280 KB for a 3-hour session
   and ~60 % of that is repeated evidence blocks. Never page it into context.

   ```bash
   .agents/bin/session-ingest view --session <id>                  # everything, ~3x smaller
   .agents/bin/session-ingest view --session <id> --needs-owner    # just the owner queue
   .agents/bin/session-ingest view --session <id> --scene s12      # one scene and its events
   ```

2. **Read `recap.draft.md`** — it is already prose, already link-terminated, and
   it is a *draft for you*, not a note. Do not copy it into the vault verbatim.

3. **Read the current state** before deciding anything is new:
   `state/story-state.md`, `state/current-state.md`, `state/clocks.md`,
   `state/entity-registry.md`, and the prep notes for the scenes that were played.

4. **Check for unfinished reviews** from interrupted conversations:

   ```bash
   .agents/bin/session-ingest chronicle --status
   ```

   A draft note with open questions means a previous review never finished —
   re-ask those questions first, don't stack a second unreviewed session on top.

> [!danger] A DM recap is not this session's play
> Sessions open with «в прошлый раз вы…». The extractor cannot tell that from
> play, so those scenes arrive in `record.json` looking exactly like new events.
> On the 2026-08-08 session, scenes s1–s4 were entirely the DM recapping earlier
> sessions already recorded in `current-campaign-timeline`. **Reconcile before
> you write**: a scene whose every claim is already canon is `consistent` — it
> produces no chronicle events and no questions, only (optionally) a note that
> the session opened with a recap.

## 1. Draft the chronicle

One file per played session: `vault/notes/sessions/sNNN-YYYY-MM-DD-<slug>.md`.

The filename must contain the **session id** (`YYYY-MM-DD`) — that is what
`prune` and `adopt --promote` glob for. `sNNN` is the play-order number
(`s000-genesis` anchors everything before the first recording).

```yaml
---
type: session          # RESERVED for play records. Prep is `type: prep`.
session: 1             # play order, not a date
date_real: 2026-08-08  # == the session id
transcript: 2026-08-08 # binds evidence links to a dataset run
world_days_elapsed: 1  # in-world days since the previous session
world_time_confidence: known | estimated | unknown
arc: padduck
participants: [koshikawa, leonsio, istrid-lovehart, zak-zak, sticky-pete]  # registry slugs
entities: [padduck, ruined-church, valerian-vane]                          # registry slugs
status: draft          # → canon only after the owner answered the questions
source: pipeline
created: 2026-08-09
tags: [campaign, session]
---
```

Body, in this fixed order:

| Section | Contains |
|---|---|
| `# Сессия NN — <название>` | |
| `## Сводка` | player-safe recap, 5–10 lines, no DM secrets |
| `## События` | chronological, one bullet per event, each ending in an evidence link |
| `## Установленные факты` | **the fact ledger** — one bullet per fact, each block-ID'd `^sNN-fMM`, each with evidence + confidence |
| `## Обязательства и нити` | commitments and open threads from the record |
| `## Добыча и ресурсы` | loot |
| `## Часы` | which clocks this session moved, and by how much (proposed, not applied) |
| `## Вопросы владельцу` | **draft only** — the review queue; deleted at freeze (see §3) |
| `## Реконсиляция` | decision records `^sN-dNN`, retcon events `^r-YYYYMMDD-nn`, rejected claims recorded as «не канон — оговорка ДМ» |
| `## DM-only` | `> [!danger]` callout — hidden truth, plans, things players must not read |
| `## Connections` / `## Sources` | wikilinks + provenance |

**Block IDs are the provenance currency.** Entity notes, clocks, and retcon
markers all cite `[[s001-2026-08-08-nelly#^s1-f03]]`. A fact without a block ID
cannot be cited, which means it cannot be promoted into an entity note later.

Every fact-ledger bullet looks like:

```markdown
- Лорд Валериан Вейн предложил героям сделку: уничтожить источник подавления в
  обмен на аудиенцию Семерых. [[transcripts/2026-08-08/11-150-165#^t968848-4]]
  (уверенность 0.95) ^s1-f14
```

Unrecognised names become **candidate-slug wikilinks** plus a question in
«Вопросы владельцу». `view` marks these for you — an entity line reading
`**не в реестре**` is a name the registry could not resolve. Do not invent a
slug for transcription noise (`бифукатор`, `зомбик`); do propose one for a real
new thing (`Зубастый кролик`).

## 2. Reconcile

Check every fact-ledger claim against existing canon, in **canon priority order**:

> user statement > played record > active canon > prep notes > books

| Class | Meaning | Action |
|---|---|---|
| `consistent` | already true in the vault | nothing — do not restate it |
| `new` | nothing contradicts it | **auto-apply as `status: draft`**, cited |
| `extends` | adds detail to an existing fact | **auto-apply as draft**, cited |
| `contradicts` | disagrees with an existing note | **question for the owner** |
| `ambiguous` | could be read two ways | **question for the owner** |

**Always a question regardless of class:**

- any clock tick;
- any change to backbone lore (`tags: [campaign-backbone]`) — the
  world-breaking case, so lead with it in chat;
- anything retcon-shaped (it rewrites an already-established fact);
- anything the record flagged `needs_owner` or `world_impact != none`.

> [!warning] `world_impact` is over-assigned
> The extract prompt marks ~60 % of events `local`. Treat `world_impact` as a
> *sorting hint*, not a queue: read the event and decide whether it actually
> changes something outside the scene. Conversely `needs_owner` has been seen
> firing on rules adjudications («нежить появляется вне хода») rather than canon
> risks — those belong in the chronicle as table rulings, not in the questions.

## 3. The review — ask in chat, same conversation

The review is a conversation, not a folder. Write the questions into the draft's
`## Вопросы владельцу` section, then **ask the owner the same questions in chat,
immediately**. The section is the crash-safe copy — it is what lets a review
survive an interrupted conversation — and the exact heading is a contract:
`chronicle --check` refuses to accept a canon note that still carries it.

Each question, in the note and in chat, numbered `### PN — <заголовок>`:

```markdown
### P3 — Валериан обещал аудиенцию Семерых

**Сессия говорит:** … [[transcripts/2026-08-08/11-150-165#^t968848-4]] ^s1-f14
**Канон говорит:** [[paddock-cast-and-factions#Семеро и их исполнитель]] — «обещает
героям аудиенцию Семерых» (подготовка, ещё не сыграно).
**Класс:** extends · **Рекомендация:** принять, отметить prep как сыгранный.
Варианты: принять / оговорка ДМ / реткон / ошибка распознавания / другое.
```

Keep it short. Twenty questions nobody answers is worse than five that get
answers. Free-text answers are normal — the owner's words become the decision.

**If the owner does not answer** (conversation interrupted, or they defer):
stop cleanly. The note stays `status: draft`, the questions stay in the
section, draft facts stay out of `story-state.md`, and
`current-state.md` names the pending note. `chronicle --status` finds it
mechanically next time. Never flip a note to canon with unanswered questions —
the check will refuse it anyway.

## 4. Apply decisions

Every answer becomes a **decision record** in `## Реконсиляция`, one line or
table row per question, block-ID'd `^sN-dNN` (matching the question number) and
quoting the owner's choice. Those anchors are what entity notes cite as decision
provenance after the questions section is gone.

- **Accepted contradiction → a retcon event.** Add `^r-YYYYMMDD-nn` to the
  session's `## Реконсиляция`, and mark the superseded canon **without deleting
  it**:

  ```markdown
  > [!warning] Реткон — см. [[s001-2026-08-08-nelly#^r-20260809-01]]
  ~~Вайл ушёл в лес~~ → Вайла утянуло под землю у разрушенной церкви.
  ```

- **Rejected claim** → record it as «не канон — оговорка ДМ» in `## Реконсиляция`
  so it is never asked again.
- **Off-session correction** → its own small `rc<YYYY-MM-DD>-<slug>.md` record in
  `sessions/`, same mechanics.
- **Fix everything the decisions touch, now:** entity notes that cited a pending
  question re-point to the decision anchor `^sN-dNN`; the registry gets the
  confirmed aliases; clocks tick only as decided; prep notes get their
  machine-asserted `play_status`. This is the "make notes consistent" step —
  nothing may still say «ожидает решения» after the review.
- **Delete the `## Вопросы владельцу` section** once every question in it has a
  decision record. Unanswered ones keep the section (and the draft status).
- **Never** auto-apply a contradiction, a clock tick, or a backbone change.

## 5. Rebuild projections and freeze

1. Regenerate `state/current-state.md` (bounded dashboard), `state/story-state.md`
   (agent-owned, ≤300 lines), `state/clocks.md`, `state/calendar.md`.
2. Assert `play_status` / `played_in` on the prep notes that were played, and
   append to their agent-owned `## Статус отыгрыша` section. **Never rewrite prep
   prose to match what happened** — divergence is recorded in the session record.
3. Flip the chronicle to `status: canon`.
4. Verify, freeze, and index:

   ```bash
   .agents/bin/session-ingest chronicle --session <id> --check
   .agents/bin/session-ingest chronicle --session <id> --freeze
   .agents/bin/qmd update && .agents/bin/qmd embed
   ```

`chronicle --check` resolves every `[[transcripts/…#^t…]]` citation against
`anchors.json`, reports missing frontmatter, and refuses a canon note that still
carries `## Вопросы владельцу`. A citation that does not resolve renders as
plain text in Obsidian — you find it at the table, not here.

`chronicle --freeze` is what makes **immutable** mean something: it records the
note's content digest (the append-allowed `## Реконсиляция` section excluded)
under `.cache/sessions/<id>/`. From then on, `--check` and `--status` report any
edit outside that section as **drift**. A *recorded* retcon marking in an old
note is legitimate — acknowledge it by re-running `--freeze`; anything else is a
silent edit to frozen history and gets repaired, not re-frozen. Deleting the
freeze record merely degrades enforcement back to convention.

**`prune` does not consult this check.** Its gate is only that *a* chronicle
mentioning the session id exists — a note with forty broken links unblocks it
exactly as well as a perfect one. `chronicle --check` is the thing that tells you
which of the two you wrote, so run it before pruning, not because the tool makes
you.

The mechanical "am I caught up?" is one command, no session id needed:

```bash
.agents/bin/session-ingest chronicle --status
```

Caught up means: every `type: session` note is canon, no open questions, no
freeze drift.

## Entity promotion

`state/entity-registry.md` maps every name the extractor emits onto a slug. It is
the reason «Кашикава», `Koshikawa` and `Koshikawa (Кошикава)` all land in one
place. Matching is **exact** — an unresolved name is a question for the owner,
never a guess.

Promote a roster entry to its own note in `npcs/`, `locations/` or `factions/`
when **any** of:

1. it appears in the fact ledgers of **two or more** sessions;
2. it is the target of a clock or a party of an open commitment;
3. the owner asks.

Until then, chronicles still wikilink the candidate slug. **Dangling links from
two or more sessions are the promotion queue** — that is the signal, not a chore.

An entity note is `## Сводка` (2–3 lines) → `## Канон` (one bullet per fact,
**every bullet citing a session block, a decision `^sN-dNN`, or a hand-authored
source**) → `## DM-only` → Connections/Sources. Distilled current truth only,
never narrative history — that is what keeps pages readable at session 100.

## Retrospective additions (mid-campaign tracking)

Session tracking started long after this campaign did, so facts from the
untracked past surface at the table indefinitely. **Do not create empty
placeholder sections for them** — an unfilled section is the ceremony creep the
design names as the silent killer. Two mechanisms instead:

- **A small fact belonging to the pre-tracking past** → append a numbered fact to
  `sessions/s000-genesis.md` with its source named inline
  (*«Источник — владелец, …»*), and add a line to that note's `## Реконсиляция`
  recording that it was added after the freeze. `s000` is the one canon record
  that accepts appends, because its whole subject is what nobody wrote down.
- **Something substantial, or a correction arriving between sessions** → its own
  `sessions/rc<YYYY-MM-DD>-<slug>.md` with the same structure and its own fact IDs.

**Check the addition against the existing ledger before writing it.** A
retrospective fact frequently contradicts a "not yet" fact recorded earlier. Real
example: `^s0-f33` (heroes met Valerian in Morgansfort) contradicted `^s0-f30`
("the heroes have not met Valerian"); `^s0-f30` was narrowed to "not in this arc"
and both edits were recorded. Adding without checking leaves the ledger
self-contradictory, and nothing downstream will catch it.

Fact ID space is open-ended — never pre-allocate.

## Mutation classes — who may write what

| Class | Files | Rule |
|---|---|---|
| Append-only ledger | `sessions/s*.md`, `rc*.md` | frozen after `status: canon` + `chronicle --freeze`; only `## Реконсиляция` may receive appends. Any other post-freeze edit is a recorded retcon marking followed by a deliberate re-freeze — never silent |
| Derived projections | `state/*` | regenerated per session; `story-state.md` agent-owned outright; `current-state.md` tolerates hand edits (capture them as user-facts before regenerating) |
| Accumulating, cited | `npcs/ locations/ factions/`, entity-registry | agent appends cited facts; DM hand-edits become `source: user` facts at the next ingestion |
| Hand-authored | `campaign/` lore and prep | ingestion touches only `play_status` frontmatter and the `## Статус отыгрыша` section |

## Hard rules

- **A transcript quote is evidence of what was SAID, not of what is TRUE.**
  Players misremember, joke, and speak out of character. Present quotes as speech
  with a timestamp and a speaker.
- **Never restate the DM's recap as new events.** Reconcile first.
- **Never delete superseded canon** — mark it and link the retcon.
- **Never invent a `world_days_elapsed`.** If the owner did not say, write
  `unknown`, ask one question in chat, and carry on without it.
- **Never hand-edit `vault/transcripts/`** or anything under `.cache/sessions/`.
- **`status: canon` is a freeze, and the freeze is verified.** Flip to canon
  only after every question has a decision; run `chronicle --check` and
  `chronicle --freeze`. Corrections after that are retcon events in a new
  record, plus a marked-and-re-frozen edit in the old one.
- **Not answering is safe, answering halfway is not special.** Unanswered
  questions keep the note `draft` and out of story-state; nothing may promote
  its facts until the owner speaks.
- **Do not fabricate a slug for transcription noise.** `дрон`, `бифукатор`,
  `зомбик` belong in `_lexicon.yaml` if they recur, not in the registry.

## Reference

- `ttrpg-session-ingest` — layer 1, the CLI chain that produces `record.json`.
- `ttrpg-vault-authoring` — placement rules and the `type:` enum.
- `ttrpg-vault-rich-notes` — callouts, block IDs, embeds.
- `ttrpg-campaign-design` — consumes `state/story-state.md`; does not write here.
- `.agents/cli/session-ingest/README.md` — `view` and `chronicle` flag surface.
