// TOML parsing for .agents/manifest.toml.
//
// A thin wrapper around `smol-toml` (a full TOML 1.0 implementation) rather
// than a hand-rolled subset parser. The harness is a real npm package with real
// dependencies; `npm install --prefix .agents` is an explicit, reported step of
// `bootstrap`. The wrapper exists only to turn a missing install into an
// actionable message instead of a bare MODULE_NOT_FOUND.
//
// The import is lazy so that merely loading this module cannot crash the CLI —
// the entrypoint needs to be able to print that message.

let parseFn = null;

async function load() {
  if (parseFn) return parseFn;
  try {
    ({ parse: parseFn } = await import("smol-toml"));
  } catch (error) {
    if (error?.code !== "ERR_MODULE_NOT_FOUND") throw error;
    throw new Error(
      "harness dependencies are not installed.\n" +
        "  Run: npm install --prefix .agents\n" +
        "  (or: .agents/harness bootstrap --apply, which does it for you)",
    );
  }
  return parseFn;
}

export async function parseToml(text) {
  return (await load())(text);
}
