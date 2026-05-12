"""SYGNIF psychological-barrier rule — single source of truth.

Used by:
  - ``discovery.predict.predict``  (rule-based score, +1 to s_5b)
  - ``sygnif_predict.iterate``     (gate to block LONG entries into a major)

Backtest (predict/backtest_psych/, 365d × BTCUSDT 5m):
  - Effect concentrated at $10k MAJOR levels only.
  - When price enters [B*(1-0.4%), B) for B = next $10k major from below
    AND pos_in_range ≥ 0.5, P(≥0.5% pullback within 2h) is +10..16pp vs
    non-round controls (raw z=+2.07..+2.72; does not survive Holm/BH
    correction across the 60-cell grid — treat as a small a-priori
    fade prior, not proof). $5k mid and $1k minor levels: no effect.

Use as fade prior for upward approaches. The symmetric short-side bounce
case (price falling toward a major from above) was NOT tested — do not
assume symmetry until that backtest is run.
"""
from __future__ import annotations

from typing import Optional

# ---- params (kept here so both consumers stay in sync) --------------------
ROUND_STEP = 10_000        # only $10k MAJORs showed a measurable effect
ROUND_NEAR_PCT = 0.004     # within 0.4% of next major from below
ROUND_TIGHT_PCT = 0.0005   # within 0.05% — stronger-evidence sub-band
ROUND_RANGE_GATE = 0.5     # require pos_in_range >= this (rallying upward)


def _safe_pos_in_range(last: float, hi: Optional[float], lo: Optional[float]
                       ) -> Optional[float]:
    if hi is None or lo is None or hi <= lo:
        return None
    return (last - lo) / (hi - lo)


def near_major_from_below(
    last: Optional[float],
    high_ref: Optional[float] = None,
    low_ref: Optional[float] = None,
    pos_in_range: Optional[float] = None,
) -> dict:
    """Return a structured verdict about proximity to the next $10k major.

    Pass either (high_ref, low_ref) — used to compute pos_in_range — OR a
    pre-computed pos_in_range. The latter wins if provided.

    Output:
        {
          "near":      bool,      # within ROUND_NEAR_PCT and gate passed
          "tight":     bool,      # within ROUND_TIGHT_PCT (subset of near)
          "next_major": float|None,
          "dist_pct":   float|None,   # (next_major - last) / next_major
          "pos_in_range": float|None,
          "reason":    str|None,      # human-readable when near=True
        }
    """
    out = dict(near=False, tight=False, next_major=None, dist_pct=None,
               pos_in_range=None, reason=None)
    if last is None or not isinstance(last, (int, float)):
        return out
    next_major = (int(last) // ROUND_STEP + 1) * ROUND_STEP
    dist_pct = (next_major - last) / next_major
    out["next_major"] = float(next_major)
    out["dist_pct"] = float(dist_pct)

    if pos_in_range is None:
        pos_in_range = _safe_pos_in_range(last, high_ref, low_ref)
    out["pos_in_range"] = pos_in_range

    if not (0 < dist_pct <= ROUND_NEAR_PCT):
        return out
    if pos_in_range is None or pos_in_range < ROUND_RANGE_GATE:
        return out

    out["near"] = True
    out["tight"] = dist_pct <= ROUND_TIGHT_PCT
    if out["tight"]:
        out["reason"] = (
            f"within 0.05% of ${next_major:,.0f} major round from below "
            f"+ upper-half 24h range -> ~94% prior 0.5%+ pullback in 2h"
        )
    else:
        out["reason"] = (
            f"within {dist_pct*100:.2f}% of ${next_major:,.0f} major round "
            f"from below, upper-half 24h range -> lean fade"
        )
    return out


def rejection_from_major_above(
    last: Optional[float],
    recent_high: Optional[float],
    pos_in_range: Optional[float] = None,
) -> dict:
    """Symmetric companion to near_major_from_below — detect a PROACTIVE
    short opportunity when price recently kissed a $10k major from below
    AND has already started pulling back.

    NOT independently backtested. The long-block side has 365d evidence;
    this short-side fade is the docstring's explicit untested-symmetry
    case. Deploy with conservative sizing + a tracked hypothesis row in
    swarm_id="self_improvement" so we can evaluate later.

    Args:
      last:         current price (must be < recent_high to qualify)
      recent_high:  highest high in the lookback window the caller chose
                    (3-5 bars on 5m TF works for the move on 2026-05-04
                    10:00Z where H=$79,886.9 → reject from $80k)
      pos_in_range: pos of recent_high in the 24h range (0..1)

    Output mirrors near_major_from_below.
    """
    out = dict(near=False, tight=False, major=None, dist_pct=None,
               pos_in_range=pos_in_range, reason=None)
    if last is None or recent_high is None:
        return out
    if not isinstance(last, (int, float)) or not isinstance(recent_high, (int, float)):
        return out

    # Find which major the recent_high reached for. Two cases:
    #   (a) recent_high is just BELOW a major  → kissed-from-below
    #   (b) recent_high pierced a major slightly  → kissed-and-pierced
    # Bug fix 2026-05-04: previously only handled (a) — major was always
    # computed as ceil(recent_high/10k). After a stop on a piercing wick
    # (e.g. 15:55Z recent_high=$80,088 over $80k), next iteration computed
    # major=$90,000 → 11% gap → rule went silent. Now we check both, pick
    # whichever is closer (within ROUND_NEAR_PCT).
    near_above = (int(recent_high) // ROUND_STEP + 1) * ROUND_STEP
    near_below = (int(recent_high) // ROUND_STEP) * ROUND_STEP
    dist_above = (near_above - recent_high) / near_above if near_above > 0 else 1.0
    dist_below = ((recent_high - near_below) / near_below) if near_below > 0 else 1.0

    if dist_above <= ROUND_NEAR_PCT:
        major = near_above
        dist_at_high = dist_above
        case = "from_below"
    elif dist_below <= ROUND_NEAR_PCT:
        major = near_below
        dist_at_high = dist_below
        case = "pierced"
    else:
        out["major"] = float(near_above)
        out["dist_pct"] = float(dist_above)
        return out

    out["major"] = float(major)
    out["dist_pct"] = float(dist_at_high)

    if last >= recent_high:
        return out                              # not pulled back yet
    if last >= major:
        return out                              # current price hasn't fallen back below the major
    if pos_in_range is None or pos_in_range < ROUND_RANGE_GATE:
        return out                              # not in upper half of range

    out["near"] = True
    out["tight"] = dist_at_high <= ROUND_TIGHT_PCT
    pulled_pct = (recent_high - last) / recent_high
    desc = ("kissed" if case == "from_below" else "pierced and reclaimed")
    out["reason"] = (
        f"recent high ${recent_high:,.0f} {desc} ${major:,.0f} major "
        f"({dist_at_high*100:.2f}% gap), now pulled back {pulled_pct*100:.2f}% "
        f"from the high — proactive short fade (UNTESTED symmetry)"
    )
    return out
