import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "typebox";
import { query5etools } from "./query-5etools.js";

export default function query5etoolsExtension(pi: ExtensionAPI) {
  pi.registerTool({
    name: "query_5etools",
    label: "Query 5etools",
    description: "Structured queries over the local 5etools mirror for creatures, spells, and items.",
    promptSnippet: "Query the local 5etools mirror for structured creature, spell, and item lookups.",
    promptGuidelines: [
      "Use query_5etools for canonical 5e creature/spell/item filters before using qmd prose search.",
      "Use query_5etools with output='summary' for candidate lists, output='markdown' for rendered statblocks or spell/item text, and output='json' for raw records.",
      "If query_5etools cannot express an unusual 5etools task, read the ttrpg-rules-5etools-native skill and use bash/node against imports/5etools directly.",
    ],
    parameters: Type.Object({
      entityType: Type.String({
        description: "Entity type: creature, spell, or item.",
        default: "creature",
      }),
      name: Type.Optional(Type.String({ description: "Case-insensitive substring match on entity name." })),
      source: Type.Optional(Type.Array(Type.String({ description: "5etools source code, e.g. MM, XMM, PHB, XPHB." }))),
      cr: Type.Optional(Type.String({ description: "Creature CR or range, e.g. '5', '1/2', '5..7', '..3'." })),
      type: Type.Optional(Type.Array(Type.String({ description: "Creature type filter, e.g. fey, undead." }))),
      size: Type.Optional(Type.Array(Type.String({ description: "Creature size filter, e.g. small, medium, large." }))),
      alignment: Type.Optional(Type.Array(Type.String({ description: "Creature alignment text filter, e.g. lawful good, neutral evil." }))),
      environment: Type.Optional(Type.Array(Type.String({ description: "Creature environment filter, e.g. forest, desert." }))),
      level: Type.Optional(Type.String({ description: "Spell level or range, e.g. '3' or '0..3'." })),
      school: Type.Optional(Type.Array(Type.String({ description: "Spell school abbreviation or name, e.g. V, evo, evocation." }))),
      class: Type.Optional(Type.Array(Type.String({ description: "Spell class filter, e.g. wizard, cleric." }))),
      concentration: Type.Optional(Type.Boolean({ description: "Require concentration spells." })),
      ritual: Type.Optional(Type.Boolean({ description: "Require ritual spells." })),
      rarity: Type.Optional(Type.Array(Type.String({ description: "Item rarity filter, e.g. common, rare, legendary." }))),
      kind: Type.Optional(Type.Array(Type.String({ description: "Item kind filter, e.g. weapon, armor, potion, ring, wondrous." }))),
      attunement: Type.Optional(Type.Boolean({ description: "Require attunement." })),
      output: Type.Optional(Type.String({ description: "Output mode: summary, json, or markdown.", default: "summary" })),
      limit: Type.Optional(Type.Number({ description: "Max number of returned results. Default 10.", default: 10 })),
      preferRuleset: Type.Optional(Type.String({ description: "Ruleset preference: 2014, 2024, or either.", default: "either" })),
    }),
    async execute(_toolCallId, params) {
      const result = await query5etools(params as Record<string, unknown>);
      return {
        content: [{ type: "text", text: result.text }],
        details: {
          query: result.query,
          totalMatches: result.totalMatches,
          returnedCount: result.returnedCount,
          truncated: result.truncated,
          results: result.results,
        },
      };
    },
  });
}
