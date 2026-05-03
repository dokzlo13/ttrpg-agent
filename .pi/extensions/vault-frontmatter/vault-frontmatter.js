import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
let YAML;

const DEFAULT_LIMIT = 100;
const DEFAULT_MAX_PATHS_PER_VALUE = 3;
const MAX_DISPLAYED_FILTERS = 8;
const GENERATED_TAGS = new Set(["book-index", "toc"]);
const SKIP_DIRS = new Set([".ingest"]);
const PATH_COLLATOR = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

export async function runVaultFrontmatter(params = {}, cwd = process.cwd()) {
  const action = params.action;
  if (!action || !["fields", "values", "find", "inspect"].includes(action)) {
    throw new Error("vault_frontmatter requires action: fields, values, find, or inspect.");
  }

  const options = normalizeOptions(params);
  const scope = resolveScope(params, cwd);
  const files = collectMarkdownFiles(scope);
  const records = [];
  const parseErrors = [];

  for (const file of files) {
    const record = readRecord(file, cwd, options);
    if (record.parseError) parseErrors.push({ path: record.path, error: record.parseError });
    records.push(record);
  }
  const frontmatterCount = records.filter((record) => record.hasFrontmatter).length;

  let payload;
  if (action === "fields") payload = actionFields(records, files.length, frontmatterCount, parseErrors, scope, options);
  else if (action === "values") payload = actionValues(records, files.length, frontmatterCount, parseErrors, scope, options, params.field);
  else if (action === "find") payload = actionFind(records, files.length, frontmatterCount, parseErrors, scope, options, params.filters ?? params.where);
  else payload = actionInspect(records, files, frontmatterCount, parseErrors, scope, options);

  payload.output = options.output;
  payload.text = options.output === "json" ? JSON.stringify(stripText(payload), null, 2) : formatMarkdown(payload);
  return payload;
}

function normalizeOptions(params) {
  return {
    collection: params.collection ?? "all",
    book: params.book,
    path: params.path,
    limit: clampPositiveInteger(params.limit, DEFAULT_LIMIT),
    output: params.output === "json" ? "json" : "markdown",
    includeGeneratedTags: params.includeGeneratedTags === true,
    includePaths: params.includePaths !== false,
    maxPathsPerValue: clampPositiveInteger(params.maxPathsPerValue, DEFAULT_MAX_PATHS_PER_VALUE),
    match: params.match === "any" ? "any" : "all",
    caseSensitive: params.caseSensitive === true,
    previewLines: clampNonNegativeInteger(params.previewLines, 0),
  };
}

function clampPositiveInteger(value, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.max(1, Math.min(Math.floor(n), 1000));
}

function clampNonNegativeInteger(value, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return fallback;
  return Math.max(0, Math.min(Math.floor(n), 50));
}

function resolveScope(params, cwd) {
  const root = path.resolve(cwd);
  const notesRoot = path.join(root, "vault", "notes");
  const booksRoot = path.join(root, "vault", "library", "books");
  const allowedRoots = [notesRoot, booksRoot];

  if (params.path) {
    const resolved = resolveVaultPath(params.path, root);
    assertInsideAllowedRoots(resolved, allowedRoots);
    return { kind: "path", cwd: root, roots: [resolved], label: rel(root, resolved), allowedRoots };
  }

  const collection = params.collection ?? "all";
  if (!["books", "notes", "all"].includes(collection)) {
    throw new Error("collection must be books, notes, or all.");
  }
  if (params.book && collection === "notes") {
    throw new Error("book can only be used with collection='books' or collection='all'.");
  }

  if (params.book) {
    const bookPath = path.join(booksRoot, params.book);
    assertInsideAllowedRoots(bookPath, allowedRoots);
    return { kind: "book", cwd: root, roots: [bookPath], label: `books/${params.book}`, allowedRoots };
  }

  const roots = [];
  if (collection === "books" || collection === "all") roots.push(booksRoot);
  if (collection === "notes" || collection === "all") roots.push(notesRoot);
  return { kind: "collection", cwd: root, collection, roots, label: collection, allowedRoots };
}

function resolveVaultPath(input, root) {
  if (input.startsWith("qmd://books/")) {
    const relPath = input.slice("qmd://books/".length);
    const direct = path.join(root, "vault", "library", "books", relPath);
    if (fs.existsSync(direct)) return direct;
    const overview = path.join(path.dirname(direct), `__${path.basename(direct)}`);
    return overview;
  }
  if (input.startsWith("qmd://notes/")) {
    return path.join(root, "vault", "notes", input.slice("qmd://notes/".length));
  }
  return path.resolve(root, input);
}

function assertInsideAllowedRoots(target, allowedRoots) {
  const resolved = path.resolve(target);
  if (!allowedRoots.some((root) => resolved === root || resolved.startsWith(root + path.sep))) {
    throw new Error(`path is outside allowed vault roots: ${target}. Allowed roots are vault/notes and vault/library/books.`);
  }
}

function collectMarkdownFiles(scope) {
  const out = [];
  for (const root of scope.roots) {
    if (!fs.existsSync(root)) continue;
    const stat = fs.statSync(root);
    if (stat.isFile()) {
      if (root.endsWith(".md")) out.push(root);
    } else if (stat.isDirectory()) {
      walk(root, out);
    }
  }
  return out.sort((a, b) => PATH_COLLATOR.compare(a, b));
}

function walk(dir, out) {
  const entries = fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => PATH_COLLATOR.compare(a.name, b.name));
  for (const entry of entries) {
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      walk(path.join(dir, entry.name), out);
    } else if (entry.isFile() && entry.name.endsWith(".md")) {
      out.push(path.join(dir, entry.name));
    }
  }
}

function getYaml() {
  if (YAML) return YAML;
  try {
    YAML = require("yaml");
    return YAML;
  } catch (error) {
    throw new Error(
      "vault_frontmatter requires the local 'yaml' npm dependency. Run: npm install --prefix .pi/extensions/vault-frontmatter",
      { cause: error },
    );
  }
}

function parseFrontmatterYaml(yamlText) {
  return getYaml().parse(yamlText);
}

function stringifyFrontmatterYaml(value) {
  return getYaml().stringify(value);
}

function readRecord(file, cwd, options) {
  const relativePath = rel(cwd, file);
  let text = "";
  try {
    text = fs.readFileSync(file, "utf8");
  } catch (error) {
    return { path: relativePath, absolutePath: file, hasFrontmatter: false, parseError: String(error?.message ?? error) };
  }

  const block = extractFrontmatterBlock(text);
  const derivedBase = deriveFromPath(file, cwd);
  if (!block) {
    return {
      ...derivedBase,
      path: relativePath,
      absolutePath: file,
      hasFrontmatter: false,
      frontmatter: {},
      title: deriveTitle({}, text, file),
      preview: makePreview(text, options.previewLines),
    };
  }

  let frontmatter = {};
  let parseError;
  try {
    const parsed = parseFrontmatterYaml(block.yaml);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) frontmatter = parsed;
    else frontmatter = {};
  } catch (error) {
    parseError = String(error?.message ?? error);
  }

  const body = text.slice(block.endIndex);
  const title = deriveTitle(frontmatter, body, file);
  return {
    ...derivedBase,
    path: relativePath,
    absolutePath: file,
    hasFrontmatter: true,
    frontmatter,
    title,
    preview: makePreview(body, options.previewLines),
    parseError,
  };
}

function extractFrontmatterBlock(text) {
  const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/);
  if (!match) return null;
  return { yaml: match[1], endIndex: match[0].length };
}

function deriveFromPath(file, cwd) {
  const root = path.resolve(cwd);
  const notesRoot = path.join(root, "vault", "notes");
  const booksRoot = path.join(root, "vault", "library", "books");
  const absolute = path.resolve(file);
  if (absolute === booksRoot || absolute.startsWith(booksRoot + path.sep)) {
    const bookRel = rel(booksRoot, absolute);
    const [book] = bookRel.split("/");
    const base = path.basename(bookRel);
    const qmdRel = base.startsWith("__") ? path.posix.join(path.posix.dirname(bookRel), base.slice(2)) : bookRel;
    return {
      collection: "books",
      book,
      qmdUri: `qmd://books/${qmdRel}`,
      kind: path.basename(file).startsWith("__") ? "book-overview" : "book-chapter",
    };
  }
  if (absolute === notesRoot || absolute.startsWith(notesRoot + path.sep)) {
    const noteRel = rel(notesRoot, absolute);
    return {
      collection: "notes",
      qmdUri: `qmd://notes/${noteRel}`,
      kind: "note",
    };
  }
  return { collection: "unknown", kind: "unknown" };
}

function deriveTitle(frontmatter, body, file) {
  const h1 = body.match(/^#\s+(.+)$/m)?.[1]?.trim();
  return stringifyScalar(frontmatter.section)
    || stringifyScalar(frontmatter.title)
    || h1
    || deslug(path.basename(file, ".md"));
}

function makePreview(body, lines) {
  if (!lines) return undefined;
  return body.split(/\r?\n/).slice(0, lines).join("\n");
}

function actionFields(records, scannedCount, frontmatterCount, parseErrors, scope, options) {
  const query = {
    collection: options.collection,
    book: options.book,
    path: options.path,
    limit: options.limit,
  };
  const fields = new Map();
  for (const record of records) {
    for (const [field, rawValue] of Object.entries(record.frontmatter)) {
      const info = fields.get(field) ?? { field, present: 0, types: new Set(), samples: [] };
      info.present += 1;
      info.types.add(typeName(rawValue));
      for (const value of flattenValueForCounts(field, rawValue, options)) {
        const sample = truncateText(value, 160);
        if (info.samples.length < 5 && !info.samples.includes(sample)) info.samples.push(sample);
      }
      fields.set(field, info);
    }
  }
  const total = records.length;
  const allResults = [...fields.values()]
    .map((info) => ({
      field: info.field,
      present: info.present,
      missing: Math.max(0, total - info.present),
      types: [...info.types].sort(),
      sampleValues: info.samples,
    }))
    .sort((a, b) => b.present - a.present || a.field.localeCompare(b.field));
  const results = allResults.slice(0, options.limit);
  return basePayload("fields", scope, scannedCount, frontmatterCount, parseErrors, {
    fieldCount: allResults.length,
    returnedCount: results.length,
    truncated: allResults.length > results.length,
    query,
    results,
  });
}

function actionValues(records, scannedCount, frontmatterCount, parseErrors, scope, options, field) {
  if (!field) throw new Error("vault_frontmatter values requires field, e.g. field: 'tags'.");
  const query = {
    collection: options.collection,
    book: options.book,
    path: options.path,
    field,
    includeGeneratedTags: options.includeGeneratedTags,
    includePaths: options.includePaths,
    maxPathsPerValue: options.maxPathsPerValue,
    limit: options.limit,
  };
  const valueMap = new Map();
  let filesWithField = 0;
  for (const record of records) {
    const rawValue = getField(record, field);
    if (isMissing(rawValue)) continue;
    filesWithField += 1;
    const values = flattenValueForCounts(field, rawValue, options);
    for (const value of values) {
      const info = valueMap.get(value) ?? { value, count: 0, paths: [] };
      info.count += 1;
      if (options.includePaths && info.paths.length < options.maxPathsPerValue) info.paths.push(record.path);
      valueMap.set(value, info);
    }
  }
  const results = [...valueMap.values()]
    .sort((a, b) => b.count - a.count || a.value.localeCompare(b.value))
    .slice(0, options.limit);
  const truncated = valueMap.size > results.length;
  return basePayload("values", scope, scannedCount, frontmatterCount, parseErrors, {
    field,
    filesWithField,
    uniqueValueCount: valueMap.size,
    returnedCount: results.length,
    truncated,
    query,
    results,
  });
}

function actionFind(records, scannedCount, frontmatterCount, parseErrors, scope, options, filters) {
  if (!Array.isArray(filters) || filters.length === 0) {
    throw new Error("vault_frontmatter find requires filters: [{ field, op, value? }].");
  }
  const compiled = filters.map((filter) => normalizeFilter(filter));
  const query = {
    collection: options.collection,
    book: options.book,
    path: options.path,
    match: options.match,
    caseSensitive: options.caseSensitive,
    filters: compiled,
    limit: options.limit,
  };
  const matched = [];
  for (const record of records) {
    const checks = compiled.map((filter) => evaluateFilter(record, filter, options));
    const ok = options.match === "any" ? checks.some((c) => c.ok) : checks.every((c) => c.ok);
    if (ok) matched.push(summarizeRecord(record, checks.filter((c) => c.ok).map((c) => c.description)));
  }
  matched.sort(compareResultRecords);
  const results = matched.slice(0, options.limit);
  return basePayload("find", scope, scannedCount, frontmatterCount, parseErrors, {
    filters: compiled,
    match: options.match,
    query,
    matchedCount: matched.length,
    returnedCount: results.length,
    truncated: matched.length > results.length,
    results,
  });
}

function actionInspect(records, files, frontmatterCount, parseErrors, scope, options) {
  const query = {
    path: options.path,
    previewLines: options.previewLines,
    limit: options.limit,
  };
  if (files.length !== 1) {
    throw new Error("vault_frontmatter inspect requires path to one Markdown file, not a directory or collection.");
  }
  const record = records[0];
  if (!record) {
    return basePayload("inspect", scope, files.length, 0, parseErrors, {
      result: null,
      query,
      message: "File has no YAML frontmatter or could not be parsed.",
    });
  }
  return basePayload("inspect", scope, files.length, frontmatterCount, parseErrors, {
    query,
    result: {
      ...summarizeRecord(record, []),
      frontmatter: record.frontmatter,
      preview: record.preview,
    },
  });
}

function basePayload(action, scope, scannedCount, frontmatterCount, parseErrors, extra) {
  return {
    action,
    scope: { label: scope.label, kind: scope.kind, collection: scope.collection, roots: scope.roots.map((p) => rel(scope.cwd, p)) },
    scannedCount,
    frontmatterCount,
    parseErrorCount: parseErrors.length,
    parseErrors,
    ...extra,
  };
}

function normalizeFilter(filter) {
  if (!filter || typeof filter !== "object") throw new Error("Each filter must be an object with field and op.");
  const { field, op, value } = filter;
  const allowed = new Set(["exists", "missing", "equals", "contains", "matches", "gte", "lte"]);
  if (!field || typeof field !== "string") throw new Error("Each filter requires a string field.");
  if (!allowed.has(op)) throw new Error(`Unsupported filter op '${op}'. Use exists, missing, equals, contains, matches, gte, or lte.`);
  if (!["exists", "missing"].includes(op) && value === undefined) {
    throw new Error(`Filter ${field} ${op} requires value.`);
  }
  return { field, op, value };
}

function evaluateFilter(record, filter, options) {
  const raw = getField(record, filter.field);
  const missing = isMissing(raw);
  let ok = false;
  if (filter.op === "exists") ok = !missing;
  else if (filter.op === "missing") ok = missing;
  else if (!missing && filter.op === "equals") ok = equalsAny(raw, filter.value, options.caseSensitive);
  else if (!missing && filter.op === "contains") ok = containsAny(raw, filter.value, options.caseSensitive);
  else if (!missing && filter.op === "matches") ok = matchesRegex(raw, filter.value, options.caseSensitive);
  else if (!missing && filter.op === "gte") ok = compareNumber(raw, filter.value, (a, b) => a >= b);
  else if (!missing && filter.op === "lte") ok = compareNumber(raw, filter.value, (a, b) => a <= b);
  return { ok, description: `${filter.field} ${filter.op}${filter.value === undefined ? "" : ` ${formatValueBrief(filter.value)}`}` };
}

function getField(record, field) {
  const derived = {
    _path: record.path,
    _qmd_uri: record.qmdUri,
    _title: record.title,
    _collection: record.collection,
    _book: record.book,
    _kind: record.kind,
  };
  if (field in derived) return derived[field];
  if (field in record.frontmatter) return record.frontmatter[field];
  if (field.includes(".")) return getNestedField(record.frontmatter, field);
  return undefined;
}

function getNestedField(value, fieldPath) {
  const parts = fieldPath.split(".").filter(Boolean);
  let current = value;
  for (const part of parts) {
    if (Array.isArray(current)) {
      const mapped = current.map((item) => item && typeof item === "object" ? item[part] : undefined).filter((item) => item !== undefined);
      current = mapped.length === 1 ? mapped[0] : mapped;
    } else if (current && typeof current === "object" && part in current) {
      current = current[part];
    } else {
      return undefined;
    }
  }
  return current;
}

function equalsAny(raw, query, caseSensitive) {
  const rawValues = Array.isArray(raw) ? raw : [raw];
  const queryValues = Array.isArray(query) ? query : [query];
  return rawValues.some((a) => queryValues.some((b) => equalScalar(a, b, caseSensitive)));
}

function containsAny(raw, query, caseSensitive) {
  const queryValues = Array.isArray(query) ? query : [query];
  if (Array.isArray(raw)) return raw.some((a) => queryValues.some((b) => equalScalar(a, b, caseSensitive)));
  const rawText = normalizeString(String(raw), caseSensitive);
  return queryValues.some((q) => rawText.includes(normalizeString(String(q), caseSensitive)));
}

function matchesRegex(raw, pattern, caseSensitive) {
  let re;
  try {
    re = new RegExp(String(pattern), caseSensitive ? "" : "i");
  } catch (error) {
    throw new Error(`Invalid matches pattern '${pattern}': ${error?.message ?? error}`);
  }
  return re.test(stringifyForMatch(raw));
}

function compareNumber(raw, query, fn) {
  const q = Number(query);
  if (!Number.isFinite(q)) return false;
  const values = Array.isArray(raw) ? raw : [raw];
  return values.some((value) => {
    const n = Number(value);
    return Number.isFinite(n) && fn(n, q);
  });
}

function equalScalar(a, b, caseSensitive) {
  if (typeof a === "number" || typeof b === "number" || typeof a === "boolean" || typeof b === "boolean") return a === b;
  return normalizeString(String(a), caseSensitive) === normalizeString(String(b), caseSensitive);
}

function normalizeString(value, caseSensitive) {
  return caseSensitive ? value : value.toLowerCase();
}

function isMissing(value) {
  return value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0);
}

function flattenValueForCounts(field, value, options) {
  const values = Array.isArray(value) ? value : [value];
  return values
    .map((item) => stringifyScalar(item))
    .filter((item) => item !== "")
    .filter((item) => field !== "tags" || options.includeGeneratedTags || !isGeneratedTag(item));
}

function isGeneratedTag(tag) {
  return GENERATED_TAGS.has(tag) || tag.startsWith("system/") || tag.startsWith("book/");
}

function typeName(value) {
  if (Array.isArray(value)) return "array";
  if (value === null) return "null";
  return typeof value;
}

function summarizeRecord(record, matched) {
  return {
    path: record.path,
    qmdUri: record.qmdUri,
    collection: record.collection,
    book: record.book,
    title: record.title,
    kind: record.kind,
    hasFrontmatter: record.hasFrontmatter,
    section: record.frontmatter.section,
    sectionIndex: record.frontmatter.section_index,
    pageStart: record.frontmatter.page_start,
    pageEnd: record.frontmatter.page_end,
    type: record.frontmatter.type,
    status: record.frontmatter.status,
    source: record.frontmatter.source,
    tags: Array.isArray(record.frontmatter.tags) ? record.frontmatter.tags : [],
    matched,
  };
}

function compareResultRecords(a, b) {
  return (a.collection ?? "").localeCompare(b.collection ?? "")
    || (a.book ?? "").localeCompare(b.book ?? "")
    || numericCompare(a.sectionIndex, b.sectionIndex)
    || numericCompare(a.pageStart, b.pageStart)
    || PATH_COLLATOR.compare(a.path, b.path);
}

function numericCompare(a, b) {
  const na = Number(a);
  const nb = Number(b);
  const fa = Number.isFinite(na);
  const fb = Number.isFinite(nb);
  if (fa && fb && na !== nb) return na - nb;
  if (fa !== fb) return fa ? -1 : 1;
  return 0;
}

function stringifyScalar(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function stringifyForMatch(value) {
  if (Array.isArray(value)) return value.map((v) => stringifyScalar(v)).join(" ");
  return stringifyScalar(value);
}

function formatValueBrief(value) {
  if (Array.isArray(value)) return `[${value.map((v) => stringifyScalar(v)).join(", ")}]`;
  return stringifyScalar(value);
}

function deslug(value) {
  return value.replace(/^__/, "").replace(/^\d+-/, "").replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function rel(base, target) {
  return path.relative(base, target).split(path.sep).join("/") || ".";
}

function stripText(payload) {
  const { text, ...rest } = payload;
  return rest;
}

function formatMarkdown(payload) {
  if (payload.action === "fields") return formatFields(payload);
  if (payload.action === "values") return formatValues(payload);
  if (payload.action === "find") return formatFind(payload);
  return formatInspect(payload);
}

function header(payload, title) {
  const lines = [
    `# ${title}`,
    "",
    `Scope: ${payload.scope.label}`,
    `Scanned: ${payload.scannedCount} markdown files; frontmatter: ${payload.frontmatterCount}; parse errors: ${payload.parseErrorCount}`,
  ];
  if (payload.query) {
    lines.push("", "Query:");
    for (const line of formatQueryLines(payload)) lines.push(line.startsWith("  - ") ? line : `- ${line}`);
  }
  if (payload.truncated) lines.push("", "Truncated: yes — narrow filters or raise limit for more results.");
  return lines.join("\n");
}

function formatQueryLines(payload) {
  const q = payload.query ?? {};
  const lines = [];
  if (q.path) lines.push(`path: ${q.path}`);
  else {
    if (q.collection) lines.push(`collection: ${q.collection}`);
    if (q.book) lines.push(`book: ${q.book}`);
  }
  if (payload.action === "values") {
    lines.push(`field: ${q.field}`);
    lines.push(`generated tags: ${q.includeGeneratedTags ? "included" : "hidden"}`);
    if (q.includePaths === false) lines.push("sample paths: hidden");
    else lines.push(`sample paths per value: ${q.maxPathsPerValue}`);
  } else if (payload.action === "find") {
    lines.push(`match: ${q.match}`);
    if (q.caseSensitive) lines.push("case-sensitive: true");
    if (q.filters?.length) {
      lines.push("filters:");
      const displayed = q.filters.slice(0, MAX_DISPLAYED_FILTERS);
      for (const filter of displayed) lines.push(`  - ${filter.field} ${filter.op}${filter.value === undefined ? "" : ` ${formatValueBrief(filter.value)}`}`);
      if (q.filters.length > displayed.length) lines.push(`  - … ${q.filters.length - displayed.length} more filters omitted`);
    }
  } else if (payload.action === "inspect") {
    lines.push(`preview lines: ${q.previewLines ?? 0}`);
  }
  if (q.limit) lines.push(`limit: ${q.limit}`);
  return lines;
}

function formatFields(payload) {
  const lines = [header(payload, "Frontmatter fields"), "", "| field | present | missing | types | sample values |", "|---|---:|---:|---|---|"];
  for (const row of payload.results) {
    lines.push(`| ${cell(row.field)} | ${row.present} | ${row.missing} | ${cell(row.types.join(", "))} | ${cell(row.sampleValues.join(", "))} |`);
  }
  if (payload.results.length === 0) lines.push("| _none_ | 0 | 0 |  |  |");
  return withErrors(lines, payload).join("\n");
}

function formatValues(payload) {
  const lines = [header(payload, `Values for \`${payload.field}\``), "", `Files with field: ${payload.filesWithField}; unique values: ${payload.uniqueValueCount}; returned: ${payload.returnedCount}`, "", "| value | count | sample paths |", "|---|---:|---|"];
  for (const row of payload.results) {
    lines.push(`| ${cell(row.value)} | ${row.count} | ${cell(row.paths.join(", "))} |`);
  }
  if (payload.results.length === 0) lines.push("| _none_ | 0 |  | ");
  return withErrors(lines, payload).join("\n");
}

function formatFind(payload) {
  const lines = [header(payload, "Frontmatter matches"), "", `Match mode: ${payload.match}; matched: ${payload.matchedCount}; returned: ${payload.returnedCount}`, "", "| path | title | page | matched | tags |", "|---|---|---:|---|---|"];
  for (const row of payload.results) {
    const page = row.pageStart === undefined ? "" : row.pageEnd && row.pageEnd !== row.pageStart ? `${row.pageStart}-${row.pageEnd}` : String(row.pageStart);
    const tags = row.tags.filter((tag) => !isGeneratedTag(tag)).join(", ");
    lines.push(`| ${cell(row.path)} | ${cell(row.title)} | ${cell(page)} | ${cell(row.matched.join("; "))} | ${cell(tags)} |`);
  }
  if (payload.results.length === 0) lines.push("| _none_ |  |  |  |  |");
  return withErrors(lines, payload).join("\n");
}

function formatInspect(payload) {
  const result = payload.result;
  const lines = [header(payload, "Frontmatter inspect")];
  if (!result) {
    lines.push("", payload.message ?? "No frontmatter found.");
    return withErrors(lines, payload).join("\n");
  }
  lines.push("", `Path: ${result.path}`, `QMD URI: ${result.qmdUri ?? ""}`, `Title: ${result.title}`, `Kind: ${result.kind}`);
  if (result.pageStart !== undefined) lines.push(`Page: ${result.pageEnd && result.pageEnd !== result.pageStart ? `${result.pageStart}-${result.pageEnd}` : result.pageStart}`);
  lines.push("", "```yaml", stringifyFrontmatterYaml(result.frontmatter).trimEnd(), "```");
  if (result.preview) lines.push("", "Preview:", "```markdown", result.preview.trimEnd(), "```");
  return withErrors(lines, payload).join("\n");
}

function withErrors(lines, payload) {
  if (payload.parseErrors.length) {
    lines.push("", "## Parse errors", "");
    for (const err of payload.parseErrors.slice(0, 5)) lines.push(`- ${err.path}: ${err.error}`);
    if (payload.parseErrors.length > 5) lines.push(`- ... ${payload.parseErrors.length - 5} more`);
  }
  return lines;
}

function truncateText(value, maxLength) {
  const text = String(value ?? "");
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(0, maxLength - 1))}…`;
}

function cell(value) {
  return truncateText(value, 260).replace(/\|/g, "\\|").replace(/\n/g, " ");
}
