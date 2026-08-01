// Canonical agent-facing parameter spec for vault_frontmatter / vault-frontmatter.
//
// Note `filters`: it is Array<{field, op, value}> and therefore cannot be a flat
// scalar flag. It is declared `objectList`, which the CLI exposes as a
// repeatable `--filter 'field:op:value'` plus `--filters-json '[...]'` for the
// cases where a value contains characters that make the compact form awkward.

export const spec = {
  name: "vault_frontmatter",
  cli: "vault-frontmatter",
  description:
    "Read-only inspection and filtering of YAML frontmatter in vault/notes and vault/library/books Markdown files.",
  promptSnippet:
    "Inspect/list/filter YAML frontmatter in active notes and ingested book chapters.",
  promptGuidelines: [
    "Use vault_frontmatter as an optional metadata/facet scout for broad, thematic, or unclear vault/library searches.",
    "Use vault_frontmatter inspect instead of read/head commands when you only need metadata, title, page range, tags, type, status, source, or a tiny preview.",
    "Do not use vault_frontmatter as body-text search. vault_frontmatter only reads YAML frontmatter plus optional short preview lines.",
    "Do not treat missing tags or frontmatter from vault_frontmatter as evidence that content is absent; use qmd search/query/get or read for evidence.",
    "After vault_frontmatter find returns candidate files, read or qmd get relevant files before quoting or summarizing.",
  ],
  params: {
    action: { type: "string", describe: "Action: fields, values, find, or inspect." },
    collection: {
      type: "string",
      optional: true,
      describe: "Scope collection: books, notes, or all. Default all. Ignored when path is provided.",
    },
    book: {
      type: "string",
      optional: true,
      describe: "Book slug under vault/library/books, e.g. heroes-of-horror. Scopes to that ingested book.",
    },
    path: {
      type: "string",
      optional: true,
      describe:
        "Exact Markdown file or directory under vault/notes or vault/library/books. qmd://books/... and qmd://notes/... are also accepted. Overrides collection/book.",
    },
    field: {
      type: "string",
      optional: true,
      describe: "Frontmatter field for action=values, e.g. tags, type, status, system, page_start.",
    },
    filters: {
      type: "objectList",
      optional: true,
      keys: ["field", "op", "value"],
      optionalKeys: ["value"],
      aliases: ["--filter"],
      describe: "Filters for action=find.",
      keyDescribe: {
        field:
          "Frontmatter field name, dotted nested path (e.g. meta.mood), or derived _path, _title, _collection, _book, _qmd_uri, _kind.",
        op: "Predicate: exists, missing, equals, contains, matches, gte, or lte.",
        value:
          "Predicate value. For equals/contains, arrays mean any listed value. matches uses a JavaScript regex string.",
      },
    },
    match: {
      type: "string",
      optional: true,
      describe: "For action=find: all filters must match ('all') or any filter may match ('any'). Default all.",
    },
    caseSensitive: {
      type: "boolean",
      optional: true,
      describe: "Make string comparisons and matches case-sensitive. Default false.",
    },
    includeGeneratedTags: {
      type: "boolean",
      optional: true,
      describe: "Include generated system/*, book/*, book-index, and toc tags in tag value listings. Default false.",
    },
    includePaths: {
      type: "boolean",
      optional: true,
      describe: "Include sample paths in value listings. Currently paths are included for values/find outputs.",
    },
    maxPathsPerValue: {
      type: "number",
      optional: true,
      describe: "Maximum sample paths per value for action=values. Default 3, max 1000.",
    },
    limit: { type: "number", optional: true, describe: "Maximum returned rows. Default 100, max 1000." },
    previewLines: {
      type: "number",
      optional: true,
      describe: "For action=inspect only: include this many body lines after frontmatter. Default 0, max 50.",
    },
    output: { type: "string", optional: true, describe: "Output mode: markdown or json. Default markdown." },
  },
};
