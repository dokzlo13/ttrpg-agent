import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import {
  buildAssetNote,
  ensureUnderImagesDir,
  generateImage,
  ImageGenError,
  makeRequestConfig,
  parseDotenv,
  planAsset,
  slugify,
  titleFromPrompt,
} from "./image-gen.js";

async function makeProject() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "image-gen-test-"));
  await fs.mkdir(path.join(root, ".pi", "extensions", "image-gen"), { recursive: true });
  await fs.mkdir(path.join(root, "vault", "notes", "images"), { recursive: true });
  return root;
}

test("parseDotenv parses simple KEY=value lines, exports, and quotes", () => {
  const text = [
    "    # comment",
    "    OPENAI_API_KEY=secret",
    "    export TTRPG_IMAGE_MODEL='gpt-image-1'",
    "    BAD LINE",
    '    TTRPG_IMAGE_SIZE="1024x1024"',
  ].join("\n");
  assert.deepEqual(parseDotenv(text), {
    OPENAI_API_KEY: "secret",
    TTRPG_IMAGE_MODEL: "gpt-image-1",
    TTRPG_IMAGE_SIZE: "1024x1024",
  });
});

for (const [raw, expected] of [
  ["Old Wizard", "old-wizard"],
  ["  !!Ancient Elven Ruin??  ", "ancient-elven-ruin"],
  ["", "generated-image"],
]) {
  test(`slugify(${JSON.stringify(raw)}) === ${JSON.stringify(expected)}`, () => {
    assert.equal(slugify(raw), expected);
  });
}

test("titleFromPrompt strips action verbs and title-cases the head", () => {
  assert.equal(
    titleFromPrompt("Draw an ancient elven ruin at dawn, watercolor."),
    "Ancient Elven Ruin At Dawn, Watercolor",
  );
  assert.equal(titleFromPrompt(""), "Generated Image");
});

test("ensureUnderImagesDir rejects paths outside vault/notes/images", async () => {
  const root = await makeProject();
  try {
    assert.throws(
      () => ensureUnderImagesDir("vault/notes/other", root),
      err => err instanceof ImageGenError && /destination must be under vault\/notes\/images/.test(err.message),
    );
    const ok = ensureUnderImagesDir("vault/notes/images", root);
    assert.equal(ok, path.resolve(root, "vault/notes/images"));
  } finally {
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("planAsset is deterministic with injected time and uuid", () => {
  const config = makeRequestConfig({
    prompt: "Draw an original fantasy ruin, no text, no watermark.",
    title: "Fantasy Ruin",
    slug: "fantasy-ruin",
    model: "gpt-image-1",
    size: "1024x1024",
    quality: "auto",
    outputFormat: "png",
    dest: "vault/notes/images",
  });
  const fixedNow = () => new Date("2026-01-02T03:04:05.000Z");
  const fixedUuid = () => "00000000-0000-4000-8000-000000000000";
  const a = planAsset(config, "/tmp/out", { now: fixedNow, uuid: fixedUuid });
  const b = planAsset(config, "/tmp/out", { now: fixedNow, uuid: fixedUuid });
  assert.equal(a.imagePath, b.imagePath);
  assert.equal(a.notePath, b.notePath);
  assert.match(a.imagePath, /\/fantasy-ruin-[0-9a-f]{8}\.png$/);
  assert.match(a.notePath, /\/fantasy-ruin-[0-9a-f]{8}\.md$/);
  assert.equal(a.created, "2026-01-02T03:04:05Z");
  assert.equal(a.markdownEmbed, `![Fantasy Ruin](${path.basename(a.imagePath)})`);
});

test("buildAssetNote emits expected frontmatter, prompt, and api_request JSON", () => {
  const config = makeRequestConfig({
    prompt: "Draw an original fantasy ruin, no text, no watermark.",
    title: "Fantasy Ruin",
    slug: "fantasy-ruin",
    model: "gpt-image-1",
    size: "1024x1024",
    quality: "auto",
    outputFormat: "png",
    dest: "vault/notes/images",
  });
  const planned = planAsset(config, "/tmp/out", {
    now: () => new Date("2026-05-01T12:00:00.000Z"),
    uuid: () => "11111111-1111-4111-8111-111111111111",
  });
  const note = buildAssetNote({
    planned,
    responseMetadata: { usage: { input_tokens: 10 } },
    promptSource: "test",
  });
  assert.ok(note.startsWith("---\ntype: handout\nsource: agent"), "frontmatter prefix");
  assert.ok(note.includes("asset_kind: image"));
  assert.ok(note.includes("## Prompt"));
  assert.ok(note.includes("Draw an original fantasy ruin"));
  assert.ok(note.includes('"api_request"'));
  assert.ok(note.includes('"output"'));
  assert.ok(note.includes(path.basename(planned.imagePath)));
});

test("generateImage dry-run produces indexable paths under vault/notes/images", async () => {
  const root = await makeProject();
  const prevRoot = process.env.TTRPG_ROOT;
  process.env.TTRPG_ROOT = root;
  try {
    const result = await generateImage({
      subject: "Draw an ancient elven ruin at dawn, watercolor, no text, no watermark.",
      dryRun: true,
    });
    assert.equal(result.dryRun, true);
    assert.ok(result.imagePath.endsWith(".png"));
    assert.ok(result.notePath.endsWith(".md"));
    assert.ok(result.imagePath.includes(`${path.sep}vault${path.sep}notes${path.sep}images${path.sep}`));
    assert.equal(result.request.provider, "openai");
    const imageStat = await fs.stat(result.imagePath).catch(() => null);
    assert.equal(imageStat, null, "dry-run must not write files");
  } finally {
    if (prevRoot === undefined) delete process.env.TTRPG_ROOT;
    else process.env.TTRPG_ROOT = prevRoot;
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("generateImage rejects dest outside vault/notes/images", async () => {
  const root = await makeProject();
  const prevRoot = process.env.TTRPG_ROOT;
  process.env.TTRPG_ROOT = root;
  try {
    await assert.rejects(
      generateImage({ subject: "old wizard", dest: "vault/notes/other", dryRun: true }),
      err => err instanceof ImageGenError && /destination must be under vault\/notes\/images/.test(err.message),
    );
  } finally {
    if (prevRoot === undefined) delete process.env.TTRPG_ROOT;
    else process.env.TTRPG_ROOT = prevRoot;
    await fs.rm(root, { recursive: true, force: true });
  }
});

test("generateImage rejects empty subject", async () => {
  const root = await makeProject();
  const prevRoot = process.env.TTRPG_ROOT;
  process.env.TTRPG_ROOT = root;
  try {
    await assert.rejects(
      generateImage({ subject: "   ", dryRun: true }),
      err => err instanceof ImageGenError && /subject must not be empty/.test(err.message),
    );
  } finally {
    if (prevRoot === undefined) delete process.env.TTRPG_ROOT;
    else process.env.TTRPG_ROOT = prevRoot;
    await fs.rm(root, { recursive: true, force: true });
  }
});
