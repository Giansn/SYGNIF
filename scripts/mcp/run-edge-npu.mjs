/**
 * Cursor MCP: OpenVINO edge-npu server using the repo venv (Windows + Unix paths).
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { platform } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..", "..");
const win = platform() === "win32";
const inferDir = path.join(repoRoot, "network", "network", "edge_npu_infer");
const serverScript = path.join(inferDir, "mcp_npu_server.py");
const python = win
  ? path.join(inferDir, ".venv", "Scripts", "python.exe")
  : path.join(inferDir, ".venv", "bin", "python");

if (!existsSync(serverScript)) {
  console.error("[mcp edge-npu] missing:", serverScript);
  process.exit(1);
}
if (!existsSync(python)) {
  console.error(
    `[mcp edge-npu] missing venv python: ${python}\n` +
      `Create: cd "${inferDir}" && python -m venv .venv && pip install -r requirements.txt`,
  );
  process.exit(1);
}

const child = spawn(python, [serverScript], {
  stdio: "inherit",
  cwd: inferDir,
  shell: false,
});

child.on("error", (err) => {
  console.error("[mcp edge-npu]", err.message);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.exit(1);
  }
  process.exit(code ?? 1);
});
