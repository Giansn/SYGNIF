/**
 * Cursor MCP helper: run npx without relying on a minimal GUI PATH (Windows/macOS/Linux).
 * Windows: Program Files\nodejs, %AppData%\npm, Scoop shims.
 * Override: NPX_PATH (full path to npx / npx.cmd).
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir, platform } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const win = platform() === "win32";
const home = homedir();
const profile = process.env.USERPROFILE || home;

function npxCmd() {
  if (process.env.NPX_PATH) {
    if (existsSync(process.env.NPX_PATH)) {
      return process.env.NPX_PATH;
    }
    return process.env.NPX_PATH;
  }
  if (win) {
    const pf = process.env.ProgramFiles || "C:\\Program Files";
    const pf86 = process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)";
    const candidates = [
      path.join(pf, "nodejs", "npx.cmd"),
      path.join(pf, "nodejs", "npx.exe"),
      path.join(pf86, "nodejs", "npx.cmd"),
      path.join(profile, "AppData", "Roaming", "npm", "npx.cmd"),
      path.join(profile, "scoop", "shims", "npx.exe"),
    ];
    for (const c of candidates) {
      if (c && existsSync(c)) {
        return c;
      }
    }
  } else {
    const candidates = [
      "/usr/bin/npx",
      "/usr/local/bin/npx",
      path.join(home, ".local", "bin", "npx"),
      path.join(home, ".fnm", "aliases", "default", "bin", "npx"),
    ];
    for (const c of candidates) {
      if (existsSync(c)) {
        return c;
      }
    }
  }
  return "npx";
}

function prependNodeBin(base) {
  const sep = win ? ";" : ":";
  const dirs = [];
  if (win) {
    const pf = process.env.ProgramFiles || "C:\\Program Files";
    const pf86 = process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)";
    dirs.push(
      path.join(pf, "nodejs"),
      path.join(pf86, "nodejs"),
      path.join(profile, "AppData", "Roaming", "npm"),
      path.join(profile, "scoop", "shims"),
    );
  } else {
    dirs.push("/usr/bin", "/usr/local/bin", path.join(home, ".local", "bin"));
  }
  const extra = dirs.filter((d) => existsSync(d)).join(sep);
  if (!extra) {
    return base || "";
  }
  return base ? `${extra}${sep}${base}` : extra;
}

const npx = npxCmd();
const args = process.argv.slice(2);

if (args.length === 0) {
  console.error("[mcp] usage: run-with-npx.mjs <npx args...>");
  process.exit(1);
}

const useShell =
  win &&
  (npx === "npx" ||
    npx.endsWith(".cmd") ||
    npx.endsWith(".bat"));

const child = spawn(npx, args, {
  stdio: "inherit",
  env: { ...process.env, PATH: prependNodeBin(process.env.PATH) },
  shell: useShell,
});

child.on("error", (err) => {
  console.error(
    "[mcp] failed to spawn npx:",
    err.message,
    "\nInstall Node.js LTS: https://nodejs.org/",
  );
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.exit(1);
  }
  process.exit(code ?? 1);
});
