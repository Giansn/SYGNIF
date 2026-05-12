"""agent/market_features.py — Bybit V5 mainnet REST features for snapshots.

Polls public endpoints (no API key needed) at decision time, caches for 30s,
and returns a flat-ish dict of microstructure + options features that
decision_snapshot embeds.

Four data sources:
  1. Order book (depth, imbalance, walls, spread)        /v5/market/orderbook
  2. Perp ticker  (funding, OI, basis, vol)              /v5/market/tickers
  3. Funding history (drift detection)                   /v5/market/funding/history
  4. Options chain (ATM IV, skew, GEX, max-pain, P/C)    /v5/market/tickers?option
  5. Open interest history (delta, velocity, label)      /v5/market/open-interest

Each source is independent — failure of one doesn't block others.

Public API:
  get_market_context(symbol="BTCUSDT") → composite dict (use this from snapshot)
  get_orderbook_features(symbol)
  get_perp_metrics(symbol)
  get_funding_history_features(symbol, n=8)
  get_options_features(base_coin="BTC", nearest_expiry_only=True)
  get_oi_features(symbol)

In-process cache: 30 seconds per source. Resets on restart.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
import urllib.parse
import urllib.request
from typing import Any

API_BASE = "https://api.bybit.com"
HTTP_TIMEOUT = 6
CACHE_TTL_S = 30

_cache: dict[str, tuple[float, dict]] = {}


def _now() -> float:
    return time.time()


def _cached(key: str) -> dict | None:
    hit = _cache.get(key)
    if hit and (_now() - hit[0]) < CACHE_TTL_S:
        return hit[1]
    return None


def _store(key: str, val: dict) -> dict:
    _cache[key] = (_now(), val)
    return val


def _fetch(path: str, params: dict) -> dict:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{API_BASE}{path}?{qs}")
    body = urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read()
    return json.loads(body)


# ---------------------------------------------------------------------------
# 1) Order book
# ---------------------------------------------------------------------------
def get_orderbook_features(symbol: str = "BTCUSDT") -> dict:
    """Top-25 orderbook → imbalance, walls, spread.

    Returns:
      {
        ok, age_s, ts_ms,
        mid, bid_top, ask_top, spread_bps,
        bid_depth_top5, ask_depth_top5,        # in BTC
        bid_depth_top25, ask_depth_top25,
        depth_imbalance_top5,                  # 0..1, 0.5 balanced
        depth_imbalance_top25,
        bid_value_usd_top5, ask_value_usd_top5, # notional value
        bid_wall_max_size, bid_wall_max_price,  # largest single bid level
        ask_wall_max_size, ask_wall_max_price,  # largest single ask level
        bid_wall_distance_bps, ask_wall_distance_bps,
      }
    """
    ck = f"orderbook:{symbol}"
    if (c := _cached(ck)): return c
    out = {"ok": False}
    try:
        r = _fetch("/v5/market/orderbook",
                    {"category": "linear", "symbol": symbol, "limit": 25})
        res = r.get("result") or {}
        bids_raw = res.get("b") or []
        asks_raw = res.get("a") or []
        if not bids_raw or not asks_raw:
            return _store(ck, {**out, "error": "empty book"})
        bids = [(float(p), float(q)) for p, q in bids_raw]
        asks = [(float(p), float(q)) for p, q in asks_raw]
        bid_top, ask_top = bids[0][0], asks[0][0]
        mid = (bid_top + ask_top) / 2

        def depth(levels, n):
            return sum(q for _, q in levels[:n])
        def value_usd(levels, n):
            return sum(p * q for p, q in levels[:n])
        def biggest_wall(levels):
            if not levels: return (0, 0)
            best = max(levels, key=lambda x: x[1])
            return best
        bid_wall = biggest_wall(bids)
        ask_wall = biggest_wall(asks)

        d5_bid = depth(bids, 5); d5_ask = depth(asks, 5)
        d25_bid = depth(bids, 25); d25_ask = depth(asks, 25)
        v5_bid = value_usd(bids, 5); v5_ask = value_usd(asks, 5)

        out = {
            "ok":                       True,
            "age_s":                    round((_now() * 1000 - res.get("ts", 0)) / 1000, 2),
            "ts_ms":                    res.get("ts"),
            "mid":                      round(mid, 2),
            "bid_top":                  round(bid_top, 2),
            "ask_top":                  round(ask_top, 2),
            "spread_bps":               round((ask_top - bid_top) / mid * 10000, 3),
            "bid_depth_top5":           round(d5_bid, 4),
            "ask_depth_top5":           round(d5_ask, 4),
            "bid_depth_top25":          round(d25_bid, 4),
            "ask_depth_top25":          round(d25_ask, 4),
            "depth_imbalance_top5":     round(d5_bid / max(d5_bid + d5_ask, 1e-9), 4),
            "depth_imbalance_top25":    round(d25_bid / max(d25_bid + d25_ask, 1e-9), 4),
            "bid_value_usd_top5":       round(v5_bid, 0),
            "ask_value_usd_top5":       round(v5_ask, 0),
            "bid_wall_size":            round(bid_wall[1], 4),
            "bid_wall_price":           round(bid_wall[0], 2),
            "ask_wall_size":            round(ask_wall[1], 4),
            "ask_wall_price":           round(ask_wall[0], 2),
            "bid_wall_distance_bps":    round((mid - bid_wall[0]) / mid * 10000, 2),
            "ask_wall_distance_bps":    round((ask_wall[0] - mid) / mid * 10000, 2),
        }
        return _store(ck, out)
    except Exception as e:
        return _store(ck, {**out, "error": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# 2) Perp ticker — funding, OI, basis, vol
# ---------------------------------------------------------------------------
def get_perp_metrics(symbol: str = "BTCUSDT") -> dict:
    """Mainnet perp ticker — current funding, OI, vol, basis, mark/index drift."""
    ck = f"perp:{symbol}"
    if (c := _cached(ck)): return c
    out = {"ok": False}
    try:
        r = _fetch("/v5/market/tickers", {"category": "linear", "symbol": symbol})
        items = (r.get("result") or {}).get("list") or []
        if not items:
            return _store(ck, {**out, "error": "no list"})
        t = items[0]

        def f(k, default=None):
            v = t.get(k)
            try:
                return float(v) if v not in (None, "") else default
            except (ValueError, TypeError):
                return default

        last  = f("lastPrice")
        mark  = f("markPrice")
        index = f("indexPrice")
        funding = f("fundingRate")  # decimal per 8h, e.g. 0.0001 = 1bps
        next_funding_ms = int(t.get("nextFundingTime") or 0)

        basis_bps = None
        if mark and index and index > 0:
            basis_bps = (mark - index) / index * 10000
        funding_bps_ann = (funding * 365 * 3 * 10000) if funding is not None else None
        secs_to_funding = max(int((next_funding_ms / 1000) - _now()), 0) if next_funding_ms else None

        out = {
            "ok":                  True,
            "symbol":              symbol,
            "last":                last,
            "mark":                mark,
            "index":               index,
            "bid1":                f("bid1Price"),
            "ask1":                f("ask1Price"),
            "spread_bps":          (f("ask1Price") - f("bid1Price")) / max(f("ask1Price") or 1, 1) * 10000
                                       if f("bid1Price") and f("ask1Price") else None,
            "funding_rate":        funding,
            "funding_bps_per_8h":  funding * 10000 if funding is not None else None,
            "funding_bps_annual":  round(funding_bps_ann, 2) if funding_bps_ann is not None else None,
            "secs_to_funding":     secs_to_funding,
            "open_interest_btc":   f("openInterest"),
            "open_interest_usd":   f("openInterestValue"),
            "volume_24h_btc":      f("volume24h"),
            "turnover_24h_usd":    f("turnover24h"),
            "price_24h_pct":       f("price24hPcnt"),  # already decimal e.g. 0.005 = +0.5%
            "high_24h":            f("highPrice24h"),
            "low_24h":             f("lowPrice24h"),
            "basis":               f("basis"),
            "basis_bps":           round(basis_bps, 3) if basis_bps is not None else None,
            "basis_rate":          f("basisRate"),
        }
        # Range info
        if out["high_24h"] and out["low_24h"] and last:
            range_pct = (out["high_24h"] - out["low_24h"]) / out["low_24h"] * 100
            range_pos = (last - out["low_24h"]) / max(out["high_24h"] - out["low_24h"], 1e-9)
            out["range_24h_pct"] = round(range_pct, 3)
            out["price_pos_in_range"] = round(range_pos, 3)  # 0 = at low, 1 = at high
        return _store(ck, out)
    except Exception as e:
        return _store(ck, {**out, "error": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# 3) Funding history — drift / regime
# ---------------------------------------------------------------------------
def get_funding_history_features(symbol: str = "BTCUSDT", n: int = 8) -> dict:
    """Last N funding rates (typically 8h intervals → 8 = ~64h history)."""
    ck = f"funding_hist:{symbol}:{n}"
    if (c := _cached(ck)): return c
    out = {"ok": False}
    try:
        r = _fetch("/v5/market/funding/history",
                    {"category": "linear", "symbol": symbol, "limit": str(n)})
        items = (r.get("result") or {}).get("list") or []
        rates = []
        for it in items:
            try:
                rates.append(float(it.get("fundingRate") or 0))
            except (ValueError, TypeError):
                continue
        if not rates:
            return _store(ck, {**out, "error": "empty"})
        # bps per 8h
        rates_bps = [r * 10000 for r in rates]
        out = {
            "ok":              True,
            "n":               len(rates),
            "current_bps":     round(rates_bps[0], 3),  # most recent
            "mean_bps":        round(sum(rates_bps) / len(rates_bps), 3),
            "max_bps":         round(max(rates_bps), 3),
            "min_bps":         round(min(rates_bps), 3),
            "n_positive":      sum(1 for r in rates_bps if r > 0),
            "n_negative":      sum(1 for r in rates_bps if r < 0),
            "trend":           "up"  if rates_bps[0] > sum(rates_bps[1:]) / max(len(rates_bps) - 1, 1)
                                    else "down" if rates_bps[0] < sum(rates_bps[1:]) / max(len(rates_bps) - 1, 1)
                                    else "flat",
        }
        return _store(ck, out)
    except Exception as e:
        return _store(ck, {**out, "error": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# 4) Options chain — ATM IV, skew, GEX, max-pain, P/C ratio
# ---------------------------------------------------------------------------
def get_options_features(base_coin: str = "BTC",
                          nearest_expiry_only: bool = True) -> dict:
    """Pull full BTC option chain (~600 contracts), compute aggregates.

    Features:
      - underlying_price
      - nearest_expiry  (e.g. "11MAY26")
      - days_to_expiry
      - atm_iv          (mark IV at strike nearest spot)
      - put_call_oi_ratio  (total put OI / total call OI)
      - skew_25d_iv     (put_iv@-0.25 - call_iv@+0.25; positive = bearish)
      - implied_1d_move_usd  (atm_iv × spot × sqrt(1/365))
      - implied_1d_move_pct
      - gex_dealer_estimate  (negative = dealers short gamma → vol amplifies)
      - max_pain_strike      (price where total OI loss is highest)
      - top_oi_call_strike, top_oi_call_size
      - top_oi_put_strike, top_oi_put_size
      - n_contracts_in_expiry
    """
    ck = f"options:{base_coin}:{nearest_expiry_only}"
    if (c := _cached(ck)): return c
    out = {"ok": False}
    try:
        r = _fetch("/v5/market/tickers", {"category": "option", "baseCoin": base_coin})
        all_opts = (r.get("result") or {}).get("list") or []
        if not all_opts:
            return _store(ck, {**out, "error": "no options"})

        # Parse helpers
        def parse_sym(sym):
            # BTC-{DDMMMYY}-{strike}-{C|P}-USDT
            parts = sym.split("-")
            if len(parts) < 5: return None
            try:
                expiry_str = parts[1]
                strike     = float(parts[2])
                kind       = parts[3]  # C or P
                expiry     = dt.datetime.strptime(expiry_str, "%d%b%y").replace(
                    hour=8, tzinfo=dt.timezone.utc)
                return {"expiry": expiry_str, "expiry_dt": expiry,
                         "strike": strike, "kind": kind}
            except (ValueError, IndexError):
                return None

        # Filter to nearest expiry
        parsed = []
        for o in all_opts:
            p = parse_sym(o.get("symbol", ""))
            if p:
                parsed.append({**p, "raw": o})
        if not parsed:
            return _store(ck, {**out, "error": "no parseable symbols"})

        future_expiries = sorted(set(p["expiry_dt"] for p in parsed
                                       if p["expiry_dt"] > dt.datetime.now(dt.timezone.utc)))
        if not future_expiries:
            return _store(ck, {**out, "error": "no future expiries"})
        target_dt = future_expiries[0]
        target_expiry_str = next((p["expiry"] for p in parsed if p["expiry_dt"] == target_dt), "?")
        chain = [p for p in parsed if p["expiry_dt"] == target_dt]

        # Underlying
        spot = None
        for p in chain:
            try:
                spot = float(p["raw"].get("underlyingPrice", 0))
                if spot > 0: break
            except (ValueError, TypeError):
                continue
        if not spot or spot <= 0:
            return _store(ck, {**out, "error": "no underlying"})

        # Days to expiry
        dte_days = (target_dt - dt.datetime.now(dt.timezone.utc)).total_seconds() / 86400

        calls = [p for p in chain if p["kind"] == "C"]
        puts  = [p for p in chain if p["kind"] == "P"]

        def safe_float(v, default=0.0):
            try: return float(v) if v not in (None, "") else default
            except (ValueError, TypeError): return default

        # ATM IV
        atm_strike = min((p["strike"] for p in chain), key=lambda s: abs(s - spot))
        atm_opts = [p for p in chain if p["strike"] == atm_strike]
        atm_iv = None
        for p in atm_opts:
            iv = safe_float(p["raw"].get("markIv"))
            if iv > 0:
                atm_iv = iv; break

        # Implied 1d move
        implied_1d_pct = atm_iv / math.sqrt(365) if atm_iv else None
        implied_1d_usd = implied_1d_pct * spot if implied_1d_pct else None

        # 25-delta skew
        def find_delta(opts_list, target):
            valid = [(p, abs(safe_float(p["raw"].get("delta")) - target))
                     for p in opts_list if safe_float(p["raw"].get("delta"))]
            return min(valid, key=lambda x: x[1])[0] if valid else None

        c25 = find_delta(calls, 0.25)
        p25 = find_delta(puts, -0.25)
        skew_25d = None
        if c25 and p25:
            ci = safe_float(c25["raw"].get("markIv"))
            pi = safe_float(p25["raw"].get("markIv"))
            if ci > 0 and pi > 0:
                skew_25d = pi - ci

        # P/C OI ratio
        call_oi = sum(safe_float(p["raw"].get("openInterest")) for p in calls)
        put_oi  = sum(safe_float(p["raw"].get("openInterest")) for p in puts)
        pc_ratio = put_oi / call_oi if call_oi > 0 else None

        # Top OI strikes
        top_call = max(calls, key=lambda p: safe_float(p["raw"].get("openInterest")), default=None)
        top_put  = max(puts,  key=lambda p: safe_float(p["raw"].get("openInterest")), default=None)

        # Max-pain — strike where total option-holder loss is maximized
        # (= dealer's max gain)
        strikes = sorted(set(p["strike"] for p in chain))
        max_pain_strike = None; min_pain_value = float("inf")
        for s in strikes:
            total_value = 0
            for p in chain:
                oi = safe_float(p["raw"].get("openInterest"))
                if p["kind"] == "C":
                    total_value += max(s - p["strike"], 0) * oi
                else:
                    total_value += max(p["strike"] - s, 0) * oi
            if total_value < min_pain_value:
                min_pain_value = total_value; max_pain_strike = s

        # GEX (gamma exposure) — proxy: sum(gamma × OI × spot²) signed by dealer pos
        # Convention: dealers short calls, long puts → call gamma is NEGATIVE for dealers,
        # put gamma is POSITIVE. Net negative = vol amplifying.
        gex = 0
        for p in chain:
            gamma = safe_float(p["raw"].get("gamma"))
            oi = safe_float(p["raw"].get("openInterest"))
            sign = -1 if p["kind"] == "C" else +1
            gex += sign * gamma * oi * spot * spot

        out = {
            "ok":                       True,
            "underlying_price":         round(spot, 2),
            "nearest_expiry":           target_expiry_str,
            "days_to_expiry":           round(dte_days, 3),
            "n_contracts_in_expiry":    len(chain),
            "atm_strike":               atm_strike,
            "atm_iv":                   round(atm_iv, 4) if atm_iv else None,
            "implied_1d_move_pct":      round(implied_1d_pct * 100, 3) if implied_1d_pct else None,
            "implied_1d_move_usd":      round(implied_1d_usd, 0) if implied_1d_usd else None,
            "skew_25d_iv":              round(skew_25d, 4) if skew_25d is not None else None,
            "skew_label":               ("bearish" if skew_25d and skew_25d > 0.01
                                           else "bullish" if skew_25d and skew_25d < -0.01
                                           else "neutral"),
            "put_oi_total":             round(put_oi, 1),
            "call_oi_total":            round(call_oi, 1),
            "put_call_oi_ratio":        round(pc_ratio, 3) if pc_ratio is not None else None,
            "max_pain_strike":          max_pain_strike,
            "max_pain_distance_pct":    round((max_pain_strike - spot) / spot * 100, 3)
                                            if max_pain_strike else None,
            "top_call_oi_strike":       top_call["strike"] if top_call else None,
            "top_call_oi_size":         round(safe_float(top_call["raw"].get("openInterest")), 1)
                                            if top_call else None,
            "top_put_oi_strike":        top_put["strike"] if top_put else None,
            "top_put_oi_size":          round(safe_float(top_put["raw"].get("openInterest")), 1)
                                            if top_put else None,
            "gex_dealer_estimate":      round(gex, 0),
            "gex_label":                ("short_gamma_amplifying"  if gex < -1e6
                                           else "long_gamma_stabilizing" if gex > 1e6
                                           else "neutral"),
        }
        return _store(ck, out)
    except Exception as e:
        return _store(ck, {**out, "error": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# 5) Open Interest features — delta / velocity / divergence / aggression
# ---------------------------------------------------------------------------
def get_oi_features(symbol: str = "BTCUSDT") -> dict:
    """OI history → deltas, velocity, divergence vs price, label.

    Pulls 4h of 5-min OI snapshots (48 points) plus matching 5-min klines and
    cross-references current funding sign + 24h volume from get_perp_metrics().

    Returns:
      {
        ok, error?,
        oi_current_btc,                  # most recent OI in BTC
        oi_change_1h_pct,                # %
        oi_change_4h_pct,                # %
        oi_velocity_btc_per_h,           # linreg slope, BTC/hour
        oi_to_volume_ratio,              # oi_current / volume_24h_btc
        price_change_1h_pct,
        price_change_4h_pct,
        oi_price_divergence,             # +1 same direction, -1 opposite, 0 unclear
        oi_funding_weighted,             # oi_change_1h_pct * sign(funding_rate)
        oi_label,                        # building_longs/shorts/unwinding/stable/short_squeeze/long_squeeze
        n_samples,                       # len of OI history actually returned
      }
    """
    ck = f"oi:{symbol}"
    if (c := _cached(ck)): return c
    out: dict[str, Any] = {
        "ok":                       False,
        "oi_current_btc":            None,
        "oi_change_1h_pct":          None,
        "oi_change_4h_pct":          None,
        "oi_velocity_btc_per_h":     None,
        "oi_to_volume_ratio":        None,
        "price_change_1h_pct":       None,
        "price_change_4h_pct":       None,
        "oi_price_divergence":       0,
        "oi_funding_weighted":       None,
        "oi_label":                  "stable",
        "n_samples":                 0,
    }
    try:
        # --- OI history: newest first, 5-min buckets, 48 = 4h ----------------
        r = _fetch("/v5/market/open-interest",
                    {"category": "linear", "symbol": symbol,
                     "intervalTime": "5min", "limit": 48})
        items = (r.get("result") or {}).get("list") or []
        if not items:
            return _store(ck, {**out, "error": "empty oi history"})
        # Bybit returns newest-first; reverse so series[0] = oldest, series[-1] = newest
        oi_series: list[tuple[int, float]] = []
        for it in items:
            try:
                ts = int(it.get("timestamp") or 0)
                oi = float(it.get("openInterest") or 0)
                if ts and oi > 0:
                    oi_series.append((ts, oi))
            except (ValueError, TypeError):
                continue
        if len(oi_series) < 2:
            return _store(ck, {**out, "error": "insufficient oi history",
                                "n_samples": len(oi_series)})
        oi_series.sort(key=lambda x: x[0])  # oldest → newest
        out["n_samples"] = len(oi_series)
        oi_now   = oi_series[-1][1]
        out["oi_current_btc"] = round(oi_now, 4)

        # 1h ago = ~12 buckets back; 4h ago = ~48 buckets back (or oldest)
        idx_1h = max(len(oi_series) - 13, 0)
        idx_4h = 0
        oi_1h_ago = oi_series[idx_1h][1]
        oi_4h_ago = oi_series[idx_4h][1]
        if oi_1h_ago > 0:
            out["oi_change_1h_pct"] = round((oi_now - oi_1h_ago) / oi_1h_ago * 100, 4)
        if oi_4h_ago > 0:
            out["oi_change_4h_pct"] = round((oi_now - oi_4h_ago) / oi_4h_ago * 100, 4)

        # --- Linreg slope (BTC/hour) on full series --------------------------
        n = len(oi_series)
        # x = bucket index (0..n-1), each bucket = 5 min = 1/12 hour
        # slope_per_bucket × 12 = BTC/hour
        xs = list(range(n))
        ys = [v for _, v in oi_series]
        x_mean = sum(xs) / n
        y_mean = sum(ys) / n
        num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
        den = sum((xs[i] - x_mean) ** 2 for i in range(n))
        slope_per_bucket = (num / den) if den > 0 else 0.0
        out["oi_velocity_btc_per_h"] = round(slope_per_bucket * 12, 4)

        # --- Price history (5-min klines, same window) -----------------------
        try:
            kr = _fetch("/v5/market/kline",
                        {"category": "linear", "symbol": symbol,
                         "interval": "5", "limit": 48})
            klines = (kr.get("result") or {}).get("list") or []
            # kline rows: [start, open, high, low, close, volume, turnover], newest-first
            closes_newest_first: list[float] = []
            for row in klines:
                try:
                    closes_newest_first.append(float(row[4]))
                except (ValueError, TypeError, IndexError):
                    continue
            if len(closes_newest_first) >= 2:
                # newest-first → newest = [0]
                price_now = closes_newest_first[0]
                idx_1h_p = min(12, len(closes_newest_first) - 1)
                idx_4h_p = len(closes_newest_first) - 1
                price_1h_ago = closes_newest_first[idx_1h_p]
                price_4h_ago = closes_newest_first[idx_4h_p]
                if price_1h_ago > 0:
                    out["price_change_1h_pct"] = round(
                        (price_now - price_1h_ago) / price_1h_ago * 100, 4)
                if price_4h_ago > 0:
                    out["price_change_4h_pct"] = round(
                        (price_now - price_4h_ago) / price_4h_ago * 100, 4)
        except Exception:
            # Don't fail the whole function if klines hiccup
            pass

        # --- Cross-references with perp ticker (cached, no extra REST hit) ---
        perp = get_perp_metrics(symbol)
        funding = perp.get("funding_rate") if perp.get("ok") else None
        vol_24h = perp.get("volume_24h_btc") if perp.get("ok") else None

        if vol_24h and vol_24h > 0:
            out["oi_to_volume_ratio"] = round(oi_now / vol_24h, 4)

        # --- OI vs price divergence (1h window) ------------------------------
        oi_d = out["oi_change_1h_pct"]
        px_d = out["price_change_1h_pct"]
        EPS = 0.05  # 0.05% noise floor — below this we say "no clear direction"
        if oi_d is not None and px_d is not None:
            oi_dir = 1 if oi_d > EPS else (-1 if oi_d < -EPS else 0)
            px_dir = 1 if px_d > EPS else (-1 if px_d < -EPS else 0)
            if oi_dir == 0 or px_dir == 0:
                out["oi_price_divergence"] = 0
            elif oi_dir == px_dir:
                out["oi_price_divergence"] = 1   # same direction (real flow)
            else:
                out["oi_price_divergence"] = -1  # opposite (covering / unwind rally)

        # --- Funding-weighted aggression -------------------------------------
        if oi_d is not None and funding is not None:
            sign = 1 if funding > 0 else (-1 if funding < 0 else 0)
            out["oi_funding_weighted"] = round(oi_d * sign, 4)

        # --- Heuristic label -------------------------------------------------
        # Thresholds (1h window):
        #   |OI Δ| < 0.3%   → stable
        #   OI ↑  & price ↑ & funding > 0  → building_longs (aggressive long flow)
        #   OI ↑  & price ↓ & funding < 0  → building_shorts
        #   OI ↑  & price ↑ & funding < 0  → short_squeeze (shorts covering, OI growing on bid)
        #   OI ↑  & price ↓ & funding > 0  → long_squeeze
        #   OI ↓                            → unwinding
        STABLE_BAND = 0.3  # %
        label = "stable"
        if oi_d is not None and abs(oi_d) >= STABLE_BAND:
            if oi_d < 0:
                label = "unwinding"
            else:
                # OI rising — disambiguate via price + funding
                f_sign = 0
                if funding is not None:
                    f_sign = 1 if funding > 0 else (-1 if funding < 0 else 0)
                p_sign = 0
                if px_d is not None:
                    p_sign = 1 if px_d > EPS else (-1 if px_d < -EPS else 0)

                if p_sign > 0 and f_sign > 0:
                    label = "building_longs"
                elif p_sign < 0 and f_sign < 0:
                    label = "building_shorts"
                elif p_sign > 0 and f_sign < 0:
                    label = "short_squeeze"
                elif p_sign < 0 and f_sign > 0:
                    label = "long_squeeze"
                elif f_sign > 0:
                    label = "building_longs"
                elif f_sign < 0:
                    label = "building_shorts"
                else:
                    label = "stable"
        out["oi_label"] = label

        out["ok"] = True
        return _store(ck, out)
    except Exception as e:
        return _store(ck, {**out, "error": f"{type(e).__name__}: {e}"})


# ---------------------------------------------------------------------------
# Composite — call this from decision_snapshot
# ---------------------------------------------------------------------------
def get_market_context(symbol: str = "BTCUSDT") -> dict:
    """Pull all four feature groups, return composite. Each group fails
    independently — overall dict always returns the same shape."""
    return {
        "fetched_utc":  dt.datetime.now(dt.timezone.utc).isoformat(),
        "symbol":       symbol,
        "orderbook":    get_orderbook_features(symbol),
        "perp":         get_perp_metrics(symbol),
        "funding_hist": get_funding_history_features(symbol),
        "options":      get_options_features("BTC"),
        "oi":           get_oi_features(symbol),
        "cache_ttl_s":  CACHE_TTL_S,
    }
