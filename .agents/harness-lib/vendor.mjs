// Pinned upstream checkouts: one declarative pin per vendor in manifest.toml,
// one command to manage them. Replaces the ref that used to be buried in
// foundry-mcp/install.sh and the entirely unmanaged 5etools clone.
//
// Tier L: `sync` is explicit and user-initiated. Bootstrap only ever reports
// "not present, ~N on first use" — a fresh clone must be usable without a
// multi-gigabyte download.

import fs from "node:fs";
import path from "node:path";

import { bash, run, runLoud } from "./exec.mjs";

function git(dir, args) {
  const res = run("git", ["-C", dir, ...args]);
  if (!res.ok) throw new Error(res.out.split("\n")[0]);
  return res.out;
}

function findVendor(manifest, name) {
  const v = (manifest.vendor ?? []).find((x) => x.name === name);
  if (!v) {
    const known = (manifest.vendor ?? []).map((x) => x.name).join(", ");
    throw new Error(`unknown vendor "${name}". Known: ${known}`);
  }
  return v;
}

export function vendorStatus(root, manifest, report) {
  report.section("Vendored checkouts");
  for (const v of manifest.vendor ?? []) {
    const dir = path.join(root, v.dest);
    if (!fs.existsSync(path.join(dir, ".git"))) {
      report.row("WILL-FETCH-ON-USE", v.name, `absent — ${v.size_hint ?? "unknown size"}`, `.agents/harness vendor sync ${v.name}`);
      continue;
    }
    let head;
    try {
      head = git(dir, ["rev-parse", "HEAD"]);
    } catch (error) {
      report.row("FAIL", v.name, `not a readable git checkout: ${error.message.split("\n")[0]}`);
      continue;
    }
    let pinned = null;
    try {
      pinned = git(dir, ["rev-parse", `${v.ref}^{commit}`]);
    } catch {
      // The pinned ref may simply not be fetched yet.
    }
    const short = (s) => (s ? s.slice(0, 8) : "?");
    if (pinned && pinned === head) {
      report.row("PASS", v.name, `at pinned ${v.ref} (${short(head)})`);
    } else if (pinned) {
      report.row("FAIL", v.name, `HEAD ${short(head)} != pinned ${v.ref} (${short(pinned)})`, `.agents/harness vendor sync ${v.name}`);
    } else {
      report.row("SKIP", v.name, `HEAD ${short(head)}; pinned ref ${v.ref} not fetched`, `.agents/harness vendor sync ${v.name}`);
    }
  }
}

export function vendorSync(root, manifest, name, report) {
  const v = findVendor(manifest, name);
  const dir = path.join(root, v.dest);
  report.section(`vendor sync ${v.name}`);

  if (!fs.existsSync(path.join(dir, ".git"))) {
    report.row("…", v.name, `cloning ${v.repo} → ${v.dest}`);
    fs.rmSync(dir, { recursive: true, force: true });
    fs.mkdirSync(path.dirname(dir), { recursive: true });
    runLoud("git", ["clone", v.repo, dir]);
  }

  runLoud("git", ["-C", dir, "fetch", "--quiet", "origin", v.ref]);
  runLoud("git", ["-C", dir, "checkout", "--quiet", "--detach", v.ref]);
  report.row("PASS", v.name, `checked out ${v.ref} (${git(dir, ["rev-parse", "HEAD"]).slice(0, 8)})`);

  if (v.build) {
    report.row("…", v.name, `building: ${v.build}`);
    // Run under the env contract so npm's cache stays project-local.
    bash(
      'set -uo pipefail\nsource "$TTRPG_ROOT/.agents/env.sh"\ncd "$VENDOR_DIR"\n' + v.build,
      { vars: { TTRPG_ROOT: root, VENDOR_DIR: dir }, inherit: true },
    );
    report.row("PASS", v.name, "build complete");
  }
}

// Moving a pin is explicit and user-initiated, never automatic: a 5etools bump
// changes rules data underneath the user's campaign notes.
export function vendorUpdate(root, manifest, name, ref, report) {
  const v = findVendor(manifest, name);
  const manifestPath = path.join(root, ".agents/manifest.toml");
  const text = fs.readFileSync(manifestPath, "utf8");

  // Rewrite only inside THIS vendor's own block. A lazy [\s\S]*? between the
  // name and `ref =` happily walks into the next [[vendor]] block when the
  // target block has no ref of its own, silently repinning a different vendor.
  const blocks = [...text.matchAll(/\[\[vendor\]\]/g)].map((m) => m.index);
  const start = blocks.find((i) => {
    const end = blocks.find((j) => j > i) ?? text.length;
    return new RegExp(`name\\s*=\\s*"${v.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`).test(
      text.slice(i, end),
    );
  });
  if (start === undefined) throw new Error(`no [[vendor]] block named "${v.name}" in manifest.toml`);
  const end = blocks.find((j) => j > start) ?? text.length;
  const block = text.slice(start, end);
  const refRe = /(^[ \t]*ref[ \t]*=[ \t]*")([^"]*)(")/m;
  if (!refRe.test(block)) {
    throw new Error(`[[vendor]] "${v.name}" has no ref pin to update; add one to manifest.toml first`);
  }
  // Function replacer: a ref containing $&, $\` or $1 would otherwise be
  // expanded by String.replace and corrupt the manifest beyond parsing.
  const updated = block.replace(refRe, (_m, pre, _old, post) => `${pre}${ref}${post}`);
  fs.writeFileSync(manifestPath, text.slice(0, start) + updated + text.slice(end));

  report.section(`vendor update ${v.name}`);
  report.row("PASS", v.name, `pin moved ${v.ref} → ${ref}`);
  report.row("SKIP", v.name, "run `.agents/harness vendor sync " + v.name + "` to apply, then re-run its smoke tests");
}
