#!/usr/bin/env python3
"""
**Synthesize** stage: read vector JSON → **synth** JSON + **one translator** print
(``swarm_btc_translate.print_swarm_btc_card``). **Analysis only — no live orders.**

Inputs/outputs use keys from ``prediction_agent/swarm_btc_flow_constants.py`` only.

Defaults:
  - Vector in: ``SWARM_BTC_VECTOR_JSON`` or ``prediction_agent/swarm_btc_vector.json``
  - Synth out: ``SWARM_BTC_SYNTH_JSON`` or ``prediction_agent/swarm_btc_synth.json``

Env (optional):
  ``SWARM_BTC_CARD_LEVERAGE``, ``SWARM_BTC_CARD_AMOUNT_BTC``,
  ``SWARM_BTC_CARD_PRICE_CATEGORY`` (spot|linear), ``SWARM_BTC_CARD_PRICE_SYMBOL``,
  ``SWARM_BTC_CARD_SKIP_PRICE`` = 1 to skip public ticker fetch.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _vector_path() -> Path:
    raw = (os.environ.get("SWARM_BTC_VECTOR_JSON") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _repo() / "prediction_agent" / "swarm_btc_vector.json"


def _synth_path() -> Path:
    raw = (os.environ.get("SWARM_BTC_SYNTH_JSON") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _repo() / "prediction_agent" / "swarm_btc_synth.json"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def main() -> int:
    ap = argparse.ArgumentParser(description="Swarm BTC synthesize + print card (no orders)")
    ap.add_argument("--vector", type=Path, default=None, help="Input vector JSON path")
    ap.add_argument("--out", type=Path, default=None, help="Synth JSON output path")
    ap.add_argument("--no-print", action="store_true", help="Skip translator stdout")
    ap.add_argument("--no-price", action="store_true", help="Skip public Bybit price fetch")
    ap.add_argument("--price", type=float, default=None, help="Override BTC price (for tests/offline)")
    args = ap.parse_args()

    repo = _repo()
    pa = str(repo / "prediction_agent")
    if pa not in sys.path:
        sys.path.insert(0, pa)

    import swarm_btc_flow as flow  # noqa: PLC0415
    import swarm_btc_translate as tr  # noqa: PLC0415

    vpath = args.vector or _vector_path()
    if not vpath.is_file():
        print(f"swarm_sintysize_btc: missing vector file {vpath} — run swarm_vectoryze_btc.py first", flush=True)
        return 2
    try:
        vector = json.loads(vpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"swarm_sintysize_btc: bad JSON {vpath}: {exc}", flush=True)
        return 3
    if not isinstance(vector, dict):
        print("swarm_sintysize_btc: vector root must be object", flush=True)
        return 3

    skip_price = args.no_price or _env_truthy("SWARM_BTC_CARD_SKIP_PRICE")
    synth: dict[str, Any] = flow.synthesize_swarm_btc_card(
        vector,
        repo=repo,
        price_override=args.price,
        skip_price_fetch=skip_price,
    )
    dest = args.out or _synth_path()
    flow.atomic_write_json(dest, synth)
    if not args.no_print:
        tr.print_swarm_btc_card(synth)
    print(f"swarm_sintysize_btc wrote {dest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
