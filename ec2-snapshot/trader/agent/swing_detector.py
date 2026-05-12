"""agent/swing_detector.py — confluence-based top/bottom detector.

Rather than "30-min move ≥ 1.5% → fire opposite", this detector scores
10 INDEPENDENT signals. Score ≥ 5 = high-confluence setup. Signals come
from price action, structural levels, and positioning (options/funding/whales).

API:
  score_top(bars_1m, bars_5m, ctx) → dict  (with score, signals_fired, target)
  score_bottom(bars_1m, bars_5m, ctx) → dict
  detect(symbol="BTCUSDT") → dict
      composite — fetches data, runs both scorers, returns whichever is dominant

Returns shape:
  {
    "side":            "top_short" | "bottom_long" | "none",
    "score":           int 0..10,
    "signals_fired":   list[str],
    "signals_missed":  list[str],
    "entry":           float (recommended entry price),
    "tp":              float,
    "sl":              float,
    "thesis":          str (one-line),
    "ts_utc":          str,
  }
"""
from __future__ import annotations

import datetime as dt
import json
import math
import urllib.parse
import urllib.request
from typing import Any

# Thresholds (env-overridable via gate_params later)
TP_RATIO_OF_SWING = 0.60     # target = 60% retrace of last swing
SL_PCT_FROM_ENTRY = 0.40 / 100
MIN_SCORE = 5                 # 5/10 signals to consider firing

# Score requires at least N "high-quality" signals among the price-action ones
# Otherwise we're trading structural levels alone — too noisy.


def _ema(values, period):
    if not values or period <= 0: return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def _rsi(closes, period=14):
    if len(closes) < period + 1: return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i-1]
        if d > 0: gains += d
        else: losses += -d
    avg_g = gains / period
    avg_l = losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i-1]
        avg_g = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
    if avg_l == 0: return 100.0
    return 100 - (100 / (1 + avg_g / avg_l))


def _fetch_klines(symbol, interval, limit):
    qs = urllib.parse.urlencode({"category":"linear","symbol":symbol,
                                   "interval":interval,"limit":limit})
    try:
        body = urllib.request.urlopen(
            f"https://api.bybit.com/v5/market/kline?{qs}", timeout=6).read()
        rows = (json.loads(body).get("result") or {}).get("list") or []
        rows.reverse()
        return [{"ts":int(r[0]), "o":float(r[1]), "h":float(r[2]),
                 "l":float(r[3]), "c":float(r[4]), "v":float(r[5])} for r in rows]
    except Exception:
        return []


def find_nearest_round_level(price: float, tol_pct: float = 0.30,
                                min_inc_pct_of_price: float = 0.5) -> tuple | None:
    """Asset-agnostic psych-level detector. Scales naturally with price
    magnitude — works for BTC at $20k, $80k, $200k, ETH at $2.4k, SOL at $96,
    DOGE at $0.20, etc.

    Args:
      tol_pct: only return a level if price is within this % of it
      min_inc_pct_of_price: only count increments ≥ this % of current price.
        Default 0.5% includes nearly all rounds (use for visualization).
        Set to 3.0+ for MEANINGFUL psych levels (per backtest 5000-bar study
        on BTC: only increments ≥ 3% of price show non-zero reversal edge).

    Returns (level, distance_pct, increment) or None.

    Examples (tol=0.30%):
      $81,167 min_inc=0.5% → (81000, 0.21%, 1000)    permissive  $1k spacing
      $81,167 min_inc=3.0% → None                     strict     $1k too small
      $80,100 min_inc=3.0% → (80000, 0.13%, 5000)    strict     $5k qualifies
      $20,150 min_inc=3.0% → (20000, 0.75%, 1000)    none — 0.75% > 0.30% tol
      $96.40  min_inc=3.0% → (96, 0.42%, 5)           SOL
    """
    if price <= 0: return None
    log_p = math.log10(price)
    candidates_inc = set()
    # Enumerate {1,2,5} × 10^n across magnitudes near log10(price)
    for n in range(int(log_p) - 3, int(log_p) + 2):
        for m in (1, 2, 5):
            inc = m * (10 ** n)
            # Only consider increments that produce meaningful psych spacing
            inc_pct = inc / price * 100
            if min_inc_pct_of_price <= inc_pct <= 50.0:
                candidates_inc.add(inc)
    best = None
    for inc in candidates_inc:
        level = round(price / inc) * inc
        dist_pct = abs(price - level) / price * 100
        # Prefer LARGER increment when distances are similar — larger increments
        # mark stronger psych levels (e.g., $80k is heavier than $81k)
        weight = inc / price
        score = dist_pct - weight * 0.05
        if best is None or score < best["score"]:
            best = {"level": level, "dist_pct": dist_pct,
                    "increment": inc, "score": score}
    if best and best["dist_pct"] <= tol_pct:
        return (best["level"], best["dist_pct"], best["increment"])
    return None


# ---------------------------------------------------------------------------
# Top-crashing scorer
# ---------------------------------------------------------------------------
def score_top(bars_5m: list[dict], bars_1h: list[dict],
               ctx: dict) -> dict:
    """Score the likelihood that price is at a TOP about to crash.

    bars_5m: at least 50 5-min bars
    bars_1h: at least 30 1-hour bars
    ctx: market features dict with keys like:
         max_pain_strike, top_call_oi_strike, funding_bps_per_8h,
         whale_imb_5m, whale_imb_15m, atm_iv
    """
    if not bars_5m or len(bars_5m) < 30:
        return {"score": 0, "signals_fired": [],
                "signals_missed": ["insufficient bars"]}

    fired = []; missed = []
    closes_5m = [b["c"] for b in bars_5m]
    highs_5m  = [b["h"] for b in bars_5m]
    lows_5m   = [b["l"] for b in bars_5m]
    vols_5m   = [b["v"] for b in bars_5m]
    last = bars_5m[-1]
    current = last["c"]

    # ── Price-level signals ──
    # 1. Within 0.3% of 4h recent swing high (48 5-min bars)
    sh_window = bars_5m[-min(48, len(bars_5m)):]
    swing_high_4h = max(b["h"] for b in sh_window)
    dist_4h_high_pct = abs(current - swing_high_4h) / swing_high_4h * 100
    if dist_4h_high_pct <= 0.30:
        fired.append(f"at_4h_high ({dist_4h_high_pct:.2f}%)")
    else:
        missed.append(f"4h_high {dist_4h_high_pct:.2f}% away")

    # 2. At a psych round level (asset-agnostic, MEANINGFUL only — min 3% of price)
    #    Per backtest (5000 BTC 1h bars): only increments ≥3% of price showed
    #    measurable reversal edge over baseline. Smaller "rounds" are noise.
    rl = find_nearest_round_level(current, tol_pct=0.30, min_inc_pct_of_price=3.0)
    if rl is not None and rl[0] >= current * 0.998:
        fired.append(f"at_round_${rl[0]:.0f} ({rl[1]:.2f}%, inc=${rl[2]:.0f})")
    else:
        missed.append("not at meaningful round level above")

    # 3. Within 0.5% of max_pain or top_call_oi_strike
    mp = ctx.get("max_pain_strike")
    tc = ctx.get("top_call_oi_strike")
    options_level_hit = False
    for label, strike in (("max_pain", mp), ("top_call_oi", tc)):
        if strike and isinstance(strike, (int, float)):
            d = abs(current - strike) / current * 100
            if d <= 0.50 and current <= strike:   # price below the resistance level
                fired.append(f"at_{label}_${int(strike)} ({d:.2f}%)")
                options_level_hit = True
                break
    if not options_level_hit:
        missed.append("not at options level")

    # 4. Fib extension — 1.272 or 1.414 of recent swing (4h)
    swing_low_4h = min(b["l"] for b in sh_window)
    swing_range = swing_high_4h - swing_low_4h
    if swing_range > 0:
        ext_127 = swing_low_4h + swing_range * 1.272
        ext_141 = swing_low_4h + swing_range * 1.414
        for lbl, ext in (("fib_1.272", ext_127), ("fib_1.414", ext_141)):
            d = abs(current - ext) / current * 100
            if d <= 0.30 and current >= swing_high_4h * 0.998:
                fired.append(f"at_{lbl} (${ext:.0f}, {d:.2f}%)")
                break
        else:
            missed.append("not at fib extension")
    else:
        missed.append("no swing range")

    # ── Price-action signals ──
    # 5. RSI(14) on 5m > 70
    rsi_5m = _rsi(closes_5m, 14)
    if rsi_5m is not None and rsi_5m > 70:
        fired.append(f"rsi_5m {rsi_5m:.0f}>70")
    elif rsi_5m is not None:
        missed.append(f"rsi_5m {rsi_5m:.0f}")
    else:
        missed.append("rsi_5m N/A")

    # 6. RSI(14) on 1h > 65 (overbought on higher TF)
    if bars_1h and len(bars_1h) >= 15:
        rsi_1h = _rsi([b["c"] for b in bars_1h], 14)
        if rsi_1h is not None and rsi_1h > 65:
            fired.append(f"rsi_1h {rsi_1h:.0f}>65")
        elif rsi_1h is not None:
            missed.append(f"rsi_1h {rsi_1h:.0f}")
    else:
        missed.append("rsi_1h insufficient")

    # 7. Last 5m bar: upper wick > 50% of total range
    last_range = last["h"] - last["l"]
    upper_wick = last["h"] - max(last["o"], last["c"])
    if last_range > 0 and upper_wick / last_range > 0.50:
        fired.append(f"upper_wick {upper_wick/last_range*100:.0f}%")
    else:
        missed.append("no upper wick")

    # 8. Last 3 bars cumulative > +0.5% (climax push)
    if len(bars_5m) >= 4:
        c3_pct = (closes_5m[-1] - closes_5m[-4]) / closes_5m[-4] * 100
        if c3_pct > 0.50:
            fired.append(f"3-bar climax +{c3_pct:.2f}%")
        else:
            missed.append(f"3-bar move {c3_pct:+.2f}%")

    # ── Positioning signals ──
    # 9. Whale flow flipped: 5m imb < 0.45 while 15m imb > 0.60
    imb_5m  = ctx.get("whale_imb_5m")
    imb_15m = ctx.get("whale_imb_15m")
    if isinstance(imb_5m, (int, float)) and isinstance(imb_15m, (int, float)):
        if imb_15m > 0.60 and imb_5m < 0.45:
            fired.append(f"whale_flip 15m={imb_15m:.2f}→5m={imb_5m:.2f}")
        else:
            missed.append(f"whale not flipped (15m={imb_15m:.2f} 5m={imb_5m:.2f})")
    else:
        missed.append("whale data N/A")

    # 10. Funding > +1bps/8h
    f_bps = ctx.get("funding_bps_per_8h")
    if isinstance(f_bps, (int, float)) and f_bps > 1.0:
        fired.append(f"funding +{f_bps:.2f}bps (longs paying)")
    elif isinstance(f_bps, (int, float)):
        missed.append(f"funding {f_bps:.2f}bps")
    else:
        missed.append("funding N/A")

    # 11. Orderbook depth imbalance — extreme ask-heavy stacking
    #     For TOP: imbalance ≤ 0.20 means ≥80% of top-5 depth is on ask side.
    #     This is distribution — sellers absorbing every buy ahead of breakdown.
    di = ctx.get("depth_imbalance_top5")
    if isinstance(di, (int, float)) and di <= 0.20:
        fired.append(f"depth_imb {di:.2f} (heavy ask wall)")
    elif isinstance(di, (int, float)):
        missed.append(f"depth_imb {di:.2f} (not extreme bearish)")
    else:
        missed.append("depth_imb N/A")

    score = len(fired)

    # Compute targets
    entry = current
    if swing_range > 0:
        tp = current - swing_range * TP_RATIO_OF_SWING
    else:
        # Fallback: ATM IV's 1d move scaled to short-term
        iv_move = ctx.get("implied_1d_move_pct") or 1.5
        tp = current * (1 - (iv_move / 4) / 100)   # 1/4 of daily move
    sl = current * (1 + SL_PCT_FROM_ENTRY)

    return {
        "side":           "top_short" if score >= MIN_SCORE else "none",
        "score":          score,
        "max_score":      11,
        "signals_fired":  fired,
        "signals_missed": missed,
        "entry":          round(entry, 1),
        "tp":             round(tp, 1),
        "sl":             round(sl, 1),
        "swing_low_4h":   round(swing_low_4h, 1),
        "swing_high_4h":  round(swing_high_4h, 1),
        "thesis":         (f"TOP at ${current:.0f} with {score}/10 signals: "
                            f"{', '.join(fired[:4])}"),
    }


# ---------------------------------------------------------------------------
# Bottom-bouncing scorer (mirror of top, with key sign flips)
# ---------------------------------------------------------------------------
def score_bottom(bars_5m: list[dict], bars_1h: list[dict],
                  ctx: dict) -> dict:
    if not bars_5m or len(bars_5m) < 30:
        return {"score": 0, "signals_fired": [],
                "signals_missed": ["insufficient bars"]}
    fired = []; missed = []
    closes_5m = [b["c"] for b in bars_5m]
    last = bars_5m[-1]
    current = last["c"]

    sl_window = bars_5m[-min(48, len(bars_5m)):]
    swing_low_4h = min(b["l"] for b in sl_window)
    swing_high_4h = max(b["h"] for b in sl_window)
    swing_range = swing_high_4h - swing_low_4h

    # 1. Within 0.3% of 4h swing low
    dist_pct = abs(current - swing_low_4h) / swing_low_4h * 100
    if dist_pct <= 0.30:
        fired.append(f"at_4h_low ({dist_pct:.2f}%)")
    else:
        missed.append(f"4h_low {dist_pct:.2f}% away")

    # 2. At a meaningful psych round level BELOW (support for long, ≥3% of price)
    rl = find_nearest_round_level(current, tol_pct=0.30, min_inc_pct_of_price=3.0)
    if rl is not None and rl[0] <= current * 1.002:
        fired.append(f"at_round_${rl[0]:.0f} ({rl[1]:.2f}%, inc=${rl[2]:.0f})")
    else:
        missed.append("not at meaningful round level below")

    # 3. At max_pain or top_put_oi (price near = support gravity)
    mp = ctx.get("max_pain_strike")
    tp_oi = ctx.get("top_put_oi_strike")
    hit = False
    for label, strike in (("max_pain", mp), ("top_put_oi", tp_oi)):
        if strike and isinstance(strike, (int, float)):
            d = abs(current - strike) / current * 100
            if d <= 0.50 and current >= strike:
                fired.append(f"at_{label}_${int(strike)} ({d:.2f}%)")
                hit = True; break
    if not hit: missed.append("not at options level")

    # 4. Fib retracement 0.618 or 0.786 of recent swing
    if swing_range > 0:
        fib_618 = swing_high_4h - swing_range * 0.618
        fib_786 = swing_high_4h - swing_range * 0.786
        for lbl, fib in (("fib_0.618", fib_618), ("fib_0.786", fib_786)):
            d = abs(current - fib) / current * 100
            if d <= 0.30 and current <= swing_low_4h * 1.002:
                fired.append(f"at_{lbl} (${fib:.0f}, {d:.2f}%)")
                break
        else:
            missed.append("not at fib retracement")
    else:
        missed.append("no swing range")

    # 5. RSI(14) on 5m < 30
    rsi_5m = _rsi(closes_5m, 14)
    if rsi_5m is not None and rsi_5m < 30:
        fired.append(f"rsi_5m {rsi_5m:.0f}<30")
    elif rsi_5m is not None:
        missed.append(f"rsi_5m {rsi_5m:.0f}")

    # 6. RSI(14) on 1h < 35
    if bars_1h and len(bars_1h) >= 15:
        rsi_1h = _rsi([b["c"] for b in bars_1h], 14)
        if rsi_1h is not None and rsi_1h < 35:
            fired.append(f"rsi_1h {rsi_1h:.0f}<35")
        elif rsi_1h is not None:
            missed.append(f"rsi_1h {rsi_1h:.0f}")

    # 7. Lower wick > 50% (rejection at bottom)
    last_range = last["h"] - last["l"]
    lower_wick = min(last["o"], last["c"]) - last["l"]
    if last_range > 0 and lower_wick / last_range > 0.50:
        fired.append(f"lower_wick {lower_wick/last_range*100:.0f}%")
    else:
        missed.append("no lower wick")

    # 8. Last 3 bars cumulative < -0.5% (capitulation push)
    if len(bars_5m) >= 4:
        c3_pct = (closes_5m[-1] - closes_5m[-4]) / closes_5m[-4] * 100
        if c3_pct < -0.50:
            fired.append(f"3-bar capitulation {c3_pct:.2f}%")
        else:
            missed.append(f"3-bar move {c3_pct:+.2f}%")

    # 9. Whale flip: 5m imb > 0.55 while 15m imb < 0.40 (buyers stepping in)
    imb_5m  = ctx.get("whale_imb_5m")
    imb_15m = ctx.get("whale_imb_15m")
    if isinstance(imb_5m, (int, float)) and isinstance(imb_15m, (int, float)):
        if imb_15m < 0.40 and imb_5m > 0.55:
            fired.append(f"whale_flip 15m={imb_15m:.2f}→5m={imb_5m:.2f}")
        else:
            missed.append(f"whale not flipped (15m={imb_15m:.2f} 5m={imb_5m:.2f})")

    # 10. Funding < -1bps (shorts paying = panic)
    f_bps = ctx.get("funding_bps_per_8h")
    if isinstance(f_bps, (int, float)) and f_bps < -1.0:
        fired.append(f"funding {f_bps:.2f}bps (shorts paying)")
    elif isinstance(f_bps, (int, float)):
        missed.append(f"funding {f_bps:.2f}bps")

    # 11. Orderbook depth imbalance — extreme bid-heavy stacking
    #     For BOTTOM: imbalance ≥ 0.80 means ≥80% of top-5 depth is on bid side.
    #     This is accumulation — buyers absorbing sells ahead of bounce.
    di = ctx.get("depth_imbalance_top5")
    if isinstance(di, (int, float)) and di >= 0.80:
        fired.append(f"depth_imb {di:.2f} (heavy bid wall)")
    elif isinstance(di, (int, float)):
        missed.append(f"depth_imb {di:.2f} (not extreme bullish)")
    else:
        missed.append("depth_imb N/A")

    score = len(fired)
    entry = current
    if swing_range > 0:
        tp = current + swing_range * TP_RATIO_OF_SWING
    else:
        iv_move = ctx.get("implied_1d_move_pct") or 1.5
        tp = current * (1 + (iv_move / 4) / 100)
    sl = current * (1 - SL_PCT_FROM_ENTRY)

    return {
        "side":           "bottom_long" if score >= MIN_SCORE else "none",
        "score":          score,
        "max_score":      11,
        "signals_fired":  fired,
        "signals_missed": missed,
        "entry":          round(entry, 1),
        "tp":             round(tp, 1),
        "sl":             round(sl, 1),
        "swing_low_4h":   round(swing_low_4h, 1),
        "swing_high_4h":  round(swing_high_4h, 1),
        "thesis":         (f"BOTTOM at ${current:.0f} with {score}/10 signals: "
                            f"{', '.join(fired[:4])}"),
    }


# ---------------------------------------------------------------------------
# Composite detector — fetches market context + runs both scorers
# ---------------------------------------------------------------------------
def detect(symbol: str = "BTCUSDT") -> dict:
    """Pulls fresh data + runs both scorers + returns whichever wins."""
    bars_5m = _fetch_klines(symbol, "5", 60)   # 5h of 5min bars
    bars_1h = _fetch_klines(symbol, "60", 30)  # 30h of 1h bars
    if not bars_5m:
        return {"side": "none", "score": 0, "error": "no klines"}

    # Build market context
    ctx = {}
    try:
        from agent import market_features as MF
        m = MF.get_market_context(symbol)
        opts = m.get("options", {}) or {}
        ob   = m.get("orderbook", {}) or {}
        ctx.update({
            "max_pain_strike":      opts.get("max_pain_strike"),
            "top_call_oi_strike":   opts.get("top_call_oi_strike"),
            "top_put_oi_strike":    opts.get("top_put_oi_strike"),
            "implied_1d_move_pct":  opts.get("implied_1d_move_pct"),
            "funding_bps_per_8h":   (m.get("perp", {}) or {}).get("funding_bps_per_8h"),
            "depth_imbalance_top5": ob.get("depth_imbalance_top5"),
            "depth_imbalance_top25": ob.get("depth_imbalance_top25"),
        })
    except Exception:
        pass

    # Whale flow
    try:
        with open("/var/lib/sygnif/whale_flow.json") as f:
            wf = json.load(f)
        ctx["whale_imb_15m"] = wf.get("whale_imbalance")
        bw = (wf.get("by_window") or {}).get("5m", {})
        ctx["whale_imb_5m"]  = bw.get("imbalance")
    except Exception: pass

    top    = score_top(bars_5m, bars_1h, ctx)
    bottom = score_bottom(bars_5m, bars_1h, ctx)

    # Choose the dominant side
    if top["score"] >= MIN_SCORE and top["score"] >= bottom["score"]:
        result = top
        result["alt_score_other_side"] = bottom["score"]
    elif bottom["score"] >= MIN_SCORE:
        result = bottom
        result["alt_score_other_side"] = top["score"]
    else:
        # No fire — return the higher-scoring one for visibility
        result = top if top["score"] >= bottom["score"] else bottom
        result["side"] = "none"  # explicit no-fire

    result["ts_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    result["ctx_used"] = ctx
    return result
