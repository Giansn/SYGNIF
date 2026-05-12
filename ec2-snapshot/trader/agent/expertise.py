"""SYGNIF trade-planner expertise — the deterministic rule table.

These rules come from:
  * /agent-init-sygnif.md (identity: regime gates, signal taxonomy, sizing,
    funding-blackout, "no live until clear for live", invalidation rules)
  * /sygnif-research/01_math_foundations.md (Kelly, vol-targeting, VaR)
  * swarm.db swarm_id="self_improvement" (today's 9+ lessons)

The expertise here is INTENTIONALLY rule-based, not LLM-generated, so the
planner runs in <1s and works even when Gemma is offline. The LLM layer
(when stable) only adds post-hoc *articulation* of the thesis, not the
decision itself.
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Static rule table (sourced from agent-init-sygnif.md)
# ---------------------------------------------------------------------------

REGIME_STRATEGY_MAP = {
    # regime label  -> list of structures sorted by preference
    "TREND_UP":         ["bull_call_spread", "perp_long_with_stop"],
    "TREND_DOWN":       ["bear_put_spread", "perp_short_with_stop"],
    "RANGE":            ["short_iron_condor", "short_strangle"],
    "HIGH_VOL_SHOCK":   ["WAIT"],
    "NORMAL":           ["depends_on_iv"],   # see vol_bias logic
    "UNKNOWN":          ["WAIT"],
}

# IV-regime split (within NORMAL/UNKNOWN) — annualised IV decimals
VOL_BIAS = {
    "cheap_vol_threshold":     0.25,    # IV < 25% → long vol favoured
    "expensive_vol_threshold": 0.50,    # IV > 50% → short vol favoured
    "iv_rv_ratio_long_bias":   0.8,     # implied < realised → buy
    "iv_rv_ratio_short_bias":  1.2,     # implied > realised → sell
    "structures_when_cheap":   ["long_strangle", "long_straddle"],
    "structures_when_expensive": ["short_iron_condor", "short_strangle"],
    "structures_when_neutral": ["short_iron_condor"],   # default income strategy
}

# Position sizing (from identity sizing rules + today's lessons)
# Two-tier sizing (operator directive 2026-05-04):
#   - default tier:        risk per trade ≤ max_risk_pct_default   (1.5% of equity)
#   - long-term-conf tier: risk per trade ≤ max_risk_pct_high_conf (6.0% of equity)
# The plan must set plan["size_tier"] = "long_term_conf" to unlock the higher cap.
# Without that flag, the sizing tuner clamps to max_risk_pct_default regardless
# of regime / IV / funding multipliers.
SIZING = {
    "default_risk_pct":          0.5,    # 0.5% of equity per perp trade — base
    "option_default_risk_pct":   1.0,    # 1.0% of equity per option position (premium-bounded) — base
    "default_perp_stop_pct":     1.0,    # 1% stop distance from entry
    "max_concurrent_open":       7,      # hard cap (lowered from 10 on 2026-04-28
                                         #   per operator: keep concentration low,
                                         #   each leg gets attention). Daily-trades
                                         #   soft cap remains 20 in modes.py.
    "max_concurrent_per_side":   3,      # avoid stacking same direction
    "min_equity_to_trade_usdc":  100,    # don't trade below this
    "max_risk_pct_default":      1.5,    # ceiling without long-term-conf flag
    "max_risk_pct_high_conf":    6.0,    # ceiling with size_tier="long_term_conf"
    "long_term_conf_multiplier": 8.0,    # boost applied to base when tier set (0.5%×8=4%, then × regime → 6% cap)
}

# Perp safety caps — enforced at agent.trade.execute pre-flight (G4, G5).
# Two-tier leverage (operator directive 2026-05-04):
#   - default:                 plan.leverage ≤ max_leverage_default   (10×)
#   - high_conf_short_hold:    plan.leverage ≤ max_leverage_high_conf (30×)
# Plans must set plan["leverage_tier"] = "high_conf_short_hold" to unlock 10–30×.
# Intent: 30× is reserved for short holds with high confidence — bounce off
# resistance/support after big moves, predict_loop strong agreement, etc.
# Below 2× is rejected (sub-2x perps add no edge over spot — use spot instead).
# Liquidation buffer: "≥ 8 bps" — SL must trigger before reaching liq price.
PERP_SAFETY = {
    "min_leverage":              2.0,    # floor — reject sub-2× plans
    "max_leverage_default":      10.0,   # default ceiling; plans above this need leverage_tier
    "max_leverage_high_conf":    30.0,   # absolute ceiling with leverage_tier="high_conf_short_hold"
    "preferred_leverage":        5.0,    # auto-set to this when account exceeds the active cap
    "btcusdt_mm_rate":           0.005,  # tier-1 maintenance margin rate (Bybit)
    "min_liq_buffer_bps":        8.0,    # SL must be ≥ 8bps "outside" liq price
}

# Funding-stamp blackout (don't open within ±5 min of these UTC hours)
FUNDING_BLACKOUT = {
    "utc_hours": [0, 8, 16],
    "minutes_either_side": 5,
}

# Strike + expiry choosing
STRIKE_RULES = {
    # for long_strangle / short_strangle: distance from F as multiple of implied 1d move
    "strangle_wing_in_implied_moves": 1.5,
    # for iron condor: short strikes 1× implied move, long strikes 3× implied move
    "condor_short_in_implied_moves": 1.0,
    "condor_long_in_implied_moves":  2.5,
    # round all strikes to this granularity
    "strike_round_to": 500,
}

EXPIRY_RULES = {
    # for long-vol structures: prefer 5-10 DTE (more vega per $)
    "long_vol_min_dte_days": 5,
    "long_vol_max_dte_days": 14,
    # for short-vol structures: prefer 1-3 DTE (theta dense)
    "short_vol_min_dte_days": 1,
    "short_vol_max_dte_days": 4,
}

# Position review / exit rules
# Structured by instrument-type-and-structure since 2026-04-30 (postmortem of
# the 30APR26 bull_call_spread loss). The flat-rules era kept around for any
# legacy callers that read the bare keys; new code should call
# `exit_rules_for(structure)` and `should_arm_trailing(...)`.
#
# Research backing (cited verbatim in instruct.file `research_basis:`):
#   - tastytrade 4,872 SPY iron condor backtest 2005-2019: managing winners
#     at 50% max profit lifts WR 64%→82%, time-in-trade 27d→14d. This is the
#     industry-standard short-premium rule.
#   - OptionAlpha 0DTE research: theta decay morning slow → 2pm 2× → 3:30pm
#     4-5× hourly rate. ATM 0DTE bleeds 90% of premium even with zero spot
#     move. Translates to last 4h before Bybit's 08:00 UTC settle = ramp zone.
EXIT_RULES_V2 = {
    "perp": {
        "fixed_tp_sl_at_open": True,
        "trailing": {
            "activate_when": "mark_gt_entry_plus_1R",
            "trail_distance_atr_mult": 1.5,
            "trail_distance_atr_tf": "60",   # 1h ATR(14)
            "mechanism": "bybit_v5_trading_stop_trailingStop",
        },
        "time_stop_bars_5m": 24,
        "atr_emergency_stop_mult": 2.0,
    },
    "options": {
        "short_premium": {
            "structures": ["iron_condor", "bull_put_credit_spread",
                           "bear_call_credit_spread", "short_strangle",
                           "short_straddle"],
            "profit_take_pct_of_max": 50,
            "loss_stop_pct_of_credit": 200,
            "time_stop_pct_dte_remaining": 25,
        },
        "long_premium": {
            "structures": ["long_call", "long_put", "long_strangle",
                           "long_straddle", "bull_call_spread", "bear_put_spread",
                           "calendar_call_spread", "broken_wing_butterfly_call",
                           "broken_wing_butterfly_put"],
            "profit_take_trailing_pct_of_HWM": 50,
            "loss_stop_pct_of_premium": 30,
            "time_stop_pct_dte_remaining": 50,
            "sub4h_emergency_loss_stop_pct": 50,   # widen stop in last 4h
                                                    # (gamma can save us; theta
                                                    # can't be outrun anyway)
        },
        "always": {
            "regime_flip_close": True,
            "intraday_theta_guard_dte_h": 8,
            "intraday_theta_guard_premium_pct_equity": 2.0,
            "sub4h_review_interval_min": 15,
        },
    },
}

# --- legacy flat keys (kept for backward compat; deprecated) ---
EXIT_RULES = {
    "option_profit_take_pct_of_max": 50,
    "option_short_loss_stop_pct":   200,
    "option_long_profit_take_pct": 100,
    "option_long_time_stop_pct_dte": 25,
    "perp_time_stop_bars": 24,
    "perp_atr_stop_multiple": 2.0,
}


# Map structure names → bucket. New structures auto-classify by substring; if
# none matches, returns "unknown" (caller should fall back to neutral exit).
_SHORT_KEYS = ("iron_condor", "credit_spread", "short_strangle",
               "short_straddle", "broken_wing_condor")
_LONG_KEYS = ("long_call", "long_put", "long_strangle", "long_straddle",
              "bull_call_spread", "bear_put_spread", "calendar_", "broken_wing_butterfly")


def classify_option_structure(structure: str) -> str:
    """Return 'short_premium' | 'long_premium' | 'unknown'."""
    s = (structure or "").lower()
    if any(k in s for k in _SHORT_KEYS):
        return "short_premium"
    if any(k in s for k in _LONG_KEYS):
        return "long_premium"
    return "unknown"


def exit_rules_for(structure: str | None) -> dict:
    """Resolve the appropriate exit-rules dict for a position's structure.

    Returns the merged 'always' rules layered onto the type-specific rules.
    Unknown structures get a conservative neutral profile.
    """
    if structure is None:
        return EXIT_RULES_V2["perp"]
    bucket = classify_option_structure(structure)
    base = EXIT_RULES_V2["options"]["always"].copy()
    if bucket == "short_premium":
        base.update(EXIT_RULES_V2["options"]["short_premium"])
    elif bucket == "long_premium":
        base.update(EXIT_RULES_V2["options"]["long_premium"])
    else:
        # unknown → conservative neutral: 50% TP, 100% SL, time-stop 25%
        base.update({
            "profit_take_pct_of_max": 50,
            "loss_stop_pct": 100,
            "time_stop_pct_dte_remaining": 25,
            "structure_classification": "unknown",
        })
    base["bucket"] = bucket
    return base


def should_arm_perp_trailing(entry: float, mark: float, sl: float,
                              side: str = "Buy") -> tuple[bool, dict]:
    """Decide if perp trailing-stop should be armed.

    Activation: mark has crossed entry + 1R in the position's direction, where
    R = abs(entry - sl) (the original SL distance).

    Returns (arm: bool, payload: dict).
    payload contains the suggested trailingStop (USD, 1.5× ATR1h ideally)
    when arm=True. ATR is fetched by the caller.
    """
    R = abs(entry - sl)
    if R <= 0:
        return False, {"reason": "zero_R", "entry": entry, "sl": sl}
    side_l = (side or "Buy").lower()
    if side_l in ("buy", "long"):
        progress = (mark - entry) / R
    else:
        progress = (entry - mark) / R
    if progress < 1.0:
        return False, {"reason": f"progress {progress:.2f}R < 1R threshold",
                       "R_usd": R, "mark": mark, "entry": entry}
    return True, {"reason": f"progress {progress:.2f}R reached",
                  "R_usd": R, "mark": mark, "entry": entry}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_funding_blackout(now_utc: datetime | None = None) -> tuple[bool, str]:
    """True if within ±5 min of a Bybit funding stamp."""
    now_utc = now_utc or datetime.now(tz=timezone.utc)
    for h in FUNDING_BLACKOUT["utc_hours"]:
        # build a datetime for this hour today
        stamp = now_utc.replace(hour=h, minute=0, second=0, microsecond=0)
        delta_min = abs((now_utc - stamp).total_seconds()) / 60
        if delta_min <= FUNDING_BLACKOUT["minutes_either_side"]:
            return True, f"within ±{FUNDING_BLACKOUT['minutes_either_side']}min of {h:02d}:00 UTC funding"
    return False, ""


def vol_state(iv_annual: float | None, iv_rv_ratio: float | None) -> str:
    """Classify vol state into 'cheap' | 'expensive' | 'neutral' | 'unknown'.

    Returns 'unknown' when either input is missing (None) or non-positive.
    Caller MUST treat 'unknown' as a hard skip — defaulting to 'cheap' when
    IV is unknown is the wrong-when-unknown bug we shipped from
    2026-04-25 to 2026-05-02 (vol_state(0, 0) silently returned 'cheap',
    biasing the planner long_vol regardless of actual market state).
    """
    if iv_annual is None or iv_rv_ratio is None:
        return "unknown"
    if iv_annual <= 0 or iv_rv_ratio <= 0:
        return "unknown"
    if iv_annual < VOL_BIAS["cheap_vol_threshold"] and iv_rv_ratio < VOL_BIAS["iv_rv_ratio_long_bias"]:
        return "cheap"
    if iv_annual > VOL_BIAS["expensive_vol_threshold"] and iv_rv_ratio > VOL_BIAS["iv_rv_ratio_short_bias"]:
        return "expensive"
    return "neutral"


# ---------------------------------------------------------------------------
# Discovery snapshot key contract (P0 fix, 2026-05-02)
# ---------------------------------------------------------------------------
#
# The discovery pass emits IV / IV-RV under newer key names than the trader
# historically read:
#
#                   discovery emits           trader read (PRE-2026-05-02)
#   atm IV          options.atm_iv_nearest    options.atm_iv_annual_pct (×0.01)
#   IV/RV           options.iv_realized_ratio_1h   options.iv_rv_ratio
#
# That mismatch produced silent zeros → vol_state(0,0) → "cheap" → planner
# locked into long_strangle regardless of market. Helpers below probe both
# names and prefer the new ones; legacy names stay as fallback so any
# upstream sources still emitting them keep working.
#
# IMPORTANT: these helpers ONLY return numeric or None — never zero on
# missing data. The caller distinguishes "0 IV" (impossible) from "no IV"
# (skip-with-reason) explicitly.

def iv_from_snap(snap: dict | None) -> float | None:
    """Extract ATM IV (decimal annualised) from a discovery snapshot.

    Returns None when both new (`atm_iv_nearest`) and legacy
    (`atm_iv_annual_pct`) keys are missing or non-positive.
    """
    if not isinstance(snap, dict):
        return None
    opts = snap.get("options") or {}
    if not isinstance(opts, dict):
        return None
    v = opts.get("atm_iv_nearest")
    if isinstance(v, (int, float)) and v > 0 and not isinstance(v, bool):
        return float(v)
    legacy = opts.get("atm_iv_annual_pct")
    if isinstance(legacy, (int, float)) and legacy > 0 and not isinstance(legacy, bool):
        return float(legacy) / 100.0
    return None


def iv_rv_ratio_from_snap(snap: dict | None) -> float | None:
    """Extract IV/RV ratio (decimal) from a discovery snapshot.

    Prefers the 1h-window ratio (`iv_realized_ratio_1h`) emitted by
    discovery_pass. Falls back to legacy `iv_rv_ratio` if present.
    Returns None when both are missing or non-positive.
    """
    if not isinstance(snap, dict):
        return None
    opts = snap.get("options") or {}
    if not isinstance(opts, dict):
        return None
    for key in ("iv_realized_ratio_1h", "iv_rv_ratio"):
        v = opts.get(key)
        if isinstance(v, (int, float)) and v > 0 and not isinstance(v, bool):
            return float(v)
    return None


def round_strike(K: float) -> float:
    return round(K / STRIKE_RULES["strike_round_to"]) * STRIKE_RULES["strike_round_to"]


# ---------------------------------------------------------------------------
# P1.5 (2026-05-04): structure-build sanity checks
# ---------------------------------------------------------------------------
#
# Postmortem of 2026-05-04 4MAY26 expiry losses (75500-P + 82000-C, total
# −$111). Both legs had strikes ~$4,000 from F=$78,000, ie ~5× implied 1d
# move ($767). The doctrine rule says strangle wings should sit at 1.5×
# implied — these were 3× too wide. The rally to $80,601 still wasn't
# enough to print the 82000-C ITM. Even worse, the expiry was 3 DTE while
# `long_vol_min_dte_days = 5`. Both gates below would have rejected the
# setup pre-trade.

# Per-strategy strike-distance caps (multiplier of implied_1d_move). Caps
# are GENEROUS — they catch egregious doctrine violations, not borderline
# misalignment. Real strike-pick logic stays where it was.
STRIKE_DISTANCE_CAPS: dict[str, float] = {
    "long_strangle":   3.0,    # rule = 1.5× wing → cap allows ~2× tolerance
    "long_straddle":   0.5,    # straddle = strikes near F
    "short_strangle":  3.0,
    "short_straddle":  0.5,
    "iron_condor":     4.0,    # outer long legs ~2.5× expected
    "short_iron_condor": 4.0,
    "bull_call_spread":  3.0,
    "bear_put_spread":   3.0,
    # default for unknown structures
    "_default": 4.0,
}

_STRIKE_KEYS = ("K", "K_put", "K_call", "K_buy", "K_sell",
                "K_put_long", "K_put_short", "K_call_long", "K_call_short")


def check_strike_distances(plan: dict, *, F: float,
                            implied_1d_move: float) -> dict | None:
    """Return None if all strikes are within the doctrine cap, else return
    a skip-reason dict. Caller treats non-None as a hard reject.

    Postmortem 2026-05-04: 75500-P / 82000-C strikes were ±5× implied move
    from F=$78k → BTC's intra-day rally to $80.6k still missed the call by
    $1.4k. A simple `|F-K| / implied_1d_move <= cap` check rejects setups
    where strikes are too far OTM to capture realistic intra-DTE moves.
    """
    if implied_1d_move <= 0 or F <= 0:
        return None  # caller's gates should have skipped already
    strategy = (plan.get("strategy") or "").lower()
    cap = STRIKE_DISTANCE_CAPS.get(strategy, STRIKE_DISTANCE_CAPS["_default"])
    violations: list[str] = []
    for key in _STRIKE_KEYS:
        K = plan.get(key)
        if not isinstance(K, (int, float)) or K <= 0 or isinstance(K, bool):
            continue
        ratio = abs(F - K) / implied_1d_move
        if ratio > cap:
            violations.append(
                f"{key}={K:.0f} is {ratio:.2f}×implied_1d_move > cap {cap:.1f}×")
    if not violations:
        return None
    return {
        "rule": "strike_too_far",
        "reason": (f"strike_distance violation for {strategy}: "
                   f"{'; '.join(violations[:4])}"),
        "strategy": strategy,
        "F_usd": F,
        "implied_1d_move_usd": implied_1d_move,
        "cap_multiplier": cap,
        "violations": violations,
    }


def check_dte_in_band(expiry: str | None, *, long_vol: bool,
                       now_utc: datetime | None = None) -> dict | None:
    """Return None if expiry's DTE is within the doctrine band for the
    structure type, else a skip-reason dict.

    Postmortem 2026-05-04: 4MAY26 was opened on 5/1 at 3 DTE for a long-vol
    structure when `long_vol_min_dte_days = 5`. `pick_expiries` falls
    back to nearest_expiry when no candidate is in band — that's a
    silent doctrine violation. Catch it here.
    """
    if not expiry:
        return {"rule": "no_expiry", "reason": "no expiry chosen"}
    now = now_utc or datetime.now(tz=timezone.utc)
    try:
        exp_dt = datetime.strptime(str(expiry) + "T08:00:00+0000",
                                    "%Y-%m-%dT%H:%M:%S%z")
        dte_days = (exp_dt - now).total_seconds() / 86400.0
    except Exception:
        return {"rule": "bad_expiry",
                "reason": f"could not parse expiry {expiry!r}"}
    band = (
        EXPIRY_RULES["long_vol_min_dte_days"], EXPIRY_RULES["long_vol_max_dte_days"]
    ) if long_vol else (
        EXPIRY_RULES["short_vol_min_dte_days"], EXPIRY_RULES["short_vol_max_dte_days"]
    )
    if band[0] <= dte_days <= band[1]:
        return None
    return {
        "rule": "dte_out_of_band",
        "reason": (f"expiry {expiry} = {dte_days:.2f} DTE outside "
                   f"{'long_vol' if long_vol else 'short_vol'} band "
                   f"[{band[0]}, {band[1]}] days"),
        "expiry": expiry,
        "dte_days": round(dte_days, 2),
        "band_min_days": band[0],
        "band_max_days": band[1],
    }


def pick_expiries(snapshot: dict, *, long_vol: bool) -> str | None:
    """Choose ONE expiry (median in-band candidate). Legacy single-expiry
    return preserved for callers that don't iterate. New code should use
    `pick_expiries_ranked` to get the full ordered list and try the next
    candidate when the first one's quotes are illiquid.
    """
    candidates = pick_expiries_ranked(snapshot, long_vol=long_vol)
    if candidates:
        return candidates[len(candidates) // 2]   # median candidate
    return (snapshot.get("options", {}) or {}).get("nearest_expiry")


def pick_expiries_ranked(snapshot: dict, *, long_vol: bool) -> list[str]:
    """Return ALL in-band expiries, sorted by ascending DTE.

    Lets the trader fail-over to the next expiry when its first choice has
    illiquid quotes (postmortem 2026-05-04 — 7MAY26 short legs had empty
    bid/ask, leg_failed_rolled_back loop). The default `pick_expiries`
    picks the median for stability; this returns the full list so a
    higher-level retry can advance to the next viable expiry.

    Empty list if no expiry falls in the long_vol/short_vol DTE band.
    """
    import json
    import os
    from pathlib import Path
    exps: list = []
    _agent_dir = Path(os.environ.get("SYGNIF_AGENT_DIR",
                                     str(Path(__file__).resolve().parent.parent)))
    try:
        full = json.loads(
            (_agent_dir / "discovery" / "latest.json").read_text()
        )
        exps = (full.get("options", {}) or {}).get("live_expiries") or []
    except Exception:
        exps = []
    if not exps:
        return []
    now = datetime.now(tz=timezone.utc)
    band = (
        EXPIRY_RULES["long_vol_min_dte_days"], EXPIRY_RULES["long_vol_max_dte_days"]
    ) if long_vol else (
        EXPIRY_RULES["short_vol_min_dte_days"], EXPIRY_RULES["short_vol_max_dte_days"]
    )
    candidates: list[tuple[float, str]] = []
    for exp_str in exps:
        try:
            exp = datetime.strptime(str(exp_str) + "T08:00:00+0000",
                                    "%Y-%m-%dT%H:%M:%S%z")
            dte = (exp - now).total_seconds() / 86400
            if band[0] <= dte <= band[1]:
                candidates.append((dte, exp_str))
        except Exception:
            continue
    candidates.sort()
    return [s for _, s in candidates]
