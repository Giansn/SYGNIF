/**
 * Cursor MCP helper: run awslabs MCP packages via uvx without relying on PATH.
 * Windows: looks for %USERPROFILE%\.local\bin\uvx.cmd (uv installer default).
 * Unix: ~/.local/bin/uvx, ~/.cargo/bin/uvx, then PATH.
 *
 * Override: set UVX_PATH to the full path of uvx (or uvx.cmd on Windows).
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir, platform } from "node:os";
import path from "node:path";

const win = platform() === "win32";

function candidates() {
  if (process.env.UVX_PATH) {
    return [process.env.UVX_PATH];
  }
  const home = homedir();
  const profile = process.env.USERPROFILE || home;
  if (win) {
    const localApp = process.env.LOCALAPPDATA || "";
    return [
      path.join(profile, ".local", "bin", "uvx.cmd"),
      path.join(profile, ".local", "bin", "uvx.exe"),
      path.join(localApp, "Programs", "uv", "uvx.exe"),
      path.join(profile, "scoop", "shims", "uvx.exe"),
    ];
  }
  return [
    path.join(home, ".local", "bin", "uvx"),
    path.join(home, ".cargo", "bin", "uvx"),
  ];
}

function findUvx() {
  for (const c of candidates()) {
    if (c && existsSync(c)) {
      return c;
    }
  }
  return "uvx";
}

function prependPath(base) {
  const sep = win ? ";" : ":";
  const home = homedir();
  const profile = process.env.USERPROFILE || home;
  const localApp = process.env.LOCALAPPDATA || "";
  const dirs = win
    ? [
        path.join(profile, ".local", "bin"),
        path.join(localApp, "Programs", "uv"),
      ]
    : [path.join(home, ".local", "bin"), path.join(home, ".cargo", "bin")];
  const extra = dirs.filter((d) => existsSync(d)).join(sep);
  if (!extra) {
    return base || "";
  }
  return base ? `${extra}${sep}${base}` : extra;
}

const uvx = findUvx();
const pkgArgs = process.argv.slice(2);

if (pkgArgs.length === 0) {
  console.error("[mcp] usage: run-with-uvx.mjs <uvx package args...>");
  process.exit(1);
}

const useShell =
  win && (uvx === "uvx" || uvx.endsWith(".cmd") || uvx.endsWith(".bat"));

const child = spawn(uvx, pkgArgs, {
  stdio: "inherit",
  env: { ...process.env, PATH: prependPath(process.env.PATH) },
  shell: useShell,
});

child.on("error", (err) => {
  console.error(
    "[mcp] failed to spawn uvx:",
    err.message,
    "\nInstall uv: https://docs.astral.sh/uv/getting-started/installation/",
  );
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.exit(1);
  }
  process.exit(code ?? 1);
});
