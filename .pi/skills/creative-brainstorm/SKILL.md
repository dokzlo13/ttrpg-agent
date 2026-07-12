---
name: creative-brainstorm
description: Generate and curate several structurally distinct possibilities for open-ended creative design tasks, including plot developments, complications, encounters, mysteries, NPC motives, factions, locations, magic items, monsters, abilities, names, and alternative design approaches.
---

# Creative brainstorm

This workflow is an isolated **idea generator and curator**, not a general worker. It cannot retrieve facts, inspect the repository or vault, operate Foundry, browse, edit, write, or execute commands. Do not enable those capabilities for its internal agents.

## Main-agent responsibility

Before invocation, the main agent must do any required lookup, reading, rules retrieval, or project inspection. Supply a self-contained creative brief containing the relevant facts and only the context the creative agents should see. Include, when material:

- the creative task and desired effect;
- established context that proposals must preserve;
- hard constraints and known facts;
- unwanted patterns or already-rejected approaches;
- desired novelty level and shortlist size.

Do not use the chain to discover missing context or perform follow-up operations. After it returns possibilities, the main agent remains responsible for fact-checking, selecting or adapting an idea, editing files, and carrying out any implementation.

Apply this workflow when an open-ended brief can support several valid answers and benefits from independent mechanisms, contrasting creative lenses, or a diversity-preserving shortlist. The main agent remains the owner of the broader task and uses the curated ideas as design input.

## Invocation

1. Call `subagent({ action: "list" })`, as required before execution.
2. Confirm the project chain with `subagent({ action: "get", chainName: "creative-brainstorm", agentScope: "project" })`, then read `.pi/chains/creative-brainstorm.chain.json`.
3. Pass the file's native `chain` array to `subagent` with the user's free-form brief as `task`, `context: "fresh"`, `async: false`, `worktree: false`, and `artifacts: false`. Do not rewrite or expand the saved workflow. The two named agents are internal chain stages and must not be invoked directly.
4. Return the curator's shortlist readably; do not run a refinement loop.

Users can run the same saved chain directly with:

```text
/run-chain creative-brainstorm -- <self-contained free-form creative brief>
```

Direct callers must include all necessary context in that brief; the chain will not look anything up.
