// Canonical agent-facing parameter spec for query_5etools / query-5etools.
//
// Single source of truth. .agents/cli/query-5etools/cli.mjs builds its argv
// parser from `params`; .pi/extensions/query-5etools/index.ts builds its
// typebox schema from the same object. Nothing here is generated.

export const spec = {
  name: "query_5etools",
  cli: "query-5etools",
  description:
    "Structured queries over the local 5etools mirror for creatures, spells, and items.",
  promptSnippet:
    "Query the local 5etools mirror for structured creature, spell, and item lookups.",
  promptGuidelines: [
    "Use query_5etools for canonical 5e creature/spell/item filters before using qmd prose search.",
    "Use query_5etools with output='summary' for candidate lists, output='markdown' for rendered statblocks or spell/item text, and output='json' for raw records.",
    "If query_5etools cannot express an unusual 5etools task, read the ttrpg-rules-5etools-native skill and use bash/node against $TTRPG_5ETOOLS_DIR directly.",
  ],
  params: {
    entityType: {
      type: "string",
      optional: true,
      default: "creature",
      describe: "Entity type: creature, spell, or item.",
    },
    name: { type: "string", optional: true, describe: "Case-insensitive substring match on entity name." },
    source: {
      type: "stringList",
      optional: true,
      describe: "5etools source code, e.g. MM, XMM, PHB, XPHB.",
    },
    cr: { type: "string", optional: true, describe: "Creature CR or range, e.g. '5', '1/2', '5..7', '..3'." },
    type: { type: "stringList", optional: true, describe: "Creature type filter, e.g. fey, undead." },
    size: { type: "stringList", optional: true, describe: "Creature size filter, e.g. small, medium, large." },
    alignment: {
      type: "stringList",
      optional: true,
      splitOnComma: false,
      describe: "Creature alignment text filter, e.g. lawful good, neutral evil.",
    },
    environment: { type: "stringList", optional: true, describe: "Creature environment filter, e.g. forest, desert." },
    level: { type: "string", optional: true, describe: "Spell level or range, e.g. '3' or '0..3'." },
    school: {
      type: "stringList",
      optional: true,
      describe: "Spell school abbreviation or name, e.g. V, evo, evocation.",
    },
    class: { type: "stringList", optional: true, describe: "Spell class filter, e.g. wizard, cleric." },
    concentration: { type: "boolean", optional: true, describe: "Require concentration spells." },
    ritual: { type: "boolean", optional: true, describe: "Require ritual spells." },
    rarity: { type: "stringList", optional: true, describe: "Item rarity filter, e.g. common, rare, legendary." },
    kind: {
      type: "stringList",
      optional: true,
      describe: "Item kind filter, e.g. weapon, armor, potion, ring, wondrous.",
    },
    attunement: { type: "boolean", optional: true, describe: "Require attunement." },
    output: {
      type: "string",
      optional: true,
      default: "summary",
      describe: "Output mode: summary, json, or markdown.",
    },
    limit: { type: "number", optional: true, default: 10, describe: "Max number of returned results. Default 10." },
    preferRuleset: {
      type: "string",
      optional: true,
      default: "either",
      describe: "Ruleset preference: 2014, 2024, or either.",
    },
  },
};
