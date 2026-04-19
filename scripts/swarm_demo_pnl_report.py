#!/usr/bin/env python3
"""
Bybit **closed linear PnL** summary for the Swarm / predict-protocol **demo** account.

Loads the same env merge as other Swarm launchers (``swarm_instance_paths.apply_swarm_instance_env``),
enables ``SYGNIF_SWARM_BYBIT_CLOSED_PNL``, then calls ``build_bybit_closed_pnl_report()``.

Examples::

  cd ~/SYGNIF && python3 scripts/swarm_demo_pnl_report.py
  python3 scripts/swarm_demo_pnl_report.py --max-rows 2000 --json
  python3 scripts/swarm_demo_pnl_report.py --env-file ~/SYGNIF/swarm_operator.env
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _ensure_import_path() -> None:
    r = str(_REPO)
    fa = str(_REPO / "finance_agent")
    if r not in sys.path:
        sys.path.insert(0, r)
    if fa not in sys.path:
        sys.path.insert(0, fa)


def compact_closed_pnl_summary(rep: dict) -> dict:
    """Strip to counts / sums / venue (no ``recent`` rows)."""
    if not isinstance(rep, dict):
        return {"ok": False, "detail": "not_a_dict"}
    if not rep.get("enabled"):
        return {"enabled": False}
    out: dict = {
        "ok": rep.get("ok"),
        "venue": rep.get("venue"),
        "symbol": rep.get("symbol"),
        "detail": rep.get("detail"),
        "retCode": rep.get("retCode"),
        "retMsg": rep.get("retMsg"),
    }
    if rep.get("ok"):
        out["n_closed"] = rep.get("n_closed")
        out["sum_closed_pnl_usdt"] = rep.get("sum_closed_pnl_usdt")
        out["wins"] = rep.get("wins")
        out["losses"] = rep.get("losses")
    return out


def text_report(rep: dict) -> str:
    if not isinstance(rep, dict):
        return "invalid response\n"
    if not rep.get("enabled"):
        return "bybit_closed_pnl disabled (set SYGNIF_SWARM_BYBIT_CLOSED_PNL=1)\n"
    if not rep.get("ok"):
        lines = [
            f"closed_pnl: ok=false venue={rep.get('venue')} symbol={rep.get('symbol')}",
            f"  detail={rep.get('detail')} retCode={rep.get('retCode')} retMsg={rep.get('retMsg')}",
        ]
        return "\n".join(lines) + "\n"
    n = rep.get("n_closed", 0)
    w = rep.get("wins", 0)
    l = rep.get("losses", 0)
    s = rep.get("sum_closed_pnl_usdt")
    lines = [
        f"venue={rep.get('venue')} symbol={rep.get('symbol')}",
        f"closed_legs={n} wins={w} losses={l} sum_closed_pnl_usdt={s}",
    ]
    recent = rep.get("recent")
    if isinstance(recent, list) and recent:
        lines.append("last closed legs (up to 8):")
        for row in recent[-8:]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"  pnl={row.get('closed_pnl')} side={row.get('side')} qty={row.get('qty')} "
                f"entry={row.get('avg_entry')} exit={row.get('avg_exit')}"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Bybit linear closed PnL stats (demo / hedge venue)")
    ap.add_argument(
        "--env-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="Optional extra .env (same as swarm_auto --env-file)",
    )
    ap.add_argument(
        "--max-rows",
        type=int,
        default=500,
        help="Cap on closed-pnl rows fetched (1..5000)",
    )
    ap.add_argument("--json", action="store_true", help="Print compact JSON only")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 when report ok=false (missing creds, API error, …)",
    )
    ap.add_argument(
        "--with-recent",
        action="store_true",
        help="Include ``recent`` closed legs in JSON output (can be large)",
    )
    args = ap.parse_args()

    _ensure_import_path()
    from swarm_instance_paths import apply_swarm_instance_env  # noqa: PLC0415

    apply_swarm_instance_env(_REPO, extra_env_file=args.env_file)

    cap = max(1, min(5000, int(args.max_rows)))
    os.environ["SYGNIF_SWARM_BYBIT_CLOSED_PNL"] = "1"
    os.environ["SYGNIF_SWARM_BYBIT_CLOSED_PNL_MAX_ROWS"] = str(cap)

    from swarm_knowledge import build_bybit_closed_pnl_report  # noqa: PLC0415

    rep = build_bybit_closed_pnl_report()

    if args.json:
        if args.with_recent:
            print(json.dumps(rep, indent=2, default=str, ensure_ascii=False))
        else:
            slim = dict(rep)
            slim.pop("recent", None)
            print(json.dumps(slim, indent=2, default=str, ensure_ascii=False))
    else:
        sys.stdout.write(text_report(rep))

    if args.strict and not (isinstance(rep, dict) and rep.get("ok")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
