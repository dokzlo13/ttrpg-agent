# vault_frontmatter extension

Project-local Pi extension that exposes one custom tool:

- `vault_frontmatter`

## Purpose

`vault_frontmatter` is a **read-only metadata/facet helper** for the local TTRPG
vault. It parses YAML frontmatter directly from Markdown files under:

- `vault/notes/**/*.md`
- `vault/library/books/**/*.md`

It does not use qmd, does not build an index, does not maintain a cache, and never
writes files. The Markdown files themselves are the source of truth. It uses the
local `yaml` npm dependency; install it with
`npm install --prefix .pi/extensions/vault-frontmatter`.

## When to use

Use `vault_frontmatter` when frontmatter can help scope or inspect a vault/library
task:

- list available tags/fields for a book or notes collection;
- browse a named book by `tags`, `section`, `page_start`, `system`, etc.;
- find active notes by `type`, `status`, `source`, or `tags`;
- inspect a file's metadata/title/page range without a full `read`/head command;
- do lightweight metadata QA, e.g. notes missing `status`.

Treat it as an optional scout. Missing or poor metadata does **not** mean missing
content. Use qmd search/query/get or file reads for body-text evidence.

## Non-goals

- No body-text search.
- No semantic/vector search.
- No qmd query execution.
- No metadata inference or automatic tagging.
- No writes to notes or generated book artifacts.

## Actions

### `fields`

List frontmatter fields present in a scope.

```json
{
  "action": "fields",
  "collection": "books",
  "book": "heroes-of-horror"
}
```

### `values`

List values for a single field.

```json
{
  "action": "values",
  "collection": "books",
  "book": "heroes-of-horror",
  "field": "tags"
}
```

Generated tags are hidden by default in `field: "tags"` value lists:

- `system/*`
- `book/*`
- `book-index`
- `toc`

Set `includeGeneratedTags: true` to include them.

### `find`

Find files whose frontmatter matches simple predicates.

```json
{
  "action": "find",
  "collection": "books",
  "book": "heroes-of-horror",
  "filters": [
    { "field": "tags", "op": "contains", "value": "gm-advice" }
  ]
}
```

Use `match: "any"` for scout-style broad matches:

```json
{
  "action": "find",
  "collection": "books",
  "book": "heroes-of-horror",
  "match": "any",
  "filters": [
    { "field": "tags", "op": "contains", "value": "gm-advice" },
    { "field": "tags", "op": "contains", "value": "horror" },
    { "field": "tags", "op": "contains", "value": "encounter" }
  ]
}
```

Page/range examples:

```json
{
  "action": "find",
  "collection": "books",
  "book": "heroes-of-horror",
  "filters": [
    { "field": "page_start", "op": "gte", "value": 20 },
    { "field": "page_end", "op": "lte", "value": 40 }
  ]
}
```

### `inspect`

Inspect one Markdown file's frontmatter and derived metadata. `path` may be a
vault path or a qmd URI.

```json
{
  "action": "inspect",
  "path": "qmd://books/heroes-of-horror/27-techniques-of-terror.md",
  "previewLines": 5
}
```

## Scope parameters

- `collection`: `books`, `notes`, or `all` (default `all`).
- `book`: slug under `vault/library/books`, e.g. `heroes-of-horror`.
- `path`: exact file or directory under `vault/notes` or `vault/library/books`;
  overrides `collection`/`book`.

`path` also accepts:

- `qmd://books/<relative-path>` → `vault/library/books/<relative-path>`
- `qmd://notes/<relative-path>` → `vault/notes/<relative-path>`

Paths outside the allowed vault roots are rejected.

## Filter operators

| op | Meaning |
|---|---|
| `exists` | field is present and non-empty |
| `missing` | field is absent, null, empty string, empty array, or the file has no frontmatter |
| `equals` | scalar equality; arrays match exact elements |
| `contains` | string substring; arrays match exact elements |
| `matches` | JavaScript regular expression against the field's string form |
| `gte` | numeric greater-than-or-equal |
| `lte` | numeric less-than-or-equal |

String comparisons are case-insensitive by default. Set `caseSensitive: true` to
change that.

Field names are generic. New top-level frontmatter fields are immediately usable
without code changes. Dot paths also work for nested objects or arrays of objects,
for example `meta.mood` or `toc.title`.

Derived read-only fields are available in `find` filters:

- `_path`
- `_qmd_uri`
- `_title`
- `_collection`
- `_book`
- `_kind`

## Output

The tool returns concise Markdown plus structured `details` containing counts,
truncation status, parse errors, and results. Use `output: "json"` when the agent
needs machine-readable output in the text channel.

## Agent workflow guidance

Good pattern for broad thematic research:

1. Use `vault_frontmatter values` to see relevant facets/tags.
2. Use `vault_frontmatter find` to get candidate files.
3. Run qmd search/query separately if body-text relevance matters.
4. Read or `qmd get` actual files before quoting/summarizing.
5. Synthesize from evidence.

Skip frontmatter scouting for exact proper-noun lookups or canonical mechanics
queries; qmd or `query_5etools` is usually the better first tool there.
