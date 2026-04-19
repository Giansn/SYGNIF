#!/usr/bin/env python3
"""
**Adjust** an open Bybit **USDT-linear** position (demo by default): TP/SL refresh, leverage, or notional scale.

Uses ``trade_overseer/bybit_linear_hedge.py`` (same env pattern as ``bybit_close_linear_position.py``).

Actions (combine as needed; all respect dry-run unless ``--execute`` + ACK):

- ``--reapply-tpsl`` — run ``finance_agent.swarm_btc_future_tpsl_apply.apply_btc_future_tpsl`` so TP/SL/trail
  match current ``btc_prediction_output.json`` + profile (sets ``SYGNIF_SWARM_TPSL_SYMBOL``).
- ``--set-leverage N`` — POST ``/v5/position/set-leverage`` for the symbol.
- ``--target-notional-usdt X`` — market **add** or **reduce-only** trim so ``positionValue`` moves toward **X**
  USDT (linear ``positionValue`` from the venue; approximate).

**Safety:** default is **dry-run** (prints plan). Live orders require::

  SYGNIF_BYBIT_ADJUST_OPEN_ACK=YES python3 scripts/bybit_adjust_open_position.py --execute ...

If TP/SL is skipped with ``swarm_conflict``, one-off apply can set ``SYGNIF_SWARM_TPSL_SKIP_ON_SWARM_CONFLICT=0``
(same knob as ``swarm_btc_future_tpsl_apply``).

Examples::

  python3 scripts/bybit_adjust_open_position.py --symbol BTCUSDT --reapply-tpsl
  SYGNIF_BYBIT_ADJUST_OPEN_ACK=YES python3 scripts/bybit_adjust_open_position.py --execute --reapply-tpsl
  SYGNIF_BYBIT_ADJUST_OPEN_ACK=YES python3 scripts/bybit_adjust_open_position.py --execute --set-leverage 50
  SYGNIF_BYBIT_ADJUST_OPEN_ACK=YES python3 scripts/bybit_adjust_open_position.py --execute --target-notional-usdt 100000
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def load_env(path: Path, *, override: bool = False) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        if "=" not in s:
            continue
        k, _, rest = s.partition("=")
        k = k.strip()
        if not k:
            continue
        v = rest.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if override or k not in os.environ:
            os.environ[k] = v


def _load_standard_env() -> None:
    repo = _repo()
    load_env(repo / ".env", override=False)
    xrp = Path.home() / "xrp_claude_bot" / ".env"
    load_env(xrp, override=True)
    extra = (os.environ.get("SYGNIF_SECRETS_ENV_FILE") or "").strip()
    if extra:
        load_env(Path(extra).expanduser(), override=True)


def _qty_step_floor(q: float, *, step: float = 0.001) -> str:
    q = max(0.0, float(q))
    flo = math.floor(q / step) * step
    s = f"{flo:.6f}".rstrip("0").rstrip(".")
    return s if s else str(step)


def _best_open_row(pr: dict, symbol: str) -> dict | None:
    sym = symbol.upper().strip()
    best: dict | None = None
    best_sz = 0.0
    for row in (pr.get("result") or {}).get("list") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol", "")).upper() != sym:
            continue
        try:
            sz = abs(float(str(row.get("size") or "0").strip() or 0))
        except (TypeError, ValueError):
            continue
        if sz < 1e-12:
            continue
        if str(row.get("side", "")).strip().lower() not in ("buy", "sell"):
            continue
        if sz > best_sz:
            best_sz = sz
            best = row
    return best


def _pos_idx(row: dict) -> int:
    try:
        return int(row.get("positionIdx") or 0)
    except (TypeError, ValueError):
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Adjust open Bybit linear position (TP/SL, leverage, notional)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--mainnet", action="store_true")
    ap.add_argument("--position-idx", type=int, default=None, help="Override hedge leg (default: from venue row)")
    ap.add_argument("--reapply-tpsl", action="store_true", help="Apply Swarm TP/SL from btc_prediction_output.json")
    ap.add_argument("--set-leverage", type=float, default=None, metavar="N", help="Set linear leverage (both sides)")
    ap.add_argument(
        "--target-notional-usdt",
        type=float,
        default=None,
        metavar="USDT",
        help="Scale position toward this USDT positionValue (add or reduce-only)",
    )
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    if (
        not args.reapply_tpsl
        and args.set_leverage is None
        and args.target_notional_usdt is None
    ):
        ap.error("specify at least one of: --reapply-tpsl, --set-leverage, --target-notional-usdt")

    _load_standard_env()
    if args.mainnet:
        os.environ["OVERSEER_BYBIT_HEDGE_MAINNET"] = "1"
        os.environ["OVERSEER_HEDGE_LIVE_OK"] = "YES"

    sym = (args.symbol or "BTCUSDT").replace("/", "").upper().strip()
    repo = _repo()
    sys.path.insert(0, str(repo / "trade_overseer"))
    sys.path.insert(0, str(repo / "finance_agent"))
    import bybit_linear_hedge as blh  # noqa: E402

    pr = blh.position_list(sym)
    if pr.get("retCode") != 0:
        print(json.dumps({"err": "position_list", "resp": pr}, indent=2))
        return 2
    row = _best_open_row(pr, sym)
    if not row:
        print(json.dumps({"err": "flat", "symbol": sym}, indent=2))
        return 3

    pidx = int(args.position_idx) if args.position_idx is not None else _pos_idx(row)
    side_api = str(row.get("side") or "").strip().lower()
    try:
        sz = abs(float(str(row.get("size") or "0").strip() or 0))
    except (TypeError, ValueError):
        sz = 0.0
    try:
        pv = float(str(row.get("positionValue") or "0").strip() or 0)
    except (TypeError, ValueError):
        pv = 0.0
    try:
        mk = float(str(row.get("markPrice") or "0").strip() or 0)
    except (TypeError, ValueError):
        mk = 0.0
    if mk <= 0:
        try:
            mk = float(str(row.get("avgPrice") or "0").strip() or 0)
        except (TypeError, ValueError):
            mk = 0.0

    plan: dict = {
        "symbol": sym,
        "positionIdx": pidx,
        "side": side_api,
        "size": sz,
        "positionValue_usdt": pv,
        "mark": mk,
        "actions": [],
    }

    exe = bool(args.execute)
    if exe and os.environ.get("SYGNIF_BYBIT_ADJUST_OPEN_ACK", "").strip().upper() != "YES":
        print("Refusing --execute: set SYGNIF_BYBIT_ADJUST_OPEN_ACK=YES", file=sys.stderr)
        return 4

    rc = 0

    if args.set_leverage is not None:
        lev = int(round(float(args.set_leverage)))
        plan["actions"].append({"set_leverage": lev})
        if exe:
            lr = blh.set_linear_leverage(sym, str(max(1, min(125, lev))))
            plan["set_leverage_resp"] = lr
            if lr.get("retCode") not in (0, 110043):
                rc = 5

    if args.reapply_tpsl:
        os.environ.setdefault("SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL", "1")
        os.environ["SYGNIF_SWARM_TPSL_SYMBOL"] = sym
        from swarm_btc_future_tpsl_apply import apply_btc_future_tpsl  # noqa: E402

        plan["actions"].append({"reapply_tpsl": True})
        tpsl = apply_btc_future_tpsl(dry_run=not exe)
        plan["tpsl_result"] = tpsl
        if exe and not tpsl.get("ok"):
            rc = max(rc, 6)

    if args.target_notional_usdt is not None:
        tgt = float(args.target_notional_usdt)
        if mk <= 0 or sz <= 0:
            plan["actions"].append({"target_notional_usdt": tgt, "error": "missing_mark_or_size"})
            rc = max(rc, 7)
        else:
            cur = pv if pv > 0 else sz * mk
            delta_usdt = tgt - cur
            dq = delta_usdt / mk
            q_s = _qty_step_floor(abs(dq))
            try:
                qf = float(q_s)
            except ValueError:
                qf = 0.0
            min_q = 0.001
            if qf + 1e-12 < min_q:
                plan["actions"].append(
                    {
                        "target_notional_usdt": tgt,
                        "current_notional_usdt": round(cur, 2),
                        "skip": "delta_qty_below_min",
                    }
                )
            else:
                is_long = side_api == "buy"
                add_long = is_long and delta_usdt > 0
                trim_long = is_long and delta_usdt < 0
                add_short = not is_long and delta_usdt > 0
                trim_short = not is_long and delta_usdt < 0
                if add_long:
                    side, ro = "Buy", False
                elif trim_long:
                    side, ro = "Sell", True
                elif add_short:
                    side, ro = "Sell", False
                else:
                    side, ro = "Buy", True
                plan["actions"].append(
                    {
                        "target_notional_usdt": tgt,
                        "current_notional_usdt": round(cur, 2),
                        "delta_usdt": round(delta_usdt, 2),
                        "qty": q_s,
                        "order_side": side,
                        "reduce_only": ro,
                    }
                )
                if exe:
                    mo = blh.create_market_order(sym, side, q_s, pidx, reduce_only=ro)
                    plan["resize_order_resp"] = mo
                    if mo.get("retCode") != 0:
                        rc = max(rc, 8)

    print(json.dumps(plan, indent=2, default=str))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
