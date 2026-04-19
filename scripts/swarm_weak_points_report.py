#!/usr/bin/env python3
"""
Print Swarm **weak-point bundle** (``swarm_knowledge`` + predict-loop dataset + demo closed PnL).

Loads ``swarm_operator.env`` when present (same merge as other Swarm scripts).

Examples::

  cd ~/SYGNIF && python3 scripts/swarm_weak_points_report.py
  python3 scripts/swarm_weak_points_report.py --json
  python3 scripts/swarm_weak_points_report.py --env-file ~/SYGNIF/swarm_operator.env
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description="Swarm weak-points report (swarm_knowledge + artefacts)")
    ap.add_argument("--env-file", type=Path, default=None, help="Optional dotenv (e.g. swarm_operator.env)")
    ap.add_argument("--json", action="store_true", help="Print full JSON bundle")
    ap.add_argument("--telegram", action="store_true", help="Print Telegram-sized text (default when not --json)")
    args = ap.parse_args()

    sys.path.insert(0, str(_REPO))
    sys.path.insert(0, str(_REPO / "finance_agent"))
    from swarm_instance_paths import apply_swarm_instance_env  # noqa: E402
    from swarm_weak_points_solution import (  # noqa: E402
        build_swarm_weak_points_bundle,
        format_swarm_weak_points_telegram,
    )

    apply_swarm_instance_env(_REPO, extra_env_file=args.env_file)
    bundle = build_swarm_weak_points_bundle(_REPO)
    if args.json:
        print(json.dumps(bundle, indent=2, ensure_ascii=False))
    else:
        print(format_swarm_weak_points_telegram(bundle), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
