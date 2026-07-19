# World-state reasoning

These are **reasoning distinctions, not a new schema**. Do not create fields,
registries, or duplicate notes for each class.

## Distinguish before designing

- **Played fact:** happened at the table or was directly confirmed as played.
- **Established hidden truth:** GM canon not necessarily known to players.
- **Approved preparation:** accepted future material that has not happened.
- **Source baseline:** book, archive, or Foundry material available for adaptation.
- **Proposal:** generated option awaiting a decision.
- **Open question:** uncertainty intentionally preserved until needed.

The vault's existing frontmatter, owning notes, source sections, and played
boundary remain the durable representation.

## Authority

Use campaign-specific precedence when one exists. Otherwise start with:

1. played facts and direct current user decisions;
2. active notes marked canon;
3. reviewed active notes;
4. explicit design drafts;
5. prepared Foundry material;
6. archive/module baselines;
7. new invention.

A campaign source register can override this default for named material. Foundry
may be authoritative for a played fact or mutable inventory while remaining only
a draft for future scenes. Classify the claim, not just the source.

## Current-state boundary

When the campaign has a single mutable current-state note:

- read it to locate the played boundary;
- do not advance it during campaign design;
- do not add future events to the played timeline;
- do not create a replacement state system.

## Contradictions

Report incompatible claims compactly:

```markdown
| Question | Claims | Authority | Handling |
|---|---|---|---|
| Cause of illness | Anchor / old poison draft | Active canon | Preserve Anchor; reject poison |
```

Do not average contradictions or quietly revive superseded material. Before
reporting a conflict, compare the exact scope, conditions, and authority of both
claims. Compatible claims about legal status, de facto influence, preparation,
or different triggers are not contradictions. A vague heading is a presentation
issue unless its body creates a real semantic conflict.

## Protected negatives

Preserve important “must not” statements, such as:

- one threat is not caused by another;
- an NPC does not know the hidden truth;
- normal investigation does not advance a danger;
- distant resolution is intentionally undecided;
- prepared material has not happened yet.

## Draft and canon boundary

Operational write rules live in the skill workflow (step 6); this reference
only classifies state.

- A requested durable draft may contain proposals and open questions.
- `reviewed`/`canon` owners are authoritative: nothing merges into them and
  nothing downgrades them without explicit approval of the exact change.
- Major causal changes are shown to the user before promotion.
