"""agent/whale_flow.py — read /var/lib/sygnif/whale_flow.json at decision time.

Wrapper around the daemon's output file. Tolerant: if file missing or stale,
returns a structured "no signal" dict so decision_snapshot doesn't break.

Used by decision_snapshot.build_snapshot() to embed whale-flow features in
every decision so the joiner can correlate them with outcomes later.
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import time
from typing import Any

WHALE_FILE = pathlib.Path("/var/lib/sygnif/whale_flow.json")
STALE_AFTER_S = 30


def get_whale_flow() -> dict:
    """Return whale-flow snapshot or a structured no-signal dict.

    Always returns the same key shape so callers can rely on it:
      {
        "ok":               bool,         # False if file missing or stale
        "ws_status":        str,
        "age_s":            float,        # seconds since file written
        "whale_imbalance":  float,        # 0.5 = balanced, 1.0 = all buys
        "n_whale_trades":   int,
        "buy_notional_usd": float,
        "sell_notional_usd": float,
        "largest_buy_usd":  float,
        "largest_sell_usd": float,
        "by_window":        {1m: {...}, 5m: {...}, 15m: {...}},
        "direction_hint":   "buy" | "sell" | "balanced",
        "strength":         float,        # 0..1, how strong is the imbalance
      }
    """
    out = {
        "ok":                False,
        "ws_status":         "missing",
        "age_s":             None,
        "whale_imbalance":   0.5,
        "n_whale_trades":    0,
        "buy_notional_usd":  0,
        "sell_notional_usd": 0,
        "largest_buy_usd":   0,
        "largest_sell_usd":  0,
        "by_window":         {},
        "direction_hint":    "balanced",
        "strength":          0.0,
    }
    if not WHALE_FILE.exists():
        return out
    try:
        with WHALE_FILE.open() as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return out

    # Compute age — be resilient to missing fields
    try:
        when = dt.datetime.fromisoformat(
            (data.get("updated_utc") or "").replace("Z", "+00:00"))
        age = time.time() - when.timestamp()
    except (ValueError, AttributeError):
        age = None

    if age is None or age > STALE_AFTER_S:
        out.update({
            "ws_status": data.get("ws_status", "stale"),
            "age_s":     round(age, 1) if age is not None else None,
        })
        return out

    imbalance = data.get("whale_imbalance", 0.5)
    n         = data.get("n_whale_trades", 0)

    # Direction hint + strength only meaningful when there's enough flow
    if n >= 3:
        if imbalance >= 0.65:
            direction = "buy"
        elif imbalance <= 0.35:
            direction = "sell"
        else:
            direction = "balanced"
        # Strength: how far from 0.5, normalized to 1.0 at 0.0 or 1.0
        strength = abs(imbalance - 0.5) * 2
    else:
        direction = "balanced"
        strength = 0.0

    out.update({
        "ok":                True,
        "ws_status":         data.get("ws_status", "?"),
        "age_s":             round(age, 1),
        "whale_imbalance":   imbalance,
        "n_whale_trades":    n,
        "buy_notional_usd":  data.get("whale_buy_notional_usd", 0),
        "sell_notional_usd": data.get("whale_sell_notional_usd", 0),
        "largest_buy_usd":   data.get("largest_buy_usd", 0),
        "largest_sell_usd":  data.get("largest_sell_usd", 0),
        "by_window":         data.get("by_window") or {},
        "direction_hint":    direction,
        "strength":          round(strength, 3),
    })
    return out


def alignment(plan_direction: str | None,
                whale: dict | None = None) -> dict:
    """Compare planner direction vs whale direction. Returns:
      {
        "alignment":         "aligned" | "diverged" | "neutral",
        "whale_direction":   "buy" | "sell" | "balanced",
        "plan_direction":    str | None,
        "strength":          float,   # whale conviction
      }

    Used by alignment-audit reports to bucket trades by whether SYGNIF agreed
    with the whales or diverged."""
    if whale is None:
        whale = get_whale_flow()
    wd = whale.get("direction_hint", "balanced")
    pd = (plan_direction or "").lower()

    # Map plan direction to whale direction language
    plan_norm = None
    if pd in ("long", "buy", "bullish"):
        plan_norm = "buy"
    elif pd in ("short", "sell", "bearish"):
        plan_norm = "sell"

    if not plan_norm or wd == "balanced":
        a = "neutral"
    elif plan_norm == wd:
        a = "aligned"
    else:
        a = "diverged"

    return {
        "alignment":       a,
        "whale_direction": wd,
        "plan_direction":  plan_norm,
        "strength":        whale.get("strength", 0.0),
    }
