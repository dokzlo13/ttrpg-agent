import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

const extensionDir = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(extensionDir, "..", "..");

function readDotEnvValue(root: string, key: string): string | undefined {
	const envPath = join(root, ".env");
	if (!existsSync(envPath)) return process.env[key];

	let value = process.env[key];
	const text = readFileSync(envPath, "utf8");
	for (const rawLine of text.split(/\r?\n/)) {
		const line = rawLine.trim();
		if (!line || line.startsWith("#")) continue;
		const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
		if (!match) continue;
		if (match[1] !== key) continue;

		let parsed = match[2].trim();
		if (
			(parsed.startsWith('"') && parsed.endsWith('"')) ||
			(parsed.startsWith("'") && parsed.endsWith("'"))
		) {
			parsed = parsed.slice(1, -1);
		}
		value = parsed;
	}

	return value && value.trim() ? value.trim() : undefined;
}

export default function (pi: ExtensionAPI) {
	pi.on("resources_discover", () => {
		const windowsAgentDir = readDotEnvValue(projectRoot, "TTRPG_WINDOWS_AGENT_DIR");
		if (!windowsAgentDir) return;

		return {
			skillPaths: [join(projectRoot, ".pi", "conditional-skills", "ttrpg-wsl-sync", "SKILL.md")],
		};
	});
}
