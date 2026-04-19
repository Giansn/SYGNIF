#!/usr/bin/env python3
"""
Set TP/SL + optional trailing stop on the **open** Bybit **demo** linear position (USDT perps).

Uses ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET`` and ``api-demo.bybit.com`` (same as ``trade_overseer/bybit_linear_hedge.py``).

Defaults (tunable): TP/SL from %% off avg entry; trailing as **price distance** (Bybit ``trailingStop``).

Examples::

  # Preview only (no API write)
  python3 scripts/bybit_demo_set_tpsl.py

  # Apply TP 0.5%%, SL 0.35%%, trail $150 from best side
  python3 scripts/bybit_demo_set_tpsl.py --apply

  # Custom
  python3 scripts/bybit_demo_set_tpsl.py --apply --tp-pct 0.5 --sl-pct 0.4 --trail 200 --symbol BTCUSDT
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_dotenv_file(path: Path, *, override: bool = False) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if not k:
            continue
        v = v.strip().strip('"').strip("'")
        if not override and k in os.environ:
            continue
        os.environ[k] = v


def _fmt_price(x: float) -> str:
    if x >= 1000:
        return f"{x:.2f}"
    if x >= 1:
        return f"{x:.4f}"
    return f"{x:.6f}"


def _pick_open_position(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            sz = abs(float(r.get("size") or 0.0))
        except (TypeError, ValueError):
            continue
        if sz >= 1e-12:
            return r
    return None


def _compute_tp_sl(side: str, avg: float, tp_pct: float, sl_pct: float) -> tuple[str, str]:
    su = (side or "").strip().upper()
    if su == "BUY":
        tp = avg * (1.0 + tp_pct / 100.0)
        sl = avg * (1.0 - sl_pct / 100.0)
    else:
        tp = avg * (1.0 - tp_pct / 100.0)
        sl = avg * (1.0 + sl_pct / 100.0)
    return _fmt_price(tp), _fmt_price(sl)


def main() -> int:
    ap = argparse.ArgumentParser(description="Set demo linear TP/SL + trailing (Bybit v5 trading-stop).")
    ap.add_argument("--symbol", default="BTCUSDT", help="Linear symbol, default BTCUSDT")
    ap.add_argument("--tp-pct", type=float, default=0.5, help="Take profit distance from avg entry (%%)")
    ap.add_argument("--sl-pct", type=float, default=0.35, help="Stop loss distance from avg entry (%%)")
    ap.add_argument(
        "--trail",
        type=float,
        default=150.0,
        help="Trailing stop **price distance** (Bybit trailingStop). 0 = no trailing",
    )
    ap.add_argument(
        "--active-price",
        type=str,
        default="",
        help="Optional activePrice (trailing activates when mark reaches this)",
    )
    ap.add_argument(
        "--position-idx",
        type=int,
        default=-1,
        help="Override positionIdx (default: from API row)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="POST trading-stop; default is dry-run (print only)",
    )
    args = ap.parse_args()

    root = _repo_root()
    raw_secrets = (os.environ.get("SYGNIF_SECRETS_ENV_FILE") or "").strip()
    if raw_secrets:
        _load_dotenv_file(Path(raw_secrets).expanduser(), override=False)
    _load_dotenv_file(Path.home() / "xrp_claude_bot" / ".env", override=False)
    _load_dotenv_file(root / ".env", override=False)

    sys.path.insert(0, str(root / "trade_overseer"))
    import bybit_linear_hedge as blh  # noqa: E402

    sym = (args.symbol or "BTCUSDT").replace("/", "").upper()
    try:
        resp = blh.position_list(sym)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    if resp.get("retCode") != 0:
        print("position/list error:", resp, file=sys.stderr)
        return 1
    rows = (resp.get("result") or {}).get("list") or []
    pos = _pick_open_position(rows)
    if not pos:
        print("No open position for", sym)
        return 0

    side = str(pos.get("side") or "")
    try:
        avg = float(pos.get("avgPrice") or 0.0)
    except (TypeError, ValueError):
        avg = 0.0
    if avg <= 0:
        print("Invalid avgPrice in position row:", pos, file=sys.stderr)
        return 1

    pidx = int(args.position_idx) if args.position_idx >= 0 else int(pos.get("positionIdx") or 0)
    tp_s, sl_s = _compute_tp_sl(side, avg, args.tp_pct, args.sl_pct)

    mark = pos.get("markPrice")
    print("symbol:", sym, "side:", side, "positionIdx:", pidx, "avg:", avg, "mark:", mark)
    print("computed takeProfit:", tp_s, "stopLoss:", sl_s, end="")
    if args.trail and args.trail > 0:
        print(" trailingStop:", str(args.trail))
    else:
        print(" (no trailing)")

    body: dict[str, Any] = {
        "take_profit": tp_s,
        "stop_loss": sl_s,
        "position_idx": pidx,
    }
    if args.trail and args.trail > 0:
        body["trailing_stop"] = str(args.trail)
    if args.active_price.strip():
        body["active_price"] = args.active_price.strip()

    if not args.apply:
        print("Dry-run: pass --apply to POST /v5/position/trading-stop")
        return 0

    r = blh.set_trading_stop_linear(
        sym,
        position_idx=pidx,
        take_profit=tp_s,
        stop_loss=sl_s,
        trailing_stop=str(args.trail) if args.trail and args.trail > 0 else None,
        active_price=args.active_price.strip() or None,
    )
    print("response:", r)
    return 0 if r.get("retCode") == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
