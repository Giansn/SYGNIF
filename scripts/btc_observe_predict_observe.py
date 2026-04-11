#!/usr/bin/env python3
"""
Observe BTC (Bybit spot) for 5×1m samples → short prediction → 5×1m → verdict.

Uses public Bybit v5 only. Heuristic is transparent (range breakout), not SygnifStrategy.
Optional: run from SYGNIF with PYTHONPATH=finance_agent for a one-line Sygnif TA hint from last 1h bar.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BYBIT = "https://api.bybit.com/v5/market"


def _get(url: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "SYGNIF-btc-observe/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def ticker_btc() -> dict:
    j = _get(f"{BYBIT}/tickers?category=spot&symbol=BTCUSDT")
    if j.get("retCode") != 0:
        raise RuntimeError(j)
    lst = j.get("result", {}).get("list") or []
    if not lst:
        raise RuntimeError("no ticker")
    return lst[0]


def kline_1m(limit: int = 15) -> list[list]:
    j = _get(
        f"{BYBIT}/kline?category=spot&symbol=BTCUSDT&interval=1&limit={limit}"
    )
    if j.get("retCode") != 0:
        raise RuntimeError(j)
    rows = j.get("result", {}).get("list") or []
    rows.sort(key=lambda x: int(x[0]))
    return rows


def closes_from_klines(rows: list[list]) -> list[float]:
    out = []
    for row in rows:
        out.append(float(row[4]))
    return out


def sygnif_hint() -> str:
    root = os.environ.get("SYGNIF_REPO", os.path.expanduser("~/SYGNIF"))
    fad = os.path.join(root, "finance_agent")
    if not os.path.isdir(fad):
        return ""
    if fad not in sys.path:
        sys.path.insert(0, fad)
    try:
        import bot as fabot  # noqa: PLC0415
        import pandas as pd  # noqa: PLC0415

        df = fabot.bybit_kline("BTCUSDT", interval="60", limit=200)
        if df is None or df.empty:
            return ""
        ind = fabot.calc_indicators(df)
        if not ind:
            return ""
        ta = fabot.calc_ta_score(ind)
        sig = fabot.detect_signals(ind, "BTC")
        return (
            f"Sygnif (1h stack): TA={ta.get('score')} "
            f"entries={sig.get('entries')[:2]} price={ind.get('price')}"
        )
    except Exception as e:
        return f"(Sygnif hint skipped: {e})"


def sample_window(label: str, n: int = 5, sleep_sec: float = 60.0) -> tuple[list[float], list[float]]:
    """Every sleep_sec, take last close from 1m kline (n samples). Returns mid times (epoch) and closes."""
    ts_list: list[float] = []
    cl_list: list[float] = []
    print(f"\n=== {label}: {n} samples, interval {sleep_sec:.0f}s ===", flush=True)
    for i in range(n):
        rows = kline_1m(10)
        cl = closes_from_klines(rows)[-1]
        t0 = int(rows[-1][0]) / 1000.0
        ts_list.append(t0)
        cl_list.append(cl)
        print(
            f"  [{i+1}/{n}] {datetime.fromtimestamp(t0, tz=timezone.utc):%H:%M:%S UTC} last 1m close ≈ {cl:,.2f}",
            flush=True,
        )
        if i + 1 < n:
            time.sleep(sleep_sec)
    return ts_list, cl_list


def main() -> int:
    fast = os.environ.get("BTC_OBSERVE_FAST", "").strip() in ("1", "true", "yes")
    sleep_sec = 5.0 if fast else 60.0
    n = 5

    print("BTC observe → predict → observe (Bybit spot BTCUSDT)", flush=True)
    print(f"UTC start: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}", flush=True)
    if fast:
        print("Mode: BTC_OBSERVE_FAST=1 → 5×5s windows (~50s total)", flush=True)

    tick = ticker_btc()
    last = float(tick.get("lastPrice", 0) or 0)
    chg = float(tick.get("price24hPcnt", 0) or 0) * 100.0
    print(f"24h ticker: last≈{last:,.2f} 24h Δ≈{chg:+.2f}%", flush=True)

    hint = sygnif_hint()
    if hint:
        print(hint, flush=True)

    # Phase 1: observe
    _, closes1 = sample_window("Phase 1 — observe", n=n, sleep_sec=sleep_sec)
    p0, p1 = closes1[0], closes1[-1]
    drift_pct = (p1 - p0) / p0 * 100.0 if p0 else 0.0
    rets = [(closes1[i] - closes1[i - 1]) / closes1[i - 1] * 100 for i in range(1, len(closes1))]
    vol_pct = (sum(r * r for r in rets) / max(1, len(rets))) ** 0.5  # RMS 1m return %
    band = max(0.02, vol_pct * 1.5, abs(drift_pct) * 0.3)  # min ~2 bps band

    # Prediction at end of phase 1: next window midpoint vs range
    mid = p1
    up_thr = mid * (1 + band / 100.0)
    dn_thr = mid * (1 - band / 100.0)
    print("\n=== Prediction (next window, heuristic) ===", flush=True)
    print(f"  Reference close (end phase 1): {mid:,.2f}", flush=True)
    print(f"  Band ±{band:.3f}% → hold zone [{dn_thr:,.2f}, {up_thr:,.2f}]", flush=True)
    print(
        "  Base: next window *last* 1m close stays inside band.",
        flush=True,
    )
    print(
        "  Up: close > upper → short-term breakout up; Down: close < lower → breakdown.",
        flush=True,
    )

    # Phase 2: observe outcome
    _, closes2 = sample_window("Phase 2 — observe (outcome)", n=n, sleep_sec=sleep_sec)
    final = closes2[-1]

    print("\n=== Verdict ===", flush=True)
    print(f"  Final close (end phase 2): {final:,.2f}", flush=True)
    if dn_thr <= final <= up_thr:
        verdict = "BASE — close stayed in band (range/mean-revert read matched)."
    elif final > up_thr:
        verdict = "UP — closed above band (continuation up vs base)."
    else:
        verdict = "DOWN — closed below band (continuation down vs base)."

    # Falsify "base" explicitly
    base_ok = dn_thr <= final <= up_thr
    print(f"  {verdict}", flush=True)
    print(f"  Strict 'base' prediction correct: {base_ok}", flush=True)
    print(f"\nUTC end: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
