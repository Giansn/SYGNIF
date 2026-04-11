#!/usr/bin/env python3
"""Probe MCP config + Network submodule; append NDJSON to .cursor/debug-bf4c82.log (debug session bf4c82)."""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parents[1] / ".cursor" / "debug-bf4c82.log"
REPO = Path(__file__).resolve().parents[1]
SESSION_ID = "bf4c82"
RUN_ID = "mcp-network-probe"


def _log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict,
) -> None:
    payload = {
        "sessionId": SESSION_ID,
        "runId": RUN_ID,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main() -> None:
    cwd = Path.cwd().resolve()
    mcp_path = REPO / ".cursor" / "mcp.json"

    _log(
        "A",
        "debug_network_mcp_check.py:main",
        "cwd_vs_repo",
        {
            "cwd": str(cwd),
            "repo": str(REPO),
            "cwd_equals_repo": str(cwd) == str(REPO),
        },
    )

    which = {
        "node": shutil.which("node") or "",
        "npx": shutil.which("npx") or "",
        "uvx": shutil.which("uvx") or "",
    }
    _log(
        "B",
        "debug_network_mcp_check.py:main",
        "toolchain_which",
        which,
    )

    net_root = REPO / "network"
    edge_infer = net_root / "network" / "edge_npu_infer"
    mcp_py = edge_infer / "mcp_npu_server.py"
    if os.name == "nt":
        venv_py = edge_infer / ".venv" / "Scripts" / "python.exe"
    else:
        venv_py = edge_infer / ".venv" / "bin" / "python"
    _log(
        "C",
        "debug_network_mcp_check.py:main",
        "network_submodule_paths",
        {
            "SYGNIF_REPO_env": os.environ.get("SYGNIF_REPO", ""),
            "net_root_is_dir": net_root.is_dir(),
            "edge_infer_is_dir": edge_infer.is_dir(),
            "mcp_npu_server_exists": mcp_py.is_file(),
            "venv_python_exists": venv_py.is_file(),
        },
    )

    _log(
        "D",
        "debug_network_mcp_check.py:main",
        "edge_npu_mcp_prereqs",
        {
            "inferDir": str(edge_infer),
            "serverScript_ok": mcp_py.is_file(),
            "venv_python_ok": venv_py.is_file(),
        },
    )

    linear_reachable: str | int = "skipped"
    try:
        req = urllib.request.Request(
            "https://mcp.linear.app/mcp",
            method="HEAD",
            headers={"User-Agent": "SYGNIF-debug-network-mcp-check"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            linear_reachable = int(getattr(resp, "status", 200) or 200)
    except urllib.error.HTTPError as e:
        linear_reachable = f"HTTPError_{e.code}"
    except Exception as e:
        linear_reachable = f"{type(e).__name__}:{e}"

    _log(
        "E",
        "debug_network_mcp_check.py:main",
        "linear_mcp_head",
        {"result": linear_reachable},
    )

    mcp_ok = mcp_path.is_file()
    servers: dict[str, dict] = {}
    if mcp_ok:
        cfg = json.loads(mcp_path.read_text(encoding="utf-8"))
        for name, spec in (cfg.get("mcpServers") or {}).items():
            cmd = spec.get("command", "")
            args = list(spec.get("args") or [])
            rel_scripts = [a for a in args if isinstance(a, str) and a.endswith(".mjs")]
            checks = []
            for rel in rel_scripts:
                p = (cwd / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
                p_repo = (REPO / rel).resolve() if not Path(rel).is_absolute() else Path(rel)
                checks.append(
                    {
                        "rel": rel,
                        "exists_from_cwd": p.is_file(),
                        "exists_from_repo_root": p_repo.is_file(),
                    }
                )
            servers[name] = {"command": cmd, "args_head": args[:4], "script_checks": checks}
    else:
        servers["_error"] = {"missing_file": str(mcp_path)}

    _log(
        "A",
        "debug_network_mcp_check.py:main",
        "mcp_json_script_resolution",
        {"mcp_json_exists": mcp_ok, "servers": servers},
    )

    brainsync_hint = ""
    ext_roots = [
        Path.home() / ".cursor-server" / "extensions",
        Path.home() / ".cursor" / "extensions",
    ]
    found = False
    for er in ext_roots:
        if not er.is_dir():
            continue
        try:
            for child in er.iterdir():
                if child.is_dir() and "brainsync" in child.name.lower():
                    found = True
                    brainsync_hint = str(child)
                    break
        except OSError:
            continue
        if found:
            break
    _log(
        "F",
        "debug_network_mcp_check.py:main",
        "brainsync_extension_dir_scan",
        {
            "brainsync_like_dir_found": found,
            "first_match": brainsync_hint or None,
            "BRAINSYNC_MCP_SERVER_set": bool(os.environ.get("BRAINSYNC_MCP_SERVER")),
        },
    )


if __name__ == "__main__":
    main()
