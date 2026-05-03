import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "typebox";
import { runVaultFrontmatter } from "./vault-frontmatter.js";

export default function vaultFrontmatterExtension(pi: ExtensionAPI) {
  pi.registerTool({
    name: "vault_frontmatter",
    label: "Vault Frontmatter",
    description: "Read-only inspection and filtering of YAML frontmatter in vault/notes and vault/library/books Markdown files.",
    promptSnippet: "Inspect/list/filter YAML frontmatter in active notes and ingested book chapters.",
    promptGuidelines: [
      "Use vault_frontmatter as an optional metadata/facet scout for broad, thematic, or unclear vault/library searches.",
      "Use vault_frontmatter inspect instead of read/head commands when you only need metadata, title, page range, tags, type, status, source, or a tiny preview.",
      "Do not use vault_frontmatter as body-text search. vault_frontmatter only reads YAML frontmatter plus optional short preview lines.",
      "Do not treat missing tags or frontmatter from vault_frontmatter as evidence that content is absent; use qmd search/query/get or read for evidence.",
      "After vault_frontmatter find returns candidate files, read or qmd get relevant files before quoting or summarizing.",
    ],
    parameters: Type.Object({
      action: Type.String({ description: "Action: fields, values, find, or inspect." }),
      collection: Type.Optional(Type.String({ description: "Scope collection: books, notes, or all. Default all. Ignored when path is provided." })),
      book: Type.Optional(Type.String({ description: "Book slug under vault/library/books, e.g. heroes-of-horror. Scopes to that ingested book." })),
      path: Type.Optional(Type.String({ description: "Exact Markdown file or directory under vault/notes or vault/library/books. qmd://books/... and qmd://notes/... are also accepted. Overrides collection/book." })),
      field: Type.Optional(Type.String({ description: "Frontmatter field for action=values, e.g. tags, type, status, system, page_start." })),
      filters: Type.Optional(Type.Array(Type.Object({
        field: Type.String({ description: "Frontmatter field name, dotted nested path (e.g. meta.mood), or derived _path, _title, _collection, _book, _qmd_uri, _kind." }),
        op: Type.String({ description: "Predicate: exists, missing, equals, contains, matches, gte, or lte." }),
        value: Type.Optional(Type.Any({ description: "Predicate value. For equals/contains, arrays mean any listed value. matches uses a JavaScript regex string." })),
      }), { description: "Filters for action=find." })),
      match: Type.Optional(Type.String({ description: "For action=find: all filters must match ('all') or any filter may match ('any'). Default all." })),
      caseSensitive: Type.Optional(Type.Boolean({ description: "Make string comparisons and matches case-sensitive. Default false." })),
      includeGeneratedTags: Type.Optional(Type.Boolean({ description: "Include generated system/*, book/*, book-index, and toc tags in tag value listings. Default false." })),
      includePaths: Type.Optional(Type.Boolean({ description: "Include sample paths in value listings. Currently paths are included for values/find outputs." })),
      maxPathsPerValue: Type.Optional(Type.Number({ description: "Maximum sample paths per value for action=values. Default 3, max 1000." })),
      limit: Type.Optional(Type.Number({ description: "Maximum returned rows. Default 100, max 1000." })),
      previewLines: Type.Optional(Type.Number({ description: "For action=inspect only: include this many body lines after frontmatter. Default 0, max 50." })),
      output: Type.Optional(Type.String({ description: "Output mode: markdown or json. Default markdown." })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const result = await runVaultFrontmatter(params as Record<string, unknown>, ctx.cwd);
      return {
        content: [{ type: "text", text: result.text }],
        details: {
          action: result.action,
          scope: result.scope,
          scannedCount: result.scannedCount,
          frontmatterCount: result.frontmatterCount,
          parseErrorCount: result.parseErrorCount,
          matchedCount: result.matchedCount,
          returnedCount: result.returnedCount,
          truncated: result.truncated,
          results: result.results ?? result.result ?? [],
        },
      };
    },
  });
}
