#!/usr/bin/env python3
"""
Rethink loop: pull Bybit linear klines, Sygnif-aligned TA + swing flags, optional multi-bar scan.

CLI:
  python user_data/scripts/rethink_sim.py              # last-bar snapshot
  python user_data/scripts/rethink_sim.py --scan       # window + NY counterfactual
  python user_data/scripts/rethink_sim.py --predict    # deep history, top pairs, forward-return edge

Network: requires outbound HTTPS to https://api.bybit.com
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import pandas_ta as pta
import requests

BYBIT = "https://api.bybit.com"
DEFAULT_SYMS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

# Rough NYSE cash overlap (UTC hours 13-15); tune in one place for counterfactuals.
NY_UTC_HOURS = (13, 14, 15)


def _parse_kline_rows(rows: list) -> pd.DataFrame:
    rows = list(reversed(rows))
    df = pd.DataFrame(rows, columns=["start", "open", "high", "low", "close", "volume", "turnover"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["start"].astype(np.int64), unit="ms", utc=True)
    return df


def fetch_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    r = requests.get(
        f"{BYBIT}/v5/market/kline",
        params={"category": "linear", "symbol": symbol, "interval": interval, "limit": limit},
        timeout=45,
    )
    r.raise_for_status()
    rows = r.json()["result"]["list"]
    return _parse_kline_rows(rows)


def fetch_klines_paginated(symbol: str, interval: str, target_bars: int, pause_s: float = 0.05) -> pd.DataFrame:
    """
    Walk backwards in time (Bybit limit max 1000 per call) until target_bars or no more data.
    Returns chronological OHLCV, most recent target_bars rows kept.
    """
    if target_bars < 200:
        raise ValueError("target_bars must be >= 200")
    batches: list[pd.DataFrame] = []
    end_ms: int | None = None
    total = 0
    max_iters = target_bars // 500 + 25
    for _ in range(max_iters):
        if total >= target_bars:
            break
        lim = min(1000, target_bars - total)
        params: dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": lim,
        }
        if end_ms is not None:
            params["end"] = end_ms
        r = requests.get(f"{BYBIT}/v5/market/kline", params=params, timeout=60)
        r.raise_for_status()
        rows = r.json()["result"]["list"]
        if not rows:
            break
        df = _parse_kline_rows(rows)
        batches.append(df)
        total += len(df)
        end_ms = int(df["start"].iloc[0]) - 1
        time.sleep(pause_s)
    if not batches:
        return pd.DataFrame()
    full = pd.concat(batches[::-1], ignore_index=True)
    full = full.drop_duplicates(subset=["start"]).sort_values("start").reset_index(drop=True)
    if len(full) > target_bars:
        full = full.iloc[-target_bars:].reset_index(drop=True)
    return full


def fetch_top_linear_usdt_symbols(n: int) -> list[str]:
    """USDT linear perpetuals ranked by 24h turnover (quote)."""
    r = requests.get(f"{BYBIT}/v5/market/tickers", params={"category": "linear"}, timeout=60)
    r.raise_for_status()
    rows = r.json().get("result", {}).get("list", [])
    scored: list[tuple[float, str]] = []
    for x in rows:
        sym = x.get("symbol") or ""
        if not sym.endswith("USDT"):
            continue
        try:
            turnover = float(x.get("turnover24h") or 0)
        except (TypeError, ValueError):
            turnover = 0.0
        scored.append((turnover, sym))
    scored.sort(key=lambda t: t[0], reverse=True)
    uniq: list[str] = []
    seen: set[str] = set()
    for _, sym in scored:
        if sym not in seen:
            seen.add(sym)
            uniq.append(sym)
        if len(uniq) >= n:
            break
    return uniq[:n]


# --- Historical edge vs baseline (exploratory; overlapping events) ------------------------------
SIGNAL_COLS_PRED = ("strong_long", "strong_short", "sf_long", "sf_short")


def _signal_fwd_metrics(
    close_a: np.ndarray,
    mask_sig: np.ndarray,
    mask_eval: np.ndarray,
    h: int,
    direction: str,
) -> dict[str, Any]:
    n = len(close_a)
    idx = np.where(mask_eval)[0]
    idx = idx[idx + h < n]
    if len(idx) == 0:
        return {"n": 0, "mean_fwd_pct": None, "hit": None, "mean_all_pct": None, "edge_vs_all_pct": None}
    fwd_pct = (close_a[idx + h] / close_a[idx] - 1.0) * 100.0
    all_mean = round(float(np.mean(fwd_pct)), 5)
    sig_idx = idx[mask_sig[idx]]
    if len(sig_idx) == 0:
        return {"n": 0, "mean_fwd_pct": None, "hit": None, "mean_all_pct": all_mean, "edge_vs_all_pct": None}
    sig_fwd = (close_a[sig_idx + h] / close_a[sig_idx] - 1.0) * 100.0
    mean_sig = float(np.mean(sig_fwd))
    if direction == "long":
        hit = float(np.mean(sig_fwd > 0))
    else:
        hit = float(np.mean(sig_fwd < 0))
    return {
        "n": int(len(sig_idx)),
        "mean_fwd_pct": round(mean_sig, 5),
        "hit": round(hit, 4),
        "mean_all_pct": round(all_mean, 5),
        "edge_vs_all_pct": round(mean_sig - all_mean, 5),
    }


def predictability_for_pair(
    df: pd.DataFrame,
    sig: pd.DataFrame,
    horizons: tuple[int, ...] = (12, 48, 288),
    warmup: int = 200,
) -> dict[str, Any]:
    close_a = df["close"].to_numpy(dtype=np.float64)
    max_h = max(horizons)
    n = len(close_a)
    eval_idx = np.arange(warmup, n - max_h)
    mask_eval = np.zeros(n, dtype=bool)
    mask_eval[eval_idx] = True

    out: dict[str, Any] = {}
    for col in SIGNAL_COLS_PRED:
        if col not in sig.columns:
            continue
        m = sig[col].to_numpy(dtype=bool)
        direction = "long" if "long" in col else "short"
        out[col] = {str(h): _signal_fwd_metrics(close_a, m, mask_eval, h, direction) for h in horizons}
    return out


def pair_makes_sense_flags(pred: dict[str, Any], primary_h: int = 48) -> dict[str, bool]:
    """Heuristic 'predictable' flags on primary horizon (default 48x5m = 4h forward)."""
    hkey = str(primary_h)
    rules = {
        "strong_long": (30, "long", 0.50),
        "strong_short": (25, "short", 0.50),
        "sf_long": (8, "long", 0.48),
        "sf_short": (8, "short", 0.48),
    }
    out: dict[str, bool] = {}
    for col, (min_n, direction, hit_min) in rules.items():
        block = pred.get(col, {}).get(hkey, {})
        n = block.get("n", 0)
        mean = block.get("mean_fwd_pct")
        edge = block.get("edge_vs_all_pct")
        hit = block.get("hit")
        if n < min_n or mean is None or edge is None or hit is None:
            out[col] = False
            continue
        if direction == "long":
            out[col] = bool(mean > 0 and edge > 0 and hit >= hit_min)
        else:
            out[col] = bool(mean < 0 and edge < 0 and hit >= hit_min)
    return out


def non_overlapping_event_indices(
    mask_full: np.ndarray,
    candidate_indices: np.ndarray,
    gap: int,
) -> np.ndarray:
    """Greedy de-overlap: earliest eligible signal, then next at least `gap` bars later."""
    hits = candidate_indices[mask_full[candidate_indices]]
    hits = np.sort(np.unique(hits))
    if len(hits) == 0:
        return np.array([], dtype=np.int64)
    picked: list[int] = []
    last = -10**9
    for i in hits:
        if i - last >= gap:
            picked.append(int(i))
            last = int(i)
    return np.array(picked, dtype=np.int64)


def tightened_signal_study(
    close_a: np.ndarray,
    mask_sig: np.ndarray,
    h: int,
    direction: str,
    warmup: int = 200,
    holdout_frac: float = 0.30,
    n_boot: int = 500,
    rng: np.random.Generator | None = None,
) -> dict[str, Any]:
    """
    Out-of-sample tail only + non-overlapping events (gap >= h) + bootstrap CI on signal mean fwd.
    Reduces overlapping-path inflation; still not a full trade sim.
    """
    rng = rng or np.random.default_rng(42)
    n = len(close_a)
    hold_start = int(n * (1 - holdout_frac))
    eval_low = max(warmup, hold_start)
    idx = np.arange(eval_low, n - h)
    if len(idx) < 20:
        return {"ok": False, "reason": "holdout_eval_too_short", "n": n, "hold_start": hold_start}

    all_fwd = (close_a[idx + h] / close_a[idx] - 1.0) * 100.0
    mean_all = float(np.mean(all_fwd))

    nom_idx = non_overlapping_event_indices(mask_sig, idx, gap=h)
    if len(nom_idx) == 0:
        return {
            "ok": True,
            "hold_start": hold_start,
            "n_eval_holdout": int(len(idx)),
            "n_nonoverlap": 0,
            "mean_all_holdout_pct": round(mean_all, 5),
            "mean_fwd_signal_nonoverlap_pct": None,
            "edge_vs_all_holdout_pct": None,
            "hit": None,
            "boot_ci_mean_signal_pct": None,
        }

    sig_fwd = (close_a[nom_idx + h] / close_a[nom_idx] - 1.0) * 100.0
    mean_sig = float(np.mean(sig_fwd))
    edge = mean_sig - mean_all
    if direction == "long":
        hit = float(np.mean(sig_fwd > 0))
    else:
        hit = float(np.mean(sig_fwd < 0))

    L = len(sig_fwd)
    boot_means: list[float] = []
    for _ in range(n_boot):
        sample = rng.choice(sig_fwd, size=L, replace=True)
        boot_means.append(float(np.mean(sample)))
    lo = float(np.percentile(boot_means, 2.5))
    hi = float(np.percentile(boot_means, 97.5))

    return {
        "ok": True,
        "hold_start": hold_start,
        "holdout_frac": holdout_frac,
        "n_eval_holdout": int(len(idx)),
        "n_nonoverlap": int(len(nom_idx)),
        "mean_all_holdout_pct": round(mean_all, 5),
        "mean_fwd_signal_nonoverlap_pct": round(mean_sig, 5),
        "edge_vs_all_holdout_pct": round(edge, 5),
        "hit": round(hit, 4),
        "boot_ci_mean_signal_pct": [round(lo, 5), round(hi, 5)],
    }


def pair_passes_tight(
    t: dict[str, Any],
    direction: str,
    min_nonoverlap: int,
    hit_min: float,
) -> bool:
    if not t.get("ok") or t.get("reason"):
        return False
    if t.get("n_nonoverlap", 0) < min_nonoverlap:
        return False
    mean_sig = t.get("mean_fwd_signal_nonoverlap_pct")
    edge = t.get("edge_vs_all_holdout_pct")
    hit = t.get("hit")
    ci = t.get("boot_ci_mean_signal_pct")
    if mean_sig is None or edge is None or hit is None or ci is None:
        return False
    lo, hi = ci[0], ci[1]
    if direction == "long":
        return bool(
            mean_sig > 0
            and edge > 0
            and hit >= hit_min
            and lo > 0
        )
    return bool(mean_sig < 0 and edge < 0 and hit >= hit_min and hi < 0)


TIGHT_RULES = {
    "strong_long": ("long", 20, 0.50),
    "strong_short": ("short", 15, 0.50),
    "sf_long": ("long", 10, 0.48),
    "sf_short": ("short", 8, 0.48),
}


def tight_pass_flags(tight_h48: dict[str, Any]) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for col, (direction, min_n, hit_m) in TIGHT_RULES.items():
        out[col] = pair_passes_tight(tight_h48.get(col, {}), direction, min_n, hit_m)
    return out


def predictability_tight_block(
    df: pd.DataFrame,
    sig: pd.DataFrame,
    primary_h: int = 48,
    holdout_frac: float = 0.30,
    n_boot: int = 500,
) -> dict[str, Any]:
    close_a = df["close"].to_numpy(dtype=np.float64)
    block: dict[str, Any] = {}
    for col in SIGNAL_COLS_PRED:
        if col not in sig.columns:
            continue
        m = sig[col].to_numpy(dtype=bool)
        direction = "long" if "long" in col else "short"
        block[col] = tightened_signal_study(
            close_a, m, h=primary_h, direction=direction, holdout_frac=holdout_frac, n_boot=n_boot
        )
    block["passes"] = tight_pass_flags(block)
    return block


def run_predictability_study(
    top_n: int = 25,
    target_bars: int = 8000,
    horizons: tuple[int, ...] = (12, 48, 288),
    symbols: list[str] | None = None,
    pause_s: float = 0.05,
) -> dict[str, Any]:
    """
    Download paginated 5m history, rank pairs by turnover if symbols not provided,
    measure forward returns after mechanical signals vs all-bar baseline.
    """
    now = datetime.now(timezone.utc)
    if symbols is not None:
        syms = []
        seen: set[str] = set()
        for s in symbols:
            u = s.strip().upper()
            if u and u not in seen:
                seen.add(u)
                syms.append(u)
    else:
        syms = fetch_top_linear_usdt_symbols(top_n)
    if not syms:
        raise ValueError("no symbols")
    syms = [s for s in syms if s != "BTCUSDT"]
    syms.insert(0, "BTCUSDT")
    if symbols is None:
        syms = syms[:top_n]

    btc = fetch_klines_paginated("BTCUSDT", "5", target_bars, pause_s=pause_s)
    if len(btc) < 500:
        raise ValueError(f"insufficient BTC history: {len(btc)} rows")
    btc_rsi_1h = resample_rsi(btc["close"], btc["date"], "1h", 14)
    btc_rsi_4h = resample_rsi(btc["close"], btc["date"], "4h", 14)

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    counts = {k: 0 for k in ("strong_long", "strong_short", "sf_long", "sf_short", "any_pair")}
    counts_tight = {k: 0 for k in ("strong_long", "strong_short", "sf_long", "sf_short", "any_pair")}

    for sym in syms:
        try:
            if sym == "BTCUSDT":
                df = populate_core(btc.copy(), None, None)
            else:
                raw = fetch_klines_paginated(sym, "5", target_bars, pause_s=pause_s)
                m = raw.merge(btc[["date"]], on="date", how="inner").reset_index(drop=True)
                if len(m) < 400:
                    errors.append({"symbol": sym, "error": f"merge_too_short_{len(m)}"})
                    continue
                r1 = pd.Series(btc_rsi_1h.values, index=btc["date"]).reindex(m["date"]).ffill().reset_index(drop=True)
                r4 = pd.Series(btc_rsi_4h.values, index=btc["date"]).reindex(m["date"]).ffill().reset_index(drop=True)
                df = populate_core(m, btc_rsi_1h=r1, btc_rsi_4h=r4)
            sig = per_bar_signal_frame(df)
            pred = predictability_for_pair(df, sig, horizons=horizons)
            sense = pair_makes_sense_flags(pred)
            tight_full = predictability_tight_block(df, sig, primary_h=48, holdout_frac=0.30, n_boot=500)
            passes_tight = tight_full.pop("passes")
            tight_h48 = tight_full
            rows.append(
                {
                    "symbol": sym,
                    "bars": len(df),
                    "predictability": pred,
                    "makes_sense": sense,
                    "tight_h48": tight_h48,
                    "passes_tight": passes_tight,
                }
            )
            if any(sense.values()):
                counts["any_pair"] += 1
                for k, v in sense.items():
                    if v:
                        counts[k] += 1
            if any(passes_tight.values()):
                counts_tight["any_pair"] += 1
                for k, v in passes_tight.items():
                    if v:
                        counts_tight[k] += 1
        except Exception as e:  # noqa: BLE001
            errors.append({"symbol": sym, "error": str(e)})

    return {
        "generated_utc": now.isoformat(timespec="seconds"),
        "source": BYBIT,
        "category": "linear",
        "interval_5m": True,
        "target_bars_requested": target_bars,
        "btc_bars_loaded": len(btc),
        "horizons_bars": list(horizons),
        "horizon_labels": {str(h): f"{h}x5m" for h in horizons},
        "warmup_skipped": 200,
        "tight_methodology": (
            "Tight pass (h=48): last 30% of bars only (holdout); signal events de-overlapped with gap>=h; "
            "edge = mean(nonoverlap signal fwd) - mean(all holdout fwd). "
            "Bootstrap 500x resamples nonoverlap signal forwards for 95% CI on mean; "
            "long requires CI_lo>0; short requires CI_hi<0; plus min nonoverlap n and hit rate."
        ),
        "pairs_attempted": len(syms),
        "pairs_ok": len(rows),
        "methodology": (
            "Loose (in-sample): forward % = (close[t+h]/close[t]-1)*100; eval skips first 200 and last max(h); "
            "overlapping signal bars; edge vs all eval bars. makes_sense @48: min n + mean/edge/hit. "
            "Not a portfolio backtest; see tight_methodology for stricter view."
        ),
        "pass_counts": counts,
        "pass_counts_tight": counts_tight,
        "how_many_make_sense": {
            "pairs_with_any_signal_type_sensible": counts["any_pair"],
            "of_pairs_ok": len(rows),
            "fraction": round(counts["any_pair"] / max(len(rows), 1), 4),
        },
        "how_many_pass_tight": {
            "pairs_with_any_tight_pass": counts_tight["any_pair"],
            "of_pairs_ok": len(rows),
            "fraction": round(counts_tight["any_pair"] / max(len(rows), 1), 4),
        },
        "details": rows,
        "errors": errors,
    }


def compact_predictability_report(study: dict[str, Any]) -> list[dict[str, Any]]:
    """Smaller structs for printing / MCP previews."""
    out = []
    for d in study.get("details", []):
        sym = d["symbol"]
        sense = d["makes_sense"]
        snap = {
            "symbol": sym,
            "bars": d["bars"],
            "makes_sense": sense,
            "passes_tight": d.get("passes_tight", {}),
        }
        p48 = {k: d["predictability"].get(k, {}).get("48") for k in SIGNAL_COLS_PRED if k in d["predictability"]}
        snap["at_48_bars"] = p48
        th = d.get("tight_h48") or {}
        snap["tight_48_nonoverlap_n"] = {k: th.get(k, {}).get("n_nonoverlap") for k in SIGNAL_COLS_PRED if k in th}
        snap["tight_48_edge_holdout"] = {
            k: th.get(k, {}).get("edge_vs_all_holdout_pct") for k in SIGNAL_COLS_PRED if k in th
        }
        out.append(snap)
    return out


def resample_rsi(close: pd.Series, dates: pd.Series, rule: str, length: int = 14) -> pd.Series:
    tmp = pd.DataFrame({"close": close.values, "date": dates.values}).set_index("date")
    ohlc = tmp["close"].resample(rule).last().dropna()
    rsi = pta.rsi(ohlc, length=length)
    if rsi is None or len(ohlc) < length + 2:
        return pd.Series(50.0, index=close.index)
    aligned = rsi.reindex(tmp.index, method="ffill")
    return pd.Series(aligned.values, index=close.index)


def populate_core(df: pd.DataFrame, btc_rsi_1h: pd.Series | None, btc_rsi_4h: pd.Series | None) -> pd.DataFrame:
    out = df.copy()
    out["RSI_3"] = pta.rsi(out["close"], length=3)
    out["RSI_14"] = pta.rsi(out["close"], length=14)
    out["EMA_9"] = pta.ema(out["close"], length=9)
    out["EMA_26"] = pta.ema(out["close"], length=26)
    bb = pta.bbands(out["close"], length=20)
    if isinstance(bb, pd.DataFrame):
        bbl = [c for c in bb.columns if c.startswith("BBL_")]
        bbu = [c for c in bb.columns if c.startswith("BBU_")]
        out["BBL_20_2.0"] = bb[bbl[0]] if bbl else np.nan
        out["BBU_20_2.0"] = bb[bbu[0]] if bbu else np.nan
    aroon = pta.aroon(out["high"], out["low"], length=14)
    if isinstance(aroon, pd.DataFrame):
        out["AROONU_14"] = aroon["AROONU_14"]
        out["AROOND_14"] = aroon["AROOND_14"]
    st = pta.stochrsi(out["close"])
    if isinstance(st, pd.DataFrame):
        kcol = [c for c in st.columns if c.startswith("STOCHRSIk")][0]
        out["STOCHRSIk_14_14_3_3"] = st[kcol]
    out["CMF_20"] = pta.cmf(out["high"], out["low"], out["close"], out["volume"], length=20)
    out["volume_sma_25"] = pta.sma(out["volume"], length=25)
    out["RSI_14_1h"] = resample_rsi(out["close"], out["date"], "1h", 14)
    out["RSI_14_4h"] = resample_rsi(out["close"], out["date"], "4h", 14)
    if btc_rsi_1h is not None:
        out["btc_RSI_14_1h"] = btc_rsi_1h.values
    if btc_rsi_4h is not None:
        out["btc_RSI_14_4h"] = btc_rsi_4h.values
    out["sf_resistance"] = out["high"].shift(1).rolling(48).max()
    out["sf_support"] = out["low"].shift(1).rolling(48).min()
    out["sf_resistance_stable"] = out["sf_resistance"] == out["sf_resistance"].shift(1)
    out["sf_support_stable"] = out["sf_support"] == out["sf_support"].shift(1)
    out["EMA_120"] = pta.ema(out["close"], length=120)
    out["sf_volatility"] = (out["close"] - out["EMA_120"]).abs() / out["EMA_120"]
    out["sf_vol_filter"] = out["sf_volatility"] > 0.03
    out["sf_long"] = (
        (out["low"] <= out["sf_support"])
        & (out["close"] > out["sf_support"])
        & out["sf_support_stable"]
        & out["sf_vol_filter"]
    )
    out["sf_short"] = (
        (out["high"] >= out["sf_resistance"])
        & (out["close"] < out["sf_resistance"])
        & out["sf_resistance_stable"]
        & out["sf_vol_filter"]
    )
    return out


def ta_score_vectorized(df: pd.DataFrame) -> pd.Series:
    """Mirror SygnifStrategy._calculate_ta_score_vectorized (2026-04)."""
    score = pd.Series(50.0, index=df.index)
    rsi = df["RSI_14"].fillna(50.0)
    score += np.where(rsi < 30, 15, np.where(rsi < 40, 8, np.where(rsi > 70, -15, np.where(rsi > 60, -8, 0))))
    rsi3 = df["RSI_3"].fillna(50.0)
    score += np.where(rsi3 < 10, 10, np.where(rsi3 < 20, 5, np.where(rsi3 > 90, -10, np.where(rsi3 > 80, -5, 0))))
    ema_bull = df["EMA_9"] > df["EMA_26"]
    ema_cross = ema_bull & (df["EMA_9"].shift(1) <= df["EMA_26"].shift(1))
    score += np.where(ema_cross, 10, np.where(ema_bull, 7, -7))
    if "BBL_20_2.0" in df.columns and "BBU_20_2.0" in df.columns:
        score += np.where(df["close"] <= df["BBL_20_2.0"], 8, np.where(df["close"] >= df["BBU_20_2.0"], -8, 0))
    if "AROONU_14" in df.columns and "AROOND_14" in df.columns:
        aroonu = df["AROONU_14"].fillna(50)
        aroond = df["AROOND_14"].fillna(50)
        score += np.where((aroonu > 80) & (aroond < 30), 8, np.where((aroond > 80) & (aroonu < 30), -8, 0))
    if "STOCHRSIk_14_14_3_3" in df.columns:
        stoch = df["STOCHRSIk_14_14_3_3"].fillna(50)
        score += np.where(stoch < 20, 5, np.where(stoch > 80, -5, 0))
    cmf = df["CMF_20"].fillna(0)
    score += np.where(cmf > 0.15, 5, np.where(cmf < -0.15, -5, 0))
    if "RSI_14_1h" in df.columns and "RSI_14_4h" in df.columns:
        r1h = df["RSI_14_1h"].fillna(50)
        r4h = df["RSI_14_4h"].fillna(50)
        score += np.where((r1h < 35) & (r4h < 40), 5, np.where((r1h > 70) & (r4h > 65), -5, 0))
    if "btc_RSI_14_1h" in df.columns:
        btc_rsi = df["btc_RSI_14_1h"].fillna(50)
        score += np.where(btc_rsi < 30, -5, np.where(btc_rsi > 60, 3, 0))
    vol_ratio = np.where(df["volume_sma_25"] > 0, df["volume"] / df["volume_sma_25"], 1.0)
    score += np.where((vol_ratio > 1.5) & (score > 50), 3, np.where((vol_ratio > 1.5) & (score < 50), -3, 0))
    return score.clip(0, 100)


def ny_window_mask(dates: pd.Series) -> pd.Series:
    h = dates.dt.hour
    return h.isin(NY_UTC_HOURS)


def per_bar_signal_frame(df: pd.DataFrame) -> pd.DataFrame:
    ta = ta_score_vectorized(df)
    volx = df["volume"] / df["volume_sma_25"].replace(0, np.nan)
    volx = volx.fillna(1.0)
    if "btc_RSI_14_4h" in df.columns:
        short_ok = df["btc_RSI_14_4h"].fillna(50) <= 60.0
    else:
        short_ok = pd.Series(True, index=df.index)
    ny = ny_window_mask(df["date"])
    strong_long = (ta >= 65) & (volx > 1.2)
    strong_short = (ta <= 25) & short_ok
    amb_long = (ta >= 40) & (ta <= 64)
    amb_short = (ta >= 30) & (ta <= 60)
    return pd.DataFrame(
        {
            "ta": ta,
            "volx": volx,
            "ny": ny,
            "strong_long": strong_long,
            "strong_short": strong_short,
            "sf_long": df["sf_long"],
            "sf_short": df["sf_short"],
            "amb_long": amb_long,
            "amb_short": amb_short,
        },
        index=df.index,
    )


def _counter_stats(mask: pd.Series, ny: pd.Series) -> dict[str, int]:
    total = int(mask.sum())
    in_ny = int((mask & ny).sum())
    outside_ny = int((mask & ~ny).sum())
    return {
        "total_bars": total,
        "in_ny_window": in_ny,
        "outside_ny_window": outside_ny,
        "would_suppress_if_block_ny": in_ny,
    }


def summarize_scan(symbol: str, df: pd.DataFrame, sig: pd.DataFrame) -> dict[str, Any]:
    ny = sig["ny"]
    n = len(sig)
    out: dict[str, Any] = {
        "symbol": symbol,
        "bars": n,
        "ny_utc_hours": list(NY_UTC_HOURS),
        "ny_window_bars": int(ny.sum()),
        "ny_fraction": round(float(ny.mean()), 4) if n else 0.0,
        "strong_long": _counter_stats(sig["strong_long"], ny),
        "strong_short": _counter_stats(sig["strong_short"], ny),
        "sf_long": _counter_stats(sig["sf_long"], ny),
        "sf_short": _counter_stats(sig["sf_short"], ny),
        "ambiguous_long_bars": int(sig["amb_long"].sum()),
        "ambiguous_short_bars": int(sig["amb_short"].sum()),
    }
    # Overlap: mechanical signals on same bar (not mutually exclusive in live due to entry ordering)
    out["bars_strong_long_and_sf_long"] = int((sig["strong_long"] & sig["sf_long"]).sum())
    out["bars_strong_short_and_sf_short"] = int((sig["strong_short"] & sig["sf_short"]).sum())
    return out


def build_symbol_frame(
    sym: str,
    btc: pd.DataFrame,
    btc_rsi_1h: pd.Series,
    btc_rsi_4h: pd.Series,
) -> pd.DataFrame:
    if sym == "BTCUSDT":
        return populate_core(btc.copy(), None, None)
    raw = fetch_klines(sym, "5", len(btc))
    m = raw.merge(btc[["date"]], on="date", how="inner").reset_index(drop=True)
    if len(m) < 300:
        raise ValueError(f"merge {sym} with BTC too few rows: {len(m)}")
    r1 = pd.Series(btc_rsi_1h.values, index=btc["date"]).reindex(m["date"]).ffill().reset_index(drop=True)
    r4 = pd.Series(btc_rsi_4h.values, index=btc["date"]).reindex(m["date"]).ffill().reset_index(drop=True)
    return populate_core(m, btc_rsi_1h=r1, btc_rsi_4h=r4)


def run_network_scan(limit: int = 2000, symbols: tuple[str, ...] | None = None) -> dict[str, Any]:
    """
    Fetch fresh *linear* 5m data from Bybit and compute multi-bar scan + NY counterfactual.
    Purely mechanical (no Claude, no global crash protections).
    """
    if limit < 200 or limit > 5000:
        raise ValueError("limit must be between 200 and 5000")
    syms = symbols or DEFAULT_SYMS
    now = datetime.now(timezone.utc)
    btc = fetch_klines("BTCUSDT", "5", limit)
    btc_rsi_1h = resample_rsi(btc["close"], btc["date"], "1h", 14)
    btc_rsi_4h = resample_rsi(btc["close"], btc["date"], "4h", 14)

    per_sym: dict[str, Any] = {}
    snapshots: list[dict[str, Any]] = []

    for sym in syms:
        df = build_symbol_frame(sym, btc, btc_rsi_1h, btc_rsi_4h)
        sig = per_bar_signal_frame(df)
        per_sym[sym] = summarize_scan(sym, df, sig)
        last_i = len(df) - 1
        row = df.iloc[last_i]
        s = sig.iloc[last_i]
        snapshots.append(
            {
                "sym": sym.replace("USDT", ""),
                "close": round(float(row["close"]), 6),
                "ta": round(float(s["ta"]), 2),
                "volx": round(float(s["volx"]), 4),
                "strong_long": bool(s["strong_long"]),
                "strong_short": bool(s["strong_short"]),
                "sf_long": bool(s["sf_long"]),
                "sf_short": bool(s["sf_short"]),
                "amb_long": bool(s["amb_long"]),
                "amb_short": bool(s["amb_short"]),
                "ny_window": bool(s["ny"]),
            }
        )

    notes: list[str] = []
    tot_sl = sum(per_sym[s]["strong_long"]["total_bars"] for s in syms)
    tot_ss = sum(per_sym[s]["strong_short"]["total_bars"] for s in syms)
    if tot_sl == 0 and tot_ss == 0:
        notes.append("No strong_long / strong_short bars in window: regime is ambiguous or mean-reverting vs thresholds.")
    for sym in syms:
        sl = per_sym[sym]["strong_long"]
        if sl["total_bars"] > 0 and sl["in_ny_window"] > 0:
            notes.append(
                f"{sym}: {sl['in_ny_window']}/{sl['total_bars']} strong_long bars sit in NY UTC window "
                f"{NY_UTC_HOURS} (hypothetical block removes that subset)."
            )

    return {
        "generated_utc": now.isoformat(timespec="seconds"),
        "source": BYBIT,
        "category": "linear",
        "interval": "5",
        "limit": limit,
        "last_bar": snapshots,
        "scan": per_sym,
        "notes": notes,
    }


def json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def main() -> None:
    p = argparse.ArgumentParser(description="Sygnif rethink_sim: Bybit linear TA scan")
    p.add_argument("--scan", action="store_true", help="Multi-bar window + NY counterfactual")
    p.add_argument(
        "--predict",
        action="store_true",
        help="Paginated history + top pairs + forward-return edge vs baseline (network heavy)",
    )
    p.add_argument("--json", action="store_true", help="JSON only (with --scan or --predict)")
    p.add_argument("--limit", type=int, default=2000, help="5m candles per fetch (200-5000)")
    p.add_argument("--top", type=int, default=25, help="With --predict: top-N USDT pairs by turnover (default 25)")
    p.add_argument("--bars", type=int, default=8000, help="With --predict: target 5m bars per symbol (200-50000)")
    p.add_argument(
        "--horizons",
        type=str,
        default="12,48,288",
        help="Comma-separated forward horizons in 5m bars (e.g. 12,48,288 = 1h,4h,24h)",
    )
    p.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated linear symbols. Default for scan/snapshot: BTC,ETH,SOL. For --predict: omit to use --top turnover list.",
    )
    args = p.parse_args()
    if args.symbols:
        syms = tuple(s.strip().upper() for s in args.symbols.split(",") if s.strip())
    else:
        syms = DEFAULT_SYMS
    if args.predict:
        hs = tuple(int(x.strip()) for x in args.horizons.split(",") if x.strip())
        if args.bars < 500 or args.bars > 50000:
            print("--bars must be 500..50000", file=sys.stderr)
            sys.exit(2)
        sym_list = list(syms) if args.symbols else None
        data = json_safe(
            run_predictability_study(
                top_n=args.top, target_bars=args.bars, horizons=hs, symbols=sym_list
            )
        )
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(f"=== rethink_sim predictability  {data['generated_utc']} ===\n")
            hm = data["how_many_make_sense"]
            print(
                f"Pairs OK: {data['pairs_ok']}/{data['pairs_attempted']}  |  "
                f"loose makes_sense @48: {hm['pairs_with_any_signal_type_sensible']} "
                f"({100 * hm['fraction']:.1f}%)"
            )
            hm2 = data.get("how_many_pass_tight", {})
            print(
                f"TIGHT (30% holdout, nonoverlap>=h, bootstrap CI on signal mean): "
                f"{hm2.get('pairs_with_any_tight_pass', 0)}/{data['pairs_ok']} "
                f"({100 * float(hm2.get('fraction', 0)):.1f}%)"
            )
            print(f"BTC bars loaded: {data['btc_bars_loaded']}  |  loose: {data['pass_counts']}")
            print(f"pass_counts_tight: {data.get('pass_counts_tight', {})}")
            if data["errors"]:
                print("\nerrors:", json.dumps(data["errors"], indent=2))
            print("\ncompact (48-bar horizon):")
            print(json.dumps(compact_predictability_report(data), indent=2))
        return
    if args.scan:
        data = json_safe(run_network_scan(limit=args.limit, symbols=syms))
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(f"=== rethink_sim multi-bar scan  {data['generated_utc']} ===\n")
            print(json.dumps(data["scan"], indent=2))
            print("\n--- last bar ---")
            print(json.dumps(data["last_bar"], indent=2))
            if data["notes"]:
                print("\n--- notes ---")
                for n in data["notes"]:
                    print(n)
        return
    # default: snapshot table only (uses default limit)
    now = datetime.now(timezone.utc)
    print(f"=== rethink_sim snapshot  UTC {now.isoformat(timespec='minutes')} ===\n")
    data = json_safe(run_network_scan(limit=args.limit, symbols=syms))
    snap = pd.DataFrame(data["last_bar"])
    print(snap.to_string(index=False))
    for line in data["notes"]:
        print(f"\n* {line}")
    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        print("Bybit request failed:", e, file=sys.stderr)
        sys.exit(1)
