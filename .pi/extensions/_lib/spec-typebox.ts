// Turn a canonical tool spec (.agents/cli/<tool>/spec.mjs) into pi's typebox
// parameter schema.
//
// This is the second of the tool contract's two surfaces; the first is
// .agents/cli/_lib/spec-args.mjs, which builds the argv parser from the same
// `params` object. Neither surface is generated and neither mirrors the other
// by hand, so pi's tool schema and the CLI cannot drift apart.

import { Type, type TSchema } from "typebox";

export interface ParamDef {
  type: "string" | "number" | "boolean" | "stringList" | "objectList";
  optional?: boolean;
  default?: unknown;
  describe: string;
  keys?: string[];
  optionalKeys?: string[];
  keyDescribe?: Record<string, string>;
  aliases?: string[];
  splitOnComma?: boolean;
}

export interface ToolSpec {
  name: string;
  cli: string;
  description: string;
  promptSnippet: string;
  promptGuidelines: string[];
  params: Record<string, ParamDef>;
}

function schemaFor(def: ParamDef): TSchema {
  const opts: Record<string, unknown> = { description: def.describe };
  if (def.default !== undefined) opts.default = def.default;

  switch (def.type) {
    case "number":
      return Type.Number(opts);
    case "boolean":
      return Type.Boolean(opts);
    case "stringList":
      return Type.Array(Type.String({ description: def.describe }), { description: def.describe });
    case "objectList": {
      const props: Record<string, TSchema> = {};
      for (const key of def.keys ?? []) {
        const description = def.keyDescribe?.[key] ?? key;
        const inner = Type.Any({ description });
        const isOptional = def.optionalKeys?.includes(key) ?? false;
        props[key] = isOptional
          ? Type.Optional(inner)
          : key === "value"
            ? inner
            : Type.String({ description });
      }
      return Type.Array(Type.Object(props), { description: def.describe });
    }
    default:
      return Type.String(opts);
  }
}

export function paramsToTypebox(spec: ToolSpec) {
  const props: Record<string, TSchema> = {};
  for (const [name, def] of Object.entries(spec.params)) {
    const schema = schemaFor(def);
    props[name] = def.optional ? Type.Optional(schema) : schema;
  }
  return Type.Object(props);
}
