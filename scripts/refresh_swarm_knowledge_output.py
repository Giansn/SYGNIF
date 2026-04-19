#!/usr/bin/env python3
"""Write ``prediction_agent/swarm_knowledge_output.json`` via ``compute_swarm()`` (host / cron).

Loads ``KEY=value`` lines from the same env files as Docker compose (no ``bash source``),
then runs ``finance_agent.swarm_knowledge`` main write path.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    secrets = Path(os.environ.get("SYGNIF_SECRETS_ENV_FILE", os.path.expanduser("~/xrp_claude_bot/.env")))
    _load_env_file(secrets)
    _load_env_file(repo / ".env")
    _load_env_file(repo / "swarm_operator.env")
    os.chdir(repo)
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    try:
        from finance_agent.swarm_knowledge import compute_swarm  # noqa: PLC0415
        from finance_agent.swarm_knowledge import _prediction_agent_dir  # noqa: PLC0415
    except ImportError as exc:
        print("import failed:", exc, file=sys.stderr)
        return 2
    out = compute_swarm()
    dest = _prediction_agent_dir() / "swarm_knowledge_output.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"[swarm] wrote {dest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
