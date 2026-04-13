#!/usr/bin/env python3
"""
Download aligned daily history: BTC (Yahoo) + US equity / risk proxies (SPY, VIX, TLT, GLD).

Reality check: **no liquid exchange BTC daily series exists back to 2009** on Yahoo; `BTC-USD`
typically starts **2014-09-17**. SPY/VIX from **2009** still anchor **Wall Street stress regimes**
(GFC tail, Flash Crash, COVID, 2022) for overlap-era correlation once BTC exists.

Outputs JSON under `finance_agent/btc_specialist/data/` for offline training and docs.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError as e:  # pragma: no cover
    raise SystemExit("Install yfinance: pip install yfinance") from e

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "finance_agent" / "btc_specialist" / "data" / "btc_macro_yfinance_daily.json"
DEFAULT_SUMMARY = REPO_ROOT / "finance_agent" / "btc_specialist" / "data" / "btc_macro_crash_correlation.json"

# US equity stress eras (calendar); BTC metrics only where dates overlap Yahoo BTC.
CRASH_WINDOWS: list[tuple[str, str, str, str]] = [
    ("gfc", "2008-09-01", "2009-03-31", "Global Financial Crisis"),
    ("flash_2010", "2010-05-01", "2010-07-15", "2010 flash / EU stress"),
    ("euro_debt_2011", "2011-08-01", "2011-10-31", "Euro debt / US downgrade summer"),
    ("covid_crash", "2020-02-19", "2020-04-07", "COVID equity crash"),
    ("2022_rates", "2022-01-01", "2022-10-14", "Fed hiking / risk-off 2022"),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _close_series(symbol: str, start: str) -> pd.Series:
    raw = yf.download(
        symbol,
        start=start,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if raw.empty:
        return pd.Series(dtype=float, name=symbol)
    close = raw.xs("Close", axis=1, level=0)
    if isinstance(close, pd.DataFrame):
        s = close.squeeze(axis=1)
    else:
        s = close
    s = pd.to_numeric(s, errors="coerce")
    s.name = symbol
    return s


def _btc_ohlcv(start: str) -> pd.DataFrame:
    """Full BTC OHLCV for TA-compatible training (Yahoo proxy, not Bybit)."""
    raw = yf.download(
        "BTC-USD",
        start=start,
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if raw.empty:
        return pd.DataFrame()
    idx = raw.index

    def _col(price: str) -> pd.Series:
        x = raw.xs(price, axis=1, level=0)
        return x.squeeze(axis=1) if isinstance(x, pd.DataFrame) else x

    out = pd.DataFrame(
        {
            "btc_open": _col("Open"),
            "btc_high": _col("High"),
            "btc_low": _col("Low"),
            "btc_close": _col("Close"),
            "btc_volume": _col("Volume"),
        },
        index=idx,
    )
    out.index = pd.to_datetime(out.index).tz_localize(None)
    return out


def build_panel(start: str = "2009-01-01") -> pd.DataFrame:
    btc = _btc_ohlcv(start)
    spy = _close_series("SPY", start)
    vix = _close_series("^VIX", start)
    tlt = _close_series("TLT", start)
    gld = _close_series("GLD", start)

    panel = pd.concat(
        [btc, spy.rename("spy_close"), vix.rename("vix_close"), tlt.rename("tlt_close"), gld.rename("gld_close")],
        axis=1,
        sort=True,
    ).sort_index()
    panel.index = pd.to_datetime(panel.index).tz_localize(None)
    return panel


def crash_window_stats(panel: pd.DataFrame) -> list[dict]:
    out: list[dict] = []
    for wid, s, e, label in CRASH_WINDOWS:
        sl = pd.Timestamp(s)
        el = pd.Timestamp(e)
        w = panel.loc[(panel.index >= sl) & (panel.index <= el)]
        if w.empty:
            out.append(
                {
                    "id": wid,
                    "label": label,
                    "start": s,
                    "end": e,
                    "rows": 0,
                    "spy_total_return_pct": None,
                    "vix_max": None,
                    "btc_overlap_rows": 0,
                    "btc_spy_corr_60d_mean_in_window": None,
                }
            )
            continue
        spy0 = w["spy_close"].iloc[0]
        spy1 = w["spy_close"].iloc[-1]
        spy_ret = float((spy1 / spy0 - 1.0) * 100) if spy0 and not np.isnan(spy0) else None
        vix_max = float(w["vix_close"].max()) if w["vix_close"].notna().any() else None
        sub = w.dropna(subset=["btc_close", "spy_close"])
        corr_mean = None
        if len(sub) > 65:
            br = sub["btc_close"].pct_change()
            sr = sub["spy_close"].pct_change()
            roll = br.rolling(60).corr(sr)
            corr_mean = float(roll.mean()) if roll.notna().any() else None
        out.append(
            {
                "id": wid,
                "label": label,
                "start": s,
                "end": e,
                "rows": int(len(w)),
                "spy_total_return_pct": round(spy_ret, 2) if spy_ret is not None else None,
                "vix_max": round(vix_max, 2) if vix_max is not None else None,
                "btc_overlap_rows": int(sub.shape[0]),
                "btc_spy_corr_60d_mean_in_window": round(corr_mean, 4) if corr_mean is not None else None,
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Pull BTC + macro Yahoo daily panel.")
    ap.add_argument("--start", default="2009-01-01", help="Calendar start for macro leg (SPY/VIX/…).")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="Full daily panel JSON path.")
    ap.add_argument("--summary-out", default=str(DEFAULT_SUMMARY), help="Crash-window correlation summary JSON.")
    args = ap.parse_args()

    panel = build_panel(args.start)
    btc_first = panel["btc_close"].first_valid_index()
    btc_last = panel["btc_close"].last_valid_index()

    overlap = panel.dropna(subset=["btc_close", "spy_close"])
    br = overlap["btc_close"].pct_change()
    sr = overlap["spy_close"].pct_change()
    roll60 = br.rolling(60).corr(sr)
    roll252 = br.rolling(252).corr(sr)

    meta = {
        "generated_utc": _utc_now(),
        "yahoo_start_requested": args.start,
        "data_truth": {
            "btc_ticker": "BTC-USD",
            "btc_first_close_date": str(btc_first.date()) if btc_first is not pd.NaT else None,
            "btc_last_close_date": str(btc_last.date()) if btc_last is not pd.NaT else None,
            "note": (
                "Bitcoin had no continuous exchange OHLC in 2009 comparable to today; "
                "Yahoo BTC-USD is a convenience proxy and begins when Yahoo has data (~2014)."
            ),
        },
        "macro_tickers": {
            "SPY": "US large-cap equity proxy",
            "^VIX": "Implied vol / fear gauge",
            "TLT": "Long Treasury ETF (duration / risk-off proxy)",
            "GLD": "Gold ETF (hard-asset / risk-off proxy)",
        },
        "overlap_btc_spy_rows": int(len(overlap)),
        "rolling_corr_btc_spy_mean_60d": round(float(roll60.mean()), 4) if roll60.notna().any() else None,
        "rolling_corr_btc_spy_mean_252d": round(float(roll252.mean()), 4) if roll252.notna().any() else None,
        "crash_windows": crash_window_stats(panel),
    }

    out_path = Path(os.environ.get("BTC_MACRO_DAILY_OUT", args.out))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Store panel as records (date + columns)
    panel_reset = panel.reset_index()
    panel_reset.rename(columns={panel_reset.columns[0]: "date"}, inplace=True)
    records = []
    for _, row in panel_reset.iterrows():
        rec = {"date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d")}
        for c in (
            "btc_open",
            "btc_high",
            "btc_low",
            "btc_close",
            "btc_volume",
            "spy_close",
            "vix_close",
            "tlt_close",
            "gld_close",
        ):
            v = row.get(c)
            if v is None or (isinstance(v, float) and np.isnan(v)):
                rec[c] = None
            else:
                rec[c] = float(v)
        records.append(rec)

    payload = {"meta": meta, "daily": records}
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[pull_btc_macro] wrote {out_path} ({len(records)} days)", flush=True)

    sum_path = Path(os.environ.get("BTC_MACRO_CRASH_SUMMARY_OUT", args.summary_out))
    sum_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"[pull_btc_macro] wrote {sum_path}", flush=True)


if __name__ == "__main__":
    main()
