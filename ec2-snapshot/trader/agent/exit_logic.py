"""SYGNIF R-ladder exit engine — staged TP/SL with MM-exploitation layer.

Replaces the single-shot 50% / HWM-trail logic with a four-stage lifecycle
per position, asymmetric risk-reward, and explicit awareness of market-maker
hedging patterns.

Stages (from FORMULAS spec, sections E–F):
  S0  Risk-on        SL = -1R + jit, TP = +R_WIN + jit, no trail
  S1  Breakeven      SL ratcheted to entry + jit       (price ≥ +1R)
  S2  Profit floor   SL ratcheted to entry + 1R + jit  (price ≥ +2R)
                     + optional partial-close (50% qty) at S2 entry
  S3  Runner trail   SL = peak_R × (keep_pct + jit)    (price ≥ +R_WIN)
                     ATR-clamped, regime-gated (trend only)

MM-exploitation layer (sections Q1–Q5):
  Q1  Funding-window override     — defer non-catastrophic closes ±5min around
                                    00/08/16 UTC; tighten S2/S3 trail at T-30min
  Q2  Sweep detection             — extend triple-tap from 3→5 cycles when a
                                    rapid mark spike is detected
  Q3  Max-pain magnetism          — for short_premium DTE≤24h within 0.5% of
                                    OI-weighted pin, widen SL by 0.5R
  Q4  Stop-cluster avoidance      — push S0 SL deeper if jittered level lands
                                    within $50 of a retail cluster zone
  Q5  Funding-rate-flip exit      — perp only: force-promote to S2 when funding
                                    sign flips and we're past +1R

State persisted at ~/.sygnif/position-hwm.json (see FORMULAS section M).

decide_exit() returns:
  {verdict ∈ {HOLD, CLOSE, PARTIAL_CLOSE, TRAIL_UPDATE},
   reason: human-readable, action: closer-hint, meta: details, state_diff: persistable}

The function is read-only modulo the state file. All exchange interactions
(close orders, conditional SL placement) are the caller's responsibility.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from agent import bybit_positions as BYBIT_POS
from agent import structure_memberships as SM

HWM_PATH = Path.home() / ".sygnif" / "position-hwm.json"

# ---- Tunables (FORMULAS section E + revised defaults) ----------------------

# Per-structure ladder configuration. Replaces the prior global S1/S2 R
# constants which made short_premium thresholds (S1=+1R, S2=+2R, S3=+3R)
# UNREACHABLE — short option max-profit is the premium kept (mark→0), so
# captured_R was capped at +0.667 with the old ru = entry × 1.5 formula.
# Result: shorts never promoted past S0 and the tastytrade 50%-of-credit
# rule from EXIT_RULES_V2 was silently ignored.
#
# Doctrine-aligned (gap fix 2026-05-02):
#   long_premium / perp:  R-multiple semantics (1R = sl_distance)
#   short_premium:        FRACTION-OF-CREDIT semantics (1R = entry premium)
#                          → captured_R range [-2, +1.0]
#                          → S2 at +0.5 = tastytrade 50%-credit-take
#                          → catastrophic at -2.0 = tastytrade 200%-loss-stop
STAGE_THRESHOLDS_BY_STRUCTURE = {
    "long_premium": {
        "S1_BE":          1.0,    # ratchet SL to entry at +1R captured
        "S2_FLOOR":       2.0,    # +1R floor lock at +2R captured
        "S3_TRAIL":       2.5,    # ATR-trail starts (= R_WIN target)
        "catastrophic_R": -1.0,
        "tp_R":           2.5,
    },
    "short_premium": {
        # captured_R = (entry - mark) / entry = fraction of credit captured.
        # +1.0 = full premium kept (mark=0, expired worthless).
        # -2.0 = mark = 3×entry (tastytrade 200% premium loss stop).
        "S1_BE":          0.25,   # lock BE after 25% credit decay
        "S2_FLOOR":       0.50,   # tastytrade close at 50% max profit
        "S3_TRAIL":       0.75,   # if held past 50%, trail tightly
        "catastrophic_R": -2.0,   # mark > 3×entry → blow-up risk
        "tp_R":           0.50,   # primary TP = 50% credit (doctrine)
    },
    "perp": {
        "S1_BE":          1.0,
        "S2_FLOOR":       2.0,
        "S3_TRAIL":       3.0,
        "catastrophic_R": -1.0,
        "tp_R":           3.0,
    },
    "unknown": {
        "S1_BE":          1.0,
        "S2_FLOOR":       2.0,
        "S3_TRAIL":       2.5,
        "catastrophic_R": -1.0,
        "tp_R":           2.5,
    },
}

# Back-compat shims so callers expecting the old globals don't break. New
# code should read STAGE_THRESHOLDS_BY_STRUCTURE directly.
S1_BE_BASE_R       = STAGE_THRESHOLDS_BY_STRUCTURE["long_premium"]["S1_BE"]
S2_FLOOR_BASE_R    = STAGE_THRESHOLDS_BY_STRUCTURE["long_premium"]["S2_FLOOR"]
R_WIN_BY_STRUCTURE = {k: v["tp_R"] for k, v in STAGE_THRESHOLDS_BY_STRUCTURE.items()}

KEEP_PCT           = 0.60     # S3 floor = peak_R × (KEEP_PCT + jit_keep)
ATR_MULT_BASE      = 1.5      # S3 trail width = atr_h1 × (ATR_MULT_BASE + jit_atr)
HARD_SL_MULT_SHORT = 2.5      # legacy — superseded by structure-specific
                              # catastrophic_R above. Retained for any
                              # external callers (mark_watcher); aligned
                              # to give the same 3×entry catastrophic mark.

JIT_SL_R_RANGE     = (-0.20, +0.20)
JIT_TP_R_RANGE     = (-0.15, +0.15)
JIT_ATR_RANGE      = (-0.20, +0.20)
JIT_KEEP_RANGE     = (-0.05, +0.05)

PARTIAL_CLOSE_PCT  = 0.50     # at S2 entry, sell 50% (per user-approved spec)
CONFIRM_N_BASE     = 3        # cycles of sustained breach for non-catastrophic
CONFIRM_N_SWEEP    = 5        # extended when sweep_suspected
SWEEP_STD_MULT     = 2.5      # |Δmark| > MULT × recent_std flags sweep

# Fix 2 (2026-05-02): grace period after first-arm before catastrophic SL is
# allowed to fire. Protects against the bid-ask spread crossing artifact: a
# fresh SHORT sold at bid will mark at the mid immediately, showing
# captured_R < -1R purely from spread, not from real adverse move. Observed
# 2026-05-01 on iron_condor SHORT 79000-C — sold at $15 bid, mark sat at
# $44 mid, system fired catastrophic at -1.46R within 90 seconds.
#
# P3b (2026-05-02): grace tuned per structure type. Long-premium positions
# can lose 30%+ in a single 60-second gamma-squeeze candle — waiting 10
# minutes there is dangerous. Short-premium needs the full 10 minutes
# because spread artifacts are most pronounced on illiquid short-DTE
# options. Perp positions execute at the bid/ask immediately, so spread
# noise is minimal — 60s is enough to absorb any opening-tick weirdness.
CATASTROPHIC_GRACE_SECONDS = 600   # legacy default; used as short_premium grace
CATASTROPHIC_GRACE_BY_STRUCTURE = {
    "short_premium": 600,   # 10 min — spread artifact protection
    "long_premium":  180,   # 3 min — gamma-squeeze risk dominant
    "perp":           60,   # 1 min — minimal spread noise on liquid perps
    "unknown":       300,   # 5 min — middle ground
}


def catastrophic_grace_for(structure: str | None) -> int:
    """Return the catastrophic-grace seconds for a position's structure.
    Falls back to the global default when structure is unrecognised.
    P3b (2026-05-02)."""
    bucket = "unknown"
    s = (structure or "").lower()
    short_keys = ("iron_condor", "credit_spread", "short_strangle",
                  "short_straddle", "short_premium", "broken_wing_condor")
    long_keys = ("long_call", "long_put", "long_strangle", "long_straddle",
                 "bull_call_spread", "bear_put_spread", "calendar_",
                 "broken_wing_butterfly", "long_premium")
    if any(k in s for k in short_keys):
        bucket = "short_premium"
    elif any(k in s for k in long_keys):
        bucket = "long_premium"
    elif "perp" in s:
        bucket = "perp"
    return CATASTROPHIC_GRACE_BY_STRUCTURE.get(bucket, CATASTROPHIC_GRACE_SECONDS)

# DTE thresholds (FORMULAS section K)
SHORT_DTE_HOURS_MAX_LADDER = 72        # ladder suppressed past S0 if dte_h ≤ 72
TIME_STOP_H_BY_BUCKET = {
    "short_premium": 4,      # close ≥4h before expiry
    "long_premium":  24,     # close ≥24h before expiry
    "perp":          0,
    "unknown":       4,
}

# P1.6b (2026-05-04): hard gamma-pin-zone gate for long-vol structures.
# In the last 12h before Bybit option expiry (08:00 UTC), MMs hedge their
# short-gamma books by pinning price near the OI cluster — long-vol bets
# bleed without realising. Force close any long_premium position that
# enters this window unless captured_R is already deeply negative (in
# which case the structure cap loss has already been taken; no point
# adding spread cost on top). Postmortem 4MAY26: 75500-P + 82000-C went
# to delivery despite the 24h time-stop (state had been pruned by daemon
# bug — fixed in P1.6a). This is the second-line defence.
PRE_EXPIRY_PIN_HOURS_LONG_VOL = 12.0
PRE_EXPIRY_MIN_R_TO_CLOSE = -1.0    # below this, taking the spread cost is worse

TRAIL_REGIMES = {"TREND_UP", "TREND_DOWN"}
CLUSTER_PROXIMITY_USD = 50.0   # Q4: push deeper if SL within this of cluster
CLUSTER_PUSH_USD      = 100.0  # Q4: how far further to push

# Q6: session phases (UTC) — BTMM/ICT-inspired patterns transposed to crypto.
# Effects:
#   confirm_extra    add cycles to triple-tap (anti-sweep, high-vol windows)
#   tighten_R        force sl_R ≥ captured_R − tighten_R (lock profit)
#   defer_noncat     skip non-catastrophic closes (let MM-driven move complete)
#   low_vol          DO NOT tighten (theta works in our favor)
#   block_new_shorts read by plan_trade — Judas swing zone, fakes likely
#   force_partial    if S2/S3 with profit, emit PARTIAL_CLOSE (book gains)
SESSION_PHASES = [
    # (name,            h_s, m_s, h_e, m_e, effects)
    ("asian_open",        0,   0,   1,  30, {"confirm_extra": 1}),
    ("tether_window",     0,  30,   2,   0, {"defer_noncat": True}),    # USDT mint cycle
    ("china_morning",     1,   0,   3,  30, {"confirm_extra": 1}),       # CN retail surge
    ("china_lunch",       3,  30,   5,   0, {"low_vol": True}),           # CN lunch — pin/decay friendly
    ("china_pm",          5,   0,   7,   0, {"confirm_extra": 1}),
    ("london_open",       7,  30,   8,  30, {"confirm_extra": 2,
                                              "block_new_shorts": True}),  # Judas swing
    ("london_active",     8,  30,  11,  30, {"confirm_extra": 1}),
    ("london_lunch",     11,  30,  12,  30, {"low_vol": True}),
    ("us_premarket",     12,   0,  13,  30, {"confirm_extra": 1}),
    ("us_open",          13,  30,  14,  30, {"confirm_extra": 2}),         # cash open impulse
    ("us_active",        14,  30,  15,  30, {"confirm_extra": 1}),
    ("london_close",     15,  30,  16,  30, {"tighten_R": 0.30}),          # EU desk inventory clear
    ("us_lunch",         16,   0,  17,  30, {"low_vol": True}),
    ("us_active2",       17,  30,  19,   0, {"confirm_extra": 1}),
    ("us_power_hour",    19,   0,  20,  30, {"confirm_extra": 2}),         # power hour
    ("us_close",         20,  30,  21,  30, {"tighten_R": 0.30,
                                              "force_partial": True}),       # NY desks square
]


# ---------------------------------------------------------------------------
# state load/save
# ---------------------------------------------------------------------------
def _load_state() -> dict:
    if not HWM_PATH.exists():
        return {}
    try:
        return json.loads(HWM_PATH.read_text())
    except Exception:
        return {}


def _save_state(d: dict) -> None:
    """Atomic write — bybit_daemon's executor pool calls _save_state from
    multiple worker threads, AND the 5-min sygnif-option-trail-arm.timer
    runs in a separate process. PID alone isn't unique across threads,
    so we add a uuid suffix per call. os.replace() is atomic on POSIX
    so the final file is either the old or new version, never partial.
    """
    import os as _os
    import uuid as _uuid
    HWM_PATH.parent.mkdir(parents=True, exist_ok=True)
    suffix = f".tmp.{_os.getpid()}.{_uuid.uuid4().hex[:8]}"
    tmp = HWM_PATH.with_suffix(HWM_PATH.suffix + suffix)
    try:
        tmp.write_text(json.dumps(d, indent=2, default=str))
        _os.replace(tmp, HWM_PATH)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------
def _stable_pid(symbol: str, side: str) -> str:
    return hashlib.sha256(f"{symbol}|{side}".encode()).hexdigest()[:8]


def _det_jitter(pid: str, lo: float, hi: float, salt: str) -> float:
    """Deterministic jitter from sha256(pid|salt) in [lo, hi]."""
    h = hashlib.sha256(f"{pid}|{salt}".encode()).digest()
    n = int.from_bytes(h[:8], "big")
    frac = (n / 2**64)
    return lo + frac * (hi - lo)


def _draw_jitters(pid: str) -> dict:
    return {
        "sl_R": _det_jitter(pid, *JIT_SL_R_RANGE, salt="sl"),
        "tp_R": _det_jitter(pid, *JIT_TP_R_RANGE, salt="tp"),
        "atr":  _det_jitter(pid, *JIT_ATR_RANGE,  salt="atr"),
        "keep": _det_jitter(pid, *JIT_KEEP_RANGE, salt="keep"),
    }


def _classify_position(pos: dict) -> str:
    """Map a position dict to a structure bucket.

    For PAPER multi-leg positions (legs > 1), the previous logic used the
    FIRST leg's side, which mis-classified iron_condor (first leg LONG put
    → "long_premium"). Now we look at the label first — paper positions
    carry the structure name in label like "agent_iron_condor_4184".

    Priority:
        1. explicit `structure` field (live Bybit positions get this)
        2. `instrument` field for single-leg
        3. paper LABEL substring match (multi-leg structures)
        4. paper marks first-leg side (single-leg paper fallback)
    """
    explicit = (pos.get("structure") or "").lower()
    if explicit in ("short_premium", "long_premium", "perp"):
        return explicit
    instrument = (pos.get("instrument") or "").lower()
    side = (pos.get("side") or "").lower()
    if instrument == "perp" or instrument == "linear":
        return "perp"
    if instrument == "option":
        return "short_premium" if side == "sell" else "long_premium"

    # ---- Paper-shape: classify multi-leg by LABEL substring ----------
    # Paper labels: "agent_iron_condor_4184", "agent_long_strangle_1165", etc.
    # Net-credit structures (we receive premium) → short_premium bucket.
    # Net-debit structures (we pay premium) → long_premium bucket.
    label = (pos.get("label") or "").lower()
    if label:
        # Net-credit / theta-positive structures
        if any(k in label for k in ("iron_condor", "iron_butterfly",
                                       "short_strangle", "short_straddle",
                                       "_credit_spread", "calendar_")):
            return "short_premium"
        # Net-debit / theta-negative structures
        if any(k in label for k in ("long_strangle", "long_straddle",
                                       "long_call", "long_put",
                                       "_debit_spread", "broken_wing")):
            return "long_premium"

    # ---- Single-leg paper fallback (last resort) ---------------------
    for m in pos.get("marks", []) or []:
        if "now_iv" in m:
            sd = (m.get("side") or side or "").lower()
            return "short_premium" if sd == "sell" else "long_premium"
    return "perp" if instrument else "unknown"


def _risk_unit(pos: dict, structure: str) -> float:
    """1R per structure. NB: short_premium uses ENTRY (not entry × 1.5) so
    captured_R = (entry - mark) / entry = fraction of credit captured.
    This makes the doctrine-aligned thresholds (+0.5 = tastytrade close,
    -2.0 = tastytrade stop) reachable.
    """
    entry = float(pos.get("entry") or 0)
    side = (pos.get("side") or "").lower()
    if structure == "long_premium":
        return entry  # max-loss = premium paid
    if structure == "short_premium":
        # 1R = entry premium (was entry × 1.5; old formula made
        # max-captured = 0.667 and S1/S2/S3 unreachable).
        return entry
    if structure == "perp":
        sl_initial = float(pos.get("stop_loss_price") or 0)
        if entry > 0 and sl_initial > 0:
            if side == "buy":
                return max(0.0, entry - sl_initial)
            return max(0.0, sl_initial - entry)
        # No SL set yet: fall back to 2% of entry as 1R proxy
        return entry * 0.02
    return 0.0


def _signed_pnl(pos: dict) -> float:
    """Per-contract P&L per FORMULAS section C."""
    side = (pos.get("side") or "").lower()
    entry = float(pos.get("entry") or 0)
    mark = float(pos.get("mark") or 0)
    if side == "buy":
        return mark - entry
    return entry - mark


def _captured_R(pos: dict, structure: str) -> float:
    ru = _risk_unit(pos, structure)
    if ru <= 0:
        return 0.0
    return _signed_pnl(pos) / ru


def _signed_for_side(pos: dict) -> int:
    return +1 if (pos.get("side") or "").lower() == "buy" else -1


def _hours_to_expiry(pos: dict, now: datetime) -> float | None:
    dte_h = pos.get("dte_h")
    if dte_h is not None:
        return float(dte_h)
    eiso = pos.get("expiry_iso") or pos.get("expiry")
    if not eiso:
        return None
    try:
        e = datetime.strptime(str(eiso) + "T08:00:00+0000",
                              "%Y-%m-%dT%H:%M:%S%z")
        return (e - now).total_seconds() / 3600.0
    except Exception:
        return None


# ---------------------------------------------------------------------------
# stage thresholds (with jitter)
# ---------------------------------------------------------------------------
def _stage_thresholds(pid: str, structure: str, jit: dict) -> dict:
    """Structure-aware thresholds (post-2026-05-02 fix). Short_premium
    gets fraction-of-credit semantics; everything else gets R-multiples.
    Jitter scale also adapts: short ladder thresholds are 4× tighter
    (0.25 vs 1.0), so we shrink jitter on short to keep relative noise."""
    cfg = STAGE_THRESHOLDS_BY_STRUCTURE.get(structure,
                                              STAGE_THRESHOLDS_BY_STRUCTURE["unknown"])
    # Shrink jitter on short_premium so a ±0.20R wobble doesn't swamp the
    # tight +0.25 / +0.50 / +0.75 thresholds.
    jit_scale = 0.25 if structure == "short_premium" else 1.0
    sl_jit = jit["sl_R"] * jit_scale
    tp_jit = jit["tp_R"] * jit_scale
    return {
        "S1_BE":          cfg["S1_BE"]          + sl_jit,
        "S2_FLOOR":       cfg["S2_FLOOR"]       + sl_jit,
        "S3_TRAIL":       cfg["S3_TRAIL"]       + tp_jit,
        "tp_R_full":      cfg["tp_R"]           + tp_jit,
        "tp_R_partial":   cfg["S2_FLOOR"]       + sl_jit,    # partial fires at S2 entry
        "catastrophic_R": cfg["catastrophic_R"] + sl_jit,
    }


def _stage_for_R(captured_R: float, t: dict) -> str:
    if captured_R < t["S1_BE"]:
        return "S0"
    if captured_R < t["S2_FLOOR"]:
        return "S1"
    if captured_R < t["S3_TRAIL"]:
        return "S2"
    return "S3"


def _stage_rank(s: str) -> int:
    return {"S0": 0, "S1": 1, "S2": 2, "S3": 3}.get(s, 0)


def _sl_R_for_stage(stage: str, t: dict, peak_R: float, jit: dict,
                    atr_R: float | None) -> float:
    """SL threshold in R-units for a given stage (FORMULAS section F)."""
    if stage == "S0":
        return t["catastrophic_R"]
    if stage == "S1":
        return 0.0 + jit["sl_R"]                                  # breakeven ± jit
    if stage == "S2":
        return 1.0 + jit["sl_R"]                                  # +1R floor ± jit
    # S3
    keep = max(0.30, min(0.85, KEEP_PCT + jit["keep"]))
    sl_R = peak_R * keep
    sl_R = max(sl_R, 1.0 + jit["sl_R"])                           # never below S2 floor
    if atr_R is not None and atr_R > 0:
        # Trail at most atr_R wide: tighter of (peak_R - atr_R) vs keep formula
        sl_R = max(sl_R, peak_R - atr_R)
    return sl_R


def _sl_R_to_price(sl_R: float, pos: dict, structure: str) -> float:
    entry = float(pos.get("entry") or 0)
    side = _signed_for_side(pos)
    ru = _risk_unit(pos, structure)
    return entry + side * sl_R * ru


# ---------------------------------------------------------------------------
# MM-exploitation layer (Q1–Q5)
# ---------------------------------------------------------------------------
def _funding_window(now: datetime) -> str:
    """Returns 'pre', 'in', 'post', or 'normal'."""
    h, m = now.hour, now.minute
    in_funding = (h % 8 == 0) and (m < 5)
    if in_funding:
        return "in"
    pre = (h % 8 == 7) and (m >= 30)
    if pre:
        return "pre"
    post = (h % 8 == 0) and (5 <= m < 30)
    if post:
        return "post"
    return "normal"


def _sweep_suspected(state_pos: dict, mark_now: float) -> bool:
    """Q2: detect rapid mark spike vs recent history."""
    history = state_pos.get("mark_history", []) or []
    history = list(history)[-5:]
    if len(history) < 3:
        return False
    prev = history[-1]
    delta = abs(mark_now - prev)
    try:
        recent_std = statistics.pstdev(history[:-1]) if len(history) > 2 else 0.0
    except statistics.StatisticsError:
        recent_std = 0.0
    if recent_std <= 0:
        return False
    return delta > SWEEP_STD_MULT * recent_std


def _max_pain_widen(structure: str, dte_h: float | None,
                     pos: dict, snapshot: dict) -> float:
    """Q3: returns SL widen amount in R-units (≤ 0). 0 if not applicable."""
    if structure != "short_premium" or dte_h is None or dte_h > 24:
        return 0.0
    options = (snapshot.get("options") or {}) if snapshot else {}
    max_pain = ((options.get("max_pain") or {}).get("strike")
                or options.get("max_pain_strike"))
    spot = float(snapshot.get("btc_perp_last") or 0) if snapshot else 0
    if not max_pain or not spot:
        return 0.0
    try:
        distance_pct = abs(spot - float(max_pain)) / spot
    except Exception:
        return 0.0
    if distance_pct <= 0.005:
        return -0.50  # widen SL by 0.5R (more room to let pin work)
    return 0.0


def _cluster_zones(snapshot: dict) -> list[float]:
    """Q4: standard retail stop clusters."""
    zones: list[float] = []
    # Round $500 increments, 70k–100k
    zones += [k * 500 for k in range(140, 200)]
    # Psychological $1000 increments
    zones += [k * 1000 for k in range(70, 101)]
    swing = BYBIT_POS.get_btc_24h_swing()
    if swing:
        zones += [float(swing.get("high", 0)), float(swing.get("low", 0))]
    return [z for z in zones if z > 0]


def _session_effects(now: datetime) -> dict:
    """Q6: return combined effects of ALL active session phases (overlap-aware).

    Multiple phases can overlap (e.g. us_premarket + us_open at 13:00-13:30, or
    london_close + us_lunch at 16:00-16:30). Effects compose as:
      confirm_extra : MAX (most cautious wins)
      tighten_R     : MAX (tightest wins)
      booleans      : OR
      active        : list of all matching names
    """
    cur = now.hour * 60 + now.minute
    active: list[str] = []
    confirm_extra = 0
    tighten_R = 0.0
    defer_noncat = False
    block_new_shorts = False
    low_vol = False
    force_partial = False
    for name, h1, m1, h2, m2, effects in SESSION_PHASES:
        s = h1 * 60 + m1
        e = h2 * 60 + m2
        if s <= cur <= e:
            active.append(name)
            confirm_extra = max(confirm_extra, int(effects.get("confirm_extra", 0)))
            tighten_R = max(tighten_R, float(effects.get("tighten_R", 0.0)))
            defer_noncat = defer_noncat or bool(effects.get("defer_noncat", False))
            block_new_shorts = block_new_shorts or bool(effects.get("block_new_shorts", False))
            low_vol = low_vol or bool(effects.get("low_vol", False))
            force_partial = force_partial or bool(effects.get("force_partial", False))
    # low_vol cancels tighten + force_partial: if multiple phases overlap and
    # any of them is low-vol, prefer holding (theta/pin works for us)
    if low_vol:
        tighten_R = 0.0
        force_partial = False
    return {
        "active":           active or ["off_peak"],
        "confirm_extra":    confirm_extra,
        "tighten_R":        tighten_R,
        "defer_noncat":     defer_noncat,
        "block_new_shorts": block_new_shorts,
        "low_vol":          low_vol,
        "force_partial":    force_partial,
    }


def _cluster_avoid(naive_sl_price: float, side_signed: int,
                    snapshot: dict) -> tuple[float, str | None]:
    """Q4: if SL too close to a cluster, push deeper. Returns (new_price, note)."""
    if naive_sl_price <= 0:
        return naive_sl_price, None
    zones = _cluster_zones(snapshot)
    if not zones:
        return naive_sl_price, None
    nearest = min(zones, key=lambda c: abs(c - naive_sl_price))
    if abs(nearest - naive_sl_price) < CLUSTER_PROXIMITY_USD:
        new_price = naive_sl_price - side_signed * CLUSTER_PUSH_USD
        return new_price, f"cluster@{nearest:.0f}±{CLUSTER_PROXIMITY_USD:.0f}→pushed_{CLUSTER_PUSH_USD:.0f}"
    return naive_sl_price, None


# ---------------------------------------------------------------------------
# the decision function
# ---------------------------------------------------------------------------
def decide_exit(pos: dict, mark_perp: float | None,
                snapshot: dict | None, now_utc: datetime,
                regime_now: str | None = None,
                regime_at_open: str | None = None) -> dict:
    """Per-position verdict. See module docstring for overview."""
    snapshot = snapshot or {}
    structure = _classify_position(pos)
    pid = pos.get("id") or _stable_pid(str(pos.get("symbol", "")),
                                        str(pos.get("side", "")))

    side_str = (pos.get("side") or "").lower()
    side_sign = _signed_for_side(pos)
    entry = float(pos.get("entry") or 0)
    mark = float(pos.get("mark") or (mark_perp or 0))
    qty = float(pos.get("qty") or 0)
    ru = _risk_unit(pos, structure)
    if ru <= 0 or entry <= 0 or mark <= 0:
        return {"verdict": "HOLD",
                "reason": f"missing entry/mark/risk_unit (entry={entry} mark={mark} ru={ru})",
                "action": None, "meta": {"structure": structure}, "state_diff": {}}

    captured_R = _signed_pnl(pos) / ru

    # ---- load / arm state ------------------------------------------------
    full_state = _load_state()
    st = full_state.get(pid)
    armed_now = False
    if not st or st.get("schema") != "v3":
        # First time we see this position — ARM the ladder
        jit = _draw_jitters(pid)
        thresholds = _stage_thresholds(pid, structure, jit)
        # Q4: cluster-avoid the catastrophic SL on first arm
        cat_price = _sl_R_to_price(thresholds["catastrophic_R"], pos, structure)
        adj_price, cluster_note = _cluster_avoid(cat_price, side_sign, snapshot)
        if cluster_note and ru > 0:
            # Recover the new R threshold from the adjusted price
            adj_R = (adj_price - entry) / ru * (1 if side_sign == +1 else -1)
            thresholds["catastrophic_R_adjusted"] = adj_R
        st = {
            "schema":           "v3",
            "symbol":           pos.get("symbol"),
            "side":             pos.get("side"),
            "structure":        structure,
            "entry":            entry,
            "qty_initial":      qty,
            "qty_current":      qty,
            "risk_unit":        ru,
            "stage":            "S0",
            "stage_first_seen_utc": now_utc.isoformat(),
            "peak_R":           captured_R,   # initial peak = current
            "captured_R":       captured_R,
            "breach_count":     0,
            "jitters":          jit,
            "thresholds":       thresholds,
            "cluster_note":     cluster_note,
            "mark_history":     [mark],
            "partial_done":     False,
            "last_funding_sign": None,
            "last_review_utc":  now_utc.isoformat(),
            "armed_at_utc":     now_utc.isoformat(),
        }
        armed_now = True
    else:
        # Update peak + history + captured_R
        st["peak_R"] = max(float(st.get("peak_R", captured_R)), captured_R)
        st["captured_R"] = captured_R
        history = list(st.get("mark_history", []) or [])
        history.append(mark)
        st["mark_history"] = history[-10:]

    # Promote stage (monotone)
    natural_stage = _stage_for_R(captured_R, st["thresholds"])
    if _stage_rank(natural_stage) > _stage_rank(st["stage"]):
        st["stage"] = natural_stage
        st["stage_first_seen_utc"] = now_utc.isoformat()

    # Q5: funding-flip → force-promote perp to S2 if past +1R
    if structure == "perp":
        cur_funding = BYBIT_POS.get_btc_funding_rate()
        if cur_funding is not None:
            cur_sign = +1 if cur_funding > 0 else (-1 if cur_funding < 0 else 0)
            prev_sign = st.get("last_funding_sign")
            if prev_sign and cur_sign != 0 and cur_sign != prev_sign:
                if captured_R >= 1.0 and _stage_rank(st["stage"]) < _stage_rank("S2"):
                    st["stage"] = "S2"
                    st["funding_flip_promoted"] = now_utc.isoformat()
            st["last_funding_sign"] = cur_sign

    # Compute SL_R for current stage
    atr_h1 = BYBIT_POS.get_btc_atr_h1() if (st["stage"] == "S3") else None
    atr_R = None
    if atr_h1 is not None and ru > 0:
        atr_R = (atr_h1 * (ATR_MULT_BASE + st["jitters"]["atr"])) / ru
    sl_R = _sl_R_for_stage(st["stage"], st["thresholds"], st["peak_R"],
                            st["jitters"], atr_R)

    # Regime gate on S3 — if not in trend, replace trail with fixed TP
    use_trail = (st["stage"] == "S3")
    if use_trail and regime_now and regime_now.upper() not in TRAIL_REGIMES:
        use_trail = False
        sl_R = 1.0 + st["jitters"]["sl_R"]   # fall back to S2 floor

    # Q3: max-pain widen for short_premium near pin
    dte_h = _hours_to_expiry(pos, now_utc)
    mp_adjust = _max_pain_widen(structure, dte_h, pos, snapshot)
    if mp_adjust < 0:
        sl_R += mp_adjust   # negative widens (lowers SL_R for shorts)

    # Magnet-aware SL tightening (added 2026-05-04). When BTC sits within
    # ±$200 of a magnet strike (max_pain) AND we're at S2/S3 with profit
    # banked, tighten SL toward captured_R - 0.20 to lock in before the
    # MM pin re-asserts. Long_premium only — short_premium uses the
    # widening above (we WANT pin to deliver our credit).
    if structure == "long_premium" and st["stage"] in ("S2", "S3"):
        try:
            opts = (snapshot or {}).get("options") or {}
            mp_strike = opts.get("max_pain_strike") or (
                opts.get("max_pain") or {}).get("strike")
            mark_under = (snapshot or {}).get("btc_perp_last") or (
                (snapshot or {}).get("btc_focus", {}).get("perp", {}).get("last"))
            if (isinstance(mp_strike, (int, float)) and mp_strike > 0
                    and isinstance(mark_under, (int, float)) and mark_under > 0):
                if abs(float(mark_under) - float(mp_strike)) <= 200.0:
                    # Magnet zone — tighten SL by 0.20R
                    sl_R = max(sl_R, captured_R - 0.20)
                    st["magnet_tightened"] = True
                    st["magnet_strike"] = float(mp_strike)
        except Exception:
            pass

    # Catastrophic = captured_R has fallen past the hard −1R floor.
    # For LONG options, max-loss is capped at the premium (mark hits 0), so
    # there's no "uncovered" catastrophic risk — only short/perp can blow up
    # past −1R unboundedly. Long positions never get the immediate-market path.
    cat_R_threshold = (st["thresholds"].get("catastrophic_R_adjusted")
                        or st["thresholds"]["catastrophic_R"])
    if structure == "long_premium":
        catastrophic = False
    else:
        catastrophic = (captured_R <= cat_R_threshold)

    # Fix 2 (2026-05-02): grace period after first-arm. The bid-ask spread on
    # options is wide; selling at bid makes mark instantly look -1R+ even
    # though we just placed the order. Suppress catastrophic for the grace
    # window so triple-tap can run normally.
    # P3b (2026-05-02): grace window is structure-aware — 600s for short
    # premium (spread artifacts dominant), 180s for long premium (gamma
    # risk), 60s for perp (clean fills).
    if catastrophic:
        try:
            armed_at = datetime.fromisoformat(st["armed_at_utc"].replace("Z", "+00:00"))
            seconds_since_arm = (now_utc - armed_at).total_seconds()
        except Exception:
            seconds_since_arm = 9999.0
        grace_s = catastrophic_grace_for(structure)
        if seconds_since_arm < grace_s:
            catastrophic = False
            st.setdefault("grace_suppressions", 0)
            st["grace_suppressions"] = int(st["grace_suppressions"]) + 1
            st["grace_window_used_s"] = grace_s

    # Q1: funding-window override
    fwin = _funding_window(now_utc)
    if fwin == "in" and not catastrophic:
        # Defer all non-catastrophic verdicts during funding sweep window
        st["last_review_utc"] = now_utc.isoformat()
        full_state[pid] = st
        _save_state(full_state)
        return {"verdict": "HOLD",
                "reason": f"funding_window: deferring non-catastrophic close (now={now_utc.strftime('%H:%M')}UTC)",
                "action": None,
                "meta": {"structure": structure, "stage": st["stage"],
                         "captured_R": captured_R, "fwin": fwin,
                         "armed_now": armed_now},
                "state_diff": {pid: st}}
    if fwin == "pre" and st["stage"] in ("S2", "S3"):
        # Tighten SL by 0.30R (lock more profit before MM rebalance)
        sl_R = max(sl_R, captured_R - 0.30)

    # P3c (2026-05-02): pre-funding TP-close block for PERP positions only.
    # If we're about to receive a funding payment (long perp + positive
    # funding rate, OR short perp + negative funding rate), defer
    # non-catastrophic TP closes through the ±5min window so we collect
    # the funding instead of forfeiting it. Options pay no funding, so
    # this only applies to perp.
    if (fwin == "pre" and not catastrophic
            and structure == "perp"
            and isinstance(snapshot, dict)):
        funding_rate = float((snapshot.get("btc_focus", {}) or {})
                             .get("funding", {}).get("stats", {})
                             .get("last") or 0)
        position_side = (pos.get("side") or "").lower()
        # We RECEIVE funding when:
        #   long  + funding_rate > 0  (positive rate paid by shorts → longs)
        #   short + funding_rate < 0  (negative rate paid by longs → shorts)
        receives_funding = (
            (position_side == "buy" and funding_rate > 0)
            or (position_side == "sell" and funding_rate < 0)
        )
        if receives_funding and captured_R > 0:
            st["last_review_utc"] = now_utc.isoformat()
            full_state[pid] = st
            _save_state(full_state)
            return {"verdict": "HOLD",
                    "reason": (f"funding_pre_block: deferring TP close — "
                               f"funding={funding_rate*100:.4f}% will pay us; "
                               f"hold through {now_utc.strftime('%H:%M')}UTC"),
                    "action": None,
                    "meta": {"structure": structure, "stage": st["stage"],
                             "captured_R": captured_R, "fwin": fwin,
                             "funding_rate": funding_rate,
                             "armed_now": armed_now},
                    "state_diff": {pid: st}}

    # Q6: session-phase effects (overlap-aware: London/EU + US + China + Asia)
    sx = _session_effects(now_utc)

    if sx["defer_noncat"] and not catastrophic:
        st["last_review_utc"] = now_utc.isoformat()
        full_state[pid] = st
        _save_state(full_state)
        return {"verdict": "HOLD",
                "reason": f"session defer ({','.join(sx['active'])}): noncatastrophic close suppressed",
                "action": None,
                "meta": {"structure": structure, "stage": st["stage"],
                         "captured_R": captured_R, "session": sx["active"]},
                "state_diff": {pid: st}}

    if sx["tighten_R"] > 0 and st["stage"] in ("S2", "S3"):
        sl_R = max(sl_R, captured_R - sx["tighten_R"])

    if (sx["force_partial"] and st["stage"] in ("S2", "S3")
            and captured_R > 1.0 and not st.get("partial_done")):
        partial_qty = round(qty * PARTIAL_CLOSE_PCT, 8)
        st["partial_done"] = True
        st["last_review_utc"] = now_utc.isoformat()
        full_state[pid] = st
        _save_state(full_state)
        return {"verdict": "PARTIAL_CLOSE",
                "reason": f"session force_partial ({','.join(sx['active'])}): book {PARTIAL_CLOSE_PCT*100:.0f}% before next-phase volatility (captured_R={captured_R:.2f})",
                "action": "marketable_limit_partial",
                "meta": {"structure": structure, "stage": st["stage"],
                         "captured_R": captured_R, "partial_qty": partial_qty,
                         "remaining_qty": qty - partial_qty,
                         "session": sx["active"]},
                "state_diff": {pid: st}}

    # K: time-stop override for short DTE
    if dte_h is not None and dte_h <= SHORT_DTE_HOURS_MAX_LADDER:
        ts_h = TIME_STOP_H_BY_BUCKET.get(structure, 4)
        if dte_h <= ts_h:
            # Fix 1 (2026-05-02): protective wings of multi-leg structures
            # MUST stay until expiry — closing them turns iron_condor into
            # naked short. Suppress time_stop when this position is a wing.
            membership = SM.lookup_role(pos.get("symbol", ""), pos.get("side", ""))
            is_wing = bool(membership) and (
                membership.get("role") in SM.PROTECTIVE_WING_ROLES)
            if is_wing:
                st["time_stop_suppressed_as_wing"] = True
                # Fall through — let normal HOLD/breach logic continue
            else:
                full_state[pid] = st
                _save_state(full_state)
                return {"verdict": "CLOSE",
                        "reason": f"time_stop: {dte_h:.1f}h to expiry ≤ {ts_h}h ({structure})",
                        "action": "market_close",
                        "meta": {"structure": structure, "dte_h": dte_h,
                                 "stage": st["stage"], "captured_R": captured_R},
                        "state_diff": {pid: st}}

    # P1.6b: long-vol gamma-pin gate. Once dte_h ≤ 12h, the MM gamma-hedging
    # pin destroys long-vol positions (theta accelerates, vol gets crushed
    # into the strike cluster). Force close unless we're already past the
    # cap-loss threshold — at that point the spread cost outweighs the
    # value left to protect. Same wing-protection rule as time_stop.
    if (dte_h is not None and dte_h <= PRE_EXPIRY_PIN_HOURS_LONG_VOL
            and structure == "long_premium"
            and captured_R > PRE_EXPIRY_MIN_R_TO_CLOSE):
        membership = SM.lookup_role(pos.get("symbol", ""), pos.get("side", ""))
        is_wing = bool(membership) and (
            membership.get("role") in SM.PROTECTIVE_WING_ROLES)
        if not is_wing:
            full_state[pid] = st
            _save_state(full_state)
            return {"verdict": "CLOSE",
                    "reason": (f"pre_expiry_pin: {dte_h:.1f}h to expiry "
                               f"≤ {PRE_EXPIRY_PIN_HOURS_LONG_VOL}h on long_vol; "
                               f"gamma-pin window — exit before MM crushes vol "
                               f"(captured_R={captured_R:+.2f})"),
                    "action": "market_close",
                    "meta": {"structure": structure, "dte_h": dte_h,
                             "stage": st["stage"], "captured_R": captured_R,
                             "gate": "pre_expiry_pin"},
                    "state_diff": {pid: st}}

    # Q2: sweep detection → extend confirm window
    # Q6 also adds session-phase confirm_extra (high-vol windows widen further)
    sweep = _sweep_suspected(st, mark)
    confirm_n = CONFIRM_N_BASE
    if sweep:
        confirm_n = max(confirm_n, CONFIRM_N_SWEEP)
    confirm_n += int(sx["confirm_extra"])

    # Triple-tap counter (catastrophic bypasses)
    if captured_R <= sl_R:
        st["breach_count"] = int(st.get("breach_count", 0)) + 1
    else:
        st["breach_count"] = 0
    breach_confirmed = catastrophic or st["breach_count"] >= confirm_n

    # ---- verdicts (priority order) --------------------------------------
    st["last_review_utc"] = now_utc.isoformat()

    # 1. Catastrophic / confirmed SL breach
    if breach_confirmed:
        verdict = "CLOSE"
        reason = (f"sl_hit stage={st['stage']} sl_R={sl_R:.2f} "
                  f"captured_R={captured_R:.2f} breaches={st['breach_count']}/{confirm_n}"
                  f"{' SWEEP' if sweep else ''}{' CATASTROPHIC' if catastrophic else ''}")
        action = "market_close" if catastrophic else "marketable_limit_then_market"
        sl_price_now = _sl_R_to_price(sl_R, pos, structure)
        full_state[pid] = st
        _save_state(full_state)
        return {"verdict": verdict, "reason": reason, "action": action,
                "meta": {"structure": structure, "stage": st["stage"],
                         "sl_R": sl_R, "sl_price": sl_price_now,
                         "captured_R": captured_R,
                         "catastrophic": catastrophic, "sweep_suspected": sweep,
                         "fwin": fwin, "max_pain_adjust": mp_adjust},
                "state_diff": {pid: st}}

    # 2. Full TP hit (fixed limit at +R_WIN ± jit)
    if captured_R >= st["thresholds"]["tp_R_full"]:
        full_state[pid] = st
        _save_state(full_state)
        return {"verdict": "CLOSE",
                "reason": f"tp_full: captured_R={captured_R:.2f} ≥ R_WIN={st['thresholds']['tp_R_full']:.2f}",
                "action": "marketable_limit_then_market",
                "meta": {"structure": structure, "stage": st["stage"],
                         "captured_R": captured_R, "fwin": fwin},
                "state_diff": {pid: st}}

    # 3. S2 partial close (50% qty), only once
    in_S2 = (st["stage"] == "S2") or (_stage_rank(st["stage"]) >= _stage_rank("S2"))
    if in_S2 and not st.get("partial_done") and captured_R >= st["thresholds"]["tp_R_partial"]:
        partial_qty = round(qty * PARTIAL_CLOSE_PCT, 8)
        st["partial_done"] = True
        full_state[pid] = st
        _save_state(full_state)
        return {"verdict": "PARTIAL_CLOSE",
                "reason": f"S2_partial: captured_R={captured_R:.2f} ≥ {st['thresholds']['tp_R_partial']:.2f}; close {PARTIAL_CLOSE_PCT*100:.0f}% qty",
                "action": "marketable_limit_partial",
                "meta": {"structure": structure, "stage": st["stage"],
                         "captured_R": captured_R, "partial_qty": partial_qty,
                         "remaining_qty": qty - partial_qty},
                "state_diff": {pid: st}}

    # 4. Stage transition signal — caller may want to re-arm conditional SL
    full_state[pid] = st
    _save_state(full_state)
    return {"verdict": "HOLD",
            "reason": f"hold stage={st['stage']} captured_R={captured_R:.2f} sl_R={sl_R:.2f} peak_R={st['peak_R']:.2f}{' use_trail' if use_trail else ''}{' sweep' if sweep else ''} fwin={fwin}",
            "action": None,
            "meta": {"structure": structure, "stage": st["stage"],
                     "captured_R": captured_R, "peak_R": st["peak_R"],
                     "sl_R": sl_R, "sl_price": _sl_R_to_price(sl_R, pos, structure),
                     "use_trail": use_trail, "fwin": fwin,
                     "sweep_suspected": sweep, "max_pain_adjust": mp_adjust,
                     "armed_now": armed_now,
                     "thresholds": st["thresholds"],
                     "jitters": st["jitters"]},
            "state_diff": {pid: st}}


# ---------------------------------------------------------------------------
# helpers exposed for the trader / watchdog
# ---------------------------------------------------------------------------
def get_catastrophic_sl_price(pos: dict) -> float | None:
    """Read the (possibly cluster-adjusted) catastrophic SL price for a pid.
    Returns None if not yet armed.
    """
    full_state = _load_state()
    pid = pos.get("id") or _stable_pid(str(pos.get("symbol", "")),
                                        str(pos.get("side", "")))
    st = full_state.get(pid)
    if not st or st.get("schema") != "v3":
        return None
    structure = st.get("structure", "unknown")
    t = st.get("thresholds", {})
    cat_R = t.get("catastrophic_R_adjusted") or t.get("catastrophic_R")
    if cat_R is None:
        return None
    return _sl_R_to_price(float(cat_R), pos, structure)


def session_block_new_shorts(now_utc: datetime | None = None) -> tuple[bool, list[str]]:
    """Q6 hook for plan_trade: True iff opening a new short_premium structure
    should be deferred. Currently fires inside the london_open Judas window.
    Returns (block, active_phase_list).
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    sx = _session_effects(now_utc)
    return bool(sx["block_new_shorts"]), sx["active"]


def state_summary() -> dict:
    """Compact state for monitoring/logging."""
    full_state = _load_state()
    out = {}
    for pid, st in full_state.items():
        if not isinstance(st, dict):
            continue
        out[pid] = {
            "symbol":     st.get("symbol"),
            "side":       st.get("side"),
            "structure":  st.get("structure"),
            "stage":      st.get("stage"),
            "peak_R":     round(float(st.get("peak_R", 0)), 3),
            "captured_R": round(float(st.get("captured_R", 0)), 3),
            "partial_done": st.get("partial_done"),
            "last_review": st.get("last_review_utc"),
        }
    return out
