"""agent/strategy_claim.py — one-trade-per-swing ownership lock.

When a strategy detects a setup and wants to commit a trade through its full
lifecycle (entry → TP/SL), it acquires a CLAIM. The claim:
  - identifies who owns it, what kind of setup, the planned levels
  - has a TTL (default 60 min) so a crashed owner doesn't lock forever
  - is per-direction: a top_short claim blocks other short entries but
    leaves long entries free

Other strategies (training_scanner, fast_reactor, standing_orders) call
claim_compatible_with(their_direction) before firing — returns False if
their direction conflicts with an active claim, True otherwise.

File: /var/lib/sygnif/strategy_claim.json (atomic write via os.replace)

API:
  acquire(owner, kind, entry, tp, sl, order_link_id=None,
          ttl_min=60) → claim dict | None
  release(owner, reason="manual") → bool
  active() → claim dict | None
  compatible_with(direction: "long"|"short") → bool
  mark_filled(owner, fill_price, order_id) → bool
  mark_closed(owner, exit_price, pnl, reason) → bool
  history(n=20) → list[dict]
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import time
import uuid
from typing import Any

CLAIM_FILE = pathlib.Path("/var/lib/sygnif/strategy_claim.json")
HISTORY_FILE = pathlib.Path("/var/lib/sygnif/strategy_claim_history.ndjson")

DIRECTION_BY_KIND = {
    "top_short":      "short",
    "breakdown_short": "short",
    "bottom_long":    "long",
    "breakout_long":  "long",
}


def _now_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


def _now_ts() -> float:
    return time.time()


def _load() -> dict:
    if not CLAIM_FILE.exists():
        return {}
    try:
        with CLAIM_FILE.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(claim: dict) -> None:
    CLAIM_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CLAIM_FILE.with_suffix(CLAIM_FILE.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(claim, f, indent=2, default=str)
    os.replace(tmp, CLAIM_FILE)


def _delete() -> None:
    if CLAIM_FILE.exists():
        try: CLAIM_FILE.unlink()
        except OSError: pass


def _append_history(claim: dict, action: str, extra: dict | None = None) -> None:
    rec = {"ts": _now_iso(), "action": action, **claim, **(extra or {})}
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with HISTORY_FILE.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        pass


def _is_expired(claim: dict) -> bool:
    if not claim: return False
    try:
        exp = dt.datetime.fromisoformat(claim.get("expires_utc","").replace("Z","+00:00"))
        return dt.datetime.now(tz=dt.timezone.utc) > exp
    except (ValueError, AttributeError):
        return False


def active() -> dict | None:
    """Return the active claim, or None. Auto-cleans expired claims."""
    c = _load()
    if not c: return None
    if _is_expired(c):
        _append_history(c, "expired")
        _delete()
        return None
    if c.get("state") in ("closed_tp", "closed_sl", "expired", "manual_release"):
        # Lingering closed claim — clean it
        _delete()
        return None
    return c


def compatible_with(direction: str) -> bool:
    """True if a strategy proposing `direction` can fire (no conflicting claim).

    Rule: same-direction claim blocks. Opposite-direction is allowed.
    No claim → always compatible.
    """
    a = active()
    if a is None:
        return True
    claim_dir = DIRECTION_BY_KIND.get(a.get("kind"), "")
    return claim_dir != direction.lower()


def acquire(owner: str, kind: str, *,
            entry: float, tp: float, sl: float,
            order_link_id: str | None = None,
            ttl_min: int = 60,
            symbol: str = "BTCUSDT",
            thesis: str = "",
            confluence_score: int | None = None,
            confluence_signals: list[str] | None = None) -> dict | None:
    """Try to acquire a claim. Returns the claim dict on success, None if
    another claim is already active (and not expired).

    kind must be in DIRECTION_BY_KIND.
    """
    if kind not in DIRECTION_BY_KIND:
        raise ValueError(f"unknown kind {kind!r}")
    cur = active()
    if cur is not None:
        # Lock taken — refuse acquire even if same owner (no nested claims)
        return None
    now = dt.datetime.now(tz=dt.timezone.utc)
    expires = now + dt.timedelta(minutes=int(ttl_min))
    claim = {
        "id":                  f"claim_{uuid.uuid4().hex[:12]}",
        "owner":                owner,
        "kind":                 kind,
        "direction":            DIRECTION_BY_KIND[kind],
        "symbol":               symbol,
        "entry_price":          float(entry),
        "tp_price":             float(tp),
        "sl_price":             float(sl),
        "order_link_id":        order_link_id,
        "claimed_utc":          now.isoformat(),
        "expires_utc":          expires.isoformat(),
        "ttl_min":              ttl_min,
        "state":                "pending",
        "thesis":               thesis,
        "confluence_score":     confluence_score,
        "confluence_signals":   confluence_signals or [],
    }
    _save(claim)
    _append_history(claim, "acquired")
    return claim


def mark_filled(owner: str, fill_price: float, order_id: str = "") -> bool:
    c = active()
    if not c or c.get("owner") != owner: return False
    c["state"] = "filled"
    c["fill_price"] = float(fill_price)
    c["filled_utc"] = _now_iso()
    if order_id: c["order_id"] = order_id
    _save(c)
    _append_history(c, "filled")
    return True


def mark_closed(owner: str, exit_price: float, pnl: float | None,
                reason: str = "tp") -> bool:
    c = active()
    if not c: return False
    if c.get("owner") != owner:
        return False
    c["state"] = f"closed_{reason}"
    c["exit_price"] = float(exit_price)
    c["pnl_usd"] = float(pnl) if pnl is not None else None
    c["closed_utc"] = _now_iso()
    c["close_reason"] = reason
    _save(c)
    _append_history(c, f"closed_{reason}")
    _delete()
    return True


def release(owner: str, reason: str = "manual") -> bool:
    c = active()
    if not c or c.get("owner") != owner: return False
    c["state"] = "manual_release"
    c["release_reason"] = reason
    c["released_utc"] = _now_iso()
    _append_history(c, "released")
    _delete()
    return True


def force_release(reason: str = "operator_force") -> bool:
    """Operator override — releases regardless of owner. Use sparingly."""
    c = _load()
    if not c: return False
    c["state"] = "force_released"
    c["release_reason"] = reason
    _append_history(c, "force_released")
    _delete()
    return True


def history(n: int = 20) -> list[dict]:
    if not HISTORY_FILE.exists(): return []
    out = []
    try:
        with HISTORY_FILE.open() as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out[-n:]
    except OSError:
        return []
