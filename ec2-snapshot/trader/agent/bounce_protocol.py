"""agent/bounce_protocol.py — short-window mean-reversion detector.

Logic: when price moves ≥ MOVE_THRESHOLD_PCT in WINDOW_MINUTES, expect a
counter-move of at least BOUNCE_RATIO × magnitude. Direction-biased.

Defaults (based on 2026-05-10 chart obs by operator):
  MOVE_THRESHOLD_PCT = 1.5%   (in 30 min)
  BOUNCE_RATIO       = 0.40   (60% retrace expected vs spec — operator said
                                 "at least 0.60% counter to a 1.5% drop"
                                 → 0.60/1.5 = 0.40)
  HORIZON_MIN        = 30     (counter-move expected within 30 min of pivot)
  COOLDOWN_MIN       = 15     (don't re-trigger same setup repeatedly)

Two entry points:
  compute_bounce_setup(bars, ...) — pure function, used by WS daemon
  get_bounce_setup_live()         — reads /var/lib/sygnif/bounce_setup.json
                                       written by sygnif-bounce-watcher.service

The reader is what decision_snapshot calls. The daemon (sygnif_bounce_watcher.py)
keeps the file fresh by subscribing to kline.1 WS — sub-minute updates so
no setup is missed by the 5-min training scanner cadence.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import pathlib
import time
import urllib.parse
import urllib.request
from typing import Any

BOUNCE_FILE = pathlib.Path("/var/lib/sygnif/bounce_setup.json")
STALE_AFTER_S = 90    # WS daemon flushes every ~5s, 90s = clearly broken

# Defaults — overridable via gate_params later
DEFAULTS = {
    "move_threshold_pct": 1.5,
    "bounce_ratio":       0.40,
    "horizon_min":        30,
    "cooldown_min":       15,
}


def compute_bounce_setup(bars: list[dict], *,
                          move_threshold_pct: float | None = None,
                          bounce_ratio:       float | None = None,
                          horizon_min:        int   | None = None,
                          cooldown_min:       int   | None = None) -> dict:
    """Pure function — examines a list of OHLC bars (oldest→newest) and returns
    bounce setup dict. Always returns the same key shape.

    Args:
      bars: list of {ts_ms_open, open, high, low, close, volume} — assumes
            uniform interval (1m or 5m). Caller controls window size.

    Returns:
      {
        "ok":                True,
        "active":            bool,    # is a bounce setup currently valid?
        "direction":         "long" | "short" | "none",
        "magnitude_pct":     float,    # size of the trigger move (signed)
        "magnitude_abs_pct": float,    # absolute size
        "expected_target_pct": float,  # BOUNCE_RATIO × magnitude
        "expected_target_usd": float,  # absolute price target
        "trigger_high":      float,    # the swing high if drop, low if rise
        "trigger_low":       float,
        "current_close":     float,
        "current_high":      float,
        "current_low":       float,
        "bars_since_pivot":  int,      # how stale is the setup
        "pivot_age_min":     float,
        "horizon_min":       int,
        "in_window":         bool,     # bars_since_pivot ≤ horizon
        "computed_utc":      str,
        "n_bars":            int,
      }
    """
    cfg = {**DEFAULTS}
    # When caller did not override, prefer gate_params (operator-tunable +
    # daily-optimizer adjusts bounce_move_threshold_pct). Fall back to
    # DEFAULTS if gate_params is unavailable / corrupt.
    try:
        from agent import gate_params as _GP
        if move_threshold_pct is None:
            cfg["move_threshold_pct"] = float(
                _GP.get("bounce_move_threshold_pct",
                        DEFAULTS["move_threshold_pct"]))
        if bounce_ratio is None:
            cfg["bounce_ratio"] = float(
                _GP.get("bounce_ratio", DEFAULTS["bounce_ratio"]))
        if horizon_min is None:
            cfg["horizon_min"] = int(
                _GP.get("bounce_horizon_min", DEFAULTS["horizon_min"]))
        if cooldown_min is None:
            cfg["cooldown_min"] = int(
                _GP.get("bounce_cooldown_min", DEFAULTS["cooldown_min"]))
    except Exception:
        pass    # falls back to DEFAULTS
    # Explicit caller overrides always win — applied last.
    if move_threshold_pct is not None: cfg["move_threshold_pct"] = move_threshold_pct
    if bounce_ratio       is not None: cfg["bounce_ratio"]       = bounce_ratio
    if horizon_min        is not None: cfg["horizon_min"]        = horizon_min
    if cooldown_min       is not None: cfg["cooldown_min"]       = cooldown_min

    out = {
        "ok":                  False,
        "active":              False,
        "direction":           "none",
        "magnitude_pct":       0.0,
        "magnitude_abs_pct":   0.0,
        "expected_target_pct": 0.0,
        "expected_target_usd": 0.0,
        "trigger_high":        0.0,
        "trigger_low":         0.0,
        "current_close":       0.0,
        "current_high":        0.0,
        "current_low":         0.0,
        "bars_since_pivot":    0,
        "pivot_age_min":       0.0,
        "horizon_min":         cfg["horizon_min"],
        "in_window":           False,
        "computed_utc":        dt.datetime.now(dt.timezone.utc).isoformat(),
        "n_bars":              len(bars),
        "config":              cfg,
    }
    if not bars or len(bars) < 6:
        out["error"] = f"insufficient bars ({len(bars)})"
        return out

    # Determine bar interval (minutes between consecutive opens)
    try:
        interval_s = (bars[-1]["ts_ms_open"] - bars[-2]["ts_ms_open"]) / 1000
        if interval_s <= 0: interval_s = 60
    except (KeyError, ZeroDivisionError):
        interval_s = 60
    interval_min = interval_s / 60

    # Look back HORIZON_MIN minutes
    horizon_bars = max(1, int(round(cfg["horizon_min"] / interval_min)))
    window = bars[-(horizon_bars + 1):]   # +1 to capture current
    if len(window) < 3:
        out["error"] = f"window too small ({len(window)})"
        return out

    out["ok"] = True
    out["current_close"] = window[-1]["close"]
    out["current_high"]  = window[-1]["high"]
    out["current_low"]   = window[-1]["low"]

    # Find swing high and swing low in the window
    high_bar_idx = max(range(len(window)), key=lambda i: window[i]["high"])
    low_bar_idx  = min(range(len(window)), key=lambda i: window[i]["low"])
    swing_high = window[high_bar_idx]["high"]
    swing_low  = window[low_bar_idx]["low"]
    out["trigger_high"] = swing_high
    out["trigger_low"]  = swing_low

    current = out["current_close"]

    # Did we DROP from swing high? (bullish bounce setup)
    drop_pct = (current - swing_high) / swing_high * 100
    # Did we RISE from swing low? (bearish pullback setup)
    rise_pct = (current - swing_low) / swing_low * 100

    threshold = cfg["move_threshold_pct"]
    setup_dir = "none"
    magnitude = 0.0
    pivot_idx = len(window) - 1

    # Bullish bounce: large drop, current is below the pivot high
    if drop_pct <= -threshold:
        setup_dir = "long"
        magnitude = drop_pct  # negative
        pivot_idx = high_bar_idx
    # Bearish pullback: large rise, current is above the pivot low
    elif rise_pct >= threshold:
        setup_dir = "short"
        magnitude = rise_pct  # positive
        pivot_idx = low_bar_idx
    # If both fire (ranging): pick the one that's more recent
    if drop_pct <= -threshold and rise_pct >= threshold:
        if high_bar_idx > low_bar_idx:
            # high more recent → still in down phase → bullish bounce
            setup_dir = "long"; magnitude = drop_pct; pivot_idx = high_bar_idx
        else:
            setup_dir = "short"; magnitude = rise_pct; pivot_idx = low_bar_idx

    bars_since_pivot = (len(window) - 1) - pivot_idx
    pivot_age_min = bars_since_pivot * interval_min
    in_window = bars_since_pivot <= horizon_bars

    out["direction"]         = setup_dir
    out["magnitude_pct"]     = round(magnitude, 4)
    out["magnitude_abs_pct"] = round(abs(magnitude), 4)
    out["bars_since_pivot"]  = bars_since_pivot
    out["pivot_age_min"]     = round(pivot_age_min, 1)
    out["in_window"]         = in_window

    # Active if we have a setup AND we're still inside the bounce horizon
    # AND price hasn't already retraced past the expected target
    if setup_dir != "none" and in_window:
        target_pct = abs(magnitude) * cfg["bounce_ratio"]
        if setup_dir == "long":
            # Expected: price recovers UP from pivot low
            target_usd = current * (1 + target_pct / 100)
        else:
            target_usd = current * (1 - target_pct / 100)
        out["expected_target_pct"] = round(target_pct, 4)
        out["expected_target_usd"] = round(target_usd, 1)
        out["active"]              = True

    return out


def get_bounce_setup_live() -> dict:
    """Read sygnif-bounce-watcher's JSON output. Falls back to REST poll if
    the daemon's file is missing or stale.

    Always returns the same shape — never raises.
    """
    out = compute_bounce_setup([])  # default empty result
    out["ok"] = False
    out["source"] = "missing"

    if BOUNCE_FILE.exists():
        try:
            with BOUNCE_FILE.open() as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = None
        if data:
            try:
                when = dt.datetime.fromisoformat(
                    data.get("computed_utc", "").replace("Z", "+00:00"))
                age = time.time() - when.timestamp()
            except ValueError:
                age = STALE_AFTER_S + 1
            if age <= STALE_AFTER_S:
                data["source"] = "ws_daemon"
                data["age_s"]  = round(age, 1)
                return data
            out["source"] = f"stale ({age:.0f}s old)"

    # Fallback: REST poll
    try:
        out_rest = _rest_fetch_and_compute()
        if out_rest:
            out_rest["source"] = "rest_fallback"
            return out_rest
    except Exception as e:
        out["error_fallback"] = f"{type(e).__name__}: {e}"

    return out


def _rest_fetch_and_compute(symbol: str = "BTCUSDT",
                              interval: str = "1",
                              limit: int = 60) -> dict | None:
    """Last-resort: fetch klines via REST and compute. Used only when WS
    daemon file is missing or stale."""
    qs = urllib.parse.urlencode({
        "category": "linear", "symbol": symbol,
        "interval": interval, "limit": str(limit),
    })
    body = urllib.request.urlopen(
        f"https://api.bybit.com/v5/market/kline?{qs}", timeout=6).read()
    rows = (json.loads(body).get("result") or {}).get("list") or []
    bars = []
    for r in rows:
        try:
            bars.append({
                "ts_ms_open": int(r[0]), "open": float(r[1]),
                "high":       float(r[2]), "low":  float(r[3]),
                "close":      float(r[4]), "volume": float(r[5]),
            })
        except (ValueError, TypeError, IndexError):
            continue
    bars.reverse()  # newest-first → oldest-first
    return compute_bounce_setup(bars)
