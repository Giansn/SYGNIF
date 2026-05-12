"""sygnif_training_policy.py — adaptive policy for experimental trader.

The trader gets agency over THREE knobs:
  1. risk_per_trade_usd  (size — scales with signal conviction)
  2. max_concurrent      (how many open at once — scales with recent win rate)
  3. min_score           (signal-strength gate — loosens when starved, tightens when over-fired)

Policy = pure function of:
  • current equity (anchors absolute caps)
  • recent outcomes (last N closed trades)
  • recent decision rate (last hour)
  • signal score for the candidate trade

State is persisted to /var/lib/sygnif/training_policy.json so adaptations
survive restarts. Every snapshot embeds the active policy values so the
joiner can correlate "what policy was active" with "did it win".

Hard floors / ceilings (NEVER overridden by adaptive logic):
  MIN_RISK_USD            = $1
  MAX_RISK_USD_PCT        = 10% of equity
  MAX_CONCURRENT          = 5
  MIN_SCORE_FLOOR         = 1
  MAX_SCORE_GATE          = 4
  DAILY_LOSS_PCT_LIMIT    = 30% of equity
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sqlite3
import time
from typing import Any

POLICY_FILE = pathlib.Path("/var/lib/sygnif/training_policy.json")
DB = "/var/lib/sygnif/swarm.db"

# Hard absolute bounds (cannot be exceeded regardless of adaptation)
MIN_RISK_USD          = 1.0
MAX_RISK_PCT_EQUITY   = 0.10        # max 10% of equity per trade
MAX_CONCURRENT_HARD   = 5
MIN_SCORE_HARD        = 1
MAX_SCORE_HARD        = 4
DAILY_LOSS_PCT_LIMIT  = 0.30        # 30% of equity → auto-pause
MIN_LEVERAGE          = 5           # absolute floor — < 5× makes no sense for 0.25% SL bracket
MAX_LEVERAGE          = 30          # absolute ceiling — beyond this liq buffer too thin
DEFAULT_LEVERAGE_MULT = 1.0         # adjusts whole leverage curve (×1 = standard)

# Defaults if no state yet
DEFAULT_BASE_RISK_PCT = 0.005       # 0.5% of equity baseline
DEFAULT_MAX_CONCUR    = 2
DEFAULT_MIN_SCORE     = 2
DEFAULT_LEV_MULT      = 1.0         # leverage_mult adapts ↑ on wins, ↓ on losses
ADAPT_COOLDOWN_S      = 600         # don't re-adapt more than once per 10 min

# Score → leverage mapping (multiplied by leverage_mult before clamp)
# The trader explores 4 leverage tiers based on signal conviction:
#   weak signals get conservative leverage, strong signals get aggressive.
SCORE_TO_LEVERAGE = {
    1: 5,    # one indicator agrees → conservative
    2: 10,   # two agree → moderate
    3: 20,   # three agree → high conviction
    4: 30,   # all four agree → max conviction
}


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def _now_utc_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


def _load_state() -> dict:
    defaults = {
        "version":           1,
        "updated_utc":       _now_utc_iso(),
        "base_risk_pct":     DEFAULT_BASE_RISK_PCT,
        "max_concurrent":    DEFAULT_MAX_CONCUR,
        "min_score":         DEFAULT_MIN_SCORE,
        "leverage_mult":     DEFAULT_LEV_MULT,
        "history":           [],
        "last_adapt_ts":     0,
    }
    if not POLICY_FILE.exists():
        return defaults
    try:
        with POLICY_FILE.open() as f:
            data = json.load(f)
        # Backfill any missing keys from defaults (forward-compat)
        for k, v in defaults.items():
            data.setdefault(k, v)
        return data
    except (json.JSONDecodeError, OSError):
        return defaults


def _save_state(state: dict) -> None:
    POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = POLICY_FILE.with_suffix(POLICY_FILE.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, POLICY_FILE)


# ---------------------------------------------------------------------------
# Recent-data lookups (read-only against swarm.db)
# ---------------------------------------------------------------------------
def _recent_outcomes(limit: int = 10, only_training: bool = True) -> list[dict]:
    """Last N outcome.attributed rows from training_scanner. Returns
    [{pnl, win, ts, hold_s}, ...] newest-first."""
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        rows = c.execute(
            "SELECT meta FROM swarm_entries WHERE topic='outcome.attributed' "
            "ORDER BY created DESC LIMIT 200").fetchall()
        c.close()
    except Exception:
        return []
    out = []
    for (meta_s,) in rows:
        try:
            m = json.loads(meta_s)
        except (json.JSONDecodeError, TypeError):
            continue
        olid = m.get("order_link_id") or ""
        if only_training and not olid.startswith("sygTRN"):
            continue
        try:
            pnl = float(m.get("closed_pnl") or 0)
        except (ValueError, TypeError):
            continue
        out.append({
            "pnl":     pnl,
            "win":     pnl > 0,
            "hold_s":  m.get("hold_seconds"),
            "olid":    olid,
        })
        if len(out) >= limit:
            break
    return out


def _trades_in_last(seconds: int) -> int:
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        cutoff = int(time.time()) - seconds
        n = c.execute(
            "SELECT COUNT(*) FROM swarm_entries WHERE topic='decision.executed' "
            "AND agent_id='sygnif-training-scanner' AND created>? "
            "AND content LIKE '%executed=True%'",
            (cutoff,)).fetchone()[0]
        c.close()
        return n
    except Exception:
        return 0


def _realized_loss_today_training() -> float:
    """Sum of negative pnl from training-scanner trades today (UTC)."""
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        today_start = int(time.time()) - (int(time.time()) % 86400)
        rows = c.execute(
            "SELECT meta FROM swarm_entries WHERE topic='outcome.attributed' "
            "AND created>?", (today_start,)).fetchall()
        c.close()
    except Exception:
        return 0.0
    loss = 0.0
    for (meta_s,) in rows:
        try:
            m = json.loads(meta_s)
        except (json.JSONDecodeError, TypeError):
            continue
        olid = m.get("order_link_id") or ""
        if not olid.startswith("sygTRN"):
            continue
        try:
            pnl = float(m.get("closed_pnl") or 0)
        except (ValueError, TypeError):
            continue
        if pnl < 0:
            loss += pnl
    return loss


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_policy() -> dict:
    """Return the current active policy state. Read-only."""
    return _load_state()


def is_paused_for_loss(equity_usd: float) -> tuple[bool, float, float]:
    """Daily-loss guard. Returns (paused, loss_today_usd, limit_usd)."""
    if equity_usd <= 0:
        return (False, 0, 0)
    loss = _realized_loss_today_training()
    limit = -DAILY_LOSS_PCT_LIMIT * equity_usd
    return (loss <= limit, loss, limit)


def adapt(equity_usd: float, *, force: bool = False) -> dict:
    """Examine recent outcomes + decide if policy should change. Persists
    new state if changed. Returns the (new) state."""
    state = _load_state()
    now_ts = int(time.time())
    if not force and (now_ts - state.get("last_adapt_ts", 0)) < ADAPT_COOLDOWN_S:
        return state

    recent = _recent_outcomes(10)
    n = len(recent)
    if n == 0:
        # No data yet — keep defaults
        return state

    wins = sum(1 for r in recent if r["win"])
    win_rate = wins / n
    sum_pnl = sum(r["pnl"] for r in recent)

    old_risk_pct  = state["base_risk_pct"]
    old_max_conc  = state["max_concurrent"]
    old_min_score = state["min_score"]
    old_lev_mult  = state.get("leverage_mult", DEFAULT_LEV_MULT)

    # === Adapt base_risk_pct ===
    # win_rate ≥ 0.6 + winning P&L: scale up by 25% (ceil 2%)
    # win_rate ≤ 0.35: scale down by 50% (floor 0.1%)
    new_risk_pct = old_risk_pct
    if n >= 5:
        if win_rate >= 0.6 and sum_pnl > 0:
            new_risk_pct = min(old_risk_pct * 1.25, 0.02)
        elif win_rate <= 0.35:
            new_risk_pct = max(old_risk_pct * 0.5, 0.001)

    # === Adapt max_concurrent ===
    new_max_conc = old_max_conc
    if n >= 5:
        if win_rate >= 0.6:
            new_max_conc = min(old_max_conc + 1, MAX_CONCURRENT_HARD)
        elif win_rate <= 0.35:
            new_max_conc = max(old_max_conc - 1, 1)

    # === Adapt min_score (based on trade frequency, not win rate) ===
    trades_last_hour = _trades_in_last(3600)
    new_min_score = old_min_score
    if trades_last_hour == 0:
        # Starved — loosen threshold to fire more often
        new_min_score = max(old_min_score - 1, MIN_SCORE_HARD)
    elif trades_last_hour >= 6:
        # Over-firing — tighten so signals are higher quality
        new_min_score = min(old_min_score + 1, MAX_SCORE_HARD - 1)

    # === Adapt leverage_mult — scales the SCORE_TO_LEVERAGE curve ===
    # win_rate ≥ 0.6 + winning P&L: shift curve up by 25% (cap 1.5×)
    # win_rate ≤ 0.35: shift curve down by 30% (floor 0.5×)
    # The hard MIN/MAX_LEVERAGE clamps still apply at the use site.
    new_lev_mult = old_lev_mult
    if n >= 5:
        if win_rate >= 0.6 and sum_pnl > 0:
            new_lev_mult = min(old_lev_mult * 1.25, 1.5)
        elif win_rate <= 0.35:
            new_lev_mult = max(old_lev_mult * 0.7, 0.5)

    changed = (new_risk_pct != old_risk_pct
               or new_max_conc != old_max_conc
               or new_min_score != old_min_score
               or abs(new_lev_mult - old_lev_mult) > 1e-6)
    if not changed:
        return state

    history = list(state.get("history") or [])
    history.append({
        "ts_utc":          _now_utc_iso(),
        "trigger":         "adapt",
        "n_recent_obs":    n,
        "win_rate":        round(win_rate, 3),
        "sum_pnl_usd":     round(sum_pnl, 2),
        "trades_last_hour": trades_last_hour,
        "from": {
            "base_risk_pct":  old_risk_pct,
            "max_concurrent": old_max_conc,
            "min_score":      old_min_score,
            "leverage_mult":  round(old_lev_mult, 3),
        },
        "to": {
            "base_risk_pct":  new_risk_pct,
            "max_concurrent": new_max_conc,
            "min_score":      new_min_score,
            "leverage_mult":  round(new_lev_mult, 3),
        },
    })
    new_state = {
        **state,
        "version":        (state.get("version") or 0) + 1,
        "updated_utc":    _now_utc_iso(),
        "base_risk_pct":  new_risk_pct,
        "max_concurrent": new_max_conc,
        "min_score":      new_min_score,
        "leverage_mult":  new_lev_mult,
        "history":        history[-50:],
        "last_adapt_ts":  now_ts,
    }
    _save_state(new_state)
    return new_state


def risk_usd_for_trade(equity_usd: float, signal_score: int,
                        state: dict | None = None) -> float:
    """Compute the actual $ risk for a candidate trade given:
      • current equity (anchors absolute cap)
      • signal score (1..4 — scales conviction)
      • policy state (base_risk_pct gives us the baseline)

    The score-multiplier amplifies high-conviction signals so the trader
    explores its action space across sizes:
      score=1 → 0.5× base
      score=2 → 1.0× base
      score=3 → 2.5× base
      score=4 → 5.0× base

    Hard cap: MAX_RISK_PCT_EQUITY × equity (typically 10%).
    Hard floor: MIN_RISK_USD ($1)."""
    if state is None:
        state = _load_state()
    base = state.get("base_risk_pct") or DEFAULT_BASE_RISK_PCT
    score_mult = {1: 0.5, 2: 1.0, 3: 2.5, 4: 5.0}.get(signal_score, 1.0)
    effective_risk_pct = base * score_mult
    hard_cap_pct = MAX_RISK_PCT_EQUITY
    if effective_risk_pct > hard_cap_pct:
        effective_risk_pct = hard_cap_pct
    risk = effective_risk_pct * equity_usd
    return max(risk, MIN_RISK_USD)


def leverage_for_trade(signal_score: int, state: dict | None = None) -> int:
    """Return the leverage to request for a trade with this signal score.

    Mapping (before leverage_mult):
      score=1 (one indicator)   → 5×    (conservative)
      score=2 (two agree)       → 10×   (moderate)
      score=3 (three agree)     → 20×   (high conviction)
      score=4 (all four agree)  → 30×   (max conviction)

    The active policy.leverage_mult shifts the whole curve. Result is
    clamped to MIN_LEVERAGE..MAX_LEVERAGE (5..30). The trader explores
    this 4-level action space across signal types so the model can later
    learn 'which leverage works for which setup'."""
    if state is None:
        state = _load_state()
    base = SCORE_TO_LEVERAGE.get(int(signal_score), 5)
    mult = float(state.get("leverage_mult") or DEFAULT_LEV_MULT)
    raw = base * mult
    clamped = max(MIN_LEVERAGE, min(int(round(raw)), MAX_LEVERAGE))
    return clamped


def max_concurrent(state: dict | None = None) -> int:
    if state is None:
        state = _load_state()
    n = int(state.get("max_concurrent") or DEFAULT_MAX_CONCUR)
    return max(1, min(n, MAX_CONCURRENT_HARD))


def min_score(state: dict | None = None) -> int:
    if state is None:
        state = _load_state()
    n = int(state.get("min_score") or DEFAULT_MIN_SCORE)
    return max(MIN_SCORE_HARD, min(n, MAX_SCORE_HARD))


def snapshot_for_decision(equity_usd: float, signal_score: int) -> dict:
    """Return the policy view the snapshot should embed. Caller passes
    this into decision_snapshot so the joiner can later correlate
    'what policy was active' with 'did it win'."""
    state = _load_state()
    return {
        "policy_version":     state.get("version"),
        "policy_updated_utc": state.get("updated_utc"),
        "base_risk_pct":      state.get("base_risk_pct"),
        "max_concurrent":     state.get("max_concurrent"),
        "min_score":          state.get("min_score"),
        "leverage_mult":      state.get("leverage_mult"),
        "risk_usd_for_trade": risk_usd_for_trade(equity_usd, signal_score, state),
        "leverage_for_trade": leverage_for_trade(signal_score, state),
        "score_mult_used":    {1: 0.5, 2: 1.0, 3: 2.5, 4: 5.0}.get(signal_score, 1.0),
        "hard_max_risk_pct":  MAX_RISK_PCT_EQUITY,
        "hard_max_concurrent": MAX_CONCURRENT_HARD,
        "hard_min_leverage":  MIN_LEVERAGE,
        "hard_max_leverage":  MAX_LEVERAGE,
        "hard_daily_loss_pct": DAILY_LOSS_PCT_LIMIT,
    }
