#!/usr/bin/env python3
"""Pull compact Bybit spot BTC context JSON for the BTC specialist agent."""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

BYBIT = "https://api.bybit.com/v5/market"
OUT = Path(__file__).resolve().parents[1] / "data"
REPO_ROOT = Path(__file__).resolve().parents[3]
FINANCE_AGENT_DIR = REPO_ROOT / "finance_agent"


def _load_dotenv_file(path: Path) -> None:
    """Parse KEY=VALUE lines into os.environ (setdefault — first loaded wins)."""
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        if not k:
            continue
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def _load_repo_env() -> None:
    """Load secrets from the shared host .env (see ~/xrp_claude_bot/.env) then repo .env."""
    home = REPO_ROOT.parent
    for p in (
        home / "xrp_claude_bot" / ".env",
        home / "finance_agent" / ".env",
        REPO_ROOT / ".env",
    ):
        _load_dotenv_file(p)


def _write_crypto_market_data_bundle(utc: str) -> bool:
    """All upstream ``data/daily/*.json`` + markdown analysis (CC BY 4.0); not Sygnif TA."""
    sys.path.insert(0, str(FINANCE_AGENT_DIR))
    try:
        from crypto_market_data import (
            build_daily_analysis_markdown,
            fetch_remote_bundle,
            list_remote_daily_json_paths,
            write_bundle_json,
        )
    except Exception:
        return False
    try:
        paths = list_remote_daily_json_paths(timeout=20.0)
        bundle = fetch_remote_bundle(paths=paths, timeout_per=15.0)
        ds = bundle.get("datasets")
        if not isinstance(ds, dict) or not any(ds.values()):
            return False
        write_bundle_json(OUT, bundle)
        md = build_daily_analysis_markdown(bundle)
        (OUT / "crypto_market_data_daily_analysis.md").write_text(
            md,
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def _write_newhedge_altcoins_correlation(utc: str) -> bool:
    if not (os.environ.get("NEWHEDGE_API_KEY") or "").strip():
        return False
    sys.path.insert(0, str(FINANCE_AGENT_DIR))
    try:
        from newhedge_client import fetch_altcoins_correlation_usd  # noqa: PLC0415
    except Exception:
        return False
    payload, err = fetch_altcoins_correlation_usd()
    if err or payload is None:
        return False
    doc = {
        "generated_utc": utc,
        "source": "NewHedge API v2 (not Sygnif TA / not Bybit)",
        "metric_path": "altcoins-correlation/altcoins_price_usd",
        "payload": payload,
    }
    (OUT / "btc_newhedge_altcoins_correlation.json").write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def _write_cryptoapis_foundation(utc: str) -> bool:
    sys.path.insert(0, str(FINANCE_AGENT_DIR))
    try:
        from cryptoapis_client import write_btc_foundation_json  # noqa: PLC0415
    except Exception:
        return False
    try:
        return bool(write_btc_foundation_json(OUT, utc))
    except Exception:
        return False


def _kline(category: str, symbol: str, interval: str, limit: int) -> list:
    r = requests.get(
        f"{BYBIT}/kline",
        params={"category": category, "symbol": symbol, "interval": interval, "limit": limit},
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    if j.get("retCode") != 0:
        raise SystemExit(j)
    rows = j.get("result", {}).get("list") or []
    rows.sort(key=lambda x: int(x[0]))
    out = []
    for row in rows:
        ts, o, h, low, c, v = row[:6]
        out.append(
            {
                "t": int(ts),
                "o": float(o),
                "h": float(h),
                "l": float(low),
                "c": float(c),
                "v": float(v),
            }
        )
    return out


def _write_sygnif_ta_snapshot(h1: list[dict], utc: str) -> None:
    """Align offline bundle with Sygnif TA (calc_indicators + calc_ta_score + detect_signals)."""
    if not h1:
        return
    err_path = OUT / "btc_sygnif_ta_snapshot.error.txt"
    if err_path.exists():
        err_path.unlink()
    rows = []
    for r in h1:
        rows.append(
            {
                "ts": int(r["t"]),
                "open": float(r["o"]),
                "high": float(r["h"]),
                "low": float(r["l"]),
                "close": float(r["c"]),
                "volume": float(r["v"]),
            }
        )
    df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    sys.path.insert(0, str(FINANCE_AGENT_DIR))
    try:
        import bot as fabot  # noqa: PLC0415
    except Exception as e:
        err_path.write_text(f"{type(e).__name__}: {e}\n", encoding="utf-8")
        return
    try:
        ind = fabot.calc_indicators(df)
        if not ind:
            err_path.write_text("calc_indicators returned empty\n", encoding="utf-8")
            return
        ta = fabot.calc_ta_score(ind)
        sig = fabot.detect_signals(ind, "BTC")
    except Exception as e:
        err_path.write_text(f"{type(e).__name__}: {e}\n", encoding="utf-8")
        return

    def _num(x):
        if x is None:
            return None
        try:
            f = float(x)
        except (TypeError, ValueError):
            return None
        if math.isnan(f):
            return None
        return f

    ind_out = {}
    for k in (
        "price",
        "rsi",
        "rsi3",
        "trend",
        "macd_signal_text",
        "willr",
        "cmf",
        "vol_ratio",
        "atr_pct",
        "mfi",
        "obv_change_pct",
        "sf_long",
        "sf_short",
        "ema_bull",
        "ema_cross",
        "bb_position",
    ):
        if k not in ind:
            continue
        v = ind[k]
        if isinstance(v, (bool, str)):
            ind_out[k] = v
        else:
            ind_out[k] = _num(v) if k != "bb_position" else v

    snap = {
        "generated_utc": utc,
        "symbol": "BTCUSDT",
        "sygnif_ta_timeframe": "1h",
        "btc_chart_ohlcv_timeframe": "1h",
        "btc_daily_timeframe": "1d",
        "interval_note": "same 1h series as btc_1h_ohlcv.json",
        "ta_score": ta.get("score"),
        "ta_components": ta.get("components") or {},
        "entries": sig.get("entries") or [],
        "exits": sig.get("exits") or [],
        "leverage": _num(sig.get("leverage")),
        "atr_pct": _num(sig.get("atr_pct")),
        "indicators": ind_out,
    }
    (OUT / "btc_sygnif_ta_snapshot.json").write_text(
        json.dumps(snap, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    _load_repo_env()
    OUT.mkdir(parents=True, exist_ok=True)
    utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tr = requests.get(f"{BYBIT}/tickers", params={"category": "spot"}, timeout=30)
    tr.raise_for_status()
    tj = tr.json()
    if tj.get("retCode") != 0:
        print(tj, file=sys.stderr)
        return 1
    btc = next(
        (x for x in tj["result"]["list"] if x.get("symbol") == "BTCUSDT"),
        None,
    )
    if not btc:
        print("BTCUSDT not in tickers", file=sys.stderr)
        return 1

    ticker_path = OUT / "bybit_btc_ticker.json"
    ticker_path.write_text(json.dumps(btc, indent=2) + "\n", encoding="utf-8")

    h1 = _kline("spot", "BTCUSDT", "60", 200)
    (OUT / "btc_1h_ohlcv.json").write_text(json.dumps(h1, indent=2) + "\n", encoding="utf-8")

    d1 = _kline("spot", "BTCUSDT", "D", 90)
    (OUT / "btc_daily_90d.json").write_text(json.dumps(d1, indent=2) + "\n", encoding="utf-8")

    _write_sygnif_ta_snapshot(h1, utc)

    files_written = [
        "bybit_btc_ticker.json",
        "btc_1h_ohlcv.json",
        "btc_daily_90d.json",
    ]
    if (OUT / "btc_sygnif_ta_snapshot.json").is_file():
        files_written.append("btc_sygnif_ta_snapshot.json")
    if _write_newhedge_altcoins_correlation(utc):
        files_written.append("btc_newhedge_altcoins_correlation.json")
    if _write_cryptoapis_foundation(utc):
        files_written.append("btc_cryptoapis_foundation.json")

    if _write_crypto_market_data_bundle(utc):
        files_written.append("btc_crypto_market_data.json")
        files_written.append("crypto_market_data_daily_analysis.md")

    manifest = {
        "generated_utc": utc,
        "source": (
            "Bybit v5 public market API (spot); optional Crypto APIs BTC mainnet + market-data; "
            "optional NewHedge altcoins correlation; "
            "optional ErcinDedeoglu/crypto-market-data (CC BY 4.0)"
        ),
        "symbol": "BTCUSDT",
        "files": files_written,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    sys.path.insert(0, str(REPO_ROOT / "finance_agent" / "btc_specialist"))
    try:
        from report import write_btc_specialist_dashboard_json  # noqa: PLC0415

        write_btc_specialist_dashboard_json(OUT, utc)
        files_written.append("btc_specialist_dashboard.json")
        manifest["files"] = files_written
        (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass

    print(f"Wrote {OUT} ({utc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
