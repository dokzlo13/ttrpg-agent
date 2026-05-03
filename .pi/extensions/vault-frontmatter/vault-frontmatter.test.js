import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { runVaultFrontmatter } from "./vault-frontmatter.js";

const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "vault-frontmatter-"));
const bookDir = path.join(cwd, "vault", "library", "books", "heroes-of-horror");
const notesDir = path.join(cwd, "vault", "notes", "sessions");
fs.mkdirSync(bookDir, { recursive: true });
fs.mkdirSync(notesDir, { recursive: true });

write("vault/library/books/heroes-of-horror/__heroes-of-horror.md", `---
type: book-index
book: heroes-of-horror
tags: [book-index, book/heroes-of-horror, toc]
---
# Heroes of Horror
Overview.
`);
write("vault/library/books/heroes-of-horror/01-intro.md", `---
book: heroes-of-horror
section: Introduction
section_index: 1
page_start: 1
page_end: 2
tags:
  - system/dnd-3-5e
  - book/heroes-of-horror
  - gm-advice
  - horror
meta:
  mood: dread
---
# Introduction
Body.
`);
write("vault/library/books/heroes-of-horror/02-techniques.md", `---
book: heroes-of-horror
section: Techniques of Terror
section_index: 2
page_start: 34
page_end: 34
tags: [gm-advice, encounter]
---
# Techniques of Terror
Split the party.
`);
write("vault/notes/sessions/pit.md", `---
type: session
source: agent
created: 2026-05-03
tags: [campaign, horror]
status: draft
---
# Pit
Prep.
`);
write("vault/notes/sessions/no-frontmatter.md", `# No Frontmatter
`);

function write(relPath, text) {
  const abs = path.join(cwd, relPath);
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  fs.writeFileSync(abs, text);
}

{
  const result = await runVaultFrontmatter({ action: "fields", collection: "books", book: "heroes-of-horror" }, cwd);
  assert.equal(result.scannedCount, 3);
  assert.equal(result.frontmatterCount, 3);
  assert(result.results.some((r) => r.field === "tags" && r.present === 3));
  assert.match(result.text, /Query:/);
  assert.match(result.text, /limit: 100/);
}

{
  const result = await runVaultFrontmatter({ action: "values", collection: "books", book: "heroes-of-horror", field: "tags" }, cwd);
  const values = new Map(result.results.map((r) => [r.value, r.count]));
  assert.match(result.text, /field: tags/);
  assert.match(result.text, /generated tags: hidden/);
  assert.equal(values.get("gm-advice"), 2);
  assert.equal(values.get("horror"), 1);
  assert.equal(values.has("system/dnd-3-5e"), false);
}

{
  const result = await runVaultFrontmatter({ action: "values", collection: "books", book: "heroes-of-horror", field: "tags", includeGeneratedTags: true }, cwd);
  const values = new Map(result.results.map((r) => [r.value, r.count]));
  assert.equal(values.get("system/dnd-3-5e"), 1);
}

{
  const result = await runVaultFrontmatter({
    action: "find",
    collection: "books",
    book: "heroes-of-horror",
    filters: [{ field: "tags", op: "contains", value: "encounter" }],
  }, cwd);
  assert.equal(result.matchedCount, 1);
  assert.match(result.text, /filters:/);
  assert.match(result.text, /tags contains encounter/);
  assert.equal(result.results[0].path, "vault/library/books/heroes-of-horror/02-techniques.md");
}

{
  const result = await runVaultFrontmatter({
    action: "find",
    collection: "books",
    book: "heroes-of-horror",
    filters: [{ field: "page_start", op: "gte", value: 20 }],
  }, cwd);
  assert.equal(result.matchedCount, 1);
  assert.equal(result.results[0].title, "Techniques of Terror");
}

{
  const result = await runVaultFrontmatter({
    action: "find",
    collection: "books",
    book: "heroes-of-horror",
    match: "any",
    filters: Array.from({ length: 10 }, (_, i) => ({ field: "section", op: "matches", value: `term${i}` })),
  }, cwd);
  assert.match(result.text, /… 2 more filters omitted/);
}

{
  const result = await runVaultFrontmatter({
    action: "find",
    collection: "notes",
    filters: [{ field: "status", op: "missing" }],
  }, cwd);
  assert.equal(result.matchedCount, 1, "files without frontmatter count as missing metadata");
  assert.equal(result.results[0].path, "vault/notes/sessions/no-frontmatter.md");
}

{
  const result = await runVaultFrontmatter({
    action: "find",
    collection: "books",
    book: "heroes-of-horror",
    filters: [{ field: "section", op: "matches", value: "terror|dread" }],
  }, cwd);
  assert.equal(result.matchedCount, 1);
}

{
  const result = await runVaultFrontmatter({
    action: "find",
    collection: "books",
    book: "heroes-of-horror",
    filters: [{ field: "meta.mood", op: "equals", value: "dread" }],
  }, cwd);
  assert.equal(result.matchedCount, 1);
  assert.equal(result.results[0].path, "vault/library/books/heroes-of-horror/01-intro.md");
}

{
  const result = await runVaultFrontmatter({ action: "inspect", path: "qmd://books/heroes-of-horror/02-techniques.md", previewLines: 2 }, cwd);
  assert.match(result.text, /preview lines: 2/);
  assert.equal(result.result.title, "Techniques of Terror");
  assert.equal(result.result.qmdUri, "qmd://books/heroes-of-horror/02-techniques.md");
  assert.match(result.text, /Preview:/);
}

{
  const result = await runVaultFrontmatter({ action: "inspect", path: "qmd://books/heroes-of-horror/heroes-of-horror.md" }, cwd);
  assert.equal(result.result.path, "vault/library/books/heroes-of-horror/__heroes-of-horror.md");
  assert.equal(result.result.qmdUri, "qmd://books/heroes-of-horror/heroes-of-horror.md");
}

{
  await assert.rejects(
    () => runVaultFrontmatter({ action: "inspect", path: "imports/books/foo.md" }, cwd),
    /outside allowed vault roots/,
  );
}

console.log("vault-frontmatter tests passed");
