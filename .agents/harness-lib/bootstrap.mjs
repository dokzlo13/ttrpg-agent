// Harness-agnostic bootstrap. All the mechanical work of getting a clone
// usable, with no conversation and no harness assumptions.
//
// The old /bootstrap prompt WAS the pi install wizard (`npm install -g
// @mariozechner/pi-coding-agent`, `~/.pi/web-search.json`, `.pi/extensions/*`
// file checks). That could not be shared, and writing one per harness would
// triplicate it. The logic lives here; the harnesses only supply conversation.

import fs from "node:fs";
import path from "node:path";

import { bash, contractEnv, run, runLoud } from "./exec.mjs";

const PASS = "PASS";
const FAIL = "FAIL";
const SKIP = "SKIP";
const LAZY = "WILL-FETCH-ON-USE";

function majorOf(version) {
  const m = version.match(/(\d+)/);
  return m ? Number.parseInt(m[1], 10) : null;
}

// --- Tier G: check and report only. NEVER install. -------------------------
function probePrereqs(manifest, report) {
  report.section("Tier G — global prerequisites (your machine; never installed by this script)");
  for (const p of manifest.prereq ?? []) {
    const res = run(p.probe[0], p.probe.slice(1));
    if (!res.ok) {
      report.row(p.optional ? SKIP : FAIL, p.name, p.optional ? "not present (optional)" : "not found", p.install_hint);
      continue;
    }
    const version = res.out.split("\n")[0];
    if (p.min) {
      const have = majorOf(version);
      if (have !== null && have < Number.parseInt(p.min, 10)) {
        report.row(FAIL, p.name, `${version} — need >= ${p.min}`, p.install_hint);
        continue;
      }
    }
    report.row(PASS, p.name, version);
  }

  const gpu = run("nvidia-smi", ["--query-gpu=name,driver_version", "--format=csv,noheader"]);
  if (gpu.ok) report.row(PASS, "gpu", gpu.out.split("\n")[0]);
  else report.row(SKIP, "gpu", "no NVIDIA GPU detected — Marker/qmd will run on CPU");
}

// --- .env scaffolding ------------------------------------------------------
const GATING = {
  OPENAI_API_KEY: "book-ingest LLM follow-ons (classify/summarize/tag) and image generation",
  TTRPG_WINDOWS_AGENT_DIR: "the ttrpg-wsl-sync conditional skill",
  FOUNDRY_MCP_PASSWORD: "the live Foundry VTT MCP integration",
};

function parseDotenvKeys(text) {
  const keys = new Map();
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const m = line.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (m) keys.set(m[1], m[2].trim());
  }
  return keys;
}

function scaffoldEnv(root, report, { apply }) {
  report.section("Project .env");
  const envPath = path.join(root, ".env");
  const examplePath = path.join(root, ".env.example");
  if (!fs.existsSync(examplePath)) {
    report.row(FAIL, ".env.example", "missing — cannot scaffold");
    return;
  }
  if (!fs.existsSync(envPath)) {
    if (apply) {
      fs.copyFileSync(examplePath, envPath);
      report.row(PASS, ".env", "created from .env.example — fill in the keys you need");
    } else {
      report.row(FAIL, ".env", "absent", "run `.agents/harness bootstrap --apply` to scaffold it");
    }
    return;
  }
  report.row(PASS, ".env", "present");

  const have = parseDotenvKeys(fs.readFileSync(envPath, "utf8"));
  const want = parseDotenvKeys(fs.readFileSync(examplePath, "utf8"));
  const missing = [...want.keys()].filter((k) => !have.has(k));
  if (missing.length) {
    report.row(SKIP, ".env keys", `${missing.length} key(s) in .env.example but not .env: ${missing.join(", ")}`);
  }
  for (const [key, why] of Object.entries(GATING)) {
    const value = have.get(key);
    if (!value) report.row(SKIP, key, `unset — disables ${why}`);
    else report.row(PASS, key, `set — enables ${why}`);
  }
}

// --- the harness's own dependencies ----------------------------------------
// The harness is a real npm package. Its deps are an explicit, reported
// bootstrap step rather than something avoided by hand-rolling parsers.
function installHarnessDeps(root, report, { apply }) {
  report.section("Harness dependencies");
  const dir = path.join(root, ".agents");
  const installed = fs.existsSync(path.join(dir, "node_modules"));
  if (installed && !apply) {
    report.row(PASS, "ttrpg-harness", "node_modules present");
    return;
  }
  if (!apply) {
    report.row(FAIL, "ttrpg-harness", "node_modules absent", "npm install --prefix .agents");
    return;
  }
  // Under the contract, so npm's cache/logs stay project-local.
  const res = run("npm", ["install", "--prefix", dir], { env: contractEnv(root) });
  if (res.ok) report.row(PASS, "ttrpg-harness", "dependencies installed");
  else report.row(FAIL, "ttrpg-harness", `install failed: ${res.out.split("\n")[0]}`);
}

// --- Tier T: one uniform per-tool loop -------------------------------------
function installTools(root, manifest, report, { apply }) {
  report.section("Tier T — per-tool isolated environments");
  for (const tool of manifest.tool ?? []) {
    if (tool.install === false) {
      report.row(SKIP, tool.name, "no dependencies of its own");
      continue;
    }
    const dir = path.join(root, tool.dir);
    if (!fs.existsSync(dir)) {
      report.row(FAIL, tool.name, `missing directory ${tool.dir}`);
      continue;
    }

    let installed;
    let cmd;
    if (tool.runtime === "python") {
      installed = fs.existsSync(path.join(dir, ".venv"));
      cmd = ["uv", ["sync", "--project", dir]];
    } else if (tool.runtime === "node") {
      installed = fs.existsSync(path.join(dir, "node_modules"));
      cmd = ["npm", ["install", "--prefix", dir]];
    } else {
      report.row(FAIL, tool.name, `unknown runtime "${tool.runtime}" — add a dispatch arm`);
      continue;
    }

    if (installed && !apply) {
      report.row(PASS, tool.name, `${tool.runtime} environment present`);
      continue;
    }
    if (!apply) {
      report.row(FAIL, tool.name, `${tool.runtime} environment absent`, `${cmd[0]} ${cmd[1].join(" ")}`);
      continue;
    }
    // uv/npm honour UV_CACHE_DIR / npm_config_cache from the environment;
    // the harness process is not itself started under the contract.
    //
    // Streamed, not captured: book-ingest pulls marker + torch + CUDA wheels
    // (~5 GB, several minutes). Swallowing that output looks like a hang.
    report.row("…", tool.name, `installing ${tool.runtime} environment${tool.notes ? " — this one is large" : ""}`);
    try {
      runLoud(cmd[0], cmd[1], { env: contractEnv(root) });
      report.row(PASS, tool.name, `${tool.runtime} environment installed`);
    } catch (error) {
      report.row(FAIL, tool.name, `install failed: ${String(error.message).split("\n")[0]}`);
    }
  }
}

// --- environment materialization -------------------------------------------
function materializeEnv(root, report, { apply }) {
  report.section("Environment contract");
  const envSh = path.join(root, ".agents/env.sh");
  if (!fs.existsSync(envSh)) {
    report.row(FAIL, "env.sh", "missing");
    return;
  }
  // Sourcing env.sh is what creates the .cache roots and the directory
  // skeleton. No symlinks are involved any more.
  const res = bash(
    'set -uo pipefail\n' +
      'source "$TTRPG_ROOT/.agents/env.sh"\n' +
      'echo "$QMD_CONFIG_DIR|$XDG_CACHE_HOME|$TTRPG_5ETOOLS_DIR|$TTRPG_QMD_BIN"',
    { vars: { TTRPG_ROOT: root } },
  );
  if (!res.ok) {
    report.row(FAIL, "env.sh", `failed to source: ${res.out.split("\n")[0]}`);
    return;
  }
  const [cfg, xdg, fivet, qmdBin] = res.out.split("\n").pop().split("|");
  report.row(PASS, "env.sh", "sourced cleanly");
  report.row(PASS, "QMD_CONFIG_DIR", cfg);
  report.row(PASS, "XDG_CACHE_HOME", xdg);
  report.row(PASS, "TTRPG_5ETOOLS_DIR", fivet);
  report.row(qmdBin ? PASS : FAIL, "qmd binary", qmdBin || "not on PATH");
}

// --- Tier L: report, never fetch -------------------------------------------
function reportLazy(root, manifest, report) {
  report.section("Tier L — fetched on first use (never pre-downloaded)");
  for (const l of manifest.lazy ?? []) {
    const abs = path.join(root, l.path);
    const present = fs.existsSync(abs) && fs.readdirSync(abs).length > 0;
    if (present) report.row(PASS, l.name, `present at ${l.path}`);
    else report.row(LAZY, l.name, `absent — ${l.size} on ${l.trigger}`);
  }
}

// --- harness detection + adapter sync --------------------------------------
function detectHarnesses(manifest, report) {
  report.section("Harnesses");
  const found = [];
  for (const [name, cfg] of Object.entries(manifest.harness ?? {})) {
    const res = run(cfg.detect[0], cfg.detect.slice(1));
    if (res.ok) {
      found.push(name);
      report.row(PASS, name, res.out.split("\n")[0]);
    } else {
      report.row(SKIP, name, "not on PATH");
    }
  }
  return found;
}

// Query outside every project so only user-level Codex configuration applies.
function codexMcpRegisteredGlobally(name) {
  return run("codex", ["mcp", "get", name], { cwd: path.parse(process.cwd()).root }).ok;
}

function reportCodexMcp(root, manifest, report, detected) {
  if (!detected.includes("codex")) return;
  report.section("Codex MCP (project-scoped)");
  const configPath = path.join(root, ".codex/config.toml");
  for (const m of manifest.mcp ?? []) {
    if (codexMcpRegisteredGlobally(m.name)) {
      report.row(
        FAIL,
        `${m.name} global isolation`,
        "still present in the user-level Codex config",
        `run from outside this repo: codex mcp remove ${m.name}`,
      );
    } else {
      report.row(PASS, `${m.name} global isolation`, "absent outside this project");
    }

    if (!fs.existsSync(configPath)) {
      report.row(FAIL, m.name, "missing .codex/config.toml", ".agents/harness sync");
      continue;
    }
    const project = run("codex", ["mcp", "get", m.name], { cwd: root });
    report.row(
      project.ok ? PASS : FAIL,
      m.name,
      project.ok ? "available from .codex/config.toml" : "not discovered from the project config",
      project.ok ? "" : ".agents/harness sync, then trust/restart Codex in this repository",
    );
  }
}

export function runBootstrap(root, manifest, { apply }, report) {
  report.header(apply ? "bootstrap --apply" : "bootstrap --check");
  probePrereqs(manifest, report);
  scaffoldEnv(root, report, { apply });
  installHarnessDeps(root, report, { apply });
  installTools(root, manifest, report, { apply });
  materializeEnv(root, report, { apply });
  reportLazy(root, manifest, report);
  const detected = detectHarnesses(manifest, report);
  reportCodexMcp(root, manifest, report, detected);
  return detected;
}
