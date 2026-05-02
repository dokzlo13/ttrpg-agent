import { Buffer } from "node:buffer";
import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

export const DEFAULT_MODEL = "gpt-image-1";
export const DEFAULT_SIZE = "1024x1024";
export const DEFAULT_QUALITY = "auto";
export const DEFAULT_OUTPUT_FORMAT = "png";
export const DEFAULT_DEST = "vault/notes/images";
export const VALID_OUTPUT_FORMATS = new Set(["png", "jpeg", "webp"]);

const ENV_KEY_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;
const SAFE_YAML_RE = /^[A-Za-z0-9_./:-]+$/;

export class ImageGenError extends Error {
  constructor(message) {
    super(message);
    this.name = "ImageGenError";
  }
}

export function parseDotenv(text) {
  const out = {};
  for (const rawLine of text.split(/\r?\n/)) {
    let line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("export ")) line = line.slice("export ".length).trim();
    const eq = line.indexOf("=");
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim();
    if (!key || !ENV_KEY_RE.test(key)) continue;
    let value = line.slice(eq + 1).trim();
    if (value.length >= 2 && value[0] === value[value.length - 1] && (value[0] === "'" || value[0] === '"')) {
      value = value.slice(1, -1);
    }
    out[key] = value;
  }
  return out;
}

export async function loadDotenvInto(env, dotenvPath) {
  let text;
  try {
    text = await fs.readFile(dotenvPath, "utf8");
  } catch (err) {
    if (err && err.code === "ENOENT") return;
    throw err;
  }
  const parsed = parseDotenv(text);
  for (const [k, v] of Object.entries(parsed)) {
    if (!(k in env)) env[k] = v;
  }
}

export async function pathExists(p) {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

export async function findProjectRoot() {
  const envRoot = process.env.TTRPG_ROOT;
  if (envRoot) return path.resolve(envRoot);

  const cwd = path.resolve(process.cwd());
  let current = cwd;
  while (true) {
    if (await pathExists(path.join(current, ".pi", "extensions", "image-gen"))) return current;
    if (
      (await pathExists(path.join(current, ".git"))) &&
      (await pathExists(path.join(current, ".env.example")))
    ) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) return cwd;
    current = parent;
  }
}

export function slugify(value, { maxLen = 56 } = {}) {
  let v = String(value).toLowerCase();
  v = v.replace(/[^a-z0-9]+/g, "-");
  v = v.replace(/^-+|-+$/g, "");
  v = v.replace(/-+/g, "-");
  if (!v) v = "generated-image";
  v = v.slice(0, maxLen).replace(/^-+|-+$/g, "");
  return v || "generated-image";
}

export function titleFromPrompt(prompt) {
  const firstLine = String(prompt).split(/\r?\n/).map(l => l.trim()).find(l => l) ?? "Generated Image";
  let head = firstLine.replace(/^(draw|create|generate|illustrate)\s+(an?|the)?\s*/i, "");
  head = head.replace(/^[\s.,:;!?"']+|[\s.,:;!?"']+$/g, "");
  if (!head) return "Generated Image";
  const words = head.split(/\s+/).slice(0, 8).join(" ");
  const title = words.replace(/\w\S*/g, w => w[0].toUpperCase() + w.slice(1).toLowerCase());
  const trimmed = title.replace(/^[\s.,:;!?"']+|[\s.,:;!?"']+$/g, "");
  return trimmed || "Generated Image";
}

export function ensureUnderImagesDir(dest, projectRoot) {
  let resolved = dest;
  if (!path.isAbsolute(resolved)) resolved = path.join(projectRoot, resolved);
  resolved = path.resolve(resolved);

  const imagesRoot = path.resolve(path.join(projectRoot, "vault", "notes", "images"));
  const rel = path.relative(imagesRoot, resolved);
  if (rel.startsWith("..") || path.isAbsolute(rel)) {
    throw new ImageGenError(
      `destination must be under vault/notes/images for Obsidian/qmd asset adoption; got ${resolved}`,
    );
  }
  return resolved;
}

export function makeRequestConfig({ prompt, title, slug, model, size, quality, outputFormat, dest }) {
  const resolvedTitle = title || titleFromPrompt(prompt);
  const resolvedSlug = slugify(slug || resolvedTitle || prompt);
  return Object.freeze({
    prompt,
    title: resolvedTitle,
    slug: resolvedSlug,
    provider: "openai",
    model,
    size,
    quality,
    outputFormat,
    dest,
  });
}

export function planAsset(config, dest, { now = () => new Date(), uuid = () => crypto.randomUUID() } = {}) {
  const created = now().toISOString().replace(/\.\d+Z$/, "Z");
  const digestSource = JSON.stringify(sortKeys(config)) + created + uuid().replace(/-/g, "");
  const shortHash = crypto.createHash("sha256").update(digestSource, "utf8").digest("hex").slice(0, 8);
  const baseName = `${config.slug}-${shortHash}`;
  const imagePath = path.join(dest, `${baseName}.${config.outputFormat}`);
  const notePath = path.join(dest, `${baseName}.md`);
  return Object.freeze({
    imagePath,
    notePath,
    markdownEmbed: `![${config.title}](${path.basename(imagePath)})`,
    created,
    request: config,
  });
}

function sortKeys(value) {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === "object") {
    const out = {};
    for (const k of Object.keys(value).sort()) out[k] = sortKeys(value[k]);
    return out;
  }
  return value;
}

export function yamlScalar(value) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return String(value);
  const text = String(value);
  if (SAFE_YAML_RE.test(text)) return text;
  return JSON.stringify(text);
}

export function jsonable(value) {
  if (value === null || value === undefined) return value;
  const t = typeof value;
  if (t === "string" || t === "number" || t === "boolean") return value;
  if (Array.isArray(value)) return value.map(jsonable);
  if (t === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[String(k)] = jsonable(v);
    return out;
  }
  return String(value);
}

export function buildAssetNote({ planned, responseMetadata, promptSource }) {
  const request = planned.request;
  const frontmatter = {
    type: "handout",
    source: "agent",
    created: planned.created.slice(0, 10),
    tags: ["campaign", "image-generation", "asset"],
    status: "draft",
    asset_kind: "image",
    image_file: path.basename(planned.imagePath),
    provider: request.provider,
    model: request.model,
    size: request.size,
    quality: request.quality,
    output_format: request.outputFormat,
  };

  const fmLines = ["---"];
  for (const [key, value] of Object.entries(frontmatter)) {
    if (Array.isArray(value)) {
      fmLines.push(`${key}: [${value.map(yamlScalar).join(", ")}]`);
    } else {
      fmLines.push(`${key}: ${yamlScalar(value)}`);
    }
  }
  fmLines.push("---");

  const params = {
    provider: request.provider,
    prompt_source: promptSource,
    api_request: {
      model: request.model,
      prompt: request.prompt,
      size: request.size,
      quality: request.quality,
      output_format: request.outputFormat,
      n: 1,
    },
    output: {
      image_file: path.basename(planned.imagePath),
      note_file: path.basename(planned.notePath),
      markdown_embed: planned.markdownEmbed,
    },
  };
  const paramsJson = JSON.stringify(params, null, 2);
  const responseJson = JSON.stringify(sortKeys(jsonable(responseMetadata) ?? {}), null, 2);

  const body = [
    "",
    `# ${request.title}`,
    "",
    planned.markdownEmbed,
    "",
    "## Prompt",
    "",
    "```text",
    request.prompt.replace(/\s+$/g, ""),
    "```",
    "",
    "## Generation Parameters",
    "",
    "```json",
    paramsJson,
    "```",
    "",
    "## Generation Result",
    "",
    "```json",
    responseJson,
    "```",
    "",
    "## Adoption Notes",
    "",
    "- Generated as a reusable visual asset.",
    "- Not yet attached to a campaign entity. When adopted, link this note from the NPC, location, scene, handout, or session note and add a relevant wikilink under Connections.",
    "",
    "## Connections",
    "",
    "- Add links here when this asset is adopted into the active campaign graph.",
    "",
    "## Sources",
    "",
    "- Generated with OpenAI Images API via `.pi/extensions/image-gen`.",
    "",
  ].join("\n");

  return fmLines.join("\n") + "\n" + body.replace(/^\n/, "");
}

export async function writeAssetNote({ planned, responseMetadata, promptSource }) {
  const text = buildAssetNote({ planned, responseMetadata, promptSource });
  await fs.writeFile(planned.notePath, text, "utf8");
}

async function callOpenAIImagesAPI({ apiKey, model, prompt, size, quality, outputFormat }) {
  const body = {
    model,
    prompt,
    size,
    quality,
    output_format: outputFormat,
    n: 1,
  };
  const res = await fetch("https://api.openai.com/v1/images/generations", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {}
    throw new ImageGenError(`OpenAI image generation failed: ${res.status} ${res.statusText}${detail ? ` - ${detail}` : ""}`);
  }
  return res.json();
}

async function fetchUrlBytes(url) {
  const res = await fetch(url);
  if (!res.ok) {
    throw new ImageGenError(`failed to fetch image url: ${res.status} ${res.statusText}`);
  }
  const buf = await res.arrayBuffer();
  return Buffer.from(buf);
}

export async function saveImageFromResponse(response, outputPath) {
  if (!response || !Array.isArray(response.data) || response.data.length === 0) {
    throw new ImageGenError("OpenAI returned no image data");
  }
  const image = response.data[0];
  let bytes;
  if (image.b64_json) {
    bytes = Buffer.from(image.b64_json, "base64");
  } else if (image.url) {
    bytes = await fetchUrlBytes(image.url);
  } else {
    throw new ImageGenError("OpenAI response contained neither b64_json nor url");
  }
  await fs.writeFile(outputPath, bytes);
  const metadata = {
    created: response.created ?? null,
    revised_prompt: image.revised_prompt ?? null,
    usage: jsonable(response.usage ?? null),
  };
  const cleaned = {};
  for (const [k, v] of Object.entries(metadata)) {
    if (v !== null && v !== undefined) cleaned[k] = v;
  }
  return cleaned;
}

function pickFirstString(...values) {
  for (const v of values) {
    if (typeof v === "string" && v.length > 0) return v;
  }
  return undefined;
}

export async function generateImage(rawParams) {
  const params = rawParams ?? {};
  const projectRoot = await findProjectRoot();
  const env = { ...process.env };
  await loadDotenvInto(env, path.join(projectRoot, ".env"));

  const subject = typeof params.subject === "string" ? params.subject : "";
  const prompt = subject.replace(/^[ \t]+/gm, "").trim();
  if (!prompt) throw new ImageGenError("subject must not be empty");

  const destText = pickFirstString(params.dest, env.TTRPG_IMAGE_OUTPUT_DIR, DEFAULT_DEST);
  const resolvedDest = ensureUnderImagesDir(destText, projectRoot);
  const config = makeRequestConfig({
    prompt,
    title: typeof params.title === "string" ? params.title : undefined,
    slug: typeof params.slug === "string" ? params.slug : undefined,
    model: pickFirstString(params.model, env.TTRPG_IMAGE_MODEL, DEFAULT_MODEL),
    size: pickFirstString(params.size, env.TTRPG_IMAGE_SIZE, DEFAULT_SIZE),
    quality: pickFirstString(params.quality, env.TTRPG_IMAGE_QUALITY, DEFAULT_QUALITY),
    outputFormat: pickFirstString(params.outputFormat, env.TTRPG_IMAGE_OUTPUT_FORMAT, DEFAULT_OUTPUT_FORMAT),
    dest: path.relative(projectRoot, resolvedDest) || ".",
  });
  if (!VALID_OUTPUT_FORMATS.has(config.outputFormat)) {
    throw new ImageGenError("output format must be one of: png, jpeg, webp");
  }

  const planned = planAsset(config, resolvedDest);
  const dryRun = params.dryRun === true;

  if (dryRun) {
    return {
      dryRun: true,
      imagePath: planned.imagePath,
      notePath: planned.notePath,
      markdownEmbed: planned.markdownEmbed,
      created: planned.created,
      request: planned.request,
      response: {},
    };
  }

  const apiKey = env.OPENAI_API_KEY;
  if (!apiKey) throw new ImageGenError("OPENAI_API_KEY is not set. Add it to .env or export it.");

  await fs.mkdir(resolvedDest, { recursive: true });
  const response = await callOpenAIImagesAPI({
    apiKey,
    model: config.model,
    prompt: config.prompt,
    size: config.size,
    quality: config.quality,
    outputFormat: config.outputFormat,
  });
  const responseMetadata = await saveImageFromResponse(response, planned.imagePath);
  await writeAssetNote({ planned, responseMetadata, promptSource: "tool" });

  return {
    dryRun: false,
    imagePath: planned.imagePath,
    notePath: planned.notePath,
    markdownEmbed: planned.markdownEmbed,
    created: planned.created,
    request: planned.request,
    response: responseMetadata,
  };
}
