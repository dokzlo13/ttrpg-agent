// Structural assertions + regenerate-and-diff + the env smoke tests.
//
// Every check here exists because something less specific could not catch the
// failure it guards against. See the plan's §7 table.

import fs from "node:fs";
import path from "node:path";

import { bash, contractEnv, run } from "./exec.mjs";
import { generateAll, orphanedArtifacts, skillNames } from "./sync.mjs";

export function runVerify(root, manifest, report) {
  report.header("verify");
  let failures = 0;
  const ok = (name, detail) => report.row("PASS", name, detail);
  const bad = (name, detail, fix) => {
    failures += 1;
    report.row("FAIL", name, detail, fix);
  };

  // --- entrypoints ---------------------------------------------------------
  report.section("Tier 1 entrypoints");
  const binDir = path.join(root, ".agents/bin");
  const bins = fs.existsSync(binDir) ? fs.readdirSync(binDir).sort() : [];
  if (!bins.length) bad(".agents/bin", "no entrypoints found");
  for (const b of bins) {
    const p = path.join(binDir, b);
    const mode = fs.statSync(p).mode;
    if (!(mode & 0o111)) bad(`bin/${b}`, "not executable", `chmod +x .agents/bin/${b}`);
  }
  if (bins.length && !failures) ok(".agents/bin", `${bins.length} entrypoints, all executable`);

  // `set -e` in a launcher turns a normal non-zero probe into a silent abort.
  const withE = bins.filter((b) => /^\s*set\s+-[a-z]*e/m.test(fs.readFileSync(path.join(binDir, b), "utf8").replace(/set -uo pipefail/g, "")));
  if (withE.length) bad("no `set -e` in bin/", `found in: ${withE.join(", ")}`, "use `set -uo pipefail`");
  else ok("no `set -e` in bin/", "all entrypoints use non-errexit mode");

  const envSh = fs.readFileSync(path.join(root, ".agents/env.sh"), "utf8");
  if (/^\s*set\s+-[a-z]*e/m.test(envSh.replace(/set -uo pipefail/g, ""))) {
    bad("no `set -e` in env.sh", "errexit would abort callers on normal non-zero probes");
  } else ok("no `set -e` in env.sh", "ok");

  // --- the errexit regression guard ---------------------------------------
  // Passes on any machine where qmd IS installed unless PATH is stripped, so
  // strip it. This is the exact failure that would break a fresh clone.
  report.section("Environment contract");
  const strict = bash('set -euo pipefail\ncd "$TTRPG_ROOT"\nsource .agents/env.sh\necho OK', {
    vars: { TTRPG_ROOT: root, PATH: "/usr/bin:/bin" },
  });
  if (strict.ok && strict.out.endsWith("OK")) ok("errexit guard", "env.sh survives `set -euo pipefail` with qmd absent");
  else bad("errexit guard", `env.sh aborts under errexit: ${strict.out.split("\n").pop()}`);

  // env.sh is sourced by the harness's Bash tool, which uses the user's login
  // shell — zsh on many machines. Bashisms (`${v,,}`, `type -aP`) and, worse,
  // `local path=…` (zsh ties lowercase `path` to PATH) break Tier 2 silently.
  const zshAvailable = bash("command -v zsh >/dev/null 2>&1 && echo yes || echo no");
  if (zshAvailable.out.trim() === "yes") {
    const zsh = bash(
      'cd "$TTRPG_ROOT" && zsh -c \'source ./.agents/env.sh; qmd status\' 2>&1 | head -20',
      { vars: { TTRPG_ROOT: root } },
    );
    const noisy = /command not found|bad substitution|parse error/i.test(zsh.out);
    if (zsh.ok && !noisy) ok("zsh compatibility", "env.sh sources cleanly and `qmd` works under zsh");
    else bad("zsh compatibility", zsh.out.split("\n").find((l) => /not found|substitution|error/i.test(l)) || "failed under zsh");
  } else {
    report.row("SKIP", "zsh compatibility", "zsh not installed");
  }

  // fish is not POSIX and cannot source env.sh at all; it gets .agents/env.fish,
  // which transcribes `TTRPG_ENV_DUMP=1 bash env.sh` and delegates qmd to the
  // Tier 1 entrypoints. Assert the dump contract and the fish shim agree.
  const fishAvailable = bash("command -v fish >/dev/null 2>&1 && echo yes || echo no");
  if (fishAvailable.out.trim() === "yes") {
    const fish = bash(
      'cd "$TTRPG_ROOT" && fish -c \'source ./.agents/env.fish; echo "XDG=$XDG_CACHE_HOME"; qmd status | head -3\' 2>&1',
      { vars: { TTRPG_ROOT: root } },
    );
    const adopted = fish.out.includes(`XDG=${path.join(root, ".cache/xdg")}`);
    const worked = fish.out.includes(path.join(root, ".cache/xdg/qmd/index.sqlite"));
    if (fish.ok && adopted && worked) ok("fish compatibility", "env.fish adopts the contract and `qmd` works");
    else bad("fish compatibility", fish.out.split("\n").slice(0, 2).join(" / ") || "failed under fish");
  } else {
    report.row("SKIP", "fish compatibility", "fish not installed");
  }

  // Every variable env.sh exports must appear in its TTRPG_ENV_DUMP list, or
  // fish silently runs with a partial environment.
  const exported = bash(
    'grep -oE "^export [A-Za-z_][A-Za-z0-9_]*" "$TTRPG_ROOT/.agents/env.sh" | awk \'{print $2}\' | sort -u',
    { vars: { TTRPG_ROOT: root } },
  );
  const dumped = bash('TTRPG_ENV_DUMP=1 bash "$TTRPG_ROOT/.agents/env.sh" | cut -d= -f1 | sort -u', {
    vars: { TTRPG_ROOT: root },
  });
  if (exported.ok && dumped.ok) {
    const dumpedSet = new Set(dumped.out.split("\n").filter(Boolean));
    const missing = exported.out.split("\n").filter((k) => k && !dumpedSet.has(k));
    if (missing.length) {
      bad("env dump completeness", `exported but not in the dump list: ${missing.join(", ")}`, "add them to _ttrpg_dump_keys in env.sh");
    } else {
      ok("env dump completeness", `${dumpedSet.size} variables reach fish`);
    }
  }

  // Root resolution must not follow the cwd into another repo.
  const scratch = bash('source "$TTRPG_ROOT/.agents/env.sh"\necho "$TTRPG_ROOT|$QMD_CONFIG_DIR"', {
    vars: { TTRPG_ROOT: root },
    cwd: "/tmp",
  });
  if (scratch.ok && scratch.out.split("\n").pop().startsWith(root)) ok("root resolution", "cwd-independent");
  else bad("root resolution", `resolved outside the repo: ${scratch.out.split("\n").pop()}`);

  // The harness spawns `uv sync` / `npm install` itself, and its own process is
  // NOT started under the contract. Without contractEnv() those quietly
  // populate ~/.cache/uv and ~/.npm — bootstrap being exactly the thing that
  // must not leak. Ask the tools where they think their caches are.
  const env = contractEnv(root);
  // npm redacts "protected" config values (e.g. a path containing a UUID), so
  // trust the contract's own value and use npm only as a corroborating probe.
  const npmFromEnv = env.npm_config_cache ?? "";
  const npmCache = run("npm", ["config", "get", "cache"], { env });
  const npmReported = npmCache.ok ? npmCache.out : "";
  if (npmFromEnv.startsWith(root) && (!npmReported || npmReported.startsWith(root) || npmReported.includes("protected"))) {
    ok("npm cache isolation", npmFromEnv);
  } else {
    bad("npm cache isolation", `npm_config_cache=${npmFromEnv || "unset"} npm says ${npmReported || "?"}`, "check contractEnv() in exec.mjs");
  }

  const uvDir = env.UV_CACHE_DIR ?? "";
  if (uvDir.startsWith(root)) ok("uv cache isolation", uvDir);
  else bad("uv cache isolation", `UV_CACHE_DIR is ${uvDir || "unset"}`);

  // --- model stores --------------------------------------------------------
  // The refetch is project-local, so the isolation check would pass and
  // `qmd status` would still answer. Only a direct size check catches it.
  report.section("Expensive caches (must be non-empty BEFORE any embed)");
  for (const rel of [".cache/xdg/qmd/models", ".cache/xdg/datalab"]) {
    const abs = path.join(root, rel);
    const n = fs.existsSync(abs) ? fs.readdirSync(abs).length : 0;
    if (n > 0) ok(rel, `${n} entries`);
    else report.row("WILL-FETCH-ON-USE", rel, "empty — will be downloaded on first use");
  }

  // --- .qmd is gone --------------------------------------------------------
  if (fs.existsSync(path.join(root, ".qmd"))) {
    bad(".qmd removed", ".qmd/ still exists at the repo root");
  } else ok(".qmd removed", "no .qmd/ at the repo root");

  const symlinks = bash('find "$TTRPG_ROOT/.cache" -maxdepth 2 -type l 2>/dev/null | head', {
    vars: { TTRPG_ROOT: root },
  });
  if (symlinks.ok && !symlinks.out) ok("no cache symlinks", "XDG_CACHE_HOME points at .cache/xdg directly");
  else if (symlinks.ok) bad("no cache symlinks", symlinks.out.replace(/\n/g, ", "));

  // --- skills --------------------------------------------------------------
  report.section("Skills");
  const names = skillNames(root);
  let skillProblems = 0;
  for (const name of names) {
    const md = fs.readFileSync(path.join(root, ".agents/skills", name, "SKILL.md"), "utf8");
    const m = md.match(/^---\n([\s\S]*?)\n---/);
    if (!m) { bad(`skill ${name}`, "no frontmatter"); skillProblems += 1; continue; }
    const fm = m[1];
    const declared = fm.match(/^name:\s*(.+)$/m)?.[1]?.trim();
    if (declared !== name) { bad(`skill ${name}`, `frontmatter name is "${declared}"`); skillProblems += 1; }
    if (!/^description:/m.test(fm)) { bad(`skill ${name}`, "no description"); skillProblems += 1; }
  }
  if (!skillProblems) ok("skill frontmatter", `${names.length} skills: name matches directory, description present`);

  const linkDir = path.join(root, ".claude/skills");
  if (!fs.existsSync(linkDir)) bad(".claude/skills", "missing", ".agents/harness sync");
  else {
    let linkProblems = 0;
    for (const name of names) {
      const link = path.join(linkDir, name);
      const st = fs.lstatSync(link, { throwIfNoEntry: false });
      if (!st) { bad(`.claude/skills/${name}`, "missing symlink"); linkProblems += 1; continue; }
      if (!st.isSymbolicLink()) { bad(`.claude/skills/${name}`, "not a symlink"); linkProblems += 1; continue; }
      const target = path.resolve(linkDir, fs.readlinkSync(link));
      if (!target.startsWith(path.join(root, ".agents/skills"))) {
        bad(`.claude/skills/${name}`, `resolves outside .agents/skills: ${target}`);
        linkProblems += 1;
      } else if (!fs.existsSync(target)) {
        bad(`.claude/skills/${name}`, "dangling symlink");
        linkProblems += 1;
      }
    }
    if (!linkProblems) ok(".claude/skills", `${names.length} symlinks resolve into .agents/skills/`);
  }

  // --- MCP -----------------------------------------------------------------
  report.section("MCP");
  const mcpPath = path.join(root, ".mcp.json");
  if (!fs.existsSync(mcpPath)) bad(".mcp.json", "missing", ".agents/harness sync");
  else {
    try {
      const doc = JSON.parse(fs.readFileSync(mcpPath, "utf8"));
      const entries = Object.entries(doc.mcpServers ?? {});
      const untyped = entries.filter(([, v]) => !v.type);
      if (untyped.length) bad(".mcp.json", `entries without "type": ${untyped.map(([k]) => k).join(", ")} — Claude drops these silently`);
      else ok(".mcp.json", `parses; ${entries.length} server(s), all typed`);
    } catch (error) {
      bad(".mcp.json", `does not parse: ${error.message}`);
    }
  }

  // --- tool deps -----------------------------------------------------------
  report.section("Tool environments");
  const yamlDir = path.join(root, ".agents/cli/vault-frontmatter/node_modules/yaml");
  if (fs.existsSync(yamlDir)) ok("vault-frontmatter yaml", "installed beside the module that requires it");
  else bad("vault-frontmatter yaml", "missing", "npm install --prefix .agents/cli/vault-frontmatter");

  // --- regenerate-and-diff -------------------------------------------------
  report.section("Adapter drift");
  const userOwned = new Set(
    Object.values(manifest.harness ?? {}).flatMap((h) => h.user_owned ?? []),
  );
  const plan = generateAll(root, manifest);
  const drifted = [];
  for (const { rel, contents } of plan) {
    if (userOwned.has(rel)) continue;
    const abs = path.join(root, rel);
    const actual = fs.existsSync(abs) ? fs.readFileSync(abs, "utf8") : null;
    if (actual !== contents) drifted.push(rel);
  }
  if (drifted.length) bad("regenerate-and-diff", `${drifted.length} file(s) differ: ${drifted.join(", ")}`, ".agents/harness sync");
  else ok("regenerate-and-diff", `${plan.length} generated artifacts are up to date`);

  const orphans = orphanedArtifacts(root, plan).filter((r) => !userOwned.has(r));
  if (orphans.length) {
    bad("orphaned artifacts", `${orphans.length} generated file(s) no longer produced: ${orphans.join(", ")}`, ".agents/harness sync");
  } else {
    ok("orphaned artifacts", "no stale generated files");
  }

  for (const rel of userOwned) {
    report.row("SKIP", rel, "user-owned — never generated, never overwritten");
  }

  // --- destructive-cleanup scopes ------------------------------------------
  // This gets its own test rather than riding on the general smoke checks: it
  // is the one place where a wrong edit costs hours of re-downloading. It runs
  // each named scope's command block against a scratch root seeded with dummy
  // files in all three .cache/ classes and asserts the expensive class survives.
  report.section("Destructive-cleanup scopes");
  const scopeTest = path.join(root, ".agents/tests/cleanup-scopes.sh");
  if (!fs.existsSync(scopeTest)) {
    bad("cleanup scopes", "missing .agents/tests/cleanup-scopes.sh");
  } else {
    const res = bash('bash "$TEST"', { vars: { TEST: scopeTest } });
    const last = (res.out || "").split("\n").pop();
    if (res.ok) ok("cleanup scopes", "expensive class survives every named scope");
    else bad("cleanup scopes", last || "scope test failed", "bash .agents/tests/cleanup-scopes.sh");
  }

  // --- stale references ----------------------------------------------------
  // Scoped to the MOVED prefixes only. A blanket "zero .pi/ references" guard
  // would be unsatisfiable by design (.pi/settings.json, .pi/mcp.json,
  // .pi/extensions all legitimately survive) and would get disabled on day one.
  report.section("Stale references");
  // NOTE the granularity: `.pi/extensions/<n>/` still legitimately exists as
  // the pi tool-registration adapter — only the *implementation files* moved
  // out of it. Matching the directory would flag correct prose forever, and a
  // guard that fails on day one gets disabled.
  const movedPrefixes = [
    "\\.pi/skills/",
    "\\.pi/cli/",
    "\\.pi/scripts/",
    "\\.pi/conditional-skills\\b",
    "\\.pi/skills\\b",
    "\\.pi/cli\\b",
    "\\.pi/scripts\\b",
    // NOT listed: .pi/prompts/, .pi/chains/, .pi/agents/. Those directories
    // still exist as GENERATED output and are legitimately referenced when
    // describing where pi finds things. Authoring moved to .agents/, which the
    // do-not-edit headers and AGENTS.md enforce instead.
    "\\.pi/extensions/image-gen/image-gen\\.js",
    "\\.pi/extensions/query-5etools/query-5etools\\.js",
    "\\.pi/extensions/vault-frontmatter/vault-frontmatter\\.js",
  ].join("|");
  const stale = bash(
    'git ls-files | grep -v "^\\.trash/" | xargs grep -lE "$MOVED" 2>/dev/null | sort',
    { vars: { MOVED: movedPrefixes }, cwd: root },
  );
  const staleFiles = (stale.out || "").split("\n").filter(Boolean);
  if (staleFiles.length) bad("moved-prefix references", staleFiles.join(", "));
  else ok("moved-prefix references", "none in tracked files");

  report.section("Result");
  if (failures) report.row("FAIL", "verify", `${failures} check(s) failed`);
  else report.row("PASS", "verify", "all checks passed");
  return failures;
}
