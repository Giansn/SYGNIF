#!/usr/bin/env python3
"""sygnif_training_scanner.py — V2 EXPERIMENTAL: full demo portfolio access.

V1 used fixed $5 risk + max 1 concurrent. V2 is the experimental phase:
the trader has agency over its own size, concurrency, and signal threshold
via the adaptive policy module. Goal: generate diverse training data
across the full action space.

Per cycle (every 5 min):
  1. Read equity + check daily-loss pause (hard 30% limit)
  2. Adapt policy from recent outcomes (cooldown 10 min between adapts)
  3. Fetch BTCUSDT 5m klines, compute TA, score signal (0..4)
  4. Apply policy.min_score gate (adaptive) → SKIP or PROPOSE
  5. Compute risk_usd via policy (signal_score × base_risk_pct × equity)
  6. Compute qty so SL-distance × qty = risk_usd
  7. Apply policy.max_concurrent gate (adaptive)
  8. Emit decision.snapshot (with policy state embedded)
  9. If trade: place demo order with TP/SL bracket, emit decision.executed
 10. Outcomes flow through existing Phase 2 chain

Hard floors enforced regardless of policy:
  • risk_per_trade ≤ 10% of equity
  • max_concurrent ≤ 5
  • daily_loss ≥ -30% of equity → auto-pause
  • DEMO ONLY (refuses to trade in live mode)
  • circuit_breaker.json tripped → no new trades

Run:
  python3 /opt/sygnif-services/sygnif_training_scanner.py            # one pass
  python3 /opt/sygnif-services/sygnif_training_scanner.py --dry-run  # report only
  python3 /opt/sygnif-services/sygnif_training_scanner.py --policy   # show policy
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from typing import Any


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
def _load_env_file(path: str) -> None:
    if not os.path.exists(path): return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env_file("/etc/sygnif/trader.env")
_load_env_file("/etc/sygnif/training-scanner.env")

# DEMO ONLY — refuse live regardless of env
if os.environ.get("SYGNIF_TRAINING_LIVE_BLOCK", "1") == "1":
    if (os.environ.get("SYGNIF_ORDERS_MODE") or "").lower() == "live":
        print("REFUSING: live mode + training_scanner. Set SYGNIF_TRAINING_LIVE_BLOCK=0 to override.",
              file=sys.stderr)
        sys.exit(2)
os.environ["SYGNIF_ORDERS_MODE"] = "demo"

sys.path.insert(0, "/home/ubuntu/sygnif-agent-mirror")
sys.path.insert(0, "/opt/sygnif-services")
try:
    import sygnif_neurons as N
    from agent import decision_snapshot as DS
    import sygnif_training_policy as POL
except Exception as e:
    print(f"FATAL: import failed: {e}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DRY_RUN     = os.environ.get("SYGNIF_TRAINING_DRY_RUN", "0") == "1"
TP_PCT      = float(os.environ.get("SYGNIF_TRAINING_TP_PCT", "0.4")) / 100
SL_PCT      = float(os.environ.get("SYGNIF_TRAINING_SL_PCT", "0.25")) / 100
# LEVERAGE is now POLICY-DRIVEN (per-trade). The env value is just a
# fallback floor used when no signal score is available.
DEFAULT_LEVERAGE = int(os.environ.get("SYGNIF_TRAINING_LEVERAGE", "5"))
DB          = "/var/lib/sygnif/swarm.db"
SYMBOL      = "BTCUSDT"


# ---------------------------------------------------------------------------
# Bybit V5 kline fetch (public)
# ---------------------------------------------------------------------------
def fetch_klines(symbol: str = SYMBOL, interval: str = "5",
                  limit: int = 60) -> list[dict]:
    host = (os.environ.get("BYBIT_DEMO_API_BASE")
            or "https://api-demo.bybit.com").rstrip("/")
    qs = urllib.parse.urlencode({
        "category": "linear", "symbol": symbol,
        "interval": interval, "limit": str(limit),
    })
    try:
        body = urllib.request.urlopen(
            urllib.request.Request(f"{host}/v5/market/kline?{qs}"),
            timeout=8).read()
        d = json.loads(body)
    except Exception as e:
        print(f"  fetch_klines failed: {e}", file=sys.stderr)
        return []
    rows = (d.get("result") or {}).get("list") or []
    out = []
    for r in rows:
        try:
            out.append({
                "ts_ms_open": int(r[0]), "open": float(r[1]),
                "high":       float(r[2]), "low":  float(r[3]),
                "close":      float(r[4]), "volume": float(r[5]),
            })
        except (ValueError, TypeError, IndexError):
            continue
    out.reverse()  # newest-first → oldest-first
    return out


def _compute_m5_momentum(symbol: str = None) -> float | None:
    """5-min % change in close price from 1-min Bybit klines.
    Returns None on fetch failure. Used by m5 veto."""
    bars = fetch_klines(symbol or SYMBOL, "1", 7)
    if len(bars) < 6:
        return None
    px_now  = bars[-1]["close"]
    px_then = bars[-6]["close"]
    if px_then <= 0:
        return None
    return (px_now - px_then) / px_then * 100.0


M5_VETO_THRESHOLD = float(os.environ.get("SYGNIF_M5_VETO_PCT", "0.15"))


# ---------------------------------------------------------------------------
# Simple TA (no numpy)
# ---------------------------------------------------------------------------
def ema(values: list[float], period: int) -> list[float]:
    if not values or period <= 0: return []
    k = 2 / (period + 1); out = [values[0]]
    for v in values[1:]: out.append(out[-1] + k * (v - out[-1]))
    return out


def rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1: return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0)); losses.append(abs(min(diff, 0)))
    if len(gains) < period: return None
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0: return 100.0
    return 100 - (100 / (1 + avg_g / avg_l))


def macd_cross(closes, fast=12, slow=26, signal=9) -> int:
    if len(closes) < slow + signal: return 0
    ef = ema(closes, fast); es = ema(closes, slow)
    macd_line = [ef[i] - es[i] for i in range(len(ef))]
    sig_line = ema(macd_line, signal)
    if len(sig_line) < 3: return 0
    diff_now  = macd_line[-1] - sig_line[-1]
    diff_prev = macd_line[-2] - sig_line[-2]
    if diff_prev < 0 and diff_now >= 0: return +1
    if diff_prev > 0 and diff_now <= 0: return -1
    return 0


def momentum_pct(closes, lookback=6) -> float:
    if len(closes) < lookback + 1: return 0.0
    return (closes[-1] - closes[-lookback - 1]) / closes[-lookback - 1] * 100


def trend_dir(closes, fast=9, slow=21) -> int:
    if len(closes) < slow: return 0
    ef = ema(closes, fast); es = ema(closes, slow)
    if ef[-1] > es[-1]: return +1
    if ef[-1] < es[-1]: return -1
    return 0


def score_signal(bars: list[dict]) -> dict:
    if len(bars) < 30:
        return {"score": 0, "direction": "skip", "reason": "insufficient bars"}
    closes = [b["close"] for b in bars]
    rsi_v = rsi(closes, 14) or 50
    macd_x = macd_cross(closes)
    mom = momentum_pct(closes, 6)
    trend = trend_dir(closes, 9, 21)

    long_s = 0; short_s = 0; reasons = []
    if rsi_v < 30:   long_s += 1; reasons.append(f"RSI={rsi_v:.0f}<30 oversold→long")
    elif rsi_v > 70: short_s += 1; reasons.append(f"RSI={rsi_v:.0f}>70 overbought→short")
    if macd_x > 0:   long_s += 1; reasons.append("MACD bullish cross")
    elif macd_x < 0: short_s += 1; reasons.append("MACD bearish cross")
    if mom > 0.20:   long_s += 1; reasons.append(f"momentum +{mom:.2f}%")
    elif mom < -0.20: short_s += 1; reasons.append(f"momentum {mom:.2f}%")
    if trend > 0:    long_s += 1; reasons.append("EMA9>EMA21 uptrend")
    elif trend < 0:  short_s += 1; reasons.append("EMA9<EMA21 downtrend")

    if long_s > short_s:
        direction = "long"; score = long_s
    elif short_s > long_s:
        direction = "short"; score = short_s
    else:
        direction = "skip"; score = max(long_s, short_s)

    return {
        "score":          score,
        "long_score":     long_s,
        "short_score":    short_s,
        "direction":      direction,
        "rsi":            round(rsi_v, 1),
        "macd_cross":     macd_x,
        "momentum_pct":   round(mom, 3),
        "trend":          trend,
        "reasons":        reasons,
        "bars_used":      len(bars),
        "last_close":     closes[-1],
    }


# ---------------------------------------------------------------------------
# Wallet / position helpers
# ---------------------------------------------------------------------------
def get_demo_equity() -> float:
    try:
        r = N.run("wallet.demo", {})
        if not r.get("ok"): return 0
        d = r.get("data") or {}
        lst = (d.get("result") or {}).get("list") or []
        if not lst: return 0
        return float((lst[0].get("totalEquity") or 0))
    except Exception:
        return 0


def count_open_positions() -> int:
    try:
        r = N.run("portfolio.demo", {})
        if not r.get("ok"): return 0
        opens = (r.get("data") or {}).get("open") or []
        return sum(1 for p in opens if (p.get("symbol") or "") == SYMBOL)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------
def ensure_leverage_set(target: int) -> tuple[bool, str, float | None]:
    """Set account-symbol leverage to `target` (or report current if cannot
    read). Returns (ok, err_msg, actual_leverage_used).

    Bybit V5 leverage is account+symbol scoped. set_leverage with the
    current value returns retCode 110043 ('not modified', treated as ok).
    Trying to change leverage with an open position on the symbol can
    fail (Bybit rejects); we fall back to whatever's currently set."""
    try:
        from order import perp as _perp
        # Try to set first (idempotent)
        out = _perp.set_leverage(SYMBOL, float(target), mode="demo",
                                   confirm=True, i_understand="i understand")
        if out.get("ok"):
            return (True, "", float(target))
        # Set failed — read current and use that as fallback
        cur = _perp.get_current_leverage(SYMBOL, mode="demo")
        err = (out.get("blocked_reason")
               or (out.get("raw") or {}).get("retMsg")
               or "set_failed")
        return (False, err, cur)
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}", None)


def calc_qty(entry: float, stop_pct: float, risk_usd: float,
              max_qty_btc: float = 0.5) -> float:
    """qty so that (entry × stop_pct) × qty = risk_usd, capped at max_qty_btc."""
    stop_dist = entry * stop_pct
    if stop_dist <= 0: return 0
    qty = risk_usd / stop_dist
    return round(min(qty, max_qty_btc), 3)


def place_demo(side: str, entry: float, cid: str, qty: float) -> dict:
    bybit_side = "Buy" if side == "long" else "Sell"
    if side == "long":
        sl = round(entry * (1 - SL_PCT), 1); tp = round(entry * (1 + TP_PCT), 1)
    else:
        sl = round(entry * (1 + SL_PCT), 1); tp = round(entry * (1 - TP_PCT), 1)
    olid = f"sygTRN{cid[:14].replace('-', '')}"
    args = {"symbol": SYMBOL, "side": bybit_side, "qty": qty,
            "type": "Market", "take_profit": tp, "stop_loss": sl,
            "order_link_id": olid, "confirm": True}
    try:
        r = N.run("order.demo.perp", args)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "order_link_id": olid}
    return {**(r if isinstance(r, dict) else {}),
            "order_link_id": olid, "tp_price": tp, "sl_price": sl}


def emit_executed(cid: str, side: str, qty: float, order: dict, sig: dict,
                    pol_view: dict, lev_requested: int,
                    lev_actual: float | None) -> None:
    olid = order.get("order_link_id") or ""
    executed = bool(order.get("ok"))
    try:
        N.run("swarm.write", {
            "content": (f"EXECUTED [demo] correlation_id={cid[:8]} "
                        f"executed={executed} legs=1 olid={olid[:14]} "
                        f"qty={qty} risk_usd=${pol_view.get('risk_usd_for_trade',0):.2f} "
                        f"lev={lev_actual or '?'}× (req {lev_requested}×)"),
            "swarm_id":  "trading", "agent_id": "sygnif-training-scanner",
            "topic":     "decision.executed",
            "tags":      ["decision", "executed", "demo", "training"],
            "meta": {
                "correlation_id":     cid, "env": "demo", "executed": executed,
                "mode":               "demo", "order_link_ids": [olid],
                "structure":          f"training_perp_{side}",
                "strategy":           "training_scanner_v2",
                "instrument":         "perp",
                "leverage_tier":      "training_adaptive",
                "size_tier":          "default",
                "leverage_requested": lev_requested,
                "leverage_actual":    lev_actual,
                "tp_price":           order.get("tp_price"),
                "sl_price":           order.get("sl_price"),
                "qty":                qty,
                "risk_usd":           pol_view.get("risk_usd_for_trade"),
                "exchange_error":     order.get("error"),
                "signal":             sig,
                "policy":             pol_view,
            },
        })
    except Exception as e:
        print(f"  emit_executed failed: {e}", file=sys.stderr)


def build_plan(sig: dict, qty: float, risk_usd: float, pol_view: dict,
                will_propose: bool, leverage: int) -> dict:
    direction = sig.get("direction")
    return {
        "action":        "propose" if will_propose else "skip",
        "structure":     f"training_perp_{direction}",
        "strategy":      "training_scanner_v2",
        "instrument":    "perp",
        "symbol":        SYMBOL,
        "leverage":      leverage,
        "qty":           qty,
        "risk_pct":      round(risk_usd / max(get_demo_equity(), 1) * 100, 4),
        "stop_pct":      SL_PCT * 100,
        "max_loss_usd":  risk_usd,
        "F":             sig.get("last_close"),
        "thesis":        (f"training_v2 score={sig.get('score')} "
                          f"dir={direction} risk=${risk_usd:.2f} "
                          f"reasons={sig.get('reasons')}"),
        "reason":        None if will_propose else (
            f"score={sig.get('score')} below policy.min_score={pol_view.get('min_score')}"),
        "rule":          None if will_propose else "training_below_min_score",
        "context":       {
            "regime":        "experimental",
            "F":             sig.get("last_close"),
            "rsi":           sig.get("rsi"),
            "momentum_pct":  sig.get("momentum_pct"),
            "policy":        pol_view,
        },
        "tier_promotion": {
            "env": "demo", "kill_switch": False, "staged": True,
            "candidates": {}, "promotions": {},
            "skipped_reason": "training_scanner_v2_uses_adaptive_policy",
        },
    }


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force",   action="store_true")
    ap.add_argument("--policy",  action="store_true",
                    help="show policy state and exit")
    args = ap.parse_args()

    if args.policy:
        s = POL.get_policy()
        print(json.dumps(s, indent=2, default=str))
        return 0

    cycle_start = dt.datetime.now(tz=dt.timezone.utc)
    print(f"=== training_scanner V2 @ {cycle_start.isoformat()} ===")

    equity = get_demo_equity()
    print(f"  equity: ${equity:.2f}")

    # Daily-loss circuit
    paused, loss_today, loss_limit = POL.is_paused_for_loss(equity)
    if paused:
        print(f"  PAUSED daily loss ${loss_today:.2f} ≤ ${loss_limit:.2f}")
        return 0
    print(f"  loss_today: ${loss_today:.2f} (limit ${loss_limit:.2f})")

    # Adapt (cooldown-protected)
    state_before = POL.get_policy()
    state = POL.adapt(equity)
    if state.get("version", 0) > state_before.get("version", 0):
        print(f"  POLICY ADAPTED v{state_before.get('version')} → v{state['version']}")
        print(f"    base_risk_pct: {state_before.get('base_risk_pct')} → {state.get('base_risk_pct')}")
        print(f"    max_concurrent: {state_before.get('max_concurrent')} → {state.get('max_concurrent')}")
        print(f"    min_score: {state_before.get('min_score')} → {state.get('min_score')}")
    print(f"  policy: base_risk={state.get('base_risk_pct')*100:.2f}%  "
          f"max_concur={state.get('max_concurrent')}  "
          f"min_score={state.get('min_score')}  "
          f"lev_mult={state.get('leverage_mult', 1.0):.2f}")

    # Signal
    bars = fetch_klines(SYMBOL, "5", 60)
    if len(bars) < 30:
        print(f"  insufficient klines ({len(bars)}); skip"); return 0
    sig = score_signal(bars)
    print(f"  signal: dir={sig['direction']} score={sig['score']} "
          f"(L{sig['long_score']}/S{sig['short_score']}) "
          f"rsi={sig['rsi']} mom={sig['momentum_pct']:+.2f}% "
          f"trend={sig['trend']:+d}")
    for r in sig.get("reasons", []):
        print(f"    • {r}")

    # 2026-05-10: bounce protocol — fast move → expect counter-move.
    # Reads from sygnif-bounce-watcher.service (kline.1 WS, sub-minute fresh).
    try:
        from agent import bounce_protocol as _BP
        bounce = _BP.get_bounce_setup_live()
    except Exception as _e:
        bounce = {"ok": False, "active": False, "error": str(_e)}

    if bounce.get("active"):
        b_dir = bounce.get("direction")
        b_mag = bounce.get("magnitude_abs_pct", 0)
        b_age = bounce.get("pivot_age_min", 0)
        print(f"  BOUNCE active: {b_dir} (move {b_mag:.2f}% pivot {b_age:.0f}min ago "
              f"target +{bounce.get('expected_target_pct',0):.2f}%)")
        if sig["direction"] == b_dir:
            # Confirmation — boost score by 1
            sig["score"] += 1
            sig["reasons"].append(f"BOUNCE confirms (mag {b_mag:.2f}% pivot {b_age:.0f}min)")
            print(f"  → BOUNCE confirms direction; score boosted to {sig['score']}")
        elif sig["direction"] == "skip":
            # No prior signal — bounce IS the signal
            sig["direction"] = b_dir
            sig["score"] = 2  # treat as moderate-conviction
            sig["reasons"].append(f"BOUNCE override (was skip; mag {b_mag:.2f}% pivot {b_age:.0f}min)")
            sig["bounce_override"] = True
            print(f"  → BOUNCE overrides skip → {b_dir} score={sig['score']}")
        else:
            # Direction conflict — bounce wins (fast move trumps TA lag)
            old_dir = sig["direction"]
            sig["direction"] = b_dir
            sig["score"] = max(sig["score"], 2) + 1   # bounce conviction
            sig["reasons"].append(f"BOUNCE OVERRIDE (was {old_dir}; mag {b_mag:.2f}% pivot {b_age:.0f}min)")
            sig["bounce_override"] = True
            print(f"  → BOUNCE overrides {old_dir} → {b_dir} score={sig['score']}")
        sig["bounce_setup"] = bounce

    # Decide if we propose
    pol_view = POL.snapshot_for_decision(equity, sig.get("score", 0))
    risk_usd = pol_view.get("risk_usd_for_trade") or 0
    leverage_chosen = POL.leverage_for_trade(sig.get("score", 0), state)
    # 2026-05-11: respect bounce_v2 claim — skip same-direction trades.
    try:
        from agent import strategy_claim as _CL
        _claim_active = _CL.active()
        _claim_dir = (_claim_active or {}).get("direction") if _claim_active else None
        if _claim_dir and _claim_dir == sig.get("direction"):
            sig["direction"] = "skip"
            sig["reasons"].append(f"BLOCKED by active claim ({_claim_active.get('owner')})")
            print(f"  → CLAIM BLOCK: {_claim_active.get('owner')} owns {_claim_dir}")
    except Exception: pass

    # 2026-05-11: M5 momentum veto — don't short rallies / long dumps.
    # Audit on 24h of losses: this filter avoids ~$46 of -$222 daily bleed.
    if sig.get("direction") in ("long", "short"):
        m5_pct = _compute_m5_momentum()
        if m5_pct is not None:
            sig["m5_pct"] = round(m5_pct, 3)
            sig["reasons"].append(f"m5={m5_pct:+.2f}%")
            if sig["direction"] == "short" and m5_pct >= M5_VETO_THRESHOLD:
                sig["direction"] = "skip"
                sig["reasons"].append(f"M5_VETO short@strength m5={m5_pct:+.2f}%")
                print(f"  → M5 VETO: SHORT into m5 momentum +{m5_pct:.2f}% blocked")
            elif sig["direction"] == "long" and m5_pct <= -M5_VETO_THRESHOLD:
                sig["direction"] = "skip"
                sig["reasons"].append(f"M5_VETO long@weakness m5={m5_pct:+.2f}%")
                print(f"  → M5 VETO: LONG into m5 momentum {m5_pct:.2f}% blocked")

    will_propose = (sig["direction"] != "skip"
                    and sig.get("score", 0) >= POL.min_score(state))
    qty = (calc_qty(sig["last_close"], SL_PCT, risk_usd)
           if will_propose else 0)

    # ALWAYS emit snapshot (skip context is training data too)
    plan = build_plan(sig, qty, risk_usd, pol_view, will_propose,
                       leverage_chosen)
    cid = DS.write_snapshot(plan)
    plan["correlation_id"] = cid
    print(f"  decision.snapshot: cid={cid[:8]}  "
          f"action={plan['action']}  qty={qty}  "
          f"risk=${risk_usd:.2f}  leverage={leverage_chosen}× (score={sig.get('score')})")

    if not will_propose:
        why = ("signal=skip" if sig["direction"] == "skip"
               else f"score {sig['score']} < min {POL.min_score(state)}")
        print(f"  → SKIP ({why})")
        return 0

    # Concurrency guard
    if not args.force:
        n_open = count_open_positions()
        if n_open >= POL.max_concurrent(state):
            print(f"  → BLOCKED: concurrent {n_open}/{POL.max_concurrent(state)}")
            return 0

    # Bybit hard caps (max_perp_qty_btc — skip if our qty exceeds)
    max_qty = float(os.environ.get("SYGNIF_MAX_PERP_QTY_BTC", "0.5"))
    if qty > max_qty:
        print(f"  → CLIPPED: qty {qty} > SYGNIF_MAX_PERP_QTY_BTC ({max_qty}); shrinking")
        qty = max_qty

    # Circuit breaker
    cb = pathlib.Path("/var/lib/sygnif/circuit_breaker.json")
    if cb.exists():
        try:
            data = json.loads(cb.read_text())
            if data.get("state") == "tripped":
                print(f"  → BLOCKED: circuit breaker tripped ({data.get('reason')})")
                return 0
        except Exception: pass

    if DRY_RUN or args.dry_run:
        print(f"  → DRY_RUN: would place {sig['direction']} qty={qty} "
              f"@ ${sig['last_close']:.1f} lev={leverage_chosen}×")
        fake = {"ok": False, "error": "dry_run",
                "order_link_id": f"sygTRN{cid[:14].replace('-','')}",
                "tp_price": None, "sl_price": None}
        emit_executed(cid, sig["direction"], qty, fake, sig, pol_view,
                       leverage_chosen, None)
        return 0

    # Set leverage to the score-derived value, then place trade.
    # If set_leverage fails (e.g., open position blocks change), we still
    # place the order at whatever leverage is currently active and log it.
    lev_ok, lev_err, lev_actual = ensure_leverage_set(leverage_chosen)
    if lev_actual is None:
        lev_actual = leverage_chosen   # best assumption when read failed
    print(f"  leverage: requested {leverage_chosen}× → "
          f"set ok={lev_ok}, actual={lev_actual}× err={lev_err}")

    print(f"  → PLACING {sig['direction']} qty={qty} @ ${sig['last_close']:.1f} "
          f"risk=${risk_usd:.2f} lev={lev_actual}×")
    order = place_demo(sig["direction"], sig["last_close"], cid, qty)
    err_msg = (order.get("error")
               or order.get("blocked_reason")
               or (order.get("raw") or {}).get("retMsg"))
    print(f"  order: ok={order.get('ok')} olid={order.get('order_link_id')} "
          f"err={err_msg}")
    emit_executed(cid, sig["direction"], qty, order, sig, pol_view,
                   leverage_chosen, lev_actual)
    return 0


if __name__ == "__main__":
    sys.exit(main())
