"""Track which Bybit positions are legs of multi-leg structures.

When place_option_strategy fills all 4 legs of an iron_condor, each leg
becomes a SEPARATE Bybit position. The Bybit position list cannot tell us
which positions belong to which strategy — Bybit doesn't store that.

We persist the mapping ourselves so exit_logic.decide_exit can know that
e.g. BTC-2MAY26-77000-P BUY is a "long_put_wing" of an active iron_condor,
not a standalone lottery ticket. This determines which exit rules apply:

  standalone long_premium → time_stop fires at DTE ≤ 24h (cap decay loss)
  long_premium WING       → suppress time_stop; wing is structural
                            protection that must stay until expiry

Persistent at ~/.sygnif/structure-memberships.json. Pruned when all the
structure's legs disappear from Bybit's position list.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

PATH = Path.home() / ".sygnif" / "structure-memberships.json"


# Known multi-leg structures and their leg-role mapping.
# Each entry: structure_name → {leg_index: (side, role)}
# Leg index is the order returned by option.strategies.TEMPLATES[name](...).
STRUCTURE_LEG_ROLES = {
    "iron_condor": {
        0: ("buy",  "long_put_wing"),     # K_put_long
        1: ("sell", "short_put_body"),    # K_put_short
        2: ("sell", "short_call_body"),   # K_call_short
        3: ("buy",  "long_call_wing"),    # K_call_long
    },
    "bull_call_spread": {
        0: ("buy",  "long_call_body"),    # K_buy (lower strike, long)
        1: ("sell", "short_call_wing"),   # K_sell (higher strike, short cap)
    },
    "bear_put_spread": {
        0: ("buy",  "long_put_body"),
        1: ("sell", "short_put_wing"),
    },
    "calendar_call_spread": {
        0: ("sell", "short_near_leg"),
        1: ("buy",  "long_far_leg"),
    },
    "broken_wing_butterfly_call": {
        0: ("buy",  "long_call_low"),
        1: ("sell", "short_call_mid"),
        2: ("sell", "short_call_mid"),
        3: ("buy",  "long_call_high"),
    },
    "broken_wing_butterfly_put": {
        0: ("buy",  "long_put_high"),
        1: ("sell", "short_put_mid"),
        2: ("sell", "short_put_mid"),
        3: ("buy",  "long_put_low"),
    },
    "long_strangle": {
        0: ("buy", "long_put_leg"),
        1: ("buy", "long_call_leg"),
    },
    "long_straddle": {
        0: ("buy", "long_put_leg"),
        1: ("buy", "long_call_leg"),
    },
    "short_strangle": {
        0: ("sell", "short_put_leg"),
        1: ("sell", "short_call_leg"),
    },
    "short_straddle": {
        0: ("sell", "short_put_leg"),
        1: ("sell", "short_call_leg"),
    },
}


# Roles that should KEEP positions even when individual exit rules say close.
# Long wings of defined-risk structures are protective; closing them
# unilaterally turns the structure into uncovered exposure.
PROTECTIVE_WING_ROLES = {
    "long_put_wing", "long_call_wing",
    "long_call_low", "long_call_high",
    "long_put_low",  "long_put_high",
}


def _stable_pid(symbol: str, side: str) -> str:
    return hashlib.sha256(f"{symbol}|{side}".encode()).hexdigest()[:8]


def _load() -> dict:
    if not PATH.exists():
        return {}
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return {}


def _save(d: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(d, indent=2, default=str))


def register_structure(*, structure: str, expiry_iso: str,
                        legs_real: list[dict],
                        label: str = "",
                        placed_at_utc: datetime | None = None) -> str:
    """Persist a freshly placed multi-leg structure.

    legs_real: the same list passed to place_option_strategy after symbol
    expansion — each entry must have {side, symbol, qty}. The list MUST be
    in the order the template returns (NOT execution order), so leg index
    can be used to look up the role from STRUCTURE_LEG_ROLES.
    """
    placed_at_utc = placed_at_utc or datetime.now(timezone.utc)
    sid = (label or f"{structure}_{expiry_iso}_{int(time.time())%10000}").replace(" ", "_")
    role_map = STRUCTURE_LEG_ROLES.get(structure, {})
    legs_with_roles = []
    for i, L in enumerate(legs_real):
        expected_side, role = role_map.get(i, (str(L.get("side", "")).lower(), "leg"))
        legs_with_roles.append({
            "leg_index": i,
            "symbol":    L.get("symbol"),
            "side":      L.get("side"),
            "qty":       float(L.get("qty") or 0),
            "role":      role,
            "pid":       _stable_pid(str(L.get("symbol", "")),
                                     str(L.get("side", ""))),
        })

    full = _load()
    full[sid] = {
        "structure":    structure,
        "expiry_iso":   expiry_iso,
        "label":        label,
        "placed_at_utc": placed_at_utc.isoformat(),
        "legs":         legs_with_roles,
        "active":       True,
    }
    _save(full)
    return sid


def lookup_role(symbol: str, side: str) -> dict | None:
    """Return the membership record for a position, or None if standalone.

    Returns: {sid, structure, role, expiry_iso, sibling_pids, ...}
    """
    pid = _stable_pid(symbol, side)
    full = _load()
    for sid, rec in full.items():
        if not rec.get("active"):
            continue
        for leg in rec.get("legs", []) or []:
            if leg.get("pid") == pid:
                sibling_pids = [l["pid"] for l in rec["legs"] if l.get("pid") != pid]
                return {
                    "sid":         sid,
                    "structure":   rec.get("structure"),
                    "role":        leg.get("role"),
                    "leg_index":   leg.get("leg_index"),
                    "expiry_iso":  rec.get("expiry_iso"),
                    "label":       rec.get("label"),
                    "placed_at_utc": rec.get("placed_at_utc"),
                    "sibling_pids": sibling_pids,
                }
    return None


def is_protective_wing(symbol: str, side: str) -> bool:
    """True if position is a protective-wing leg of an active multi-leg structure.
    Used by exit_logic to suppress time_stop for these positions.
    """
    rec = lookup_role(symbol, side)
    if not rec:
        return False
    return rec.get("role") in PROTECTIVE_WING_ROLES


def prune_inactive(live_pids: set[str]) -> int:
    """Mark structures inactive once ALL their legs have left the live position
    set. Returns the number of structures pruned.
    """
    full = _load()
    pruned = 0
    for sid, rec in full.items():
        if not rec.get("active"):
            continue
        leg_pids = {l.get("pid") for l in rec.get("legs", []) or []}
        if not leg_pids & live_pids:
            rec["active"] = False
            rec["pruned_at_utc"] = datetime.now(timezone.utc).isoformat()
            pruned += 1
    if pruned:
        _save(full)
    return pruned


def list_active() -> list[dict]:
    full = _load()
    return [{"sid": sid, **rec} for sid, rec in full.items() if rec.get("active")]
