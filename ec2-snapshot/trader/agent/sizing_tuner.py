"""sygnif sizing tuner — deterministic, rule-based knob adjustment per cycle.

Reads market context (regime, IV, funding, recent portfolio outcomes) and
computes a SIZING-dict override. Stdlib only. NOT LLM-driven.

Determinism:
    Given identical context, returns identical output. The exploration
    epsilon uses an hourly-stable seed, so within one UTC hour the agent
    can re-evaluate and get the same answer.

Usage from a neuron / agent.trader:

    from agent.sizing_tuner import compute_sizing
    sizing = compute_sizing(context)         # SIZING dict + "_trace" key
    risk_pct = sizing["default_risk_pct"]    # use as you would EXP.SIZING

CLI:
    python -m agent.sizing_tuner             # run live tune + pretty-print
    python -m agent.sizing_tuner --selftest  # synthetic regime sweep
"""

from __future__ import annotations

import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Mirror agent.expertise.SIZING — keep these defaults in sync. They're the
# floor we adjust *from*. If expertise.SIZING ever changes, update here too.
BASE_SIZING: dict[str, float] = {
    "default_risk_pct":          0.5,    # % equity per perp trade
    "option_default_risk_pct":   1.0,    # % equity per option position
    "default_perp_stop_pct":     1.0,    # % stop distance from entry
    "max_concurrent_open":      10,      # hard cap on simultaneous positions
    "max_concurrent_per_side":   3,      # cap on stacked same-side positions
    "min_equity_to_trade_usdc":  100,    # don't trade below this equity
}

# Two-tier risk caps (operator directive 2026-05-04). Sizing tuner clamps
# default_risk_pct / option_default_risk_pct to one of these depending on
# plan["size_tier"]. Without a tier flag, default cap applies.
SIZE_TIER_CAPS: dict[str, float] = {
    "default":         1.5,    # max % equity per trade in normal mode
    "long_term_conf":  6.0,    # max % equity per trade with long-term confidence
}
LONG_TERM_CONF_BOOST = 8.0     # multiplier applied to base risk when tier set

# Hard ceilings the tuner WILL NOT exceed. Even max experimentation respects
# these. Use to prevent runaway sizing on flawed context inputs.
# 2026-05-04: lifted from 5% to 6% to support long_term_conf size tier — but
# the post-tune clamp in agent/trader.py applies SIZING.max_risk_pct_default
# (1.5%) to plans that don't carry size_tier="long_term_conf".
HARD_CEILINGS: dict[str, float] = {
    "default_risk_pct":          6.0,    # absolute ceiling — long-term-conf tier max
    "option_default_risk_pct":   6.0,
    "default_perp_stop_pct":     5.0,    # never wider stop than 5%
    "max_concurrent_open":      40,
    "max_concurrent_per_side":  10,
    "min_equity_to_trade_usdc": 500,
}

# Hard floors. Below these, sizing is meaningless or unsafe.
HARD_FLOORS: dict[str, float] = {
    "default_risk_pct":          0.05,   # 5 bp minimum
    "option_default_risk_pct":   0.1,
    "default_perp_stop_pct":     0.3,    # 30 bp minimum
    "max_concurrent_open":       1,
    "max_concurrent_per_side":   1,
    "min_equity_to_trade_usdc":  10,
}


@dataclass
class TuneTrace:
    regime: str
    iv_pct: float | None
    funding_bps: float | None
    realized_pnl_pct: float | None
    win_rate_recent: float | None
    open_count: int
    equity_usdc: float
    rules_applied: list[str]
    multipliers: dict[str, float]
    experiment: bool
    final: dict[str, float]


def _hourly_seed() -> int:
    """Stable seed per UTC hour — exploration choices repeatable within an hour."""
    return int(time.time() // 3600)


def _clip(name: str, value: float) -> float:
    if name in HARD_CEILINGS:
        value = min(value, HARD_CEILINGS[name])
    if name in HARD_FLOORS:
        value = max(value, HARD_FLOORS[name])
    return value


def _walk(d: Any, *keys, default=None):
    """Walk a possibly-missing nested dict path safely."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


def _content_dict(entry: Any) -> dict:
    """A swarm entry's `content` may be a dict, a JSON string, or anything."""
    c = entry.get("content") if isinstance(entry, dict) else None
    if isinstance(c, dict):
        return c
    if isinstance(c, str):
        try:
            import json
            obj = json.loads(c)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _win_rate_from(last_trades: list[dict]) -> float | None:
    closures = []
    for t in last_trades:
        cd = _content_dict(t)
        if "realized_pnl_usdc" in cd:
            closures.append(cd["realized_pnl_usdc"])
    if not closures:
        return None
    wins = sum(1 for v in closures if (v or 0) > 0)
    return wins / len(closures)


def compute_sizing(
    context: dict,
    *,
    base: dict | None = None,
    seed: int | None = None,
    explore_prob: float = 0.05,
) -> dict:
    """Compute regime-adjusted SIZING for one trader cycle.

    `context` shape (all keys optional, missing → defaults apply):
        {
          "discovery": {"regime": "TREND_UP", "iv_pct": 35.2, "funding_bps": 8.5, ...},
          "portfolio": {"equity_usdc": 505, "open_count": 2, "total_realized_usdc": 5},
          "last_trades": [{"content": {"realized_pnl_usdc": 1.05}}, ...],
        }

    Returns SIZING-shaped dict + "_trace" key (TuneTrace as dict).
    """
    base = dict(base or BASE_SIZING)
    multipliers: dict[str, float] = {k: 1.0 for k in base}
    rules: list[str] = []

    disc = context.get("discovery") or {}
    port = context.get("portfolio") or {}
    last_trades = context.get("last_trades") or []
    plan_in = context.get("plan") or {}
    size_tier = (plan_in.get("size_tier") or "default").strip().lower()
    if size_tier == "long_term_conf":
        # Operator directive 2026-05-04: long-term-conf tier multiplies base risk
        # so regime tuning can carry it from 0.5% baseline up to the 6% cap.
        # 2026-05-10: STAGED rollout. Until SYGNIF_TIER_FULL=1, halve the
        # boost (×4 instead of ×8) so first ~50 promoted trades run at a
        # safer cap. Lift to full once tier_audit shows positive expectancy.
        import os as _os
        boost = LONG_TERM_CONF_BOOST
        if _os.environ.get("SYGNIF_TIER_FULL", "0") != "1":
            boost = LONG_TERM_CONF_BOOST / 2.0
            multipliers["default_risk_pct"]        *= boost
            multipliers["option_default_risk_pct"] *= boost
            rules.append(f"size_tier=long_term_conf STAGED: ×{boost} risk boost (half — set SYGNIF_TIER_FULL=1 to lift)")
        else:
            multipliers["default_risk_pct"]        *= boost
            multipliers["option_default_risk_pct"] *= boost
            rules.append(f"size_tier=long_term_conf FULL: ×{boost} risk boost")

    regime = (disc.get("regime") or disc.get("market_regime") or "UNKNOWN").upper()
    # P0 (2026-05-02): the legacy iv_pct/options.iv_pct keys are never emitted
    # by discovery_pass. The actual key is options.atm_iv_nearest (decimal,
    # not percentile). Convert to percentile-like score so the existing
    # >80 / <20 thresholds keep their meaning roughly: BTC ATM IV historically
    # ranges 20%–120% annualised; map iv_decimal × 100 to a percentile-ish
    # score by clamping to [0,100]. Caller can refine to true percentile when
    # we wire iv-percentile capture into discovery.
    _iv_dec = _walk(disc, "options", "atm_iv_nearest")
    if isinstance(_iv_dec, (int, float)) and _iv_dec > 0:
        iv_pct = float(_iv_dec) * 100.0  # rough %ile proxy: 20% IV → 20, 80% IV → 80
    else:
        iv_pct = _walk(disc, "iv_pct") or _walk(disc, "options", "iv_pct")
    funding_bps = _walk(disc, "funding_bps") or _walk(disc, "funding_rate_bps")
    equity = float(port.get("equity_usdc") or port.get("starting_balance_usdc") or 0)
    open_count = int(port.get("open_count") or 0)
    realized = float(port.get("total_realized_usdc") or 0)
    recent_pnl_pct = (realized / equity * 100) if equity > 0 else 0.0
    win_rate = _win_rate_from(last_trades)

    # --- regime rules ---
    if regime in ("HIGH_VOL_SHOCK", "VOLATILITY_SHOCK", "PANIC", "BLACK_SWAN"):
        multipliers["default_risk_pct"]        *= 0.4
        multipliers["option_default_risk_pct"] *= 0.5
        multipliers["max_concurrent_open"]     *= 0.5
        multipliers["default_perp_stop_pct"]   *= 1.5
        rules.append(f"regime={regime}: shrink size, widen stops")
    elif regime in ("TREND_UP", "TREND_DOWN", "TREND", "STRONG_TREND"):
        multipliers["default_risk_pct"]         *= 1.8
        multipliers["max_concurrent_per_side"]  *= 1.7
        rules.append(f"regime={regime}: scale up, allow stacking")
    elif regime in ("RANGE", "RANGE_BOUND", "MEAN_REVERTING", "CHOP"):
        multipliers["default_risk_pct"]    *= 0.8
        multipliers["max_concurrent_open"] *= 0.7
        rules.append(f"regime={regime}: be selective")
    elif regime in ("CALM", "LOW_VOL", "QUIET", "NORMAL"):
        multipliers["default_risk_pct"]    *= 1.5
        multipliers["max_concurrent_open"] *= 1.5
        rules.append(f"regime={regime}: exploration mode (more iteration)")
    else:
        rules.append(f"regime={regime}: no rule, defaults stand")

    # --- funding-rate signal (perp positioning) ---
    if isinstance(funding_bps, (int, float)):
        if abs(funding_bps) > 5.0:
            multipliers["default_risk_pct"] *= 0.7
            rules.append(f"funding={funding_bps:+.2f}bps extreme: shrink")
        elif abs(funding_bps) < 1.0:
            multipliers["default_risk_pct"] *= 1.05
            rules.append(f"funding={funding_bps:+.2f}bps neutral: slight up")

    # --- option IV percentile (vol mispricing) ---
    if isinstance(iv_pct, (int, float)):
        if iv_pct > 80:
            multipliers["option_default_risk_pct"] *= 1.4
            rules.append(f"iv_pct={iv_pct:.0f}: rich IV (sell vol bigger)")
        elif iv_pct < 20:
            multipliers["option_default_risk_pct"] *= 1.4
            rules.append(f"iv_pct={iv_pct:.0f}: cheap IV (buy vol bigger)")

    # --- doctrine signals (gamma flip, IV regime, RR skew, blackout) ---------
    # Per options-walls-and-bias.md + options-advanced-doctrine.md. The gate
    # module is pure; we read facts and apply size multipliers.
    try:
        # Lazy import — keeps tuner usable in unit tests without the predict
        # package being importable.
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from predict import options_doctrine_gate as DOCTRINE  # type: ignore

        snap_for_doctrine = disc if isinstance(disc, dict) else {}
        if snap_for_doctrine:
            advice = DOCTRINE.doctrine_advice(snap_for_doctrine)

            # Hard skip on blackout — return zero option risk so trader sits out.
            if advice["blackout"]["active"]:
                multipliers["option_default_risk_pct"] *= 0.0
                rules.append("doctrine: blackout window — option risk zeroed")

            # Gamma flip regime
            gflip = advice["gamma_flip"]
            if gflip.get("regime") == "short_gamma":
                multipliers["option_default_risk_pct"] *= 0.7
                rules.append("doctrine: gamma=short → ×0.7 option risk (vol-amplifying)")
            elif gflip.get("regime") == "long_gamma":
                multipliers["option_default_risk_pct"] *= 1.2
                rules.append("doctrine: gamma=long → ×1.2 option risk (vol-muting)")

            # IV regime (per iv_realized_ratio_1h)
            iv_reg = advice["iv_regime"]
            if iv_reg.get("regime") == "rich":
                # extra confidence in selling premium
                multipliers["option_default_risk_pct"] *= 1.15
                rules.append(
                    f"doctrine: iv=rich (iv_rv={iv_reg.get('iv_rv_ratio'):.2f}) "
                    f"→ ×1.15 option risk")
            elif iv_reg.get("regime") == "cheap":
                multipliers["option_default_risk_pct"] *= 1.15
                rules.append(
                    f"doctrine: iv=cheap (iv_rv={iv_reg.get('iv_rv_ratio'):.2f}) "
                    f"→ ×1.15 option risk")

            # RR skew z-score: stretched → mean-reversion structure favoured,
            # but reduce raw size (mean-reversion plays carry tail risk).
            rr = advice["rr_skew"]
            if rr.get("signal") in ("calls_stretched_rich", "puts_stretched_rich"):
                multipliers["option_default_risk_pct"] *= 0.8
                rules.append(
                    f"doctrine: rr_skew={rr.get('signal')} z={rr.get('z'):+.2f} "
                    f"→ ×0.8 (mean-reversion size discipline)")

            # Max-pain window: when active AND we're inside last 24h, slightly
            # boost confidence in defined-direction structures (handled via
            # plan logic), but tighten size if the magnet implies giving up
            # spot bias. Apply small ×0.95 to acknowledge the tail.
            mp = advice["max_pain_window"]
            if mp.get("reason") == "ok":
                multipliers["option_default_risk_pct"] *= 0.95
                rules.append(
                    f"doctrine: max_pain window active (T={mp.get('hours_to_expiry')}h) "
                    f"→ ×0.95")
    except Exception as _e:
        # never let doctrine failure break sizing
        rules.append(f"doctrine: skipped ({type(_e).__name__})")

    # --- drawdown / streak protection ---
    if recent_pnl_pct < -2.0:
        multipliers["default_risk_pct"]        *= 0.5
        multipliers["option_default_risk_pct"] *= 0.5
        rules.append(f"realized={recent_pnl_pct:+.2f}%: drawdown protect")
    elif recent_pnl_pct > 5.0:
        multipliers["default_risk_pct"] *= 1.2
        rules.append(f"realized={recent_pnl_pct:+.2f}%: press the edge")

    # --- win-rate signal ---
    if win_rate is not None:
        if win_rate >= 0.75:
            multipliers["max_concurrent_open"] *= 1.2
            rules.append(f"win_rate={win_rate:.0%}: more concurrency")
        elif win_rate < 0.40:
            multipliers["default_risk_pct"] *= 0.6
            rules.append(f"win_rate={win_rate:.0%}: dampen risk")

    # --- equity floor handling ---
    if 0 < equity < 200:
        multipliers["min_equity_to_trade_usdc"] *= 0.5
        rules.append(f"equity=${equity:.0f}: lower trade-floor to keep iterating")

    # --- exploration tick (hourly stable) ---
    rng_seed = seed if seed is not None else _hourly_seed()
    rng = random.Random(rng_seed)
    experiment = rng.random() < explore_prob
    if experiment:
        multipliers["default_risk_pct"]        *= 1.5
        multipliers["max_concurrent_per_side"] *= 1.5
        rules.append(f"EXPERIMENT (seed={rng_seed}): bump risk + side-stack")

    # --- apply + clip ---
    out: dict[str, Any] = {}
    for k in base:
        v = base[k] * multipliers[k]
        v = _clip(k, v)
        if k.startswith("max_") or k == "min_equity_to_trade_usdc":
            v = int(round(v))
        out[k] = v

    # --- tier-aware ceiling on risk_pct fields (operator directive 2026-05-04) ---
    # The HARD_CEILINGS clamp at 6% is the absolute legal max. Without an
    # explicit long-term-conf tier flag from the planner, we clamp tighter so
    # ordinary plans keep behaving like the historical 1.5%-ish ceiling.
    active_cap = SIZE_TIER_CAPS.get(size_tier, SIZE_TIER_CAPS["default"])
    for k in ("default_risk_pct", "option_default_risk_pct"):
        if out.get(k, 0) > active_cap:
            rules.append(f"tier-clamp {k} {out[k]:.2f}→{active_cap:.2f} (tier={size_tier})")
            out[k] = active_cap

    out["_trace"] = asdict(TuneTrace(
        regime=regime,
        iv_pct=iv_pct if isinstance(iv_pct, (int, float)) else None,
        funding_bps=funding_bps if isinstance(funding_bps, (int, float)) else None,
        realized_pnl_pct=round(recent_pnl_pct, 3),
        win_rate_recent=round(win_rate, 3) if win_rate is not None else None,
        open_count=open_count,
        equity_usdc=round(equity, 2),
        rules_applied=rules,
        multipliers={k: round(v, 3) for k, v in multipliers.items()},
        experiment=experiment,
        final={k: out[k] for k in base},
    ))
    return out


def explain(sizing: dict) -> str:
    """Pretty-print a tuned sizing decision."""
    t = sizing.get("_trace", {})
    lines = [
        f"regime: {t.get('regime')}    iv: {t.get('iv_pct')}    funding: {t.get('funding_bps')}bps",
        f"equity: ${t.get('equity_usdc')}    open: {t.get('open_count')}    realized: {t.get('realized_pnl_pct')}%    win_rate: {t.get('win_rate_recent')}",
        f"experiment: {t.get('experiment')}",
        "",
        "rules fired:",
    ]
    for r in t.get("rules_applied", []):
        lines.append(f"  · {r}")
    lines += ["", "final knobs (vs base):"]
    for k, v in t.get("final", {}).items():
        base_v = BASE_SIZING.get(k)
        delta = ""
        if base_v and base_v != v:
            ratio = v / base_v if base_v else 0
            delta = f"   ({ratio:+.2f}× base {base_v})"
        lines.append(f"  {k:<28} = {v}{delta}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Neurons (registered in sygnif_neurons.py)
# ---------------------------------------------------------------------------


def _load_live_context() -> dict:
    """Reuse trader.py's context loader so we see the same data the trader sees."""
    from agent import trader as T
    return T._load_context()


def n_expertise_tune(params: dict) -> dict:
    """Compute regime-adjusted sizing from current market+portfolio state."""
    try:
        seed = params.get("seed")
        explore = float(params.get("explore_prob", 0.05))
        ctx = _load_live_context()
        sizing = compute_sizing(ctx, seed=seed, explore_prob=explore)
        return {"ok": True, "data": sizing}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def n_expertise_tune_explain(params: dict) -> dict:
    """Same as expertise.tune but returns the human-readable explain() string."""
    r = n_expertise_tune(params)
    if not r.get("ok"):
        return r
    return {"ok": True, "data": {"text": explain(r["data"]), "trace": r["data"].get("_trace")}}


# ---------------------------------------------------------------------------
# CLI / self-test
# ---------------------------------------------------------------------------


def _selftest() -> int:
    """Sweep synthetic regimes; assert outputs are within hard ranges + sane deltas."""
    cases = [
        {"name": "panic",          "ctx": {"discovery": {"regime": "HIGH_VOL_SHOCK", "funding_bps": 12.0},
                                             "portfolio": {"equity_usdc": 500, "open_count": 0, "total_realized_usdc": 0}}},
        {"name": "trend_up",       "ctx": {"discovery": {"regime": "TREND_UP", "funding_bps": 0.5, "iv_pct": 35},
                                             "portfolio": {"equity_usdc": 500, "open_count": 2, "total_realized_usdc": 30}}},
        {"name": "calm_explore",   "ctx": {"discovery": {"regime": "CALM", "funding_bps": 0.1, "iv_pct": 18},
                                             "portfolio": {"equity_usdc": 500, "open_count": 0, "total_realized_usdc": 0}}},
        {"name": "drawdown",       "ctx": {"discovery": {"regime": "RANGE"},
                                             "portfolio": {"equity_usdc": 480, "open_count": 1, "total_realized_usdc": -20}}},
        {"name": "no_data",        "ctx": {}},
    ]
    failed = 0
    for c in cases:
        s = compute_sizing(c["ctx"], seed=42)
        for k, v in s.items():
            if k == "_trace":
                continue
            if k in HARD_CEILINGS and v > HARD_CEILINGS[k]:
                print(f"FAIL {c['name']}: {k}={v} > ceiling {HARD_CEILINGS[k]}")
                failed += 1
            if k in HARD_FLOORS and v < HARD_FLOORS[k]:
                print(f"FAIL {c['name']}: {k}={v} < floor {HARD_FLOORS[k]}")
                failed += 1
        print(f"\n=== case: {c['name']} ===")
        print(explain(s))
    print(f"\n{'PASS' if failed == 0 else 'FAIL'}: {failed} hard-bound violation(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    # default: live tune from current trader context
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sizing = compute_sizing(_load_live_context())
    print(explain(sizing))
