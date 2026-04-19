#!/usr/bin/env python3
"""
Open a **Bybit USDT-linear demo** market position from BTC analysis JSON (direct REST).

**Bypasses Nautilus** — no ``TradingNode``, no ``LiveRiskEngine``. Prefer the canonical path:
``run_sygnif_btc_trading_node.py`` / ``start_nautilus_btc_predict_protocol.sh`` (see
``research/nautilus_lab/README.md``). Reserve this script for debugging or when Nautilus cannot run.

Reads the same files as ``btc_analysis_forceenter.py`` and uses
``btc_analysis_order_signal.decide_forceenter_intent`` (R01 + MIXED / direction_logistic).

**Automation (default)** — both are **deterministic REST calls**, not operator picks:

- **Leverage:** from ``direction_logistic.confidence`` (0–100), linearly mapped between
  ``BYBIT_DEMO_ORDER_MIN_LEVERAGE`` and ``BYBIT_DEMO_ORDER_MAX_LEVERAGE`` over
  ``--min-dir-conf`` … 100 (same floor as intent resolution).
- **Qty (BTC):** ``available_USDT * effective_stake_frac / last_price``, where
  ``effective_stake_frac = BYBIT_DEMO_ORDER_STAKE_FRAC * (0.5 + 0.5 * t)`` and ``t`` is the same
  0…1 confidence position as for leverage (higher confidence → slightly larger notional). Clamped by
  ``BYBIT_DEMO_ORDER_MIN_QTY`` / ``BYBIT_DEMO_ORDER_MAX_QTY``. Wallet from signed demo API; last
  price from public Bybit linear ticker.

Optional overrides: ``--manual-leverage``, ``--manual-qty`` (disables that dimension’s automation).

**Safety**
- Default: **dry-run** (prints plan only; still fetches wallet/price when keys allow).
- Live: ``--execute`` **and** ``SYGNIF_PREDICTION_DEMO_TRADE_ACK=YES``.
- Shorts: pass ``--allow-short`` when consensus is **BEARISH** (opens **Sell** on demo linear).
- Uses ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET`` on ``api-demo.bybit.com``
  (see ``trade_overseer/bybit_linear_hedge.py``).

MIXED consensus: high logreg confidence does **not** mean RF/XGB agree — a note is still printed.

**Swarm-governed BTC linear (demo “futures” profile):** ``--from-swarm-signal`` reads
``bybitapidemo_btc_predicted_move_signal.json`` (``SYGNIF_BYBIT_DEMO_SIGNAL_JSON`` or default under
``prediction_agent/``). Use ``--refresh-swarm-signal`` to recompute via
``bybit_demo_predicted_move_export`` (same governance as ``SYGNIF_BYBIT_DEMO_PREDICTED_MOVE_EXPORT``).
Leverage/qty automation then use ``governance_probability_pct`` instead of direction_logistic.

**positionIdx:** one-way merged single = ``0``; hedge long leg = ``1``. Override with ``--position-idx``.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PA = _REPO / "prediction_agent"
sys.path.insert(0, str(_PA))
sys.path.insert(0, str(_REPO / "trade_overseer"))

import bybit_demo_predicted_move_export as bdexp  # noqa: E402
import bybit_linear_hedge as blh  # noqa: E402
from btc_analysis_order_signal import decide_forceenter_intent  # noqa: E402


def _load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        out = json.loads(path.read_text(encoding="utf-8"))
        return out if isinstance(out, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    raw = os.environ.get(name, str(default)).strip() or str(default)
    try:
        v = int(float(raw))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, str(default)).strip() or str(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _leverage_bounds() -> tuple[int, int]:
    hi = _env_int("BYBIT_DEMO_ORDER_MAX_LEVERAGE", 25, lo=1, hi=125)
    lo = _env_int("BYBIT_DEMO_ORDER_MIN_LEVERAGE", 5, lo=1, hi=125)
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def _qty_bounds() -> tuple[float, float]:
    min_q = _env_float("BYBIT_DEMO_ORDER_MIN_QTY", 0.001)
    max_q = _env_float("BYBIT_DEMO_ORDER_MAX_QTY", 0.05)
    if min_q <= 0:
        min_q = 0.001
    if max_q < min_q:
        max_q = min_q
    return min_q, max_q


def _confidence_t(conf: float, floor: float) -> float:
    if conf >= 100.0:
        return 1.0
    span = 100.0 - floor
    if span <= 1e-9:
        return 1.0 if conf >= floor else 0.0
    t = (conf - floor) / span
    return max(0.0, min(1.0, t))


def auto_leverage(conf: float, conf_floor: float) -> int:
    lo, hi = _leverage_bounds()
    t = _confidence_t(conf, conf_floor)
    lev = int(round(lo + (hi - lo) * t))
    return max(lo, min(hi, lev))


def _parse_usdt_available(resp: dict) -> float | None:
    if resp.get("retCode") != 0:
        return None
    lst = (resp.get("result") or {}).get("list") or []
    if not lst:
        return None
    coins = lst[0].get("coin") or []
    for c in coins:
        if str(c.get("coin", "")).upper() != "USDT":
            continue
        for key in ("availableToWithdraw", "availableBalance", "transferBalance"):
            raw = c.get(key)
            if raw is not None and str(raw).strip() != "":
                try:
                    return float(raw)
                except (TypeError, ValueError):
                    pass
        try:
            return float(c.get("walletBalance") or 0.0)
        except (TypeError, ValueError):
            return None
    return None


def public_linear_last_price(symbol: str) -> float | None:
    sym = (symbol or "").replace("/", "").upper().strip() or "BTCUSDT"
    q = urllib.parse.urlencode({"category": "linear", "symbol": sym})
    url = f"https://api.bybit.com/v5/market/tickers?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "SYGNIF-btc_analysis_bybit_demo_market"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    rows = (data.get("result") or {}).get("list") or []
    if not rows:
        return None
    try:
        px = float(rows[0].get("lastPrice") or 0.0)
    except (TypeError, ValueError):
        return None
    return px if px > 0 else None


def auto_qty_btc(
    *,
    free_usdt: float,
    price: float,
    conf: float,
    conf_floor: float,
) -> tuple[str, float]:
    """
    Return (qty string for Bybit, effective stake fraction used).
    """
    min_q, max_q = _qty_bounds()
    base_frac = _env_float("BYBIT_DEMO_ORDER_STAKE_FRAC", 0.001)
    base_frac = max(1e-6, min(1.0, base_frac))
    t = _confidence_t(conf, conf_floor)
    eff_frac = base_frac * (0.5 + 0.5 * t)
    eff_frac = min(eff_frac, 1.0)
    notional = free_usdt * eff_frac
    if price <= 0:
        return "", eff_frac
    raw_qty = notional / price
    raw_qty = max(min_q, min(max_q, raw_qty))
    step = 0.001
    q = math.floor(raw_qty / step) * step
    if q + 1e-12 < min_q:
        return "", eff_frac
    s = f"{q:.6f}".rstrip("0").rstrip(".")
    return (s if s else str(min_q)), eff_frac


def _direction_confidence(pred: dict | None) -> float:
    d = ((pred or {}).get("predictions") or {}).get("direction_logistic") or {}
    try:
        return float(d.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Bybit demo market order from btc_prediction_output.json")
    ap.add_argument(
        "--from-swarm-signal",
        action="store_true",
        help="Trade from swarm-governed demo signal JSON (BTCUSDT linear); not decide_forceenter_intent.",
    )
    ap.add_argument(
        "--refresh-swarm-signal",
        action="store_true",
        help="Regenerate demo signal JSON (compute_swarm + governance) before reading.",
    )
    ap.add_argument(
        "--swarm-signal-json",
        type=Path,
        default=None,
        help="Path to bybitapidemo signal JSON (default: SYGNIF_BYBIT_DEMO_SIGNAL_JSON or prediction_agent default).",
    )
    ap.add_argument(
        "--prediction-json",
        type=Path,
        default=_PA / "btc_prediction_output.json",
        help="Path to btc_prediction_output.json",
    )
    ap.add_argument(
        "--training-json",
        type=Path,
        default=_PA / "training_channel_output.json",
        help="Path to training_channel_output.json (R01)",
    )
    ap.add_argument("--symbol", default="BTCUSDT", help="Linear symbol, e.g. BTCUSDT")
    ap.add_argument(
        "--manual-qty",
        default=None,
        metavar="QTY",
        help="Override automated BTC qty (default: stake_frac * wallet / price from confidence)",
    )
    ap.add_argument(
        "--manual-leverage",
        type=float,
        default=None,
        metavar="N",
        help="Override automated leverage (default: map direction_logistic confidence to min–max)",
    )
    ap.add_argument(
        "--position-idx",
        type=int,
        default=int(os.environ.get("BYBIT_DEMO_POSITION_IDX", "0") or 0),
        help="0 one-way, 1 long hedge, 2 short hedge",
    )
    ap.add_argument("--min-dir-conf", type=float, default=65.0, help="direction_logistic min confidence for MIXED")
    ap.add_argument(
        "--allow-short",
        action="store_true",
        help="Allow BEARISH → short demo ``Sell`` (default: long only).",
    )
    ap.add_argument("--execute", action="store_true", help="Set leverage + place market order (requires ACK env)")
    args = ap.parse_args()

    pred = _load_json(args.prediction_json)
    train = _load_json(args.training_json)
    cons = ""
    dlog: dict = {}

    if args.from_swarm_signal:
        if args.refresh_swarm_signal:
            bdexp.write_signal_json(_REPO)
        sig_path = args.swarm_signal_json or bdexp.signal_output_path(_REPO)
        payload = _load_json(sig_path)
        if not payload:
            print(f"No swarm demo signal at {sig_path} (try --refresh-swarm-signal). Abort.")
            return 2
        if not payload.get("signal_active"):
            print(f"signal_active=false; governance={payload.get('governance')!r}. Abort.")
            return 2
        move = str(payload.get("predicted_move") or "").strip().lower()
        gov = payload.get("governance") if isinstance(payload.get("governance"), dict) else {}
        try:
            conf = float(gov.get("governance_probability_pct") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        sw = payload.get("swarm") if isinstance(payload.get("swarm"), dict) else {}
        cons = str(sw.get("swarm_label") or "swarm_signal")
        if move == "up":
            intent = {"side": "long", "reason": "swarm_predicted_move_up"}
        elif move == "down":
            if not args.allow_short:
                print("predicted_move=down requires --allow-short for demo Sell. Abort.")
                return 3
            intent = {"side": "short", "reason": "swarm_predicted_move_down"}
        else:
            print(f"predicted_move={move!r} not tradable (need up|down). Abort.")
            return 2
    else:
        intent = decide_forceenter_intent(
            train,
            pred,
            allow_short=args.allow_short,
            direction_min_confidence=args.min_dir_conf,
        )
        if not intent:
            print("No intent from analysis (consensus / R01 / confidence). Abort.")
            return 2
        if intent["side"] not in ("long", "short"):
            print(f"Intent side {intent['side']!r} unsupported. Abort.")
            return 3
        conf = _direction_confidence(pred)
        preds = (pred or {}).get("predictions") or {}
        cons = str(preds.get("consensus_nautilus_enhanced") or preds.get("consensus") or "")
        dlog = preds.get("direction_logistic") or {}
    lo_lev, hi_lev = _leverage_bounds()
    if args.manual_leverage is not None:
        lev = int(round(float(args.manual_leverage)))
        lev = max(lo_lev, min(hi_lev, lev))
        lev_source = "manual"
    else:
        lev = auto_leverage(conf, args.min_dir_conf)
        lev_source = f"auto (conf={conf:.1f} → {lo_lev}..{hi_lev}x)"

    price = public_linear_last_price(args.symbol)
    if price is None:
        try:
            pc = float((pred or {}).get("current_close") or 0.0)
            price = pc if pc > 0 else None
        except (TypeError, ValueError):
            price = None

    qty_s: str
    qty_source: str
    eff_frac = 0.0
    if args.manual_qty is not None and str(args.manual_qty).strip():
        qty_s = str(args.manual_qty).strip()
        qty_source = "manual"
    else:
        w = blh.wallet_balance_unified_coin("USDT")
        free = _parse_usdt_available(w)
        if free is None or free <= 0:
            print("Automated qty: wallet USDT unavailable or zero (check demo keys / retCode). Abort.")
            print("wallet-balance response:", w)
            return 4
        if price is None or price <= 0:
            print("Automated qty: could not resolve last price (ticker + current_close). Abort.")
            return 4
        qty_s, eff_frac = auto_qty_btc(
            free_usdt=free,
            price=price,
            conf=conf,
            conf_floor=args.min_dir_conf,
        )
        if not qty_s:
            print("Automated qty: below min qty after rounding. Abort.")
            return 4
        qty_source = f"auto (free≈{free:.2f} USDT, stake_eff≈{eff_frac:.6f}, px≈{price:.2f})"

    print("--- plan ---")
    print(f"intent: {intent}")
    print(f"consensus: {cons!r}  direction_logistic: {dlog}")
    _side = "Buy" if intent["side"] == "long" else "Sell"
    print(f"symbol={args.symbol} side={_side} qty={qty_s} ({qty_source}) leverage={lev}x ({lev_source}) positionIdx={args.position_idx}")
    if cons.upper() == "MIXED":
        print(
            "Note: consensus is MIXED — RF/XGB next_mean may disagree with direction_logistic; "
            "automation still keys off logreg confidence."
        )

    if not args.execute:
        print("Dry-run only. Pass --execute and set SYGNIF_PREDICTION_DEMO_TRADE_ACK=YES to send orders.")
        return 0

    ack = os.environ.get("SYGNIF_PREDICTION_DEMO_TRADE_ACK", "").strip().upper()
    if ack != "YES":
        print("Refusing --execute: set SYGNIF_PREDICTION_DEMO_TRADE_ACK=YES after reviewing the plan.")
        return 5

    lr = blh.set_linear_leverage(args.symbol, str(lev))
    print("set-leverage:", lr)
    if lr.get("retCode") != 0:
        return 6

    order_side = "Buy" if intent["side"] == "long" else "Sell"
    mo = blh.create_market_order(
        args.symbol,
        order_side,
        qty_s,
        args.position_idx,
        reduce_only=False,
    )
    print("order/create:", mo)
    return 0 if mo.get("retCode") == 0 else 7


if __name__ == "__main__":
    raise SystemExit(main())
