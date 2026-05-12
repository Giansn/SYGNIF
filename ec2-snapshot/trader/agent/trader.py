"""SYGNIF deterministic trade planner + reviewer.

Two entry points:

    plan_trade(verbose=False) -> dict
        Reads context (discovery + portfolio + recent lessons), applies the
        deterministic rule table from agent.expertise, returns a single trade
        plan dict. Does NOT execute. Caller decides.

    review_positions() -> dict
        For each open paper position, evaluates exit criteria and returns
        a verdict per position (HOLD | CLOSE | ROLL | SCALE).

Both are SAFE: pure read + plan, no side effects beyond the optional logging
to swarm_id="trading" topic="trade.plan" / topic="position.review".
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# allow `from agent.X import Y` from inside ~/sygnif-agent/
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import expertise as EXP
from agent import exit_logic as XL
from agent import bybit_positions as BYBIT_POS
from agent import journal as JOURNAL
from predict import options_doctrine_gate as DOCTRINE


# ---------------------------------------------------------------------------
# 2026-05-05: predict-loop regime-confirmation helper (option B from regime
# mismatch analysis). Discovery refreshes only every 30 min, so a fast trend
# transition can leave it stuck on NORMAL/UNKNOWN for 25+ minutes after the
# market has clearly trended. Predict-loop runs every 5 min with the
# RF/XGB/LogReg ensemble — when 3+ consecutive forecasts agree on TREND_UP
# or TREND_DOWN, promote that label so trend-only scanners (scan_bos etc.)
# unblock without waiting for the next discovery cycle.
# ---------------------------------------------------------------------------

_REGIME_CONFIRM_WINDOW_S = 30 * 60   # look back at most 30 min of forecasts
_REGIME_CONFIRM_MIN_RUN = 3          # need this many consecutive matching labels


def _recent_predict_regime() -> tuple[str | None, int]:
    """Read most recent predict_loop forecasts from the master swarm and return
    ``(label, run_length)`` where *label* is the dominant TREND_UP / TREND_DOWN
    label across the consecutive most-recent forecasts (counting backwards
    from latest until a non-trend / different-trend label appears). Returns
    ``(None, 0)`` when there is no consensus, no swarm access, or fewer than
    one trend-labelled forecast in the lookback window.
    """
    import sqlite3
    db_path = os.environ.get("SWARM_KNOWLEDGE_DB") or "/var/lib/sygnif/swarm.db"
    try:
        if not os.path.exists(db_path):
            return None, 0
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
        try:
            rows = con.execute(
                "SELECT content FROM swarm_entries "
                "WHERE topic='forecast' "
                "  AND created > strftime('%s','now','-' || ? || ' seconds') "
                "  AND content LIKE '{%' "
                "ORDER BY created DESC LIMIT 24",
                (str(_REGIME_CONFIRM_WINDOW_S),),
            ).fetchall()
        finally:
            con.close()
    except Exception:
        return None, 0

    last_label: str | None = None
    run = 0
    for (content,) in rows:
        try:
            d = json.loads(content)
        except Exception:
            continue
        reg = d.get("regime")
        if isinstance(reg, dict):
            lbl = reg.get("label")
        else:
            lbl = reg
        if lbl in ("TREND_UP", "TREND_DOWN"):
            if last_label is None:
                last_label = lbl
                run = 1
            elif lbl == last_label:
                run += 1
            else:
                # opposite trend label breaks the consecutive run
                break
        else:
            # 2026-05-08 fix: UNKNOWN / non-trend labels are NO SIGNAL,
            # not counter-signal. Treat as gaps: skip without breaking
            # the consecutive run. Only an opposite trend (TREND_DOWN
            # after TREND_UP run, or vice versa) breaks the streak.
            # Lookback window is bounded by SQL (30 min) — stale trends
            # cannot survive forever.
            continue

    if last_label is None:
        return None, 0
    return last_label, run


# ---------------------------------------------------------------------------
# context loader (independent of the neuron registry to avoid circular import)
# ---------------------------------------------------------------------------


def _load_context() -> dict:
    """Pull discovery snapshot + portfolio + recent self_improvement lessons +
    latest forecasts from both predict producers.

    Imports sygnif_neurons lazily to avoid circular imports.

    Forecast cooperation channel (added 2026-04-28):
      Both predict producers write to swarm_id="btc_demo" topic="forecast".
        - agent_id="predict_loop"     EC2 ML ensemble (RF/XGB/logreg)
        - agent_id="sygnif-predict"   X1 rule-based bias (psych-barrier
                                      gated, runs every 5 min)
      The planner reads the latest from each as additional context. They
      do NOT override the deterministic plan — they're hints fed into
      llm.advise snapshots and used as soft confidence multipliers in
      plan_trade() (e.g. demote tier when producers disagree, boost
      confidence when they concur).
    """
    sys.path.insert(0, str(ROOT))
    import sygnif_neurons as N

    disc = N.run("discovery.read", {})
    port = N.run("order.paper.portfolio", {})
    # Merge live Bybit demo positions into port["open"] so the dedup gate
    # (_already_have_similar_position) sees real positions, not just the
    # paper journal. Postmortem 2026-04-25 remediation A: without this the
    # gate wave-through every repeat fire because demo legs never appear
    # in the paper book. Equity / open_count fields stay from paper since
    # those refer to paper-journal accounting; live legs only enrich the
    # similarity-matching list.
    port_data = (port.get("data") if isinstance(port.get("data"), dict) else {}) if port.get("ok") else {}
    try:
        from agent import bybit_positions as _bp
        live = _bp.fetch_open_positions(mode="demo") or []
        if live:
            port_data["open"] = (port_data.get("open") or []) + live
    except Exception:
        pass  # paper-only fallback if live fetch fails
    lessons = N.run("swarm.recent",
                    {"swarm_id": "self_improvement", "limit": 10})
    last_trades = N.run("swarm.recent",
                        {"swarm_id": "trading", "limit": 10})

    # Pull the latest forecast from each producer. Tolerant of failures: if
    # swarm.recent returns nothing or errors, the planner just sees None and
    # falls back to its deterministic-only path.
    def _latest_forecast(agent_id: str) -> dict | None:
        try:
            r = N.run("swarm.recent",
                      {"swarm_id": "btc_demo", "topic": "forecast",
                       "agent_id": agent_id, "limit": 1})
            rows = r.get("data") if r.get("ok") else None
            return rows[0] if rows else None
        except Exception:
            return None

    return {
        "discovery":      (disc.get("data") if isinstance(disc.get("data"), dict) else {}) if disc.get("ok") else {},
        "portfolio":      port_data,
        "lessons_raw":    lessons.get("data", []) if lessons.get("ok") else [],
        "last_trades":    last_trades.get("data", []) if last_trades.get("ok") else [],
        "forecasts": {
            "ec2_ml_ensemble": _latest_forecast("predict_loop"),
            "x1_rule_bias":    _latest_forecast("sygnif-predict"),
        },
        "now_utc":        datetime.now(tz=timezone.utc),
    }


def _forecast_signal(forecasts: dict) -> dict | None:
    """Extract the latest directional setup signal from the X1 sygnif-predict
    forecast (richer than the bias coarse-grain). Used by plan_trade() as a
    soft structure-override in undecided regimes.

    Looks for `signal in {swing_failure_*, bos_*}` — the two scanner families
    sygnif_predict.scan_swing_failure / scan_bos produce. Side is derived from
    the signal-name suffix (more robust than the explicit `side` field, which
    can be None even when the signal fired). Returns None when no directional
    signal is present.

    Returns:
        {signal, side, action, setup_conf, pred_conf} or None
    """
    fc = (forecasts or {}).get("x1_rule_bias")
    if not fc:
        return None
    content = fc.get("content") or ""
    try:
        data = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    sig_name = (data.get("signal") or "").strip()
    if not sig_name:
        return None
    # Accepted scanner families. `range_failed_breakout_*` exists in the
    # doc but isn't yet implemented in sygnif_predict. `psych_barrier_*`
    # added 2026-05-04 — symmetric short-side of the $10k major fade prior;
    # untested but deployed at user request after a missed $80k rejection.
    if not (sig_name.startswith("swing_failure_")
            or sig_name.startswith("bos_")
            or sig_name.startswith("psych_barrier_")):
        return None
    if sig_name.endswith("_long"):
        side = "Buy"
    elif sig_name.endswith("_short"):
        side = "Sell"
    else:
        return None
    return {
        "signal": sig_name,
        "side": side,
        "action": data.get("action") or "hold",
        "setup_conf": data.get("setup_conf"),
        "pred_conf": data.get("pred_conf"),
    }


def _forecast_bias(forecasts: dict) -> tuple[str, list[str]]:
    """Distill the two forecast feeds into a single combined bias hint.

    Returns (bias, why_lines) where bias ∈ {LONG, SHORT, NEUTRAL, MIXED}.
      - LONG / SHORT: both producers agree on direction
      - MIXED: producers disagree (one long, one short)
      - NEUTRAL: at least one is neutral, none is strong-opposite
    `why_lines` is a list of human-readable rationale strings the planner
    can append to plan["rule_chain"] for the swarm audit trail.
    """
    why: list[str] = []
    if not forecasts:
        return "NEUTRAL", ["forecasts: missing"]

    def _label(fc: dict | None, name: str) -> str | None:
        if not fc:
            why.append(f"{name}: no recent forecast")
            return None
        # forecast row is a swarm_entries row; the bias may live in content
        # ("vote=-3 RFΔ=…") or meta. Be conservative — just look at content.
        content = (fc.get("content") or "").lower()
        if "vote=-" in content or "down" in content or "short" in content or "bearish" in content:
            why.append(f"{name}: SHORT — {content[:80]}")
            return "SHORT"
        if "vote=+" in content or "up" in content or "long" in content or "bullish" in content:
            why.append(f"{name}: LONG — {content[:80]}")
            return "LONG"
        why.append(f"{name}: NEUTRAL — {content[:80]}")
        return "NEUTRAL"

    a = _label(forecasts.get("ec2_ml_ensemble"), "ec2_ml")
    b = _label(forecasts.get("x1_rule_bias"), "x1_rule")
    pair = (a, b)
    if pair == ("LONG", "LONG"):
        return "LONG", why
    if pair == ("SHORT", "SHORT"):
        return "SHORT", why
    if "LONG" in pair and "SHORT" in pair:
        return "MIXED", why
    if "LONG" in pair:
        return "LONG", why
    if "SHORT" in pair:
        return "SHORT", why
    return "NEUTRAL", why


# ---------------------------------------------------------------------------
# planner
# ---------------------------------------------------------------------------




def _snap_to_chain_strikes(expiry: str, base: str = "BTC") -> list[float]:
    """Return sorted list of available strikes for a given expiry on Bybit."""
    try:
        sys.path.insert(0, str(ROOT))
        from option import bybit_options as bo  # type: ignore
        rows = bo.get_tickers(base)
        strikes = set()
        # symbol format: BTC-DDMMMYY-STRIKE-C/P-USDT
        from datetime import datetime as _dt
        target = _dt.strptime(expiry, "%Y-%m-%d").strftime("%-d%b%y").upper()
        for r in rows:
            sym = r.get("symbol", "")
            parts = sym.split("-")
            if len(parts) >= 4 and parts[1].upper() == target:
                try:
                    strikes.add(float(parts[2]))
                except Exception:
                    continue
        return sorted(strikes)
    except Exception as e:
        return []


def _nearest_strike(target: float, available: list[float]) -> float:
    if not available:
        return target
    return min(available, key=lambda K: abs(K - target))


def _precheck_iron_condor_quotes(*, expiry: str,
                                    K_put_long: float, K_put_short: float,
                                    K_call_short: float, K_call_long: float,
                                    base: str = "BTC") -> dict:
    """Pre-trade leg-quote check for an iron_condor proposal.

    Mirrors order.option._precheck_leg_quotes side-aware semantics — checks
    the same things the executor will check, but earlier (at plan time) so
    the trader can advance to the next expiry before committing.

    Returns:
        {"ok": True}                      — all four legs have crossable quotes
        {"ok": False, "fail_legs": [...]} — at least one leg's required side is empty

    Iron condor leg roles:
        K_put_long    — Buy  → cross ASK
        K_put_short   — Sell → cross BID
        K_call_short  — Sell → cross BID
        K_call_long   — Buy  → cross ASK
    Returns ok=True on infrastructure errors (don't block planning on chain
    fetch flake).
    """
    legs = [
        ("Buy",  K_put_long,    "P", "no_ask_to_cross"),
        ("Sell", K_put_short,   "P", "no_bid_to_cross"),
        ("Sell", K_call_short,  "C", "no_bid_to_cross"),
        ("Buy",  K_call_long,   "C", "no_ask_to_cross"),
    ]
    try:
        from datetime import datetime as _dt
        from option.bybit_options import get_tickers as _get_tickers  # type: ignore
        chain = _get_tickers(base)
    except Exception:
        return {"ok": True}
    quotes = {r.get("symbol", ""): r for r in chain}
    try:
        dt = _dt.strptime(expiry, "%Y-%m-%d")
        dd = dt.strftime("%d").lstrip("0") or "0"
        mmm = dt.strftime("%b").upper()
        yy = dt.strftime("%y")
    except Exception:
        return {"ok": True}
    fail_legs: list[dict] = []
    for side, K, kind, fail_reason in legs:
        sym = f"{base}-{dd}{mmm}{yy}-{int(round(K))}-{kind}-USDT"
        q = quotes.get(sym) or {}
        bid = float(q.get("bid1Price") or 0)
        ask = float(q.get("ask1Price") or 0)
        if bid <= 0 and ask <= 0:
            fail_legs.append({"symbol": sym, "side": side, "bid": bid, "ask": ask,
                              "reason": "no_quote"})
        elif side == "Buy" and ask <= 0:
            fail_legs.append({"symbol": sym, "side": side, "bid": bid, "ask": ask,
                              "reason": fail_reason})
        elif side == "Sell" and bid <= 0:
            fail_legs.append({"symbol": sym, "side": side, "bid": bid, "ask": ask,
                              "reason": fail_reason})
    if fail_legs:
        return {"ok": False, "fail_legs": fail_legs}
    return {"ok": True}


def _walk_dict(d, *keys, default=None):
    """Walk a possibly-missing nested dict path safely. Used by P2a
    naked_shorts_allowed gate to read snapshot flags without exploding
    on partial discoveries."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


_LIQ_SNAP_PATH = Path.home() / ".sygnif" / "liquidation-summary.json"
_LIQ_SNAP_STALE_S = 90  # consider snapshot stale if older than this


def _load_liquidation_snap(symbol: str = "BTCUSDT") -> dict | None:
    """Load the daemon's liquidation summary file (Phase A 2026-05-05).

    Returns None when the file doesn't exist or is stale (>90s old). The
    daemon writes it every 30s, so a 90s stale-window means we tolerate
    one missed write before treating it as no-signal.

    Output shape per symbol:
      {"60s": ClusterSummary.to_dict(), "300s": ..., "900s": ...}
    """
    if not _LIQ_SNAP_PATH.exists():
        return None
    try:
        age = time.time() - _LIQ_SNAP_PATH.stat().st_mtime
        if age > _LIQ_SNAP_STALE_S:
            return None
        data = json.loads(_LIQ_SNAP_PATH.read_text())
        return (data.get("symbols") or {}).get(symbol.upper())
    except Exception:
        return None


def _detect_mm_opportunity(snap: dict, *, now_utc: datetime,
                              port: dict | None = None) -> dict | None:
    """P2.0 (2026-05-04): proactive MM-counter-play scanner.

    Returns a structure-override proposal when the snapshot shows a
    high-conviction MM-counter setup. Three detectors:

      A. theta_harvest      — short_iron_condor into a developing pin
      B. post_release_vol   — long_strangle after a sweep prints
      C. cascade_counter    — perp_short into crowded-long pre-expiry

    Each emits {play, structure, confidence, rationale} or None. The
    planner uses the override in place of the regime-default structure
    when present (and only when the regime is non-directional, ie
    NORMAL/UNKNOWN — never overrides TREND/RANGE picks).
    """
    opts = snap.get("options") or {}
    liq = snap.get("liquidity_levels") or {}
    spot = (snap.get("btc_perp_last")
            or (snap.get("btc_focus", {}) or {}).get("perp", {}).get("last"))
    if not isinstance(spot, (int, float)) or spot <= 0:
        return None

    # ---- common signals ----
    funding_last = snap.get("funding_last_pct")
    funding_pctile = snap.get("funding_pctile")
    iv = EXP.iv_from_snap(snap)
    iv_rv = EXP.iv_rv_ratio_from_snap(snap)
    max_pain = opts.get("max_pain_strike") or (
        opts.get("max_pain") or {}).get("strike")
    nearest_expiry = opts.get("nearest_expiry") or opts.get("nearest_live_expiry")

    # Compute hours to next expiry
    hours_to_exp = None
    if nearest_expiry:
        try:
            exp_dt = datetime.strptime(str(nearest_expiry) + "T08:00:00+0000",
                                        "%Y-%m-%dT%H:%M:%S%z")
            hours_to_exp = (exp_dt - now_utc).total_seconds() / 3600
        except Exception:
            pass

    # ---- A. THETA HARVEST INTO PIN ----
    # Conditions:
    #   - 24h ≤ hours_to_exp ≤ 48h  (in short_vol band, not too tight)
    #   - max_pain within 1% of spot (pin already developing)
    #   - IV/RV >= 0.85  (premium not absurdly cheap → sellable)
    #   - regime is rangebound or undecided (caller's gate)
    if (hours_to_exp is not None and 24 <= hours_to_exp <= 48
            and isinstance(max_pain, (int, float)) and max_pain > 0
            and abs(spot - max_pain) / spot <= 0.01
            and isinstance(iv_rv, (int, float)) and iv_rv >= 0.85):
        return {
            "play": "theta_harvest",
            "structure": "short_iron_condor",
            "confidence": "high",
            "rationale": (f"pin developing — max_pain ${max_pain:,.0f} "
                          f"within 1% of spot ${spot:,.0f}, "
                          f"{hours_to_exp:.0f}h to expiry, IV/RV {iv_rv:.2f} "
                          f"(>= 0.85). Tastytrade 50%-of-credit close rule applies."),
            "expected_win_rate": 0.75,
            "exit_at_R": 0.50,    # short_premium TP doctrine
            "hours_to_expiry": round(hours_to_exp, 1),
            "max_pain_strike": max_pain,
        }

    # ---- C. CASCADE-COUNTER PERP ----
    # Conditions:
    #   - 2h ≤ hours_to_exp ≤ 6h     (post-pin window approaching)
    #   - funding_pctile >= 0.85 AND funding_last > +0.005%  (crowded long)
    #   - asymmetry == "down"        (sweep target is below)
    #   - liquidity_levels has nearest_low_below
    if (hours_to_exp is not None and 2 <= hours_to_exp <= 6
            and isinstance(funding_pctile, (int, float)) and funding_pctile >= 0.85
            and isinstance(funding_last, (int, float)) and funding_last > 0.005
            and liq.get("asymmetry") == "down"
            and isinstance(liq.get("nearest_low_below"), (int, float))):
        target = liq["nearest_low_below"]
        # Phase A enhancement (2026-05-05): if real liquidations are
        # already cascading down, boost confidence + tag the rationale.
        liq_snap = _load_liquidation_snap("BTCUSDT")
        liq5m = (liq_snap or {}).get("300s") or {}
        cluster_long_usd = float(liq5m.get("longs_liq_usd") or 0)
        cluster_imb = float(liq5m.get("imbalance") or 0)
        liq_active = (cluster_long_usd >= 1_000_000 and cluster_imb <= -0.4)
        confidence = "high" if liq_active else "medium"
        liq_tag = (f" Liq-cascade ACTIVE: 5m_long_liq=${cluster_long_usd:,.0f} "
                   f"imb={cluster_imb:+.2f}." if liq_active else "")
        return {
            "play": "cascade_counter",
            "structure": "perp_short_with_stop",
            "confidence": confidence,
            "rationale": (f"crowded long ({funding_pctile*100:.0f}th pctile, "
                          f"{funding_last:+.4f}%) + {hours_to_exp:.1f}h "
                          f"pre-expiry + downside cluster ${target:,.0f}. "
                          f"Liquidation fuel + funding flip both pay shorts."
                          f"{liq_tag}"),
            "target_price": float(target),
            "expected_win_rate": 0.65 if liq_active else 0.55,
            "rr_ratio_target": 2.5,
            "funding_pctile": round(float(funding_pctile), 3),
            "liq_5m_long_usd": cluster_long_usd,
            "liq_5m_imbalance": cluster_imb,
        }

    # ---- C-bis. CASCADE-IN-PROGRESS (Phase A, 2026-05-05) ----
    # Fires when liquidations are CURRENTLY cascading regardless of expiry
    # window — the cleanest fade-the-cascade signal exists when:
    #   - 5m long-liq USD > $5M  (real flush, not noise)
    #   - imbalance <= -0.5      (dominantly longs being rekt)
    #   - 1m count >= 5          (still active, not finishing)
    # Direction: BUY (counter the down cascade, longs got flushed → bottom).
    # Time-bounded: only when within 24h of an option expiry (fits SYGNIF's
    # MM-counter doctrine; outside that we let directional regime handle it).
    liq_snap = _load_liquidation_snap("BTCUSDT")
    if (liq_snap and hours_to_exp is not None and hours_to_exp <= 24):
        liq5m = liq_snap.get("300s") or {}
        liq1m = liq_snap.get("60s") or {}
        long_usd_5m = float(liq5m.get("longs_liq_usd") or 0)
        imb_5m = float(liq5m.get("imbalance") or 0)
        n_1m = int(liq1m.get("n_events") or 0)
        if long_usd_5m >= 5_000_000 and imb_5m <= -0.5 and n_1m >= 5:
            return {
                "play": "cascade_in_progress",
                "structure": "perp_long_with_stop",
                "confidence": "medium",
                "rationale": (f"Active long-liq cascade: "
                              f"5m_long_liq=${long_usd_5m:,.0f} "
                              f"imb={imb_5m:+.2f} 1m_n={n_1m}. "
                              f"Fade the flush — counter-bid on capitulation."),
                "expected_win_rate": 0.60,
                "rr_ratio_target": 2.0,
                "liq_5m_long_usd": long_usd_5m,
                "liq_5m_imbalance": imb_5m,
                "liq_1m_n_events": n_1m,
            }

    # ---- B. POST-RELEASE VOL EXPANSION ----
    # Conditions:
    #   - now is 11:00-13:00 UTC  (post-expiry release window)
    #   - asymmetry has SHIFTED in last 4h or recent_sweep evidence in liq
    #   - IV/RV < 0.70   (vol cheap enough that long_vol has positive expected value)
    #   - hours_to_exp >= 96h on the NEXT-NEXT expiry (chosen by pick_expiries)
    h, m = now_utc.hour, now_utc.minute
    in_window = (11 <= h < 13)
    if (in_window and isinstance(iv_rv, (int, float)) and iv_rv < 0.70):
        # Sweep evidence: nearest_low_below or nearest_high_above is < 0.8% from spot
        nh = liq.get("nearest_high_above")
        nl = liq.get("nearest_low_below")
        sweep_evidence = False
        if isinstance(nh, (int, float)) and abs(nh - spot) / spot < 0.008:
            sweep_evidence = True
        if isinstance(nl, (int, float)) and abs(nl - spot) / spot < 0.008:
            sweep_evidence = True
        if sweep_evidence:
            return {
                "play": "post_release_vol",
                "structure": "long_strangle",
                "confidence": "medium",
                "rationale": (f"post-expiry vol expansion window "
                              f"({h:02d}:{m:02d} UTC) + IV/RV {iv_rv:.2f} "
                              f"(< 0.70 cheap) + sweep evidence "
                              f"(price within 0.8% of recent pivot). "
                              f"Volatility expansion likely as MM gamma re-emerges."),
                "expected_win_rate": 0.45,
                "rr_ratio_target": 2.0,
            }

    return None


def _liquidity_asymmetry_check(snap: dict, struct: str) -> dict | None:
    """P1.6f (2026-05-04): block perp opens that point INTO a closer
    stop-cluster pool than they point AWAY from.

    Mechanic: discovery emits `liquidity_levels.asymmetry` ∈ {up, down,
    balanced} based on swing-pivot proximity. When asymmetry == "down",
    the closer stop cluster is BELOW spot — opening a long perp here
    means the next sweep eats your SL before any rally can develop.
    Mirror for "up" → block short perps.

    Postmortem 4MAY26: spot $79,008 had nearest_low_below=$77,860 (1.3%)
    and nearest_high_above=$80,601 (2.0%) — asymmetry="down". A long
    perp at this exact moment would have eaten the 10:00 UTC sweep to
    $78,170 and likely stopped out before the snap-back.

    Long-vol option structures bypass — they profit from EITHER
    direction sweeping, so cluster proximity is FUEL not threat.
    """
    s = (struct or "").lower()
    # Only apply to perp / directional spreads
    if not (s.startswith("perp_") or "spread" in s):
        return None
    liq = snap.get("liquidity_levels") or {}
    if not isinstance(liq, dict) or not liq:
        return None
    asym = liq.get("asymmetry")
    if asym not in ("up", "down"):
        return None
    nh = liq.get("nearest_high_above")
    nl = liq.get("nearest_low_below")
    cur = liq.get("current_close")
    if not all(isinstance(x, (int, float)) and x > 0 for x in (nh, nl, cur)):
        return None

    long_aligned  = ("perp_long", "bull_call_spread")
    short_aligned = ("perp_short", "bear_put_spread")

    if asym == "down" and any(k in s for k in long_aligned):
        return {
            "rule": "liquidity_asymmetry",
            "reason": (f"liquidity asymmetric DOWN — buy-stop pool "
                       f"${nl:,.0f} ({(nl-cur)/cur*100:+.2f}%) closer than "
                       f"sell-stop ${nh:,.0f} ({(nh-cur)/cur*100:+.2f}%); "
                       f"long entry walks into the sweep"),
            "asymmetry": asym,
            "nearest_high_above": nh,
            "nearest_low_below": nl,
            "current_close": cur,
        }
    if asym == "up" and any(k in s for k in short_aligned):
        return {
            "rule": "liquidity_asymmetry",
            "reason": (f"liquidity asymmetric UP — sell-stop pool "
                       f"${nh:,.0f} ({(nh-cur)/cur*100:+.2f}%) closer than "
                       f"buy-stop ${nl:,.0f} ({(nl-cur)/cur*100:+.2f}%); "
                       f"short entry walks into the squeeze"),
            "asymmetry": asym,
            "nearest_high_above": nh,
            "nearest_low_below": nl,
            "current_close": cur,
        }
    return None


# ----------------------------------------------------------------------
# Phase A entry gates (2026-05-05): orderbook + skew + insurance kill-switches
# ----------------------------------------------------------------------
# Snapshot file paths produced by the EC2 feed services. Reads tolerate
# missing/stale snapshots — when data is absent the gates pass through
# (no signal ≠ adverse signal).
_MICRO_SNAP_PATH = Path.home() / ".sygnif" / "microstructure-snapshot.json"
_HIVEMIND_SNAP_PATH = Path.home() / ".sygnif" / "hivemind-snapshot.json"
_SNAP_STALE_S = 180  # 3min — generous window for 30-300s feed cadences


def _load_snap(path: Path) -> dict | None:
    """Atomic snapshot reader with staleness check."""
    if not path.exists():
        return None
    try:
        age = time.time() - path.stat().st_mtime
        if age > _SNAP_STALE_S:
            return None
        return json.loads(path.read_text())
    except Exception:
        return None


def _orderbook_imbalance_check(struct: str, side_hint: str | None = None) -> dict | None:
    """Block entries that go INTO a heavy wall.

    Edge: order-flow imbalance is a well-documented short-term price
    predictor (Cont/Kukanov 2014, Bouchaud). When the top-5 levels of one
    side dwarf the other, prices typically move toward the heavy side
    before continuing. Entering against that bias gets immediately stopped.

    Logic:
      - imb >= +0.5 (heavy bid wall) + SHORT entry → block
      - imb <= -0.5 (heavy ask wall) + LONG entry  → block
      - 0.3 ≤ |imb| < 0.5 + adverse direction      → flag (size_pct=0.7)
      - otherwise pass through
    """
    snap = _load_snap(_MICRO_SNAP_PATH)
    if not snap:
        return None
    sym_data = (snap.get("symbols") or {}).get("BTCUSDT") or {}
    imb = sym_data.get("imbalance")
    if not isinstance(imb, (int, float)):
        return None
    s = (struct or "").lower()
    is_short_dir = ("perp_short" in s or "bear_call" in s or side_hint == "Sell")
    is_long_dir = ("perp_long" in s or "bull_put" in s or "bull_call" in s
                    or side_hint == "Buy")
    if is_short_dir and imb >= 0.5:
        return {
            "rule": "orderbook_wall_blocks_short",
            "reason": (f"orderbook imb={imb:+.2f} (top-5 heavy bid wall) — "
                       f"short entry into support has poor near-term EV"),
            "imbalance": round(float(imb), 3),
            "size_pct": 0.0,
        }
    if is_long_dir and imb <= -0.5:
        return {
            "rule": "orderbook_wall_blocks_long",
            "reason": (f"orderbook imb={imb:+.2f} (top-5 heavy ask wall) — "
                       f"long entry into resistance has poor near-term EV"),
            "imbalance": round(float(imb), 3),
            "size_pct": 0.0,
        }
    if is_short_dir and imb >= 0.3:
        return {
            "rule": "orderbook_wall_reduces_short",
            "reason": (f"orderbook imb={imb:+.2f} (mild bid support) — "
                       f"reduce short entry size to 70%"),
            "imbalance": round(float(imb), 3),
            "size_pct": 0.7,
        }
    if is_long_dir and imb <= -0.3:
        return {
            "rule": "orderbook_wall_reduces_long",
            "reason": (f"orderbook imb={imb:+.2f} (mild ask resistance) — "
                       f"reduce long entry size to 70%"),
            "imbalance": round(float(imb), 3),
            "size_pct": 0.7,
        }
    return None


def _skew_25d_check(struct: str) -> dict | None:
    """Block selling premium into extreme put-skew.

    Edge: 25Δ skew measures the IV premium puts command over equidistant
    calls. When skew is extreme (>0.06 = +6 IV-pp put fear), the market is
    paying up for tail protection AND realised tail-event probability is
    elevated. Selling iron condors / bull-put-spreads into that gets
    immediately gored on any down move.

    Logic:
      - struct in {short_iron_condor, bull_put_spread} AND skew > 0.06 → block
      - same structs AND skew > 0.04 → flag (size_pct=0.6)
      - bear_call_spread + skew < -0.04 (call skew, rare in BTC) → flag
      - other structs → pass through
    """
    s = (struct or "").lower()
    sells_premium = ("short_iron_condor" in s or "bull_put_spread" in s
                      or "iron_butterfly" in s or "iron_condor" in s)
    bear_credit = "bear_call_spread" in s
    if not (sells_premium or bear_credit):
        return None
    snap = _load_snap(_HIVEMIND_SNAP_PATH)
    if not snap:
        return None
    expiries = snap.get("expiries") or []
    if not expiries:
        return None
    nearest = expiries[0]
    skew = nearest.get("skew_25d")
    if not isinstance(skew, (int, float)):
        return None
    if sells_premium and skew >= 0.06:
        return {
            "rule": "skew_blocks_short_premium",
            "reason": (f"25d skew={skew:+.3f} (extreme put fear) — selling "
                       f"premium has -EV when tail-protection is paid up; "
                       f"defer {struct}"),
            "skew_25d": round(float(skew), 3),
            "size_pct": 0.0,
        }
    if sells_premium and skew >= 0.04:
        return {
            "rule": "skew_reduces_short_premium",
            "reason": (f"25d skew={skew:+.3f} (high put fear) — reduce "
                       f"{struct} size to 60%"),
            "skew_25d": round(float(skew), 3),
            "size_pct": 0.6,
        }
    if bear_credit and skew <= -0.04:
        return {
            "rule": "skew_blocks_bear_credit",
            "reason": (f"25d skew={skew:+.3f} (call skew, upside vol "
                       f"premium) — selling cheap calls has -EV; defer "
                       f"{struct}"),
            "skew_25d": round(float(skew), 3),
            "size_pct": 0.0,
        }
    return None


def _insurance_stress_check() -> dict | None:
    """Halt all new entries during systemic stress.

    Edge: when the Bybit USDT insurance pool drops materially in 24h, it
    means cascading liquidations exhausted user margins and tapped the
    pool. Historically this marks 'blood in the streets' — vol expands,
    correlations spike, normal regime models break down. Stand aside.

    Logic:
      - 24h pool delta <= -10%  → halt all new entries
      - -10% < delta <= -5%     → flag (size_pct=0.5)
    """
    snap = _load_snap(_MICRO_SNAP_PATH)
    if not snap:
        return None
    ins = snap.get("insurance") or {}
    delta_24h_pct = ins.get("delta_24h_pct")
    pool = ins.get("pool_usdt")
    if not isinstance(delta_24h_pct, (int, float)):
        return None
    if delta_24h_pct <= -10.0:
        return {
            "rule": "insurance_pool_stress",
            "reason": (f"insurance pool 24h delta {delta_24h_pct:+.1f}% "
                       f"(${pool:,.0f}) — systemic stress, halt new entries"),
            "delta_24h_pct": round(float(delta_24h_pct), 2),
            "pool_usdt": float(pool) if pool else 0.0,
            "size_pct": 0.0,
        }
    if delta_24h_pct <= -5.0:
        return {
            "rule": "insurance_pool_caution",
            "reason": (f"insurance pool 24h delta {delta_24h_pct:+.1f}% "
                       f"(${pool:,.0f}) — elevated stress, reduce size 50%"),
            "delta_24h_pct": round(float(delta_24h_pct), 2),
            "pool_usdt": float(pool) if pool else 0.0,
            "size_pct": 0.5,
        }
    return None


def _funding_crowded_check(snap: dict, struct: str) -> dict | None:
    """P1.6c (2026-05-04): block direction-aligned opens when funding is at
    a crowded extreme.

    Postmortem 4MAY26: funding had been positive for 3 consecutive 8h
    cycles (peak +7.5% annualized) when the post-expiry liquidation
    cascade fired. Long-direction structures opened in that window get
    bagged by the cascade. The signal:
      funding_pctile > 0.85 AND funding_last_pct > +0.003%   → crowded LONG
      funding_pctile > 0.85 AND funding_last_pct < -0.003%   → crowded SHORT

    Direction-neutral structures (iron_condor, long_strangle, long_straddle)
    bypass — they aren't directional. Only the directional perp + spread
    structures are blocked.
    """
    funding_last = snap.get("funding_last_pct")
    funding_pctile = snap.get("funding_pctile")
    if (not isinstance(funding_last, (int, float)) or isinstance(funding_last, bool)
            or not isinstance(funding_pctile, (int, float))
            or isinstance(funding_pctile, bool)):
        return None  # data missing — trader's other gates handle absent fields
    if funding_pctile <= 0.85:
        return None  # not at extreme

    s = (struct or "").lower()
    long_directional = ("bull_call_spread", "perp_long")
    short_directional = ("bear_put_spread", "perp_short")

    if funding_last > 0.003 and any(k in s for k in long_directional):
        return {
            "rule": "funding_crowded_long",
            "reason": (f"funding {funding_last:+.4f}% at "
                       f"{funding_pctile*100:.0f}th percentile — longs "
                       f"crowded, defer long-direction open ({struct})"),
            "funding_last_pct": funding_last,
            "funding_pctile": round(funding_pctile, 3),
        }
    if funding_last < -0.003 and any(k in s for k in short_directional):
        return {
            "rule": "funding_crowded_short",
            "reason": (f"funding {funding_last:+.4f}% at "
                       f"{funding_pctile*100:.0f}th percentile — shorts "
                       f"crowded, defer short-direction open ({struct})"),
            "funding_last_pct": funding_last,
            "funding_pctile": round(funding_pctile, 3),
        }
    return None


def _post_expiry_blackout_check(now_utc: datetime, struct: str) -> dict | None:
    """P1.6d (2026-05-04): block new long-vol opens 08:00-11:00 UTC every
    day (Bybit option expiry release window).

    Postmortem 4MAY26: BTC dropped $1,716 in 30 min at 10:00 UTC after
    08:00 delivery — classic gamma-release sweep. Opening long-vol in
    this window means the underlying has typically *just moved*, so IV
    is rich and skew is unstable. Wait for post-release drift to settle
    (normally by 12:00 UTC) before re-engaging.

    Long-vol = long_strangle / long_straddle / bull_call_spread /
    bear_put_spread. Iron condors and short_strangles aren't blocked
    (they want post-release vol crush).
    """
    s = (struct or "").lower()
    long_vol = ("long_strangle", "long_straddle",
                "bull_call_spread", "bear_put_spread")
    if not any(k in s for k in long_vol):
        return None
    h, m = now_utc.hour, now_utc.minute
    minutes_since_8 = (h - 8) * 60 + m
    if 0 <= minutes_since_8 <= 180:   # 08:00 — 11:00 UTC inclusive
        return {
            "rule": "post_expiry_blackout",
            "reason": (f"post-expiry release window {h:02d}:{m:02d} UTC — "
                       f"long-vol open blocked until 11:00 UTC ({struct})"),
            "minutes_since_0800": minutes_since_8,
        }
    return None


def _oi_cluster_entry_check(plan: dict, snap: dict) -> dict | None:
    """P1.6e (2026-05-04): reject long-vol when its strikes are too close
    to the max-pain magnet zone.

    Long-vol bets need price to MOVE AWAY from max-pain. If our strikes
    are within $200 of max-pain, we're betting the magnet won't pull —
    a low-conviction trade. Reject and force the planner to re-pick
    further from the cluster (or stand down).

    short_iron_condor short legs near max_pain are FINE (the magnet keeps
    price between them). Skip the check for non-long-vol structures.
    """
    s = (plan.get("strategy") or "").lower()
    long_vol = ("long_strangle", "long_straddle",
                "bull_call_spread", "bear_put_spread")
    if not any(k in s for k in long_vol):
        return None
    opts = (snap.get("options") or {})
    max_pain = opts.get("max_pain_strike")
    if not isinstance(max_pain, (int, float)) or max_pain <= 0:
        # Try the nested form (different schema versions)
        mp_block = opts.get("max_pain") or {}
        if isinstance(mp_block, dict):
            max_pain = mp_block.get("strike")
    if not isinstance(max_pain, (int, float)) or max_pain <= 0:
        return None  # no max-pain data → can't gate; let other gates decide
    proximity = 200.0
    strike_keys = ("K_put", "K_call", "K_buy", "K_sell")
    too_close = []
    for key in strike_keys:
        K = plan.get(key)
        if isinstance(K, (int, float)) and K > 0:
            if abs(K - max_pain) <= proximity:
                too_close.append(f"{key}={K:.0f} within ${proximity:.0f} of max_pain={max_pain:.0f}")
    if too_close:
        return {
            "rule": "oi_cluster_proximity",
            "reason": (f"long-vol strikes too close to max_pain "
                       f"({', '.join(too_close[:2])}) — magnet zone "
                       f"reduces edge"),
            "max_pain_strike": max_pain,
            "violations": too_close,
        }
    return None


def _strategy_side_bucket(strategy: str) -> str:
    """Classify a strategy into a "side bucket" for concentration limits.
    P4a (2026-05-02). Returns one of:
      'long_vol'           — long_strangle, long_straddle
      'short_vol'          — iron_condor, short_strangle, short_straddle
      'long_directional'   — bull_call_spread, perp_long, long_call
      'short_directional'  — bear_put_spread, perp_short, long_put
      'unknown'            — fallback
    Two positions in the SAME bucket are correlated and count against
    the per-side cap.
    """
    s = (strategy or "").lower()
    if "long_strangle" in s or "long_straddle" in s:
        return "long_vol"
    if ("iron_condor" in s or "short_strangle" in s
            or "short_straddle" in s):
        return "short_vol"
    if ("bull_call" in s or "perp_long" in s or s == "long_call"
            or "calendar_call" in s or "broken_wing_butterfly_call" in s):
        return "long_directional"
    if ("bear_put" in s or "perp_short" in s or s == "long_put"
            or "calendar_put" in s or "broken_wing_butterfly_put" in s):
        return "short_directional"
    return "unknown"


def _count_open_in_bucket(port: dict, bucket: str) -> int:
    """Count open positions whose strategy maps to the same side bucket."""
    n = 0
    for p in port.get("open", []) or []:
        if not isinstance(p, dict):
            continue
        # Paper-shape uses 'strategy'; live Bybit positions use 'structure'.
        strat = p.get("strategy") or p.get("structure") or ""
        if _strategy_side_bucket(strat) == bucket:
            n += 1
    return n


def _already_have_similar_position(port: dict, structure: str, expiry: str | None) -> bool:
    """True if any open position competes on the same expiry.

    Two shape conventions handled:
      • Paper-journal positions carry the structure name in `label` — strict
        match on (structure-substring AND expiry).
      • Live Bybit positions have empty `label` (fetch_open_positions sets
        label="") — tolerant match on expiry alone (any live leg on this
        expiry blocks a new fire). Defense against the 2026-04-25 overfire
        and the 2026-05-01 5-IC stack on 5/4 expiry.

    Bybit option symbols come in two date formats: "%-d%b%y" (e.g. 4MAY26)
    and zero-padded "%d%b%y" (e.g. 04MAY26). Match either.
    """
    if not expiry:
        return False
    from datetime import datetime as _dt
    try:
        d = _dt.strptime(expiry, "%Y-%m-%d")
        targets = {d.strftime("%-d%b%y").upper(), d.strftime("%d%b%y").upper()}
    except Exception:
        return False
    sname = structure.replace("short_", "").replace("long_", "").lower()
    for p in port.get("open", []) or []:
        label = (p.get("label") or "").lower()
        marks = p.get("marks") or p.get("legs") or []
        for m in marks:
            sym = m.get("symbol", "")
            if not sym:
                continue
            if not any(f"-{t}-" in sym for t in targets):
                continue
            # Strict: paper-journal label carries the structure name
            if sname and sname in label:
                return True
            # Tolerant: live leg with empty label — block on expiry alone
            if not label:
                return True
    return False



# ---------------------------------------------------------------------------
# POINT 5 (2026-05-10): selective theta-harvest gate
# ---------------------------------------------------------------------------
def _theta_harvest_strict_gate(snap: dict, port: dict | None,
                                 struct: str) -> dict | None:
    """Additional risk gates for theta-harvest structures (short_iron_condor,
    iron_butterfly, iron_condor). Returns {rule, reason} to skip, or None to allow.

    Rationale: 7-day backtest showed iron_condor-style structures bled
    -$284 (concentrated in 5/4 / 5/8 sizing slips + adverse-move days).
    The doctrine is theta-harvest IS edge in genuinely range-bound cond-
    itions, but firing it indiscriminately kills equity. These gates
    require ALL of the following before allowing a short-vol fire:

      • IV/RV ratio > 1.10  (premium meaningfully rich vs realized)
      • Spot NOT within 1% of a major psych barrier (round numbers)
      • ADX < 25  (no developing trend even within the regime label)
      • Daily realized P&L > -1% equity (let losing days end early)
      • At most 1 concurrent option position (no stacked condors)

    Bypass via env SYGNIF_THETA_STRICT=0 if you want the old loose behavior.
    """
    import os as _os
    if _os.environ.get("SYGNIF_THETA_STRICT", "1") != "1":
        return None
    if not any(k in struct for k in ("iron_condor", "iron_butterfly")):
        return None

    # G1: IV/RV ratio
    options = snap.get("options") or {}
    iv_rv = options.get("iv_realized_ratio_1h")
    if iv_rv is None:
        iv_rv = (snap.get("iv_rv_ratio") or 0)
    try:
        iv_rv = float(iv_rv or 0)
    except Exception:
        iv_rv = 0
    # Phase 3.1 (2026-05-10): threshold reads from gate_params store, default
    # 1.10 matches old hardcoded value. Optimizer can propose new value via
    # gate_params_challenger.json; operator promotes by `cp` to champion.
    try:
        from agent import gate_params as _GP
        _iv_rv_min = float(_GP.get("theta_iv_rv_min", 1.10))
    except Exception:
        _iv_rv_min = 1.10
    if iv_rv < _iv_rv_min:
        return {"rule": "theta_strict_iv_too_cheap",
                "reason": f"IV/RV {iv_rv:.2f} < {_iv_rv_min:.2f} — premium not rich enough to sell"}

    # G2: psych barrier proximity
    spot = ((snap.get("btc_focus") or {}).get("perp", {}) or {}).get("last")             or snap.get("btc_perp_last") or 0
    try:
        spot = float(spot or 0)
    except Exception:
        spot = 0
    if spot > 0:
        major_barriers = [50000, 60000, 70000, 75000, 80000, 85000, 90000,
                          95000, 100000, 110000, 120000]
        for barrier in major_barriers:
            if abs(spot - barrier) / spot < 0.01:
                return {"rule": "theta_strict_psych_proximity",
                        "reason": (f"spot ${spot:.0f} within 1% of psych barrier "
                                   f"${barrier:,} — fade risk too high")}

    # G3: ADX trend strength
    regime = snap.get("regime") or {}
    adx = (regime.get("adx") if isinstance(regime, dict)
           else snap.get("adx_1h") or snap.get("adx") or 0)
    try:
        adx = float(adx or 0)
    except Exception:
        adx = 0
    if adx >= 25:
        return {"rule": "theta_strict_trend_developing",
                "reason": f"ADX {adx:.1f} >= 25 — trend developing inside regime"}

    # G4: daily loss cap (uses portfolio.total_unrealized as proxy until
    # closedPnl aggregator works — conservative, may underreport realized)
    if isinstance(port, dict):
        equity = float(port.get("equity_usdc") or 0)
        upnl = float(port.get("total_unrealized_usdc") or 0)
        if equity > 0:
            day_pnl_pct = upnl / equity * 100
            if day_pnl_pct < -1.0:
                return {"rule": "theta_strict_daily_loss",
                        "reason": (f"day uPnL {day_pnl_pct:+.2f}% < -1% — let losing "
                                   f"day finish before adding theta exposure")}

    # G5: max concurrent option position cap
    if isinstance(port, dict):
        opens = port.get("open") or []
        n_options = sum(1 for p in opens if (p.get("kind") or "").lower() == "option")
        if n_options >= 4:  # 1 condor = 4 legs
            return {"rule": "theta_strict_already_open",
                    "reason": (f"{n_options} option legs already open (assume 1 "
                               f"existing condor) — no stacking")}

    return None


def plan_trade() -> dict:
    """Return one structured trade plan. Caller may execute or skip."""
    ctx = _load_context()
    snap = ctx["discovery"]
    port = ctx["portfolio"]

    # --------- preflight gates ------------------------------------------------
    blackout, reason = EXP.is_funding_blackout(ctx["now_utc"])
    if blackout:
        return {"action": "skip", "reason": reason, "rule": "funding_blackout"}

    # Doctrine pre-trade snapshot (informational here; structure-specific
    # check runs after we pick a structure, below). Always compute the facts
    # so they ride along in the plan dict for swarm logging.
    doctrine_facts = DOCTRINE.doctrine_advice(snap)

    equity = float(port.get("equity_usdc", 0))
    if equity < EXP.SIZING["min_equity_to_trade_usdc"]:
        return {"action": "skip",
                "reason": f"equity ${equity:.2f} < min ${EXP.SIZING['min_equity_to_trade_usdc']}",
                "rule": "min_equity"}

    open_count = int(port.get("open_count", 0))
    if open_count >= EXP.SIZING["max_concurrent_open"]:
        return {"action": "skip",
                "reason": f"{open_count}/{EXP.SIZING['max_concurrent_open']} positions open",
                "rule": "max_open"}

    # P4a (2026-05-02): per-side concentration cap. EXP.SIZING declares
    # max_concurrent_per_side=3 but it was never enforced — three short
    # iron_condors on the same expiry are correlated, not independent.
    # Defer the cap-check to AFTER structure is picked (we need to know
    # which side the candidate would belong to). Stash open positions
    # for that downstream check.

    # --------- regime + vol state --------------------------------------------
    regime = snap.get("regime") or "UNKNOWN"
    regime_origin = "discovery"

    # 2026-05-05 — predict-loop confirmation override (option B from the
    # regime-mismatch analysis). Discovery_pass refreshes every 30 min with
    # ATR-percentile bucketing; predict_loop refreshes every 5 min with
    # RF/XGB/LogReg consensus. When discovery is non-committal (NORMAL/UNKNOWN)
    # but predict_loop has been TREND_UP/TREND_DOWN consistently for ≥3
    # consecutive forecasts (≥ ~15 min of agreement), promote that label so
    # trend-gated scanners unblock without waiting for the next discovery
    # cycle. Logged in plan dict as regime_origin="predict_loop_confirmed".
    if regime in ("NORMAL", "UNKNOWN"):
        plbl, prun = _recent_predict_regime()
        if plbl in ("TREND_UP", "TREND_DOWN") and prun >= _REGIME_CONFIRM_MIN_RUN:
            regime = plbl
            regime_origin = f"predict_loop_confirmed_{prun}"
    # P0 (2026-05-02): use snap-aware helpers that probe BOTH the new keys
    # discovery actually emits (atm_iv_nearest, iv_realized_ratio_1h) AND the
    # legacy names (atm_iv_annual_pct, iv_rv_ratio). Returns None when both
    # are missing — vol_state then yields "unknown" → we skip below.
    iv = EXP.iv_from_snap(snap)            # decimal annualised, or None
    iv_rv = EXP.iv_rv_ratio_from_snap(snap)  # ratio, or None
    # Fail closed if discovery snapshot is missing the BTC reference price.
    # Pre-2026-04-28 this fell back to a hardcoded $77k anchor, which silently
    # produced bogus strikes whenever the snapshot was stale or partial —
    # violating the "Real data only" rule from AGENT.md.
    F_raw = snap.get("btc_perp_last")
    if not isinstance(F_raw, (int, float)) or isinstance(F_raw, bool) or F_raw <= 0:
        return {"action": "skip",
                "reason": f"discovery snapshot missing btc_perp_last (got {F_raw!r})",
                "rule": "no_btc_price"}
    F = float(F_raw)

    state = EXP.vol_state(iv, iv_rv)

    # P0 (2026-05-02): when IV state is unknown — discovery is missing the
    # data we need to pick a side — REFUSE to trade. The pre-fix behaviour
    # silently classified missing IV as "cheap" and biased the planner long.
    if state == "unknown":
        return {"action": "skip",
                "reason": (f"vol_state=unknown (iv={iv!r}, iv_rv={iv_rv!r}); "
                          "discovery snapshot missing IV data — sit out"),
                "rule": "iv_missing"}

    # iv guaranteed non-None past this point (state != "unknown")
    implied_1d_move = float((snap.get("options") or {}).get("implied_1d_move_usd")
                              or (F * iv / 19))

    # P1 (2026-05-02): IV staleness gate. If the discovery snapshot is
    # older than IV_STALE_THRESHOLD_S, refuse to trade on it.
    #
    # Postmortem 2026-05-04: shipped at 600s but sygnif-discovery.timer
    # fires every 30 min (1800s), so the trader was in a forced 67%
    # blackout — only the first 10 min of each 30-min discovery cycle
    # actually traded. Aligned the threshold with the discovery cadence.
    # IV moves ~1-3 vol points/hour intraday → 30 min ≈ 0.5-1.5 vol points
    # of drift, acceptable for IV-regime structure choice. To tighten,
    # reduce sygnif-discovery.timer's OnUnitActiveSec instead of cutting
    # this threshold (a tighter threshold without faster discovery just
    # turns trades off).
    IV_STALE_THRESHOLD_S = 1800  # 30 min, matches discovery refresh cadence
    captured_at = snap.get("captured_at_utc")
    if captured_at:
        try:
            cap_ts = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            age_s = (ctx["now_utc"] - cap_ts).total_seconds()
            if age_s > IV_STALE_THRESHOLD_S:
                return {"action": "skip",
                        "reason": (f"discovery snapshot {age_s:.0f}s old "
                                  f"(>{IV_STALE_THRESHOLD_S}s threshold); "
                                  f"IV likely stale"),
                        "rule": "iv_stale",
                        "snapshot_age_s": round(age_s, 1)}
        except Exception:
            # malformed timestamp — fail closed too
            return {"action": "skip",
                    "reason": f"discovery captured_at_utc unparseable: {captured_at!r}",
                    "rule": "iv_stale_unparseable"}

    if regime == "HIGH_VOL_SHOCK":
        return {"action": "skip", "reason": "HIGH_VOL_SHOCK regime — flat preferred",
                "rule": "regime_gate"}

    # --------- pick structure -------------------------------------------------
    # P2.0 (2026-05-04): proactive MM-counter-play scanner. In NORMAL/UNKNOWN
    # regimes ONLY (we trust TREND/RANGE classifier when it has confidence),
    # check whether the snapshot shows a textbook MM-counter setup. If yes,
    # override the regime default. We never override RANGE (which already
    # picks short_iron_condor) or TREND (clear directional signal).
    mm_play: dict | None = None
    if regime in ("NORMAL", "UNKNOWN"):
        mm_play = _detect_mm_opportunity(snap, now_utc=ctx["now_utc"], port=port)

    if regime in ("TREND_UP", "TREND_DOWN"):
        struct = EXP.REGIME_STRATEGY_MAP[regime][0]
    elif regime == "RANGE":
        struct = EXP.REGIME_STRATEGY_MAP["RANGE"][0]
    elif mm_play is not None:
        struct = mm_play["structure"]
    else:  # NORMAL or UNKNOWN with no MM-counter setup detected
        if state == "cheap":
            struct = EXP.VOL_BIAS["structures_when_cheap"][0]
        elif state == "expensive":
            struct = EXP.VOL_BIAS["structures_when_expensive"][0]
        else:
            struct = EXP.VOL_BIAS["structures_when_neutral"][0]

    # POINT 5 (2026-05-10): selective theta-harvest gate
    theta_skip = _theta_harvest_strict_gate(snap, port, struct)
    if theta_skip is not None:
        return {"action": "skip",
                "reason": theta_skip["reason"],
                "rule": theta_skip["rule"],
                "rejected_structure": struct}

    # --------- doctrine pre-trade check (post structure pick) ----------------
    # Reject IV-regime violations (e.g. selling premium when IV is cheap, or
    # buying premium when IV is rich) per the doctrine in
    # sygnif-plugin/references/options-walls-and-bias.md. Perp structures
    # bypass — IV-regime rules apply to options only.
    if "perp" not in struct:
        d = DOCTRINE.doctrine_pre_trade_check(snap, structure=struct,
                                              now_utc=ctx["now_utc"])
        if not d.allow:
            return {"action": "skip", "reason": d.reason,
                    "rule": d.rule, "doctrine_facts": d.facts,
                    "rejected_structure": struct}

    # P1.6c (2026-05-04): funding-crowding gate — block direction-aligned
    # opens when funding is at the 85th+ percentile.
    crowd = _funding_crowded_check(snap, struct)
    if crowd is not None:
        return {"action": "skip",
                "reason": crowd["reason"],
                "rule": crowd["rule"],
                "rejected_structure": struct,
                "funding_facts": crowd}

    # P1.6f (2026-05-04): liquidity-asymmetry gate — block perp / directional
    # spread opens that point INTO the closer stop-cluster pool.
    asym = _liquidity_asymmetry_check(snap, struct)
    if asym is not None:
        return {"action": "skip",
                "reason": asym["reason"],
                "rule": asym["rule"],
                "rejected_structure": struct,
                "liquidity_facts": asym}

    # Phase A entry gates (2026-05-05): orderbook + skew + insurance.
    # Each gate returns either None (pass) or a dict with `size_pct`. A
    # size_pct of 0.0 means BLOCK; (0,1) means reduce-size and continue.
    # Captured into ctx for downstream sizing modulation; hard blocks
    # short-circuit out as `action=skip`.
    gates_size_multiplier = 1.0
    gates_log = []
    for gate_fn, gate_name in (
        (lambda: _orderbook_imbalance_check(struct), "orderbook"),
        (lambda: _skew_25d_check(struct), "skew_25d"),
        (lambda: _insurance_stress_check(), "insurance"),
    ):
        try:
            r = gate_fn()
        except Exception as e:
            sys.stderr.write(f"[trader] gate {gate_name} raised "
                              f"{type(e).__name__}: {e}\n")
            continue
        if r is None:
            continue
        sp = float(r.get("size_pct", 1.0))
        if sp <= 0.0:
            return {"action": "skip",
                    "reason": r["reason"],
                    "rule": r["rule"],
                    "rejected_structure": struct,
                    f"{gate_name}_facts": r}
        # Soft reduction — apply multiplicatively
        gates_size_multiplier *= sp
        gates_log.append((gate_name, sp, r["rule"]))
    if gates_size_multiplier < 1.0:
        sys.stderr.write(f"[trader] entry gates softened size: "
                          f"×{gates_size_multiplier:.2f} ({gates_log})\n")
        ctx["entry_gates_size_multiplier"] = round(gates_size_multiplier, 3)
        ctx["entry_gates_log"] = gates_log

    # P1.6d (2026-05-04): post-expiry blackout — block long-vol opens during
    # the 08:00-11:00 UTC release window.
    blackout = _post_expiry_blackout_check(ctx["now_utc"], struct)
    if blackout is not None:
        return {"action": "skip",
                "reason": blackout["reason"],
                "rule": blackout["rule"],
                "rejected_structure": struct,
                "blackout_facts": blackout}

    # P4a (2026-05-02): per-side concentration enforcement. Block opening a
    # new position when we already have max_concurrent_per_side positions
    # in the same correlated bucket. Three short_iron_condors are not
    # three independent bets — they're one big short-vol bet, sized 3×.
    bucket = _strategy_side_bucket(struct)
    if bucket != "unknown":
        per_side_cap = int(EXP.SIZING.get("max_concurrent_per_side", 3))
        n_in_bucket = _count_open_in_bucket(port, bucket)
        if n_in_bucket >= per_side_cap:
            return {"action": "skip",
                    "reason": (f"{n_in_bucket}/{per_side_cap} already in "
                               f"side bucket {bucket!r}; concentration cap"),
                    "rule": "max_per_side",
                    "rejected_structure": struct,
                    "side_bucket": bucket,
                    "open_in_bucket": n_in_bucket}

    # --------- build the plan -------------------------------------------------
    # de-dup: skip if a near-identical position is already open on same expiry
    expiry_for_dedup = EXP.pick_expiries(snap, long_vol=(struct in ("long_strangle", "long_straddle", "bull_call_spread", "bear_put_spread")))
    if _already_have_similar_position(port, struct, expiry_for_dedup):
        return {"action": "skip",
                "reason": f"already have {struct} on {expiry_for_dedup} open",
                "rule": "dedup"}

    # --------- forecast cooperation hint (soft, additive) --------------------
    # Distill the EC2 ML ensemble + X1 rule-bias forecasts into a combined
    # bias, attach to the plan for visibility in swarm audit + llm.advise
    # snapshots. Deterministic planner remains authoritative — structure pick
    # above is unchanged. The combined bias only acts as a soft GATE: skip
    # when producers strongly disagree (MIXED), since neither feed will give
    # the trade enough conviction.
    fc_bias, fc_why = _forecast_bias(ctx.get("forecasts") or {})
    if fc_bias == "MIXED":
        return {"action": "skip",
                "reason": "predict producers disagree on direction; "
                          "stand down until they realign",
                "rule": "forecast_mixed",
                "forecast_why": fc_why}

    # --------- swing-failure / BOS structure override -----------------------
    # When sygnif-predict has fired a fresh directional signal
    # (swing_failure_long/short, bos_long/short) AND the regime is undecided
    # (NORMAL or UNKNOWN), override the regime-default neutral structure to a
    # directional perp. This gives the planner a way to act on conviction
    # signals when the regime classifier alone is non-committal.
    #
    # Guarded:
    #   - Only fires in regime ∈ {NORMAL, UNKNOWN}. TREND_UP/DOWN already
    #     align with directional structures from REGIME_STRATEGY_MAP; RANGE
    #     deliberately wants neutral structures (chop benefits from theta).
    #   - Bias must agree with signal direction (avoids overriding into a
    #     setup the bias-veto would have caught).
    #   - Action MUST be 'open' (high conviction). 'rejected' signals were
    #     previously also honoured as soft hints, but the 30-day SFP
    #     backtest (2026-05-04, /tmp/sfp_backtest.py) showed the rejected
    #     cohort has −0.447 R per-fire expectancy vs +0.187 R for 'open'.
    #     The producer's own threshold gate is correct; the consumer must
    #     respect it.
    fc_signal = _forecast_signal(ctx.get("forecasts") or {})
    signal_override: dict | None = None
    if (fc_signal
            and regime in ("NORMAL", "UNKNOWN")
            and fc_signal["action"] == "open"):
        sig_side = fc_signal["side"]
        sig_bias = "LONG" if sig_side == "Buy" else "SHORT"
        # Require bias agreement so we're not overriding into a trade the
        # combined-bias veto would block.
        if fc_bias in (sig_bias, "NEUTRAL"):
            new_struct = ("perp_long_with_stop"
                          if sig_side == "Buy"
                          else "perp_short_with_stop")
            if new_struct != struct:
                signal_override = {
                    "from": struct,
                    "to": new_struct,
                    "signal": fc_signal["signal"],
                    "action": fc_signal["action"],
                    "setup_conf": fc_signal.get("setup_conf"),
                }
                struct = new_struct

    # P3.0 (2026-05-04): live A/B shadow log of the variant-A "legacy" rule
    # that ALSO accepts action="rejected" signals. We do NOT execute the
    # variant — only record what it would have proposed at this exact moment.
    # After 60-90 days of accumulation, comparing matched-pairs of
    # (production_decision, variant_a_decision) against realized outcomes
    # tells us whether the producer's threshold gate is too strict, too
    # loose, or right where it should be.
    try:
        from datetime import datetime as _dt, timezone as _tz
        if fc_signal:
            sig_side = fc_signal["side"]
            sig_bias_v = "LONG" if sig_side == "Buy" else "SHORT"
            variant_a_would_override = bool(
                regime in ("NORMAL", "UNKNOWN")
                and fc_signal["action"] in ("open", "rejected")
                and fc_bias in (sig_bias_v, "NEUTRAL"))
            production_overrode = signal_override is not None
            ab_payload = {
                "kind": "sfp.ab_shadow",
                "ts": ctx["now_utc"].timestamp(),
                "ts_utc": ctx["now_utc"].isoformat(timespec="seconds"),
                "signal": fc_signal["signal"],
                "side": fc_signal["side"],
                "action_emitted": fc_signal["action"],
                "regime": regime,
                "fc_bias": fc_bias,
                "production_overrode": production_overrode,
                "production_struct": struct if production_overrode else None,
                "variant_a_would_override": variant_a_would_override,
                "variant_a_struct": (("perp_long_with_stop" if sig_side == "Buy"
                                      else "perp_short_with_stop")
                                      if variant_a_would_override else None),
                "differ": (production_overrode != variant_a_would_override),
            }
            try:
                import sygnif_neurons as _N
                _N.run("swarm.write", {
                    "agent_id": "agent-trader",
                    "topic": "sfp.ab_shadow",
                    "swarm_id": "trading",
                    "content": json.dumps(ab_payload, default=str),
                })
            except Exception:
                pass  # never fail plan_trade on shadow-log error
    except Exception:
        pass

    rule_chain = [
        f"regime={regime}",
        f"iv={iv*100:.2f}% iv_rv={iv_rv:.2f} → vol_state={state}",
        f"selected={struct}",
        f"forecast_bias={fc_bias}",
        *[f"  · {w}" for w in fc_why],
    ]
    if signal_override:
        rule_chain.append(
            f"signal-override: {signal_override['from']} → {signal_override['to']} "
            f"(sygnif-predict {signal_override['signal']} action={signal_override['action']})"
        )
    if mm_play:
        rule_chain.append(
            f"mm-counter-play: {mm_play['play']} → {mm_play['structure']} "
            f"(confidence={mm_play['confidence']})"
        )
    plan = {
        "action": "propose",
        "structure": struct,
        "rule_chain": rule_chain,
        "context": {
            "F": F, "iv": iv, "iv_rv": iv_rv, "regime": regime,
            "regime_origin": regime_origin,
            "implied_1d_move_usd": implied_1d_move, "equity_usdc": equity,
            "open_positions": open_count,
            "forecast_bias": fc_bias,
            "forecasts": ctx.get("forecasts") or {},
            "signal_override": signal_override,
            "doctrine": doctrine_facts,
            "mm_counter_play": mm_play,
        },
    }

    # --------- structure-specific params --------------------------------------
    if struct in ("long_strangle",):
        wing = implied_1d_move * EXP.STRIKE_RULES["strangle_wing_in_implied_moves"]
        K_call_target = EXP.round_strike(F + wing)
        K_put_target = EXP.round_strike(F - wing)
        expiry = EXP.pick_expiries(snap, long_vol=True)
        avail = _snap_to_chain_strikes(expiry) if expiry else []
        K_call = _nearest_strike(K_call_target, avail) if avail else K_call_target
        K_put = _nearest_strike(K_put_target, avail) if avail else K_put_target
        plan.update({
            "instrument": "option",
            "strategy": "long_strangle",
            "expiry": expiry,
            "K_put": K_put, "K_call": K_call,
            "qty": 1,
            "iv_for_pricing": iv,
            "thesis": f"buy vol on {expiry}: IV {iv*100:.1f}% < threshold "
                      f"{EXP.VOL_BIAS['cheap_vol_threshold']*100:.0f}% AND "
                      f"IV/RV {iv_rv:.2f} < {EXP.VOL_BIAS['iv_rv_ratio_long_bias']}",
            "neuron": "order.paper.option",
        })
    elif struct in ("short_iron_condor",):
        wing_short = implied_1d_move * EXP.STRIKE_RULES["condor_short_in_implied_moves"]
        wing_long  = implied_1d_move * EXP.STRIKE_RULES["condor_long_in_implied_moves"]
        # Cascade through ALL in-band expiries (added 2026-05-04). For each
        # candidate, build the 4 strikes, run a side-aware bid/ask precheck
        # against the live chain, and accept the FIRST expiry whose short
        # legs (the legs we cross to fill) have crossable quotes.
        cand_expiries = EXP.pick_expiries_ranked(snap, long_vol=False) or []
        if not cand_expiries:
            fallback = EXP.pick_expiries(snap, long_vol=False)
            if fallback:
                cand_expiries = [fallback]
        chosen = None
        cascade_attempts: list[dict] = []

        def _snap_outward(target: float, avail_strikes: list[float],
                            *, direction: str) -> float:
            """Snap to chain strike AWAY from spot.
            direction='above': pick lowest available ≥ target (call legs)
            direction='below': pick highest available ≤ target (put legs)
            Falls back to nearest if no strike on the safe side exists.
            Postmortem 2026-05-04: short_put was rounding to NEAREST which
            could land closer to F than the doctrine 1× implied wing. With
            short_put 1× wing the closer side is near-ATM = retCode 110013
            margin blowup. Always round outward for short_premium safety.
            """
            if not avail_strikes:
                return EXP.round_strike(target)
            if direction == "above":
                higher = [s for s in avail_strikes if s >= target]
                if higher:
                    return min(higher)
                return max(avail_strikes)
            # direction == "below"
            lower = [s for s in avail_strikes if s <= target]
            if lower:
                return max(lower)
            return min(avail_strikes)

        for cand in cand_expiries:
            avail = _snap_to_chain_strikes(cand) if cand else []
            # Snap each leg AWAY from F (further OTM). Guarantees the wing
            # is at least as wide as the doctrine target, never narrower.
            cs = _snap_outward(F + wing_short, avail, direction="above")  # short call: ≥ target
            cl = _snap_outward(F + wing_long,  avail, direction="above")  # long  call: ≥ target
            ps = _snap_outward(F - wing_short, avail, direction="below")  # short put : ≤ target
            pl = _snap_outward(F - wing_long,  avail, direction="below")  # long  put : ≤ target
            quotes_ok = _precheck_iron_condor_quotes(
                expiry=cand, K_put_long=pl, K_put_short=ps,
                K_call_short=cs, K_call_long=cl)
            cascade_attempts.append({
                "expiry": cand, "ok": quotes_ok["ok"],
                "fail_legs": quotes_ok.get("fail_legs", []),
            })
            if quotes_ok["ok"]:
                chosen = {
                    "expiry": cand,
                    "K_put_long": pl, "K_put_short": ps,
                    "K_call_short": cs, "K_call_long": cl,
                }
                break
        if chosen is None:
            return {"action": "skip",
                    "reason": (f"all in-band expiries had illiquid quotes — "
                               f"tried {len(cascade_attempts)} candidates"),
                    "rule": "no_liquid_expiry",
                    "cascade_attempts": cascade_attempts,
                    "rejected_structure": struct}
        expiry = chosen["expiry"]
        K_call_short = chosen["K_call_short"]; K_call_long = chosen["K_call_long"]
        K_put_short = chosen["K_put_short"]; K_put_long = chosen["K_put_long"]
        # P2a (2026-05-02): max_loss per contract = wider wing - credit.
        wing_width = max(K_call_long - K_call_short, K_put_short - K_put_long)
        max_loss_upper_bound = float(wing_width)
        # Size qty against equity-cap risk budget (postmortem 2026-05-04:
        # qty=1 hardcoded was 100× too big for our $1.7k demo equity →
        # Bybit retCode 110013 margin overflow on the short leg). Bybit
        # option min qty = 0.01, step = 0.01.
        risk_pct = float(EXP.SIZING.get("option_default_risk_pct", 1.0)) / 100.0
        risk_budget_usd = max(0.0, equity * risk_pct)
        if max_loss_upper_bound > 0 and risk_budget_usd > 0:
            qty_target = risk_budget_usd / max_loss_upper_bound
        else:
            qty_target = 0.01
        # Round DOWN to 0.01 step, but never below 0.01 (Bybit minimum)
        OPTION_MIN_QTY = 0.01
        qty_final = max(OPTION_MIN_QTY,
                        int(qty_target / OPTION_MIN_QTY) * OPTION_MIN_QTY)
        # Round to 2 decimals to avoid float drift
        qty_final = round(qty_final, 2)
        plan.update({
            "instrument": "option",
            "strategy": "iron_condor",
            "expiry": expiry,
            "K_put_long": K_put_long, "K_put_short": K_put_short,
            "K_call_short": K_call_short, "K_call_long": K_call_long,
            "qty": qty_final,
            "iv_for_pricing": iv,
            "sizing_basis": "max_loss",
            "max_loss_per_contract_usd": max_loss_upper_bound,
            "risk_budget_usd": round(risk_budget_usd, 2),
            "qty_target_unrounded": round(qty_target, 4),
            "expiry_cascade": cascade_attempts,   # which expiries were tried
            "thesis": (f"theta harvest on {expiry}: short {wing_short:.0f} wing "
                       f"= ±1× implied move; wing_width=${wing_width:.0f}; "
                       f"qty={qty_final} risk_budget=${risk_budget_usd:.2f}"),
            "neuron": "order.paper.option",
        })
    elif struct == "short_strangle":
        wing = implied_1d_move * EXP.STRIKE_RULES["strangle_wing_in_implied_moves"]
        K_call = EXP.round_strike(F + wing)
        K_put = EXP.round_strike(F - wing)
        expiry = EXP.pick_expiries(snap, long_vol=False)
        # P2a (2026-05-02): naked short_strangle has unbounded upside loss
        # (call side) and put-side loss bounded only by strike going to 0.
        # Use 2σ adverse heuristic: one-day move at 2× implied (sqrt(2))
        # — captures ~95% of normal-day distributions. Intentionally
        # generous so the sizer chokes back on naked positions.
        # If naked structures are not explicitly opted-in by the operator,
        # fall back to short_iron_condor (the doctrine-safe alternative).
        sigma2_loss = float(implied_1d_move * 2.0)
        if not bool(_walk_dict(snap, "options", "naked_shorts_allowed", default=False)):
            # Hard guard — refuse naked structures unless discovery explicitly
            # advertises naked_shorts_allowed=True. Operator escape valve only.
            return {"action": "skip",
                    "reason": ("short_strangle requires naked_shorts_allowed "
                              "flag in discovery.options; falling back to "
                              "short_iron_condor recommended"),
                    "rule": "naked_shorts_blocked",
                    "rejected_structure": struct}
        plan.update({
            "instrument": "option",
            "strategy": "short_strangle",
            "expiry": expiry, "K_put": K_put, "K_call": K_call, "qty": 1,
            "iv_for_pricing": iv,
            # P2a sizing semantics
            "sizing_basis": "max_loss",
            "max_loss_per_contract_usd": sigma2_loss,
            "thesis": f"NAKED short strangle — UNDEFINED upside risk; size 1 contract only. "
                      f"Theta on {expiry}. 2σ adverse move ~${sigma2_loss:.0f}",
            "neuron": "order.paper.option",
            "warning": "naked short — unbounded loss; consider iron_condor instead",
        })
    elif struct == "perp_long_with_stop":
        # use order.size for qty; 0.5% risk, 1% stop. TP at 2× the stop
        # distance (1R risk → 2R reward target = ~0.67 win-rate breakeven).
        stop_pct = EXP.SIZING["default_perp_stop_pct"] / 100.0
        tp_pct = stop_pct * 2.0
        plan.update({
            "instrument": "perp",
            "strategy": "perp_long",
            "symbol": "BTCUSDT",
            "side": "Buy",
            "risk_pct": EXP.SIZING["default_risk_pct"],
            "stop_pct": EXP.SIZING["default_perp_stop_pct"],
            "stop_loss_price":   round(F * (1 - stop_pct), 1),
            "take_profit_price": round(F * (1 + tp_pct),   1),
            "thesis": f"trend continuation in {regime}; sized at "
                      f"{EXP.SIZING['default_risk_pct']}% risk, "
                      f"{EXP.SIZING['default_perp_stop_pct']}% stop, "
                      f"2× TP",
            "neuron": "order.paper.perp",
            "size_via": "order.size",
        })
    elif struct == "perp_short_with_stop":
        stop_pct = EXP.SIZING["default_perp_stop_pct"] / 100.0
        tp_pct = stop_pct * 2.0
        plan.update({
            "instrument": "perp",
            "strategy": "perp_short",
            "symbol": "BTCUSDT",
            "side": "Sell",
            "risk_pct": EXP.SIZING["default_risk_pct"],
            "stop_pct": EXP.SIZING["default_perp_stop_pct"],
            # short: SL above entry, TP below
            "stop_loss_price":   round(F * (1 + stop_pct), 1),
            "take_profit_price": round(F * (1 - tp_pct),   1),
            "thesis": f"trend continuation short in {regime}; sized at "
                      f"{EXP.SIZING['default_risk_pct']}% risk, "
                      f"{EXP.SIZING['default_perp_stop_pct']}% stop, "
                      f"2× TP",
            "neuron": "order.paper.perp",
            "size_via": "order.size",
        })
    elif struct == "bull_call_spread":
        K_buy = EXP.round_strike(F)
        K_sell = EXP.round_strike(F + implied_1d_move * 1.0)
        expiry = EXP.pick_expiries(snap, long_vol=True)
        plan.update({
            "instrument": "option", "strategy": "bull_call_spread",
            "expiry": expiry, "K_buy": K_buy, "K_sell": K_sell, "qty": 1,
            "iv_for_pricing": iv,
            "thesis": f"directional long in {regime}; defined risk debit spread",
            "neuron": "order.paper.option",
        })
    elif struct == "bear_put_spread":
        K_buy = EXP.round_strike(F)
        K_sell = EXP.round_strike(F - implied_1d_move * 1.0)
        expiry = EXP.pick_expiries(snap, long_vol=True)
        plan.update({
            "instrument": "option", "strategy": "bear_put_spread",
            "expiry": expiry, "K_buy": K_buy, "K_sell": K_sell, "qty": 1,
            "iv_for_pricing": iv,
            "thesis": f"directional short in {regime}; defined risk debit spread",
            "neuron": "order.paper.option",
        })
    else:
        plan.update({"action": "skip",
                     "reason": f"no concrete plan for structure {struct!r}",
                     "rule": "no_template"})
        return plan

    # P1.5 (2026-05-04): post-build sanity gates.
    # Both gates were triggered by the 4MAY26 75500-P / 82000-C expiry-day
    # loss: strikes were 5× implied move wide and DTE was 3 days for a
    # long-vol structure (rule says ≥5 days). Either gate alone would have
    # blocked that setup pre-trade.
    is_long_vol = struct in ("long_strangle", "long_straddle",
                              "bull_call_spread", "bear_put_spread")
    chosen_expiry = plan.get("expiry")
    dte_problem = EXP.check_dte_in_band(chosen_expiry, long_vol=is_long_vol,
                                          now_utc=ctx["now_utc"])
    if dte_problem is not None:
        return {"action": "skip",
                "reason": dte_problem["reason"],
                "rule": dte_problem["rule"],
                "rejected_structure": struct,
                "rejected_expiry": chosen_expiry,
                "dte_facts": dte_problem}

    if "perp" not in struct:
        strike_problem = EXP.check_strike_distances(
            plan, F=F, implied_1d_move=implied_1d_move)
        if strike_problem is not None:
            return {"action": "skip",
                    "reason": strike_problem["reason"],
                    "rule": strike_problem["rule"],
                    "rejected_structure": struct,
                    "rejected_expiry": chosen_expiry,
                    "strike_facts": strike_problem}

    # P1.6e (2026-05-04): OI cluster proximity for long-vol structures.
    # Magnet zone near max_pain reduces realized vol and bleeds long-vol
    # premia. Reject when our strikes are within $200 of max_pain.
    cluster_problem = _oi_cluster_entry_check(plan, snap)
    if cluster_problem is not None:
        return {"action": "skip",
                "reason": cluster_problem["reason"],
                "rule": cluster_problem["rule"],
                "rejected_structure": struct,
                "rejected_expiry": chosen_expiry,
                "cluster_facts": cluster_problem}

    return plan


# ---------------------------------------------------------------------------
# reviewer
# ---------------------------------------------------------------------------


def review_positions() -> dict:
    """For each open position (paper + live Bybit), evaluate exit rules.

    The R-ladder (agent.exit_logic v3, 2026-05-01) reads LIVE Bybit positions
    via agent.bybit_positions.fetch_open_positions, not just the paper
    portfolio. This closes the gap that left 5 live positions unmanaged for
    8+ hours during the iron_condor partial-fill incident.

    Live positions are deduped against paper by stable pid (sha256(symbol|side));
    paper wins when both are present. CLOSE / PARTIAL_CLOSE verdicts on live
    positions are routed through order.closes (marketable-limit then market).
    """
    ctx = _load_context()
    port = ctx["portfolio"]
    snap = ctx["discovery"]

    F = float(snap.get("btc_perp_last") or 0)

    open_pos = port.get("open", []) or []
    verdicts = []

    now = ctx["now_utc"]

    # ---- merge LIVE Bybit positions into the iteration set --------------
    # Paper positions still iterated (legacy + paper-mode users); live positions
    # added when their pid (stable hash) doesn't already appear in paper.
    paper_pids = {str(p.get("id", "")) for p in open_pos}
    try:
        live_pos = BYBIT_POS.fetch_open_positions(mode="demo", now_utc=now)
    except Exception:
        live_pos = []
    live_added = 0
    for lp in live_pos:
        if str(lp.get("id", "")) in paper_pids:
            continue
        open_pos = list(open_pos) + [lp]
        live_added += 1

    for p in open_pos:
        pid = p["id"]
        label = p.get("label", "")
        unreal = float(p.get("unrealized_pnl_usdc", 0))
        opened_iso = p.get("opened_ts_utc", "")
        marks = p.get("marks", []) or []
        is_option = any("now_iv" in m for m in marks)
        is_short_vol = "condor" in label.lower() or "short" in label.lower()

        # Software-side TP/SL targets attached at open (set by
        # order.paper.attach_targets, also mirrored to Bybit demo/live).
        # In paper-only mode this is the ONLY enforcement; in demo/live the
        # exchange holds the same triggers — these reads are the local mirror.
        tp_price = p.get("take_profit_price")
        sl_price = p.get("stop_loss_price")
        # Latest perp mark for trigger-comparison. F is BTC; for non-BTC perps
        # this comparison would need per-symbol marks (TODO when alts arrive).
        perp_mark = None
        is_perp = (not is_option)
        if is_perp:
            for m in marks:
                if m.get("symbol", "").endswith("USDT") and m.get("now_mark"):
                    perp_mark = float(m["now_mark"])
                    break
            if perp_mark is None and F > 0:
                perp_mark = F

        verdict = "HOLD"
        why: list[str] = [f"unreal=${unreal:+.4f}"]
        action_payload = None
        action_meta = {}

        # ---- doctrine-driven exit decision (EXIT_RULES_V2, post 2026-04-30) ----
        # Calls agent.exit_logic.decide_exit which knows the per-structure
        # rules (50% max for short_premium, trailing 50% HWM for long_premium,
        # +1R-arm trailing for perps, regime-flip close, sub-4h theta guard).
        # Falls back to legacy heuristics below when verdict=HOLD.
        try:
            regime_now = (snap.get("regime") or "").upper() or None
            regime_at_open = (p.get("regime_at_open") or "").upper() or None
            d = XL.decide_exit(p, mark_perp=perp_mark, snapshot=snap,
                               now_utc=now,
                               regime_now=regime_now,
                               regime_at_open=regime_at_open)
            if d["verdict"] in ("CLOSE", "TRAIL_ARM", "TRAIL_UPDATE"):
                verdict = d["verdict"]
                why.append(d["reason"])
                action_payload = d.get("action")
                action_meta = d.get("meta", {})
        except Exception as e:
            why.append(f"exit_logic error: {type(e).__name__}: {e}")

        # ---- legacy fallback (only fires when doctrine layer says HOLD) ------
        if verdict == "HOLD":
            # Exchange-style TP/SL triggers — informational; exchange enforces
            if is_perp and perp_mark is not None and (tp_price or sl_price):
                first_leg = (p.get("legs") or [{}])[0]
                side = (first_leg.get("side") or "").lower()
                if side == "buy":
                    if tp_price and perp_mark >= float(tp_price):
                        verdict = "CLOSE"; why.append(f"TP hit: mark {perp_mark:.2f} ≥ {float(tp_price):.2f}")
                    elif sl_price and perp_mark <= float(sl_price):
                        verdict = "CLOSE"; why.append(f"SL hit: mark {perp_mark:.2f} ≤ {float(sl_price):.2f}")
                elif side == "sell":
                    if tp_price and perp_mark <= float(tp_price):
                        verdict = "CLOSE"; why.append(f"TP hit: mark {perp_mark:.2f} ≤ {float(tp_price):.2f}")
                    elif sl_price and perp_mark >= float(sl_price):
                        verdict = "CLOSE"; why.append(f"SL hit: mark {perp_mark:.2f} ≥ {float(sl_price):.2f}")

            # Catch-all loss stop — for both perp (when no SL set) and option
            if verdict == "HOLD" and unreal < -2.0:
                verdict = "CLOSE"
                why.append(f"loss ${unreal:+.4f} > 0.4% account drawdown")

        verdicts.append({
            "id": pid, "label": label, "unrealized_pnl_usdc": unreal,
            "verdict": verdict, "why": "; ".join(why),
            "is_option": is_option, "is_short_vol": is_short_vol,
            "action": action_payload, "action_meta": action_meta,
            "source": p.get("source") or "paper",
            "symbol": p.get("symbol") or (p.get("legs") or [{}])[0].get("symbol"),
            "live_side": p.get("side"),
            "live_qty":  p.get("qty"),
        })

    return {
        "now_utc": now.isoformat(),
        "F": F,
        "open_count": len(open_pos),
        "live_added_to_review": live_added,
        "verdicts": verdicts,
        "summary": {
            "HOLD":  sum(1 for v in verdicts if v["verdict"] == "HOLD"),
            "CLOSE": sum(1 for v in verdicts if v["verdict"] == "CLOSE"),
            "ROLL":  sum(1 for v in verdicts if v["verdict"] == "ROLL"),
            "SCALE": sum(1 for v in verdicts if v["verdict"] == "SCALE"),
            "PARTIAL_CLOSE": sum(1 for v in verdicts if v["verdict"] == "PARTIAL_CLOSE"),
        },
    }


# ---------------------------------------------------------------------------
# Journal hooks (entry / exit / block) — Phase J (2026-05-05)
# ---------------------------------------------------------------------------


def _classify_entry_path(plan: dict) -> tuple[str, str | None]:
    """Derive the entry_path label and confidence from a plan dict.

    Returns (entry_path, confidence). The path identifies which decision
    branch fired so we can attribute P&L back to it.
    """
    ctx = plan.get("context") or {}
    mm = ctx.get("mm_counter_play") if isinstance(ctx, dict) else None
    if mm and isinstance(mm, dict) and mm.get("play"):
        return (mm["play"], mm.get("confidence"))
    sig_override = ctx.get("signal_override") if isinstance(ctx, dict) else None
    if sig_override and isinstance(sig_override, dict):
        return (f"sfp_{sig_override.get('signal','sfp')}", None)
    return ("regime_default", None)


def _side_from_structure(structure: str) -> str | None:
    """Map structure name → expected order side."""
    s = (structure or "").lower()
    if "perp_long" in s or "bull_call" in s or "bull_put" in s:
        return "Buy"
    if "perp_short" in s or "bear_put" in s or "bear_call" in s:
        return "Sell"
    if "long_strangle" in s or "long_straddle" in s:
        return "Buy"  # both legs Buy
    if "short_iron_condor" in s or "iron_butterfly" in s:
        return "Sell"  # body legs Sell
    return None


def _build_gates_trace(plan: dict) -> dict:
    """Reconstruct gate trace from plan rule_chain + facts."""
    trace = {}
    for k in ("doctrine_facts", "funding_facts", "liquidity_facts",
              "blackout_facts", "oi_cluster_facts",
              "orderbook_facts", "skew_25d_facts", "insurance_facts"):
        if k in plan:
            facts = plan[k]
            gate_name = k.replace("_facts", "")
            trace[gate_name] = facts.get("rule") if isinstance(facts, dict) else "BLOCKED"
    # Mark passed gates as PASS (those that didn't block)
    for gate in ("doctrine", "funding", "liquidity", "blackout",
                 "oi_cluster", "orderbook", "skew_25d", "insurance"):
        if gate not in trace and plan.get("action") == "propose":
            trace[gate] = "PASS"
    return trace


def _journal_plan_result(plan: dict, snap: dict | None = None) -> None:
    """Write a journal record for the plan result. Best-effort; never raises.

    - action="propose"  → log_entry (entry decided)
    - action="skip" + rejected_structure → log_block (gate blocked)
    - action="skip" + no rejected_structure → silent (no decision to log)
    """
    try:
        action = plan.get("action")
        if action == "propose":
            entry_path, confidence = _classify_entry_path(plan)
            structure = plan.get("structure") or ""
            ctx = plan.get("context") or {}
            JOURNAL.log_entry(
                entry_path=entry_path,
                structure=structure,
                side=_side_from_structure(structure),
                qty=plan.get("qty"),
                entry_price_intended=ctx.get("F"),
                snap=snap,
                gates_trace=_build_gates_trace(plan),
                gates_size_multiplier=ctx.get("entry_gates_size_multiplier", 1.0)
                if isinstance(ctx, dict) else 1.0,
                rationale=" → ".join(plan.get("rule_chain", []))[:240],
                confidence=confidence,
                extra={"thesis": plan.get("thesis"),
                       "instrument": plan.get("instrument"),
                       "expiry": plan.get("expiry")},
            )
        elif action == "skip" and plan.get("rejected_structure"):
            # Gate-blocked attempt — counterfactual data
            ctx = plan.get("context") or {}
            JOURNAL.log_block(
                would_be_entry_path=plan.get("rejected_path") or "regime_default",
                would_be_structure=plan.get("rejected_structure", ""),
                blocking_gate=plan.get("rule", "unknown").split("_")[0],
                blocking_rule=plan.get("rule", "unknown"),
                reason=plan.get("reason", ""),
                snap=snap,
                extra={"facts_keys": [k for k in plan.keys() if k.endswith("_facts")]},
            )
    except Exception as e:
        import sys
        sys.stderr.write(f"[trader._journal_plan_result] {type(e).__name__}: {e}\n")




# ---------------------------------------------------------------------------
# Phase 1 — Tier promotion (2026-05-10)
# ---------------------------------------------------------------------------
#
# Stage-1 observability shipped 2026-05-04 records tier_candidates in the
# heartbeat. This patch promotes those candidates to plan["leverage_tier"]
# and plan["size_tier"] so the sizing_tuner + executor G4 gate honor them.
#
# Env controls (all default off — explicit opt-in required):
#   SYGNIF_TIER_PROMOTION=1                     master kill switch (demo)
#   SYGNIF_TIER_PROMOTION_LIVE=clear-for-live   required ALSO for live mode
#   SYGNIF_TIER_FULL=1                          lift caps to spec maxes
#                                               (default: staged at half)
#
def _resolve_orders_env() -> str:
    """Return 'demo' | 'live' | 'paper' from current env. Mirrors
    sygnif_neurons.n_agent_trade_execute resolution."""
    import os as _os
    mode = (_os.environ.get("SYGNIF_ORDERS_MODE") or "paper").strip().lower()
    if mode not in ("paper", "demo", "live"):
        mode = "paper"
    return mode


def _compute_tier_candidates_inline(plan: dict) -> dict:
    """Same logic as agent.loop._compute_tier_candidates but callable from
    trader. Reads predict_loop, BTC tape, BTC ticker via sygnif_neurons.
    Returns the 4 boolean signals + raw values. Never raises."""
    import datetime as _dt
    out = {
        "predict_strong":     False,
        "big_recent_move":    False,
        "at_psych_barrier":   False,
        "regime_is_trend":    False,
        "predict_signal":     None,
        "recent_move_pct":    None,
        "psych_distance_bps": None,
        "ts_utc":             _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
    }
    try:
        import sygnif_neurons as _N
    except Exception:
        return out

    # 1. predict signal — sygnif-predict emits JSON with signal + regime.
    #    predict_strong fires when any scanner-derived signal is set
    #    (bos_long, swing_failure_long, swing_failure_short, etc.).
    #    Also pulls regime.label as a fallback for `regime_is_trend` so we
    #    don't depend solely on the planner-context regime.
    try:
        import json as _json
        r = _N.run("swarm.recent", {"limit": 5,
                                       "agent_id": "sygnif-predict",
                                       "topic": "forecast"})
        if r.get("ok"):
            for entry in (r.get("data") or {}).get("entries") or []:
                try:
                    fc = _json.loads(entry.get("content") or "{}")
                except (_json.JSONDecodeError, TypeError):
                    continue
                sig = fc.get("signal")
                if sig:
                    out["predict_signal"] = sig
                    out["predict_strong"] = True   # any scanner = high conv
                regime_lbl = ((fc.get("regime") or {}).get("label") or "").upper()
                if regime_lbl and not out["regime_is_trend"]:
                    out["regime_is_trend"] = regime_lbl.startswith("TREND_")
                if out["predict_signal"]:
                    break
    except Exception:
        pass

    # 2. recent 4h move from BTC 1h klines
    try:
        t = _N.run("btc.tape.live", {"interval": "60", "limit": 5,
                                       "symbol": "BTCUSDT"})
        if t.get("ok"):
            data = t.get("data") or {}
            bars = data.get("bars") or data.get("klines") or []
            if len(bars) >= 4:
                close_now = float(bars[-1].get("close")
                                  or bars[-1].get("c") or 0)
                anchor = bars[-4]
                anchor_open = float(anchor.get("open")
                                     or anchor.get("o") or 0)
                if anchor_open > 0:
                    pct = (close_now - anchor_open) / anchor_open * 100.0
                    out["recent_move_pct"] = round(pct, 2)
                    out["big_recent_move"] = abs(pct) >= 3.0
    except Exception:
        pass

    # 3. distance to nearest 5k psych barrier
    try:
        ctx = plan.get("context") if isinstance(plan, dict) else None
        ctx = ctx if isinstance(ctx, dict) else {}
        F = float(ctx.get("F") or plan.get("F") or 0)
        if F == 0:
            tk = _N.run("btc.ticker", {"symbol": "BTCUSDT"})
            if tk.get("ok"):
                d = tk.get("data") or {}
                F = float(d.get("mid") or d.get("mark") or d.get("last") or 0)
        if F > 0:
            nearest_5k = round(F / 5000.0) * 5000.0
            dist_bps = abs(F - nearest_5k) / F * 10000.0
            out["psych_distance_bps"] = round(dist_bps, 1)
            out["at_psych_barrier"] = dist_bps <= 50.0
    except Exception:
        pass

    # 4. regime check from plan context (planner already resolved it)
    try:
        regime = ((plan.get("context") or {}).get("regime") or "").upper()
        out["regime_is_trend"] = regime.startswith("TREND_")
    except Exception:
        pass

    return out


def _promote_tier_flags(plan: dict) -> dict:
    """Stage-2 of tier rollout — promote candidates to plan flags.

    Mutates plan in-place to add:
      plan["leverage_tier"]  = "high_conf_short_hold" | "default"
      plan["size_tier"]      = "long_term_conf"       | "default"
      plan["tier_promotion"] = {
          "candidates":  {...},   the 4 booleans + raw values
          "promotions":  {...},   what was set and why
          "env":         "demo" | "live" | "paper",
          "staged":      bool,    True = caps half-applied
          "kill_switch": bool,    True = promotion fully disabled
      }

    Returns the tier_promotion dict (also stored on plan).
    Never raises — failures are silent and leave plan untouched.
    """
    import os as _os
    promo_info = {
        "candidates":  {},
        "promotions":  {},
        "env":         _resolve_orders_env(),
        "staged":      _os.environ.get("SYGNIF_TIER_FULL", "0") != "1",
        "kill_switch": False,
        "skipped_reason": None,
    }

    # Master kill switch
    if _os.environ.get("SYGNIF_TIER_PROMOTION", "0") != "1":
        promo_info["kill_switch"] = True
        promo_info["skipped_reason"] = "SYGNIF_TIER_PROMOTION!=1"
        plan["tier_promotion"] = promo_info
        return promo_info

    # Live mode requires explicit additional opt-in
    if promo_info["env"] == "live":
        if (_os.environ.get("SYGNIF_TIER_PROMOTION_LIVE")
            or "").strip() != "clear-for-live":
            promo_info["kill_switch"] = True
            promo_info["skipped_reason"] = (
                "live mode but SYGNIF_TIER_PROMOTION_LIVE != "
                "'clear-for-live'")
            plan["tier_promotion"] = promo_info
            return promo_info

    # Compute candidates
    try:
        cand = _compute_tier_candidates_inline(plan)
        promo_info["candidates"] = cand
    except Exception as e:
        promo_info["skipped_reason"] = (
            f"candidate compute failed: {type(e).__name__}: {e}")
        plan["tier_promotion"] = promo_info
        return promo_info

    # leverage_tier="high_conf_short_hold" — needs ALL THREE:
    #   predict_loop says STRONG_*
    #   recent 4h |move| >= 3%
    #   within 50bps of nearest 5k psych level
    if (cand.get("predict_strong")
        and cand.get("big_recent_move")
        and cand.get("at_psych_barrier")):
        plan["leverage_tier"] = "high_conf_short_hold"
        promo_info["promotions"]["leverage_tier"] = {
            "value": "high_conf_short_hold",
            "reason": (
                f"predict={cand.get('predict_signal')} "
                f"4h_move={cand.get('recent_move_pct'):+.2f}% "
                f"psych_dist={cand.get('psych_distance_bps'):.1f}bps"),
        }

    # size_tier="long_term_conf" — needs:
    #   regime is TREND_*
    #   predict_loop says STRONG_*
    # (Phase 2 will add: positive expectancy from outcome attribution)
    if (cand.get("regime_is_trend") and cand.get("predict_strong")):
        plan["size_tier"] = "long_term_conf"
        promo_info["promotions"]["size_tier"] = {
            "value": "long_term_conf",
            "reason": (
                f"regime=TREND predict={cand.get('predict_signal')}"),
        }

    plan["tier_promotion"] = promo_info
    return promo_info


# 2026-05-10 Phase 2.1: in-process plan cache. The trader loop calls
# plan_trade_journaled twice per cycle (once directly, once inside
# n_agent_trade_execute). We need both calls to see the same plan with
# the SAME correlation_id so decision.snapshot links cleanly to the
# eventual trade.open / trade.close. TTL is short (20s) so successive
# cycles always replan from current market state.
_PLAN_CACHE: dict = {"ts": 0.0, "plan": None}
_PLAN_CACHE_TTL_S = 20.0


def plan_trade_journaled(*, force_fresh: bool = False) -> dict:
    """Public wrapper: calls plan_trade(), promotes tier flags, writes a
    decision.snapshot, journals. Caches the resulting plan briefly so a
    single cycle's repeated calls (loop direct + executor internal) see the
    same correlation_id.

    Use THIS from external callers (sygnif_neurons.py, scripts) so every
    decision is captured. The inner plan_trade() remains unchanged so
    existing tests/audits keep working.

    2026-05-10:
      Phase 1 — tier promotion (when SYGNIF_TIER_PROMOTION=1 and candidates
                agree, sets plan["leverage_tier"] / plan["size_tier"]).
      Phase 2.1 — decision_snapshot writer (sets plan["correlation_id"],
                  emits decision.snapshot swarm row for later joiner).
    """
    import time as _time
    now = _time.time()
    cached = _PLAN_CACHE.get("plan")
    if (cached is not None and not force_fresh
        and (now - _PLAN_CACHE.get("ts", 0)) < _PLAN_CACHE_TTL_S):
        # Same cycle, second caller — return the cached plan with the
        # correlation_id already attached. Decision.snapshot already written.
        return cached

    plan = plan_trade()

    # Promote BEFORE snapshot so snapshot captures the promoted tier flags.
    # _promote_tier_flags swallows all exceptions internally.
    try:
        _promote_tier_flags(plan)
    except Exception as _e:
        import sys as _sys
        _sys.stderr.write(
            f"[trader._promote_tier_flags] {type(_e).__name__}: {_e}\n")

    # Phase 2.1 (2026-05-10): write the decision snapshot. Sets plan
    # ["correlation_id"] so downstream join works. Best-effort — never
    # blocks planning on swarm-write failure.
    try:
        from agent import decision_snapshot as _DS
        cid = _DS.write_snapshot(plan)
        plan["correlation_id"] = cid
    except Exception as _e:
        import sys as _sys
        _sys.stderr.write(
            f"[trader.decision_snapshot] {type(_e).__name__}: {_e}\n")

    try:
        snap = _load_context().get("discovery")
    except Exception:
        snap = None
    _journal_plan_result(plan, snap)

    # Cache for the rest of this cycle's calls
    _PLAN_CACHE["ts"] = now
    _PLAN_CACHE["plan"] = plan
    return plan
