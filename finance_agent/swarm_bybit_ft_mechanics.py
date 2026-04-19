"""
Freqtrade-**inspired** automation helpers for **Bybit-only** Swarm / predict-protocol loops.

Implements a subset of ideas from bot scheduling/protections (cooldown, consecutive failure cap,
persistent JSON state) **without** importing or running Freqtrade. Venue orders stay in
``btc_predict_protocol_loop`` + ``bybit_linear_hedge``.

Env (all optional; default **off** = no extra gating):

- ``SWARM_BYBIT_FT_MECHANICS`` — ``0``/``false`` disables all checks in this module.
- ``SWARM_BYBIT_ENTRY_COOLDOWN_SEC`` — minimum seconds between **successful** market **opens** on a symbol
  (flat → open). ``0`` = disabled. When unset and you run ``swarm_auto_predict_protocol_loop.py``, the launcher
  ``setdefault`` is **120** s (``demo_safe`` profile also uses **120**).
- ``SWARM_BYBIT_MAX_CONSEC_OPEN_FAILS`` — after this many consecutive **open** failures (``retCode != 0``),
  block further opens until a **successful** open resets the counter. ``0`` = disabled (default).
- ``SWARM_BYBIT_FT_STATE_JSON`` — override path for state file (default: ``prediction_agent/swarm_bybit_ft_state.json``).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _env_truthy(name: str, *, default: bool = True) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, "").strip() or default))
    except ValueError:
        return default


def state_path(repo_root: Path) -> Path:
    raw = (os.environ.get("SWARM_BYBIT_FT_STATE_JSON") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return repo_root / "prediction_agent" / "swarm_bybit_ft_state.json"


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schema_version": 1, "symbols": {}}
    try:
        o = json.loads(path.read_text(encoding="utf-8"))
        return o if isinstance(o, dict) else {"schema_version": 1, "symbols": {}}
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "symbols": {}}


def _atomic_write(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _sym_key(symbol: str) -> str:
    return (symbol or "").replace("/", "").upper().strip() or "BTCUSDT"


def entry_allowed(repo_root: Path, symbol: str, *, iter_count: int) -> tuple[bool, str]:
    """
    Return (allowed, reason). When not allowed, predict loop should **skip the open** (return 0).
    """
    if not _env_truthy("SWARM_BYBIT_FT_MECHANICS", default=True):
        return True, ""
    path = state_path(repo_root)
    st = _load(path)
    sym = _sym_key(symbol)
    symbols = st.get("symbols")
    if not isinstance(symbols, dict):
        symbols = {}
    raw_row = symbols.get(sym)
    row: dict[str, Any] = dict(raw_row) if isinstance(raw_row, dict) else {}

    max_fails = max(0, _env_int("SWARM_BYBIT_MAX_CONSEC_OPEN_FAILS", 0))
    if max_fails > 0:
        cf = int(row.get("consec_open_fails") or 0)
        if cf >= max_fails:
            return False, f"max_consec_open_fails:{cf}>={max_fails}"

    cd = max(0.0, _env_float("SWARM_BYBIT_ENTRY_COOLDOWN_SEC", 0.0))
    if cd > 1e-9:
        last = float(row.get("last_open_success_unix") or 0.0)
        if last > 0:
            elapsed = time.time() - last
            if elapsed < cd:
                return False, f"entry_cooldown:{elapsed:.1f}s<{cd:.0f}s"

    return True, ""


def record_open_success(repo_root: Path, symbol: str, *, iter_count: int) -> None:
    if not _env_truthy("SWARM_BYBIT_FT_MECHANICS", default=True):
        return
    path = state_path(repo_root)
    st = _load(path)
    symbols = dict(st.get("symbols", {})) if isinstance(st.get("symbols"), dict) else {}
    sym = _sym_key(symbol)
    prev = dict(symbols.get(sym, {})) if isinstance(symbols.get(sym), dict) else {}
    prev.update(
        {
            "last_open_success_unix": time.time(),
            "last_open_iter": int(iter_count),
            "consec_open_fails": 0,
        }
    )
    symbols[sym] = prev
    st["symbols"] = symbols
    st["schema_version"] = 1
    try:
        _atomic_write(path, st)
    except OSError:
        pass


def record_open_fail(repo_root: Path, symbol: str) -> None:
    if not _env_truthy("SWARM_BYBIT_FT_MECHANICS", default=True):
        return
    path = state_path(repo_root)
    st = _load(path)
    symbols = dict(st.get("symbols", {})) if isinstance(st.get("symbols"), dict) else {}
    sym = _sym_key(symbol)
    prev = dict(symbols.get(sym, {})) if isinstance(symbols.get(sym), dict) else {}
    cf = int(prev.get("consec_open_fails") or 0) + 1
    prev["consec_open_fails"] = cf
    prev["last_open_fail_unix"] = time.time()
    symbols[sym] = prev
    st["symbols"] = symbols
    st["schema_version"] = 1
    try:
        _atomic_write(path, st)
    except OSError:
        pass
