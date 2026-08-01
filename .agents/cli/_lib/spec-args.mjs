// Build an argv parser from a tool's `spec.params`.
//
// One spec, two surfaces: this drives the CLI (.agents/cli/<tool>/cli.mjs) while
// .pi/extensions/<tool>/index.ts builds its typebox schema from the very same
// object. Neither is generated and neither mirrors the other by hand, so the
// agent-facing parameter set cannot drift between harnesses.
//
// Supported param types:
//   string      --flag <value>
//   number      --flag <number>
//   boolean     --flag        (and --no-flag)
//   stringList  --flag <v>    repeatable, or comma-separated
//   objectList  --flag 'a:b:c' repeatable, plus --flag-json '<json array>'
//               (a flat scalar flag cannot express Array<{field,op,value}>)

export function flagFor(name) {
  return `--${name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`;
}

function fail(message) {
  const error = new Error(message);
  error.usageError = true;
  throw error;
}

function parseNumber(flag, raw) {
  const value = Number(raw);
  if (!Number.isFinite(value)) fail(`${flag} expects a number, got: ${raw}`);
  return value;
}

// objectList entries arrive as `field:op:value`. Only the first two colons are
// separators so a value may itself contain colons (e.g. a qmd:// URI).
function parseObjectEntry(flag, raw, keys) {
  const parts = [];
  let rest = raw;
  for (let i = 0; i < keys.length - 1; i += 1) {
    const idx = rest.indexOf(":");
    if (idx === -1) break;
    parts.push(rest.slice(0, idx));
    rest = rest.slice(idx + 1);
  }
  parts.push(rest);
  if (parts.length < 2) {
    fail(`${flag} expects '${keys.join(":")}', got: ${raw}`);
  }
  const out = {};
  keys.forEach((key, i) => {
    if (parts[i] !== undefined && parts[i] !== "") out[key] = parts[i];
  });
  return out;
}

export function parseArgv(spec, argv) {
  const byFlag = new Map();
  for (const [name, def] of Object.entries(spec.params)) {
    byFlag.set(flagFor(name), { name, def });
    if (def.type === "boolean") {
      byFlag.set(`--no-${flagFor(name).slice(2)}`, { name, def, negated: true });
    }
    if (def.type === "objectList") {
      byFlag.set(`${flagFor(name)}-json`, { name, def, json: true });
    }
    for (const alias of def.aliases ?? []) {
      byFlag.set(alias, { name, def });
    }
  }

  const out = {};
  const positional = [];

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") return { help: true, values: out };
    if (arg === "--") {
      positional.push(...argv.slice(i + 1));
      break;
    }
    if (!arg.startsWith("--")) {
      positional.push(arg);
      continue;
    }

    // Accept both `--flag value` and `--flag=value`.
    let token = arg;
    let inlineValue;
    const eq = arg.indexOf("=");
    if (eq !== -1) {
      token = arg.slice(0, eq);
      inlineValue = arg.slice(eq + 1);
    }

    const hit = byFlag.get(token);
    if (!hit) fail(`unknown option: ${token}`);
    const { name, def } = hit;

    if (def.type === "boolean") {
      // `--dry-run=false` must mean false. Ignoring the inline value silently
      // inverted the caller's intent — on --dry-run that means really spending
      // money, on --concentration it means the opposite filter.
      if (inlineValue !== undefined) {
        const v = inlineValue.toLowerCase();
        if (["true", "1", "yes", "on"].includes(v)) out[name] = !hit.negated;
        else if (["false", "0", "no", "off"].includes(v)) out[name] = !!hit.negated;
        else fail(`${token} expects true/false (or no value at all), got: ${inlineValue}`);
      } else {
        out[name] = !hit.negated;
      }
      continue;
    }

    const raw = inlineValue !== undefined ? inlineValue : argv[++i];
    if (raw === undefined) fail(`${token} expects a value`);

    if (hit.json) {
      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch (error) {
        fail(`${token} expects a JSON array: ${error.message}`);
      }
      if (!Array.isArray(parsed)) fail(`${token} expects a JSON array`);
      out[name] = [...(out[name] ?? []), ...parsed];
      continue;
    }

    switch (def.type) {
      case "number":
        out[name] = parseNumber(token, raw);
        break;
      case "stringList":
        out[name] = [
          ...(out[name] ?? []),
          ...(def.splitOnComma === false ? [raw] : raw.split(",").filter(Boolean)),
        ];
        break;
      case "objectList":
        out[name] = [...(out[name] ?? []), parseObjectEntry(token, raw, def.keys)];
        break;
      default:
        out[name] = raw;
    }
  }

  return { help: false, values: out, positional };
}

export function usage(spec) {
  const lines = [
    `${spec.cli} — ${spec.description}`,
    "",
    "Options:",
  ];
  for (const [name, def] of Object.entries(spec.params)) {
    const flag = flagFor(name);
    let form = flag;
    if (def.type === "stringList") form = `${flag} <v>        (repeatable)`;
    else if (def.type === "objectList") form = `${flag} <${def.keys.join(":")}>  (repeatable; or ${flag}-json '[...]')`;
    else if (def.type === "boolean") form = `${flag} / --no-${flag.slice(2)}`;
    else if (def.type === "number") form = `${flag} <n>`;
    else form = `${flag} <v>`;

    const bits = [def.describe];
    if (def.default !== undefined) bits.push(`Default ${JSON.stringify(def.default)}.`);
    lines.push(`  ${form.padEnd(46)} ${bits.join(" ")}`);
  }
  lines.push("", "  --help, -h                                     Show this message.");
  return lines.join("\n");
}

// Shared entrypoint wiring: parse, print usage/errors sanely, emit the result.
export async function runCli(spec, argv, invoke) {
  let parsed;
  try {
    parsed = parseArgv(spec, argv);
  } catch (error) {
    if (error.usageError) {
      process.stderr.write(`${spec.cli}: ${error.message}\n\n${usage(spec)}\n`);
      process.exitCode = 2;
      return;
    }
    throw error;
  }

  if (parsed.help) {
    process.stdout.write(`${usage(spec)}\n`);
    return;
  }

  try {
    const text = await invoke(parsed.values, parsed.positional ?? []);
    if (text) process.stdout.write(text.endsWith("\n") ? text : `${text}\n`);
  } catch (error) {
    process.stderr.write(`${spec.cli}: ${error?.message ?? error}\n`);
    process.exitCode = 1;
  }
}
