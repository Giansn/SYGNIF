#!/usr/bin/env python3
"""
**Vector** stage: read Sygnif BTC sidecars + ``compute_swarm()`` → JSON with **constant keys**
(``swarm_btc_flow_constants``). **No orders.**

Output default: ``prediction_agent/swarm_btc_vector.json`` (override ``SWARM_BTC_VECTOR_JSON``).

Next: ``scripts/swarm_sintysize_btc.py`` reads this file and runs the single translator.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _vector_path() -> Path:
    raw = (os.environ.get("SWARM_BTC_VECTOR_JSON") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _repo() / "prediction_agent" / "swarm_btc_vector.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="Swarm BTC vector stage (constant-key JSON, no orders)")
    ap.add_argument("--out", type=Path, default=None, help="Override output path")
    ap.add_argument("--print-json", action="store_true", help="Also print JSON to stdout")
    args = ap.parse_args()

    repo = _repo()
    sys.path.insert(0, str(repo / "prediction_agent"))
    import swarm_btc_flow as flow  # noqa: PLC0415

    vec = flow.build_swarm_btc_vector(repo)
    dest = args.out or _vector_path()
    flow.atomic_write_json(dest, vec)
    if args.print_json:
        print(json.dumps(vec, indent=2), flush=True)
    print(f"swarm_vectoryze_btc wrote {dest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
