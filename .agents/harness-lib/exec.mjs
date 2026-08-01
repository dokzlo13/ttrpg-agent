// Process helpers.
//
// Everything here takes an argv ARRAY, never a shell string. Paths reach bash
// through the environment rather than string interpolation, so a directory with
// a space or a quote in it cannot change what runs.

import { execFileSync } from "node:child_process";

function capture(file, args, opts = {}) {
  try {
    const out = execFileSync(file, args, {
      stdio: ["ignore", "pipe", "pipe"],
      encoding: "utf8",
      ...opts,
    });
    return { ok: true, out: (out ?? "").trim() };
  } catch (error) {
    const stderr = error.stderr?.toString() ?? "";
    const stdout = error.stdout?.toString() ?? "";
    return {
      ok: false,
      out: (stderr || stdout || error.message || "").trim(),
      status: error.status ?? null,
    };
  }
}

/** Run a command by argv. Never invokes a shell. */
export function run(file, args = [], opts = {}) {
  return capture(file, args, opts);
}

/** Run an inherited-stdio command by argv; throws on failure. */
export function runLoud(file, args = [], opts = {}) {
  execFileSync(file, args, { stdio: "inherit", ...opts });
}

/**
 * Run a bash snippet. `vars` become environment variables — reference them as
 * "$NAME" inside the script instead of interpolating values into it.
 */
export function bash(script, { vars = {}, inherit = false, cwd } = {}) {
  const opts = { env: { ...process.env, ...vars }, cwd };
  if (inherit) {
    execFileSync("bash", ["-c", script], { stdio: "inherit", ...opts });
    return { ok: true, out: "" };
  }
  return capture("bash", ["-c", script], opts);
}

/**
 * Run a bash snippet with the project environment contract already sourced.
 * This is the only correct way for the harness to invoke anything that depends
 * on the contract — it is exactly what .agents/bin/* does.
 */
export function bashWithEnv(root, script, opts = {}) {
  const prelude = 'set -uo pipefail\nsource "$TTRPG_ROOT/.agents/env.sh"\n';
  return bash(prelude + script, { ...opts, vars: { TTRPG_ROOT: root, ...(opts.vars ?? {}) } });
}

let contractEnvCache = null;

/**
 * The full environment the contract defines, as a plain object suitable for a
 * child process `env`.
 *
 * The harness spawns `uv sync` and `npm install`, and those honour UV_CACHE_DIR
 * / npm_config_cache from their environment. The harness process itself is NOT
 * started under the contract, so without this every bootstrap would quietly
 * populate ~/.cache/uv and ~/.npm instead of the project — bootstrap being
 * exactly the thing that must not leak. Reuses env.sh's dump mode so there is
 * no second definition of the environment.
 */
export function contractEnv(root) {
  if (contractEnvCache) return contractEnvCache;
  const res = bash('TTRPG_ENV_DUMP=1 bash "$TTRPG_ROOT/.agents/env.sh"', {
    vars: { TTRPG_ROOT: root },
  });
  const env = { ...process.env };
  if (!res.ok) {
    // Falling back to the ambient environment here means bootstrap installs a
    // 5 GB torch tree into ~/.cache/uv — exactly the leak this exists to stop.
    throw new Error(
      `could not read the environment contract from ${root}/.agents/env.sh:\n` +
        `${res.out.split("\n").slice(0, 3).join("\n")}`,
    );
  }
  for (const line of res.out.split("\n")) {
    const eq = line.indexOf("=");
    if (eq > 0) env[line.slice(0, eq)] = line.slice(eq + 1);
  }
  if (!env.UV_CACHE_DIR || !env.npm_config_cache) {
    throw new Error("environment contract did not define UV_CACHE_DIR / npm_config_cache");
  }
  // npm writes debug logs next to its cache only if told to; default is ~/.npm.
  env.npm_config_logs_dir = env.npm_config_logs_dir ?? `${root}/.cache/npm/_logs`;
  contractEnvCache = env;
  return env;
}
