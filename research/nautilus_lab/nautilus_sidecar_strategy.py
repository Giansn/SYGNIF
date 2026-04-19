#!/usr/bin/env python3
"""
Lightweight **strategy sidecar** inside the Nautilus Docker image (pandas only).

Reads 1h OHLCV already written by ``bybit_nautilus_spot_btc_training_feed.py``,
computes a simple regime hint, writes ``nautilus_strategy_signal.json`` next to it.

**Does not** place exchange orders and **does not** call Bybit — avoids racing the sink.
Downstream execution is outside this sidecar; this file is an optional signal feed for research and dashboards.

**Swarm hook:** ``NAUTILUS_SWARM_HOOK=1`` and/or ``NAUTILUS_FUSION_SIDECAR_SYNC=1`` →
``prediction_agent/nautilus_swarm_hook.py`` after each signal write. Add ``NAUTILUS_SWARM_HOOK_KNOWLEDGE=1``
to refresh ``swarm_knowledge_output.json``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

OUT_NAME = "nautilus_strategy_signal.json"


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _maybe_swarm_hook_after_sidecar_write() -> None:
    """Optional: ``nautilus_swarm_hook`` (fusion + optional swarm_knowledge) — see prediction_agent."""
    if not (
        _env_truthy("NAUTILUS_SWARM_HOOK")
        or _env_truthy("NAUTILUS_FUSION_SIDECAR_SYNC")
        or _env_truthy("SYGNIF_BYBIT_DEMO_PREDICTED_MOVE_EXPORT")
    ):
        return
    try:
        root = Path(__file__).resolve().parents[2]
        pa = root / "prediction_agent"
        if not pa.is_dir():
            return
        if str(pa) not in sys.path:
            sys.path.insert(0, str(pa))
        from nautilus_swarm_hook import run_nautilus_swarm_hook  # noqa: PLC0415

        run_nautilus_swarm_hook(phase="sidecar", repo_root=root)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"nautilus_swarm_hook_sidecar_error": str(exc)}), flush=True)


def _data_dir() -> Path:
    return Path(os.environ.get("NAUTILUS_BTC_OHLCV_DIR", "/lab/btc_specialist_data"))


def _load_ohlcv(path: Path) -> pd.DataFrame | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, list) or not raw:
        return None
    rows = []
    for c in raw:
        if not isinstance(c, dict) or "t" not in c:
            continue
        rows.append(
            {
                "t": int(c["t"]),
                "o": float(c["o"]),
                "h": float(c["h"]),
                "l": float(c["l"]),
                "c": float(c["c"]),
            }
        )
    if len(rows) < 30:
        return None
    df = pd.DataFrame(rows).sort_values("t").reset_index(drop=True)
    return df


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def analyze(df: pd.DataFrame) -> dict[str, Any]:
    c = df["c"].astype(float)
    rsi = _rsi(c, 14).iloc[-1]
    ema20 = c.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = c.ewm(span=50, adjust=False).mean().iloc[-1]
    last = float(c.iloc[-1])
    if np.isnan(rsi):
        rsi_f = None
    else:
        rsi_f = float(rsi)

    if rsi_f is None:
        bias = "neutral"
    elif last > ema20 > ema50 and rsi_f < 70:
        bias = "long"
    elif last < ema20 < ema50 and rsi_f > 30:
        bias = "short"
    else:
        bias = "neutral"

    return {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "nautilus_sidecar_strategy",
        "ohlcv_rows": len(df),
        "last_t_ms": int(df["t"].iloc[-1]),
        "close": round(last, 2),
        "rsi14": None if rsi_f is None else round(rsi_f, 2),
        "ema20": round(float(ema20), 2),
        "ema50": round(float(ema50), 2),
        "bias": bias,
        "disclaimer": "Research hint only — not wired to live entries by default.",
    }


def _atomic_write(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_once() -> dict[str, Any] | None:
    ddir = _data_dir()
    df = _load_ohlcv(ddir / "btc_1h_ohlcv.json")
    if df is None:
        return None
    out = analyze(df)
    out_path = ddir / OUT_NAME
    _atomic_write(out_path, out)
    _maybe_swarm_hook_after_sidecar_write()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Nautilus-container strategy sidecar (signal JSON only).")
    ap.add_argument("--loop", action="store_true", help="repeat every NAUTILUS_STRATEGY_POLL_SEC")
    args = ap.parse_args()
    interval = int(os.environ.get("NAUTILUS_STRATEGY_POLL_SEC", "300"))

    def once() -> int:
        r = run_once()
        print(json.dumps(r or {"ok": False, "error": "no_ohlcv"}, default=str), flush=True)
        return 0 if r else 1

    if args.loop:
        while True:
            try:
                once()
            except Exception as e:  # noqa: BLE001
                print(json.dumps({"ok": False, "error": str(e)}), flush=True)
            time.sleep(max(30, interval))
        return 0
    return once()


if __name__ == "__main__":
    raise SystemExit(main())
