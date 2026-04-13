#!/usr/bin/env python3
"""
**Rule-trade agent helper** — read-only context for **/ruleprediction-agent** + trade agents.

Combines:
  - **Overseer** HTTP ``/overview`` + ``/trades`` (when ``OVERSEER_URL`` / default reachable)
  - **Data inflow:** ``training_channel_output.json``, ``btc_prediction_output.json``, optional Nautilus 1h JSON
  - **R01–R03** from ``letscrash/btc_strategy_0_1_rule_registry.json``

**Scope:** **BTC/USDT futures perpetual only** (``BTC/USDT:USDT``). No orders, no JWT handling.
Leverage and execution stay in **Freqtrade** ``leverage()`` / ``BTC_Strategy_0_1`` (R01–R03 tags).

Env:
  ``SYGNIF_REPO_ROOT`` — override repo root (default: parent of ``prediction_agent/``).
  ``OVERSEER_URL`` — default ``http://127.0.0.1:8090``.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# --- Whitelist (futures USDT perp only) ---
BTC_FUTURES_PERP = "BTC/USDT:USDT"


def repo_root() -> Path:
    raw = (os.environ.get("SYGNIF_REPO_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def is_btc_futures_whitelist(pair: str) -> bool:
    """True only for the canonical BTC USDT-margined perpetual pair."""
    p = (pair or "").strip().upper().replace(" ", "")
    return p in ("BTC/USDT:USDT", "BTC:USDT")


def leverage_handled_by_strategy_note() -> str:
    """Human/agent reminder: sizing is Freqtrade-side, not this helper."""
    return (
        "Leverage/stake: Freqtrade ``BTC_Strategy_0_1`` / ``SygnifStrategy.leverage()`` "
        "(majors e.g. 5x cap, ATR-aware); this module does not set exchange leverage."
    )


def _load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_rule_registry(root: Path | None = None) -> dict[str, Any] | None:
    """``btc_strategy_0_1_rule_registry.json`` (R01–R03 metadata)."""
    r = root or repo_root()
    raw = _load_json(r / "letscrash" / "btc_strategy_0_1_rule_registry.json")
    return raw if isinstance(raw, dict) else None


def load_training_channel(root: Path | None = None) -> dict[str, Any] | None:
    raw = _load_json((root or repo_root()) / "prediction_agent" / "training_channel_output.json")
    return raw if isinstance(raw, dict) else None


def load_btc_prediction(root: Path | None = None) -> dict[str, Any] | None:
    raw = _load_json((root or repo_root()) / "prediction_agent" / "btc_prediction_output.json")
    return raw if isinstance(raw, dict) else None


def load_nautilus_btc_1h(root: Path | None = None) -> dict[str, Any] | None:
    """Optional regime / OHLCV mirror used by training + btc-specialist."""
    r = root or repo_root()
    for name in ("btc_1h_ohlcv_nautilus_bybit.json", "btc_1h_ohlcv.json"):
        got = _load_json(r / "finance_agent" / "btc_specialist" / "data" / name)
        if isinstance(got, dict):
            return got
    return None


def _plays_json_path(root: Path) -> Path | None:
    base = root / "trade_overseer" / "data"
    for rel in ("plays.json", "futures/plays.json", "spot/plays.json"):
        p = base / rel
        if p.is_file():
            return p
    return None


def load_overseer_plays_file(root: Path | None = None) -> dict[str, Any] | None:
    """Local ``plays.json`` (same store overseer uses when HTTP POST is unavailable)."""
    r = root or repo_root()
    p = _plays_json_path(r)
    return _load_json(p) if p else None


def fetch_overseer_json(subpath: str, base_url: str | None = None, timeout_sec: float = 8.0) -> Any | None:
    """
    GET ``{OVERSEER_URL or http://127.0.0.1:8090}{subpath}`` (e.g. ``/trades``, ``/overview``).
    Returns parsed JSON or None if unreachable.
    """
    base = (base_url or os.environ.get("OVERSEER_URL") or "http://127.0.0.1:8090").rstrip("/")
    url = f"{base}{subpath if subpath.startswith('/') else '/' + subpath}"
    try:
        with urllib.request.urlopen(url, timeout=timeout_sec) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def filter_btc_futures_trades(overseer_trades_payload: Any) -> list[dict[str, Any]]:
    """From overseer ``/trades`` body, keep only BTC futures perp rows."""
    if not isinstance(overseer_trades_payload, dict):
        return []
    trades = overseer_trades_payload.get("trades")
    if not isinstance(trades, list):
        return []
    out: list[dict[str, Any]] = []
    for row in trades:
        if not isinstance(row, dict):
            continue
        pair = str(row.get("pair") or "")
        if is_btc_futures_whitelist(pair):
            out.append(row)
    return out


def build_rule_trade_agent_snapshot(
    *,
    root: Path | None = None,
    overseer_url: str | None = None,
) -> dict[str, Any]:
    """
    Single JSON-serializable dict for **rule prediction / trade subagents** (read-only).

    Keys: ``whitelist``, ``registry``, ``training_channel``, ``btc_prediction``,
    ``nautilus_btc_1h``, ``overseer`` (overview + filtered trades), ``plays_file``,
    ``r_guidance``, ``leverage_note``.
    """
    r = root or repo_root()
    reg = load_rule_registry(r)
    rules = reg.get("rules") if isinstance(reg, dict) else None
    r_guidance: dict[str, Any] = {}
    if isinstance(rules, list):
        for row in rules:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or "")
            if rid.startswith("BTC-0.1-R"):
                r_guidance[rid] = {
                    "kind": row.get("kind"),
                    "rule_tag": row.get("rule_tag"),
                    "tags": row.get("tags"),
                    "log": row.get("log"),
                    "pine_reference": row.get("pine_reference"),
                }

    ov_trades = fetch_overseer_json("/trades", overseer_url)
    ov_overview = fetch_overseer_json("/overview", overseer_url)
    plays_disk = load_overseer_plays_file(r)

    return {
        "whitelist": {"pair": BTC_FUTURES_PERP, "trading_mode": "futures"},
        "registry": reg,
        "r_guidance": r_guidance,
        "training_channel": load_training_channel(r),
        "btc_prediction": load_btc_prediction(r),
        "nautilus_btc_1h": load_nautilus_btc_1h(r),
        "overseer": {
            "overview": ov_overview,
            "trades_btc_futures_only": filter_btc_futures_trades(ov_trades),
            "trades_raw_ok": ov_trades is not None,
        },
        "plays_file": plays_disk,
        "leverage_note": leverage_handled_by_strategy_note(),
    }


def format_brief_lines(snapshot: dict[str, Any], *, max_lines: int = 12) -> str:
    """Compact multi-line string for logs or LLM context (no secrets)."""
    lines: list[str] = [
        f"whitelist={snapshot.get('whitelist', {}).get('pair')}",
        snapshot.get("leverage_note") or "",
    ]
    rg = snapshot.get("r_guidance") or {}
    for k in sorted(rg.keys()):
        row = rg[k]
        lines.append(f"{k}|kind={row.get('kind')}|tags={row.get('tags')}")
    ov = snapshot.get("overseer") or {}
    n = len(ov.get("trades_btc_futures_only") or [])
    lines.append(f"overseer_btc_open_rows={n}|raw={ov.get('trades_raw_ok')}")
    tc = snapshot.get("training_channel")
    if isinstance(tc, dict):
        lines.append(f"training_channel_generated={tc.get('generated_utc', '?')[:24]}")
    return "\n".join(lines[:max_lines])


if __name__ == "__main__":
    snap = build_rule_trade_agent_snapshot()
    print(json.dumps(snap, indent=2, default=str)[:8000])
