/**
 * VS Code / Cursor: Brainsync MCP with portable paths.
 * Set BRAINSYNC_MCP_SERVER to mcp-server.js, or rely on auto-discovery under
 * ~/.cursor-server/extensions, ~/.cursor/extensions (Linux/macOS/Windows).
 *
 * Optional env: BRAINSYNC_DB, BRAINSYNC_ENV_FILE (defaults under ~/.brainsync/sygnif/).
 */
import { spawn } from "node:child_process";
import { existsSync, readdirSync } from "node:fs";
import { homedir, platform } from "node:os";
import path from "node:path";

const win = platform() === "win32";
const home = homedir();
const profile = process.env.USERPROFILE || home;

function extensionRoots() {
  if (win) {
    return [
      path.join(profile, ".cursor-server", "extensions"),
      path.join(profile, ".cursor", "extensions"),
      path.join(profile, ".vscode-server", "extensions"),
      path.join(profile, ".vscode", "extensions"),
    ];
  }
  return [
    path.join(home, ".cursor-server", "extensions"),
    path.join(home, ".cursor", "extensions"),
    path.join(home, ".vscode-server", "extensions"),
    path.join(home, ".vscode", "extensions"),
  ];
}

function findBrainsyncServer() {
  if (process.env.BRAINSYNC_MCP_SERVER && existsSync(process.env.BRAINSYNC_MCP_SERVER)) {
    return process.env.BRAINSYNC_MCP_SERVER;
  }
  for (const root of extensionRoots()) {
    if (!existsSync(root)) {
      continue;
    }
    let dirs;
    try {
      dirs = readdirSync(root, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const d of dirs) {
      if (!d.isDirectory() || !d.name.toLowerCase().startsWith("dawoodahmad.brainsync")) {
        continue;
      }
      const candidate = path.join(root, d.name, "dist", "mcp-server.js");
      if (existsSync(candidate)) {
        return candidate;
      }
    }
  }
  return null;
}

const serverJs = findBrainsyncServer();
if (!serverJs) {
  console.error(
    "[mcp brainsync] Could not find Brainsync mcp-server.js.\n" +
      "Install the Brainsync extension, or set BRAINSYNC_MCP_SERVER to the full path to dist/mcp-server.js",
  );
  process.exit(1);
}

const defaultDir = path.join(home, ".brainsync", "sygnif");
const db =
  process.env.BRAINSYNC_DB || path.join(defaultDir, "state.dat");
const envFile =
  process.env.BRAINSYNC_ENV_FILE ||
  path.join(defaultDir, "mcp-secret-env.json");

const child = spawn(
  process.execPath,
  [serverJs, "--db", db],
  {
    stdio: "inherit",
    env: {
      ...process.env,
      BRAINSYNC_ENV_FILE: envFile,
    },
  },
);

child.on("error", (err) => {
  console.error("[mcp brainsync]", err.message);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.exit(1);
  }
  process.exit(code ?? 1);
});
