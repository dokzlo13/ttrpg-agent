import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

const RULESET_2024_SOURCES = new Set(["XPHB", "XDMG", "XMM"]);
const VALID_ENTITY_TYPES = new Set(["creature", "spell", "item"]);
const VALID_OUTPUTS = new Set(["summary", "json", "markdown"]);
const VALID_RULESETS = new Set(["2014", "2024", "either"]);

const DATASET_DEFS = {
  creature: {
    indexPath: "data/bestiary/index.json",
    key: "monster",
  },
  spell: {
    indexPath: "data/spells/index.json",
    key: "spell",
  },
  item: {
    files: [
      { path: "data/items.json", keys: ["item"] },
      { path: "data/items-base.json", keys: ["baseitem", "itemGroup"] },
    ],
  },
};

let initPromise;
const datasetCache = new Map();

function getRepoRoot() {
  return path.resolve(process.env.TTRPG_5ETOOLS_DIR || path.join(process.cwd(), "imports/5etools"));
}

async function withRepoCwd(repoRoot, fn) {
  const previous = process.cwd();
  process.chdir(repoRoot);
  try {
    return await fn();
  } finally {
    process.chdir(previous);
  }
}

function ensureEntityType(entityType) {
  if (!VALID_ENTITY_TYPES.has(entityType)) {
    throw new Error(`Unsupported entityType: ${entityType}`);
  }
}

function ensureOutput(output) {
  if (!VALID_OUTPUTS.has(output)) {
    throw new Error(`Unsupported output mode: ${output}`);
  }
}

function ensureRuleset(preferRuleset) {
  if (!VALID_RULESETS.has(preferRuleset)) {
    throw new Error(`Unsupported preferRuleset: ${preferRuleset}`);
  }
}

function normalizeArray(value) {
  if (!value) return [];
  return Array.isArray(value)
    ? value.map(it => `${it}`.trim()).filter(Boolean)
    : [`${value}`.trim()].filter(Boolean);
}

function normalizeScalar(value) {
  if (value == null) return undefined;
  const text = `${value}`.trim();
  return text || undefined;
}

function normalizeBoolean(value) {
  return value === true;
}

function normalizeInteger(value, fallback) {
  if (value == null) return fallback;
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(1, Math.min(50, Math.floor(n)));
}

function normalizeQuery(input) {
  const entityType = normalizeScalar(input.entityType) ?? "creature";
  const output = normalizeScalar(input.output) ?? "summary";
  const preferRuleset = normalizeScalar(input.preferRuleset) ?? "either";

  ensureEntityType(entityType);
  ensureOutput(output);
  ensureRuleset(preferRuleset);

  return {
    entityType,
    name: normalizeScalar(input.name),
    source: normalizeArray(input.source),
    cr: normalizeScalar(input.cr),
    type: normalizeArray(input.type),
    size: normalizeArray(input.size),
    alignment: normalizeArray(input.alignment),
    environment: normalizeArray(input.environment),
    level: normalizeScalar(input.level),
    school: normalizeArray(input.school),
    class: normalizeArray(input.class),
    concentration: normalizeBoolean(input.concentration),
    ritual: normalizeBoolean(input.ritual),
    rarity: normalizeArray(input.rarity),
    kind: normalizeArray(input.kind),
    attunement: normalizeBoolean(input.attunement),
    limit: normalizeInteger(input.limit, 10),
    output,
    preferRuleset,
  };
}

async function readJson(filePath) {
  return JSON.parse(await fs.readFile(filePath, "utf8"));
}

async function import5etoolsModule(repoRoot, relPath) {
  const href = pathToFileURL(path.join(repoRoot, relPath)).href;
  return import(href);
}

async function initialize5etools() {
  const repoRoot = getRepoRoot();
  const dataDir = path.join(repoRoot, "data");
  try {
    await fs.access(dataDir);
  } catch {
    throw new Error(`No 5etools clone at ${repoRoot}. Clone https://github.com/5etools-mirror-3/5etools-src.git into imports/5etools/ first.`);
  }

  if (!initPromise) {
    initPromise = (async () => {
      await import5etoolsModule(repoRoot, "js/parser.js");
      await import5etoolsModule(repoRoot, "js/utils.js");
      await import5etoolsModule(repoRoot, "js/utils-ui.js");
      await import5etoolsModule(repoRoot, "js/render.js");
      await import5etoolsModule(repoRoot, "js/render-dice.js");
      await import5etoolsModule(repoRoot, "js/hist.js");
      await import5etoolsModule(repoRoot, "js/utils-dataloader.js");
      await import5etoolsModule(repoRoot, "js/utils-config.js");
      await import5etoolsModule(repoRoot, "js/filter.js");
      await import5etoolsModule(repoRoot, "js/utils-brew.js");
      await import5etoolsModule(repoRoot, "js/omnidexer.js");
      await import5etoolsModule(repoRoot, "js/render-markdown.js");
      const nodeUtil = await import5etoolsModule(repoRoot, "node/util.js");
      nodeUtil.patchLoadJson();
      return {
        repoRoot,
        Parser: globalThis.Parser,
        RendererMarkdown: globalThis.RendererMarkdown,
      };
    })();
  }

  return initPromise;
}

function withProvenance(record, sourceFile) {
  return {
    ...record,
    _provenance: {
      sourceFile,
      tool: "query_5etools",
    },
  };
}

async function loadDataset(entityType) {
  if (datasetCache.has(entityType)) return datasetCache.get(entityType);

  const env = await initialize5etools();
  const repoRoot = env.repoRoot;
  const def = DATASET_DEFS[entityType];
  const records = [];

  if (entityType === "spell") {
    const spells = await withRepoCwd(repoRoot, () => globalThis.DataUtil.spell.pLoadAll());
    for (const entry of spells) records.push(withProvenance(entry, "data/spells/*"));
    datasetCache.set(entityType, records);
    return records;
  }

  if (def.indexPath) {
    const indexAbs = path.join(repoRoot, def.indexPath);
    const index = await readJson(indexAbs);
    const baseDir = path.dirname(indexAbs);
    for (const fileName of Object.values(index)) {
      const abs = path.join(baseDir, fileName);
      const rel = path.relative(repoRoot, abs).replaceAll(path.sep, "/");
      const data = await withRepoCwd(repoRoot, () => globalThis.DataUtil.loadJSON(abs));
      const entries = Array.isArray(data?.[def.key]) ? data[def.key] : [];
      for (const entry of entries) records.push(withProvenance(entry, rel));
    }
  } else {
    for (const fileDef of def.files) {
      const abs = path.join(repoRoot, fileDef.path);
      const rel = path.relative(repoRoot, abs).replaceAll(path.sep, "/");
      const data = await withRepoCwd(repoRoot, () => globalThis.DataUtil.loadJSON(abs));
      for (const key of fileDef.keys) {
        const entries = Array.isArray(data?.[key]) ? data[key] : [];
        for (const entry of entries) records.push(withProvenance(entry, rel));
      }
    }
  }

  datasetCache.set(entityType, records);
  return records;
}

function is2024Source(source) {
  return RULESET_2024_SOURCES.has(`${source || ""}`.toUpperCase());
}

function sourceRank(source, preferRuleset) {
  if (preferRuleset === "either") return 0;
  const is2024 = is2024Source(source);
  if (preferRuleset === "2024") return is2024 ? 0 : 1;
  return is2024 ? 1 : 0;
}

function toFractionalNumber(value) {
  if (value == null) return null;
  if (typeof value === "number") return value;
  if (typeof value === "object") {
    if (typeof value.cr !== "undefined") return toFractionalNumber(value.cr);
    if (typeof value.value !== "undefined") return toFractionalNumber(value.value);
    return null;
  }
  const text = `${value}`.trim();
  if (!text) return null;
  if (text.includes("/")) {
    const [numText, denText] = text.split("/");
    const num = Number(numText);
    const den = Number(denText);
    if (Number.isFinite(num) && Number.isFinite(den) && den !== 0) return num / den;
  }
  const asNumber = Number(text);
  return Number.isFinite(asNumber) ? asNumber : null;
}

function parseRange(spec) {
  if (!spec) return null;
  if (!spec.includes("..")) {
    const value = toFractionalNumber(spec);
    return value == null ? null : { min: value, max: value };
  }
  const [minText, maxText] = spec.split("..");
  const min = minText ? toFractionalNumber(minText) : null;
  const max = maxText ? toFractionalNumber(maxText) : null;
  return { min, max };
}

function matchesRange(value, spec) {
  if (!spec) return true;
  const range = parseRange(spec);
  if (!range) return false;
  const current = toFractionalNumber(value);
  if (current == null) return false;
  if (range.min != null && current < range.min) return false;
  if (range.max != null && current > range.max) return false;
  return true;
}

function matchesText(value, expected) {
  if (!expected) return true;
  return `${value || ""}`.toLowerCase().includes(expected.toLowerCase());
}

function matchesAny(value, expectedValues) {
  if (!expectedValues.length) return true;
  const hay = `${value || ""}`.toLowerCase();
  return expectedValues.some(expected => hay === expected.toLowerCase());
}

function normalizeCreatureType(mon) {
  const type = mon.type;
  if (typeof type === "string") return type;
  return type?.type || "";
}

function normalizeCreatureSize(mon) {
  const size = Array.isArray(mon.size) ? mon.size[0] : mon.size;
  return `${size || ""}`;
}

function normalizeCreatureAlignment(mon) {
  if (!Array.isArray(mon.alignment)) return `${mon.alignment || ""}`;
  try {
    return globalThis.Parser.alignmentListToFull(mon.alignment);
  } catch {
    return mon.alignment.join(", ");
  }
}

function spellClassNames(spell) {
  const fromClassList = spell.classes?.fromClassList || [];
  const fromClassListVariant = spell.classes?.fromClassListVariant || [];
  return [...fromClassList, ...fromClassListVariant]
    .map(it => it?.name)
    .filter(Boolean);
}

function spellSchoolAliases(spell) {
  const raw = `${spell.school || ""}`;
  const values = new Set([raw.toLowerCase()]);
  try {
    values.add(globalThis.Parser.spSchoolAbvToFull(raw).toLowerCase());
  } catch {}
  return [...values].filter(Boolean);
}

function itemKinds(item) {
  if (item.wondrous) return ["wondrous"];
  if (item.staff) return ["staff"];
  if (item.poison) return ["poison"];
  if (item.ammo) return ["ammunition"];
  const typeMap = {
    A: ["armor"],
    AF: ["ammunition"],
    G: ["adventuring gear"],
    INS: ["instrument"],
    LA: ["armor"],
    M: ["melee weapon", "weapon"],
    MA: ["armor"],
    P: ["potion"],
    R: ["ranged weapon", "weapon"],
    RD: ["rod"],
    RG: ["ring"],
    S: ["shield", "armor"],
    SC: ["scroll"],
    SCF: ["spellcasting focus"],
    ST: ["staff"],
    T: ["tool"],
    TAH: ["tack and harness"],
    TG: ["trade good"],
    VEH: ["vehicle"],
    WD: ["wand"],
    W: ["weapon"],
  };
  return typeMap[item.type] || [`${item.type || ""}`.toLowerCase()];
}

function normalizeItemKind(item) {
  return itemKinds(item)[0] || "";
}

function itemRequiresAttunement(item) {
  return !!item.reqAttune;
}

function matchesRuleset(source, preferRuleset) {
  if (preferRuleset === "either") return true;
  return preferRuleset === "2024" ? is2024Source(source) : !is2024Source(source);
}

function matchesEntity(record, query) {
  if (query.name && !matchesText(record.name, query.name)) return false;
  if (query.source.length && !query.source.some(src => `${record.source || ""}`.toLowerCase() === src.toLowerCase())) return false;
  if (!matchesRuleset(record.source, query.preferRuleset)) return false;

  if (query.entityType === "creature") {
    if (!matchesRange(record.cr, query.cr)) return false;
    if (query.type.length && !query.type.some(type => normalizeCreatureType(record).toLowerCase() === type.toLowerCase())) return false;
    if (query.size.length && !query.size.some(size => normalizeCreatureSize(record).toLowerCase() === size.toLowerCase())) return false;
    if (query.alignment.length) {
      const alignment = normalizeCreatureAlignment(record).toLowerCase();
      if (!query.alignment.some(expected => alignment.includes(expected.toLowerCase()))) return false;
    }
    if (query.environment.length) {
      const environments = Array.isArray(record.environment) ? record.environment.map(it => `${it}`.toLowerCase()) : [];
      if (!query.environment.some(expected => environments.includes(expected.toLowerCase()))) return false;
    }
  }

  if (query.entityType === "spell") {
    if (!matchesRange(record.level, query.level)) return false;
    if (query.school.length) {
      const schools = spellSchoolAliases(record);
      if (!query.school.some(school => schools.includes(school.toLowerCase()))) return false;
    }
    if (query.class.length) {
      const classes = spellClassNames(record).map(it => it.toLowerCase());
      if (!query.class.some(cls => classes.includes(cls.toLowerCase()))) return false;
    }
    if (query.concentration && !record.concentration) return false;
    if (query.ritual && !record.meta?.ritual) return false;
  }

  if (query.entityType === "item") {
    if (query.rarity.length) {
      const rarity = `${record.rarity || ""}`.toLowerCase();
      if (!query.rarity.some(expected => rarity === expected.toLowerCase())) return false;
    }
    if (query.kind.length) {
      const kinds = itemKinds(record).map(it => it.toLowerCase());
      if (!query.kind.some(expected => kinds.includes(expected.toLowerCase()))) return false;
    }
    if (query.attunement && !itemRequiresAttunement(record)) return false;
  }

  return true;
}

function sortResults(results, query) {
  results.sort((a, b) => {
    const rankDiff = sourceRank(a.source, query.preferRuleset) - sourceRank(b.source, query.preferRuleset);
    if (rankDiff) return rankDiff;

    const exactA = query.name ? `${a.name || ""}`.toLowerCase() === query.name.toLowerCase() : false;
    const exactB = query.name ? `${b.name || ""}`.toLowerCase() === query.name.toLowerCase() : false;
    if (exactA !== exactB) return exactA ? -1 : 1;

    return `${a.name || ""}`.localeCompare(`${b.name || ""}`) || `${a.source || ""}`.localeCompare(`${b.source || ""}`);
  });
}

function sourceLabel(record) {
  const source = record.source || "?";
  try {
    return `${globalThis.Parser.sourceJsonToAbv(source)} — ${globalThis.Parser.sourceJsonToFull(source)}`;
  } catch {
    return `${source}`;
  }
}

function formatCr(record) {
  const cr = record?.cr;
  if (cr == null) return "?";
  if (typeof cr === "string" || typeof cr === "number") return `${cr}`;
  if (typeof cr === "object" && cr.cr != null) return `${cr.cr}`;
  return `${cr}`;
}

function formatCreatureSummary(record) {
  const typeText = (() => {
    try {
      return globalThis.Parser.monTypeToFullObj(record.type).asText;
    } catch {
      return normalizeCreatureType(record) || "creature";
    }
  })();
  const sizeText = (() => {
    try {
      return globalThis.Parser.sizeAbvToFull(normalizeCreatureSize(record));
    } catch {
      return normalizeCreatureSize(record);
    }
  })();
  const alignment = normalizeCreatureAlignment(record);
  return `- **${record.name}** (${sourceLabel(record)}; CR ${formatCr(record)}; ${sizeText} ${typeText}; ${alignment})`;
}

function formatSpellSummary(record) {
  const levelSchool = (() => {
    try {
      return globalThis.Parser.spLevelSchoolMetaToFull(record.level, record.school, record.meta, record.subschools);
    } catch {
      return `Level ${record.level ?? "?"} ${record.school || ""}`.trim();
    }
  })();
  const classes = spellClassNames(record);
  const extras = [record.concentration ? "concentration" : null, record.meta?.ritual ? "ritual" : null].filter(Boolean);
  return `- **${record.name}** (${sourceLabel(record)}; ${levelSchool}${classes.length ? `; ${classes.join(", ")}` : ""}${extras.length ? `; ${extras.join(", ")}` : ""})`;
}

function formatItemSummary(record) {
  const parts = [sourceLabel(record)];
  if (record.rarity) parts.push(`${record.rarity}`);
  const kind = normalizeItemKind(record);
  if (kind) parts.push(kind);
  if (record.reqAttune) parts.push(`attunement: ${record.reqAttune === true ? "yes" : record.reqAttune}`);
  try {
    const value = globalThis.Parser.itemValueToFull(record);
    if (value) parts.push(value);
  } catch {}
  try {
    const weight = globalThis.Parser.itemWeightToFull(record);
    if (weight) parts.push(weight);
  } catch {}
  return `- **${record.name}** (${parts.join("; ")})`;
}

function formatSummary(records, query) {
  if (!records.length) return `No ${query.entityType} matches found.`;
  const heading = `Found ${records.length} ${query.entityType}${records.length === 1 ? "" : "s"}:`;
  const lines = records.map(record => {
    if (query.entityType === "creature") return formatCreatureSummary(record);
    if (query.entityType === "spell") return formatSpellSummary(record);
    return formatItemSummary(record);
  });
  return [heading, ...lines].join("\n");
}

async function renderMarkdownRecord(record, query) {
  const { repoRoot } = await initialize5etools();
  if (query.entityType === "creature") {
    return withRepoCwd(repoRoot, () => globalThis.RendererMarkdown.exporting.pGetMarkdownDoc({ ents: [record], prop: "monster" }));
  }
  if (query.entityType === "spell") {
    return withRepoCwd(repoRoot, () => globalThis.RendererMarkdown.spell.getCompactRenderedString(record));
  }
  if (query.entityType === "item") {
    try {
      return await withRepoCwd(repoRoot, () => globalThis.RendererMarkdown.item.getCompactRenderedString(record));
    } catch {
      return formatItemSummary(record);
    }
  }
  return JSON.stringify(record, null, 2);
}

async function formatOutput(records, query) {
  if (query.output === "summary") return formatSummary(records, query);
  if (query.output === "json") return JSON.stringify(records.length === 1 ? records[0] : records, null, 2);
  const rendered = [];
  for (const record of records) rendered.push(await renderMarkdownRecord(record, query));
  return rendered.join("\n\n---\n\n");
}

export async function query5etools(rawInput) {
  const query = normalizeQuery(rawInput);
  const records = await loadDataset(query.entityType);
  const matches = records.filter(record => matchesEntity(record, query));
  sortResults(matches, query);
  const limited = matches.slice(0, query.limit);
  const text = await formatOutput(limited, query);
  return {
    query,
    totalMatches: matches.length,
    returnedCount: limited.length,
    truncated: matches.length > limited.length,
    results: limited,
    text,
  };
}
