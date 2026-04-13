#!/usr/bin/env python3
"""
Bull / bear assessment aligned with SygnifStrategy BTC informative gates (spot context).

Sources (in order):
  1) GET {FINANCE_AGENT_BASE_URL}/briefing  (Docker: finance-agent:8091)
  2) JSON OHLCV candles — see ``_load_ohlcv_local`` (Nautilus Bybit sink file preferred, then legacy ``btc_1h_ohlcv.json``)

Exit codes: 0 = ok printed JSON; 1 = no data.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import requests

# Thresholds mirror SygnifStrategy protections / exits (approximate regime read)
DUMP_1H_PCT = -1.15
DUMP_RSI_1H = 37.0
DUMP_RSI_4H = 46.0
PUMP_RSI_3_1H = 85.0
PUMP_RSI_14_4H_FLOOR = 70.0
STRUCTURAL_BULL_RSI_4H = 60.0


@dataclass
class BtcRegime:
    label: str  # bull | bear | neutral | risk_off | pump_guard
    confidence: str  # low | medium | high
    rationale: list[str]
    spot_notional_usdt: float


def _load_ohlcv_local(base: Path) -> list[dict[str, Any]] | None:
    """Prefer Nautilus Bybit sink (Docker mount), then host path, then legacy OHLCV file."""
    env_p = os.environ.get("NAUTILUS_BTC_OHLCV_JSON", "").strip()
    candidates: list[Path] = []
    if env_p:
        candidates.append(Path(env_p))
    candidates.append(base / "btc_specialist_data" / "btc_1h_ohlcv_nautilus_bybit.json")
    candidates.append(
        base / "finance_agent" / "btc_specialist" / "data" / "btc_1h_ohlcv_nautilus_bybit.json"
    )
    candidates.append(base / "finance_agent" / "btc_specialist" / "data" / "btc_1h_ohlcv.json")
    for p in candidates:
        if not p.is_file():
            continue
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return raw
    return None


def _rsi_wilder(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))


def _rsi3(closes: list[float]) -> float | None:
    return _rsi_wilder(closes, 3)


def _pct_change_1h(candles: list[dict[str, Any]]) -> float | None:
    if len(candles) < 2:
        return None
    o = float(candles[-1]["o"])
    c = float(candles[-1]["c"])
    if o == 0:
        return None
    return (c - o) / o * 100.0


def assess_from_candles(candles: list[dict[str, Any]], notional: float) -> BtcRegime:
    closes = [float(c["c"]) for c in candles]
    r14_1h = _rsi_wilder(closes, 14)
    r3_1h = _rsi3(closes)
    # 4h RSI proxy: sample every 4 candles from 1h file (coarse)
    closes_4h = closes[::4]
    r14_4h = _rsi_wilder(closes_4h, 14) if len(closes_4h) >= 15 else None
    ch1 = _pct_change_1h(candles)

    rationale: list[str] = []
    label = "neutral"
    confidence = "low"

    if ch1 is not None and r14_1h is not None and r14_4h is not None:
        if ch1 <= DUMP_1H_PCT and r14_1h < DUMP_RSI_1H and r14_4h < DUMP_RSI_4H:
            label = "risk_off"
            confidence = "high"
            rationale.append(
                f"1h candle weak + RSI14_1h {r14_1h:.1f} < {DUMP_RSI_1H} "
                f"& RSI14_4h~ {r14_4h:.1f} < {DUMP_RSI_4H} (aligns with exit_btc_risk_off)"
            )
        elif r14_4h is not None and r14_4h > STRUCTURAL_BULL_RSI_4H:
            label = "bull"
            confidence = "medium"
            rationale.append(f"Structural BTC 4h RSI proxy > {STRUCTURAL_BULL_RSI_4H} — shorts blocked in strategy")
        if r3_1h is not None and r3_1h > PUMP_RSI_3_1H and (r14_4h or 0) < PUMP_RSI_14_4H_FLOOR:
            if label == "neutral":
                label = "pump_guard"
            rationale.append("BTC 1h micro RSI hot — alt-short pump guard zone")

    if ch1 is not None:
        rationale.append(f"Last 1h body change: {ch1:+.2f}%")
    if r14_1h is not None:
        rationale.append(f"RSI14 (1h closes): {r14_1h:.1f}")
    if r14_4h is not None:
        rationale.append(f"RSI14 (4h-thin proxy): {r14_4h:.1f}")

    return BtcRegime(
        label=label,
        confidence=confidence,
        rationale=rationale,
        spot_notional_usdt=notional,
    )


def fetch_briefing(base_url: str) -> str | None:
    url = base_url.rstrip("/") + "/briefing"
    try:
        r = requests.get(url, timeout=8)
        if r.ok:
            return r.text
    except OSError:
        return None
    return None


def main() -> int:
    base_url = os.environ.get("FINANCE_AGENT_BASE_URL", "http://127.0.0.1:8091").rstrip("/")
    notional = float(os.environ.get("SYGNIF_SPOT_NOTIONAL_USDT", "100"))

    lab_root = Path(os.environ.get("LAB_ROOT", "/lab"))
    if not lab_root.is_dir():
        lab_root = Path(__file__).resolve().parents[2]

    briefing = fetch_briefing(base_url)
    out: dict[str, Any] = {"finance_agent_briefing_ok": briefing is not None}

    candles = _load_ohlcv_local(lab_root)
    if candles:
        regime = assess_from_candles(candles, notional)
        out["regime"] = asdict(regime)
    else:
        out["regime"] = None
        out["error"] = (
            "missing OHLCV JSON: set NAUTILUS_BTC_OHLCV_JSON or add "
            "btc_1h_ohlcv_nautilus_bybit.json (Nautilus sink) or btc_1h_ohlcv.json under btc_specialist/data/"
        )

    if briefing:
        out["briefing_excerpt"] = briefing[:1200]

    print(json.dumps(out, indent=2))
    return 0 if out.get("regime") else 1


if __name__ == "__main__":
    sys.exit(main())
