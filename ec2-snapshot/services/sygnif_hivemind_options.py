#!/usr/bin/env python3
"""SYGNIF option-implied "hivemind" probabilities → NeuroLinked.

Pulls the Bybit BTC option chain every HIVEMIND_POLL_SEC (default 300s),
computes risk-neutral probabilities Pr(BTC > K @ T) and Pr(BTC < K @ T)
from Black-Scholes (using each option's mark IV), and publishes a tight
summary to the NeuroLinked brain.

This replaces the dead truthcoin-dc hivemind layer with a real
crowd-sourced signal: option markets price these probabilities with
billions of dollars of implied conviction, every second of the day.

Output (one line per emit):
  SYGNIF_HIVEMIND_OPTIONS v1 src=bybit-options ts=... spot=80500
    exp=2026-05-06 dte_d=0.8 atm_iv=0.55 Pr_up5%=0.18 Pr_dn5%=0.34
    Pr_break_24h_hi=0.21 Pr_break_24h_lo=0.28
    exp=2026-05-09 dte_d=3.8 atm_iv=0.62 Pr_up10%=0.12 Pr_dn10%=0.22
    ...

Runs as systemd unit sygnif-hivemind-feed.service.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Snapshot path consumed by trader's entry gates (skew, IV-rank).
# Written atomically every cycle.
SNAPSHOT_PATH = Path(os.environ.get("HIVEMIND_SNAPSHOT_PATH",
                                       str(Path.home() / ".sygnif" / "hivemind-snapshot.json")))
SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sygnif_hivemind_options")

NL_URL = (os.environ.get("SYGNIF_NEUROLINKED_HOST_URL")
          or "http://127.0.0.1:8889").rstrip("/")
POLL_SEC = int(os.environ.get("HIVEMIND_POLL_SEC", "300"))
POST_TIMEOUT = int(os.environ.get("HIVEMIND_POST_TIMEOUT_SEC", "30"))
HTTP_TIMEOUT = int(os.environ.get("HIVEMIND_HTTP_TIMEOUT_SEC", "15"))
BASE_PUBLIC = os.environ.get("BYBIT_PUBLIC_BASE", "https://api.bybit.com").rstrip("/")
BASE_COIN = os.environ.get("HIVEMIND_BASE_COIN", "BTC")
PERP_SYMBOL = f"{BASE_COIN}USDT"
MAX_DTE_DAYS = float(os.environ.get("HIVEMIND_MAX_DTE_D", "30"))
MAX_EXPIRIES = int(os.environ.get("HIVEMIND_MAX_EXPIRIES", "4"))

# --- math helpers ---


def _norm_cdf(x: float) -> float:
    """Standard normal CDF. Accurate to ~1e-7 via math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_prob_above(spot: float, strike: float, iv: float, dte_yr: float) -> float:
    """Risk-neutral Pr(S_T > K) using Black-Scholes (no drift, no dividends).

    For BTC options on Bybit (USDT-margined, expiring at 08:00 UTC),
    we use the spot/forward as F=spot and ignore funding (small over <30d).
    """
    if iv <= 0 or dte_yr <= 0 or strike <= 0 or spot <= 0:
        return float("nan")
    sigT = iv * math.sqrt(dte_yr)
    d2 = (math.log(spot / strike) - 0.5 * sigT * sigT) / sigT
    return _norm_cdf(d2)


def _bs_prob_below(spot: float, strike: float, iv: float, dte_yr: float) -> float:
    p_up = _bs_prob_above(spot, strike, iv, dte_yr)
    return 1.0 - p_up if not math.isnan(p_up) else float("nan")


# --- Bybit fetchers ---


def _get(path: str, params: dict) -> dict:
    q = urllib.parse.urlencode(params)
    url = f"{BASE_PUBLIC}{path}?{q}"
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read())


def _fetch_spot_and_24h() -> tuple[float, float, float] | None:
    try:
        r = _get("/v5/market/tickers", {"category": "linear", "symbol": PERP_SYMBOL})
        t = r["result"]["list"][0]
        return (float(t["lastPrice"]), float(t["highPrice24h"]), float(t["lowPrice24h"]))
    except Exception as e:
        log.warning("spot fetch failed: %s", e)
        return None


def _fetch_option_chain() -> list[dict]:
    """Returns list of dicts: {symbol, expiry_ms, strike, kind, iv, mark, bid, ask}."""
    try:
        r = _get("/v5/market/tickers", {"category": "option", "baseCoin": BASE_COIN})
        rows = r["result"]["list"]
    except Exception as e:
        log.warning("option chain fetch failed: %s", e)
        return []
    out = []
    for o in rows:
        sym = o.get("symbol", "")
        # Bybit option symbol shape: BTC-6MAY26-79000-C-USDT
        try:
            parts = sym.split("-")
            if len(parts) < 4:
                continue
            expiry_str = parts[1]
            strike = float(parts[2])
            kind = parts[3]
            if kind not in ("C", "P"):
                continue
            # Bybit expiry format e.g. 6MAY26 → 2026-05-06 08:00 UTC
            dt = datetime.strptime(expiry_str, "%d%b%y").replace(
                hour=8, minute=0, tzinfo=timezone.utc)
            expiry_ms = int(dt.timestamp() * 1000)
            iv_raw = o.get("markIv") or o.get("askIv") or o.get("bidIv") or "0"
            iv = float(iv_raw) if iv_raw else 0.0
            if iv > 5.0:  # Some Bybit feeds return % not decimal — normalise
                iv = iv / 100.0
            out.append({
                "symbol": sym,
                "expiry_ms": expiry_ms,
                "strike": strike,
                "kind": kind,
                "iv": iv,
                "mark": float(o.get("markPrice", "0") or 0),
                "bid": float(o.get("bid1Price", "0") or 0),
                "ask": float(o.get("ask1Price", "0") or 0),
                "delta": float(o.get("delta", "0") or 0),
            })
        except Exception:
            continue
    return out


# --- per-expiry probability summary ---


def _atm_iv_for_expiry(rows: list[dict], spot: float) -> float | None:
    """Return ATM mark IV (closest strike to spot, prefer call if both exist)."""
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: abs(r["strike"] - spot))
    # take the closest 2 strikes, average their call+put IV
    pool = []
    for r in rows[:4]:
        if r["iv"] and r["iv"] > 0.05:
            pool.append(r["iv"])
    if not pool:
        return None
    return sum(pool) / len(pool)


def _25delta_skew(rows: list[dict]) -> float | None:
    """25-delta skew = put_25Δ_IV − call_25Δ_IV. Positive = put skew (fear).

    For each expiry, finds the put with delta closest to -0.25 and the call
    with delta closest to +0.25, returns their IV difference. Defensive
    against missing delta fields by falling back to None.
    """
    puts = [r for r in rows if r["kind"] == "P" and r.get("delta") and r.get("iv")]
    calls = [r for r in rows if r["kind"] == "C" and r.get("delta") and r.get("iv")]
    if not puts or not calls:
        return None
    p25 = min(puts, key=lambda r: abs(r["delta"] - (-0.25)))
    c25 = min(calls, key=lambda r: abs(r["delta"] - 0.25))
    if abs(p25["delta"] - (-0.25)) > 0.15 or abs(c25["delta"] - 0.25) > 0.15:
        return None  # no decent 25Δ candidate
    return p25["iv"] - c25["iv"]


def _bs_greeks_totals(rows: list[dict], spot: float, dte_yr: float) -> dict:
    """Aggregate Greeks per expiry: total open interest of vega/theta is
    not directly available from /v5/market/tickers (no OI per row), so we
    sum the GREEKS THEMSELVES across strikes within ±15% of spot. This
    gives a 'positioning shape' metric — high vega total = expiry has lots
    of strikes near spot (gamma cluster), high theta total = lots of
    decay-pumping options live at this expiry.
    """
    band_lo = spot * 0.85
    band_hi = spot * 1.15
    in_band = [r for r in rows if band_lo <= r["strike"] <= band_hi
                and r.get("iv") and r["iv"] > 0]
    if not in_band:
        return {"vega_band": 0.0, "theta_band_per_d": 0.0,
                "n_in_band": 0, "iv_min": 0.0, "iv_max": 0.0, "iv_p50": 0.0}
    # Per-row Black-Scholes vega + theta (no Bybit-supplied per-row Greeks
    # in tickers payload, only delta+gamma+iv). We compute simple BS
    # approximations using ATM-IV and time. Daily theta = annualised / 365.
    vegas = []
    thetas = []
    ivs = []
    sqrtT = math.sqrt(max(dte_yr, 1e-6))
    for r in in_band:
        K, iv = r["strike"], r["iv"]
        ivs.append(iv)
        d1 = (math.log(spot / K) + 0.5 * iv * iv * dte_yr) / (iv * sqrtT)
        # vega per 1.0 IV move (= per 100 IV-pp move /100). Use $-vega.
        vega = spot * sqrtT * math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
        vegas.append(vega)
        # theta — daily. Approximation ignores rate term.
        theta = -(spot * iv * math.exp(-0.5 * d1 * d1)
                   / (2 * sqrtT * math.sqrt(2 * math.pi))) / 365.0
        thetas.append(theta)
    ivs.sort()
    return {
        "vega_band_total": round(sum(vegas), 1),
        "theta_band_total_per_d": round(sum(thetas), 1),
        "n_in_band": len(in_band),
        "iv_min": round(ivs[0], 3),
        "iv_max": round(ivs[-1], 3),
        "iv_p50": round(ivs[len(ivs) // 2], 3),
    }


def _summarize_expiry(rows: list[dict], spot: float, hi_24h: float,
                      lo_24h: float, now_ms: int) -> dict | None:
    if not rows:
        return None
    expiry_ms = rows[0]["expiry_ms"]
    dte_d = max((expiry_ms - now_ms) / 86_400_000.0, 0.001)
    dte_yr = dte_d / 365.0
    atm_iv = _atm_iv_for_expiry(rows, spot)
    if not atm_iv:
        return None
    K_up5 = spot * 1.05
    K_up10 = spot * 1.10
    K_dn5 = spot * 0.95
    K_dn10 = spot * 0.90
    skew_25d = _25delta_skew(rows)
    greeks = _bs_greeks_totals(rows, spot, dte_yr)
    return {
        "expiry_ms": expiry_ms,
        "expiry_str": datetime.fromtimestamp(expiry_ms / 1000, timezone.utc).strftime("%Y-%m-%d"),
        "dte_d": round(dte_d, 2),
        "atm_iv": round(atm_iv, 3),
        "skew_25d": round(skew_25d, 3) if skew_25d is not None else None,
        "Pr_up5pct": round(_bs_prob_above(spot, K_up5, atm_iv, dte_yr), 3),
        "Pr_up10pct": round(_bs_prob_above(spot, K_up10, atm_iv, dte_yr), 3),
        "Pr_dn5pct": round(_bs_prob_below(spot, K_dn5, atm_iv, dte_yr), 3),
        "Pr_dn10pct": round(_bs_prob_below(spot, K_dn10, atm_iv, dte_yr), 3),
        "Pr_break_24h_hi": round(_bs_prob_above(spot, hi_24h, atm_iv, dte_yr), 3),
        "Pr_break_24h_lo": round(_bs_prob_below(spot, lo_24h, atm_iv, dte_yr), 3),
        "n_strikes": len(rows),
        **greeks,
    }


# --- NL post ---


def _post_nl(text: str) -> bool:
    body = json.dumps({"text": text, "skip_claude_bridge": True}).encode("utf-8")
    req = urllib.request.Request(f"{NL_URL}/api/input/text", data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=POST_TIMEOUT) as r:
            return r.status == 200
    except Exception as e:
        log.warning("NL post failed: %s", e)
        return False


# --- main loop ---


def _format_payload(spot: float, hi: float, lo: float, summaries: list[dict]) -> str:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = (f"SYGNIF_HIVEMIND_OPTIONS v2 src=bybit-options ts={ts} "
            f"spot={spot:.0f} hi24h={hi:.0f} lo24h={lo:.0f} expiries={len(summaries)}")
    lines = [head]
    for s in summaries:
        skew = s.get("skew_25d")
        skew_s = f"{skew:+.3f}" if skew is not None else "n/a"
        lines.append(
            f"  exp={s['expiry_str']} dte_d={s['dte_d']} atm_iv={s['atm_iv']} "
            f"skew_25d={skew_s} "
            f"Pr_up5%={s['Pr_up5pct']} Pr_up10%={s['Pr_up10pct']} "
            f"Pr_dn5%={s['Pr_dn5pct']} Pr_dn10%={s['Pr_dn10pct']} "
            f"Pr_break_24h_hi={s['Pr_break_24h_hi']} Pr_break_24h_lo={s['Pr_break_24h_lo']} "
            f"vega_band={s.get('vega_band_total', 0)} "
            f"theta_band_per_d={s.get('theta_band_total_per_d', 0)} "
            f"iv_p50={s.get('iv_p50', 0)} iv_range=[{s.get('iv_min', 0)},{s.get('iv_max', 0)}] "
            f"n_strikes={s['n_strikes']} n_in_band={s.get('n_in_band', 0)}"
        )
    return "\n".join(lines)


def cycle() -> bool:
    px = _fetch_spot_and_24h()
    if not px:
        return False
    spot, hi, lo = px
    chain = _fetch_option_chain()
    if not chain:
        log.warning("empty option chain")
        return False
    now_ms = int(time.time() * 1000)
    # group by expiry
    by_exp: dict[int, list[dict]] = {}
    for r in chain:
        ms = r["expiry_ms"]
        dte_d = (ms - now_ms) / 86_400_000.0
        if dte_d <= 0 or dte_d > MAX_DTE_DAYS:
            continue
        by_exp.setdefault(ms, []).append(r)
    if not by_exp:
        log.warning("no expiries within %sd window", MAX_DTE_DAYS)
        return False
    expiries_sorted = sorted(by_exp.keys())[:MAX_EXPIRIES]
    summaries = []
    for em in expiries_sorted:
        s = _summarize_expiry(by_exp[em], spot, hi, lo, now_ms)
        if s:
            summaries.append(s)
    if not summaries:
        log.warning("no usable summaries")
        return False
    text = _format_payload(spot, hi, lo, summaries)
    ok = _post_nl(text)
    # Write snapshot for trader gates (skew, IV-rank, term structure)
    try:
        snap_payload = {
            "ts_ms": int(time.time() * 1000),
            "spot": spot,
            "hi24h": hi,
            "lo24h": lo,
            "expiries": summaries,  # list of dicts incl. skew_25d, atm_iv, etc
        }
        tmp = SNAPSHOT_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snap_payload, indent=2))
        tmp.replace(SNAPSHOT_PATH)
    except Exception as e:
        log.warning("snapshot write err: %s", e)
    log.info("emit ok=%s spot=%.0f expiries=%d head=%s",
             ok, spot, len(summaries),
             summaries[0]["expiry_str"] if summaries else "-")
    return ok


def main() -> int:
    log.info("sygnif-hivemind-options starting; poll=%ds nl=%s base=%s",
             POLL_SEC, NL_URL, BASE_PUBLIC)
    while True:
        t0 = time.time()
        try:
            cycle()
        except Exception as e:
            log.exception("cycle failed: %s", e)
        elapsed = time.time() - t0
        time.sleep(max(POLL_SEC - elapsed, 30))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
