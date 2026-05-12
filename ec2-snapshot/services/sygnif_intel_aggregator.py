#!/usr/bin/env python3
"""sygnif_intel_aggregator.py — Pre-digest all intelligence into one tiny file.

Reads every state file we maintain, compresses the signal into a small
intel_summary.json that fast-reactor (and any future low-latency daemon)
can read in sub-millisecond time.

Output: /var/lib/sygnif/intel_summary.json  (< 4 KB, mmap-friendly)

Cadence: every 30s. The on-chain layer moves over minutes-hours, so 30s
staleness is fine.

The summary contains:
  - scores: bullish/bearish 0-100 + net_directional
  - vetoes_short / vetoes_long: lists of named blockers
  - boosts_short / boosts_long: confluence boosters
  - active_signals: raw numerical context for reference

How fast-reactor uses it:
  before fire_trade(), check if intel_summary vetoes our direction.
  if vetoes empty: allow with confidence modifier from boosts.
  if vetoed: skip with reason.

The aggregator does ALL the math. Fast-reactor just reads + checks list
membership. This keeps fast-reactor's hot path under 1ms.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import signal
import sys
import time
from collections import defaultdict

OUT_FILE     = pathlib.Path("/var/lib/sygnif/intel_summary.json")
POLL_S       = float(os.environ.get("SYGNIF_INTEL_POLL_S", "30"))

# ---------------------------------------------------------------------------
# Configurable thresholds
# ---------------------------------------------------------------------------
COLD_ACCUM_WINDOW_S        = 4 * 3600   # cold accumulation events in last 4h
COLD_ACCUM_THRESHOLD_N     = 3
LTH_SPEND_WINDOW_S         = 12 * 3600
LTH_SPEND_THRESHOLD_N      = 1
TRON_USDT_WINDOW_S         = 6 * 3600
TRON_USDT_THRESHOLD_USD    = 100_000_000      # $100M
ETH_USDT_WINDOW_S          = 6 * 3600
ETH_USDT_THRESHOLD_USD     = 100_000_000
DORMANCY_WINDOW_S          = 12 * 3600
PREMIUM_CB_BEARISH_BPS     = -10              # cb→bn ≤ -10bps = US selling
PREMIUM_CB_BULLISH_BPS     = +10
BASIS_BACKWARDATION_BPS    = +5               # bn→bb ≥ +5 = perp discount = bullish
BASIS_CONTANGO_BPS         = -5
DOMINANCE_DELTA_PP         = 0.5              # 0.5pp shift in 4h
LIQ_CLUSTER_WINDOW_S       = 30 * 60          # liq clusters in last 30 min
ML_BULLISH_MIN             = 1
ML_BEARISH_MIN             = -1

_running = True


# ---------------------------------------------------------------------------
# Helpers — safe state-file readers
# ---------------------------------------------------------------------------
def jload(p: str) -> dict:
    try:
        return json.loads(pathlib.Path(p).read_text())
    except Exception:
        return {}


def in_window(ts_s: float, window_s: float, now: float) -> bool:
    return ts_s and (now - ts_s) <= window_s



# ---------------------------------------------------------------------------
# Extended helpers — ChartInspect macro + swarm topic readers
# ---------------------------------------------------------------------------
import urllib.request, sqlite3
DB_PATH = "/var/lib/sygnif/swarm.db"

CHARTINSPECT_KEY = os.environ.get("SYGNIF_CHARTINSPECT_KEY", "")
_macro_cache = {"ts": 0, "data": {}}
_MACRO_REFRESH_S = 600  # refresh VIX/DXY/MSTR every 10min

def _ci_get(endpoint: str, timeout: int = 6):
    if not CHARTINSPECT_KEY:
        return None
    try:
        req = urllib.request.Request(
            f"https://chartinspect.com/api/v1/{endpoint}",
            headers={"X-API-KEY": CHARTINSPECT_KEY,
                     "User-Agent": "sygnif-intel-aggregator/1.0"})
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception:
        return None


def macro_indicators() -> dict:
    """Pull VIX/DXY/MSTR purchases — cached 10min."""
    now = time.time()
    if now - _macro_cache["ts"] < _MACRO_REFRESH_S and _macro_cache["data"]:
        return _macro_cache["data"]
    out = {}
    for ind in ("vix", "dxy", "gold"):
        d = _ci_get(f"economic/{ind}?limit=2")
        if d and d.get("data"):
            rows = d["data"]
            latest = rows[-1] if rows else {}
            out[ind] = {
                "value":   latest.get("value"),
                "change":  latest.get("change"),
                "change_pct": latest.get("changePercent"),
                "date":    latest.get("date"),
            }
    # MSTR purchases — recent 5
    d = _ci_get("economic/mstr-purchases?limit=5")
    if d and d.get("data"):
        out["mstr_recent"] = [
            {"date": p.get("date"), "btc": p.get("btc_count"),
             "avg_price": p.get("average_price"),
             "total_usd": p.get("total_purchase_price")}
            for p in d["data"][-3:]
        ]
    _macro_cache["ts"] = now
    _macro_cache["data"] = out
    return out


def swarm_recent_topic(topic: str, since_s: int, limit: int = 50) -> list:
    """Read latest swarm rows for a topic. Cached at caller level if needed."""
    try:
        c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        rows = c.execute(
            "SELECT created, content, meta FROM swarm_entries "
            "WHERE topic = ? AND created > ? "
            "ORDER BY created DESC LIMIT ?",
            (topic, int(time.time()) - since_s, limit)).fetchall()
        c.close()
        return rows
    except Exception:
        return []


def brain_live_state() -> dict:
    """Read NeuroLinked live.json — current neuromodulator levels."""
    try:
        return json.loads(pathlib.Path(
            "/home/ubuntu/SYGNIF/third_party/neurolinked/brain_state/live.json"
        ).read_text())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------
def build_summary() -> dict:
    now = time.time()
    score_bull = 0
    score_bear = 0
    vetoes_short = []   # reasons to BLOCK shorts (bullish signals)
    vetoes_long = []    # reasons to BLOCK longs (bearish signals)
    boosts_short = []   # reasons short is HIGH conviction
    boosts_long = []    # reasons long is HIGH conviction
    signals = {}        # raw context

    # ── 1. BTC on-chain (chain_state.json) ────────────────────────────────
    cs = jload("/var/lib/sygnif/chain_state.json")
    events = cs.get("recent_events", []) or []
    cold_in_4h = lth_in_12h = dormancy_in_12h = 0
    whale_net_btc_24h = 0
    deposit_btc_24h = 0
    withdrawal_btc_24h = 0
    for e in events:
        ts = e.get("ts", 0)
        if not ts: continue
        flags = e.get("flags") or []
        cat = e.get("category", "")
        v_btc = e.get("value_btc", 0) or 0
        if in_window(ts, 86400, now):
            if cat == "WITHDRAWAL_FROM_EXCHANGE":  withdrawal_btc_24h += v_btc
            elif cat == "DEPOSIT_TO_EXCHANGE":     deposit_btc_24h += v_btc
            elif cat == "ACCUMULATION_TO_COLD":    withdrawal_btc_24h += v_btc
        if in_window(ts, COLD_ACCUM_WINDOW_S, now):
            if cat == "ACCUMULATION_TO_COLD": cold_in_4h += 1
        if in_window(ts, LTH_SPEND_WINDOW_S, now):
            if "LTH_SPEND_HEAVY" in flags: lth_in_12h += 1
        if in_window(ts, DORMANCY_WINDOW_S, now):
            if "DORMANCY_BREAK_5YR" in flags: dormancy_in_12h += 1
    whale_net_btc_24h = withdrawal_btc_24h - deposit_btc_24h
    signals["btc_whale_net_24h_btc"] = round(whale_net_btc_24h, 1)
    signals["cold_accum_4h"]   = cold_in_4h
    signals["lth_spend_12h"]   = lth_in_12h
    signals["dormancy_12h"]    = dormancy_in_12h

    if cold_in_4h >= COLD_ACCUM_THRESHOLD_N:
        vetoes_short.append(f"cold_accum_4h:{cold_in_4h}")
        boosts_long.append(f"cold_accum_4h:{cold_in_4h}")
        score_bull += 20
    if lth_in_12h >= LTH_SPEND_THRESHOLD_N:
        vetoes_short.append(f"lth_spend_12h:{lth_in_12h}")
        score_bull += 15
    if dormancy_in_12h >= 1:
        vetoes_long.append(f"dormancy_break:{dormancy_in_12h}")
        score_bear += 15

    if whale_net_btc_24h > 1000:
        boosts_long.append(f"whale_net_24h:{whale_net_btc_24h:+.0f}BTC")
        score_bull += 15
    elif whale_net_btc_24h < -1000:
        boosts_short.append(f"whale_net_24h:{whale_net_btc_24h:+.0f}BTC")
        score_bear += 15

    # ── 2. EVM USDT/USDC mints (evm_state.json) ──────────────────────────
    es = jload("/var/lib/sygnif/evm_state.json")
    eth_usdt_in_6h = sum(m.get("amount_usd", 0) for m in (es.get("recent_mints") or [])
                          if m.get("token") == "USDT"
                          and in_window(m.get("ts", 0), ETH_USDT_WINDOW_S, now))
    eth_usdc_in_6h = sum(m.get("amount_usd", 0) for m in (es.get("recent_mints") or [])
                          if m.get("token") == "USDC"
                          and in_window(m.get("ts", 0), ETH_USDT_WINDOW_S, now))
    signals["eth_usdt_6h_usd"] = round(eth_usdt_in_6h)
    signals["eth_usdc_6h_usd"] = round(eth_usdc_in_6h)
    if eth_usdt_in_6h >= ETH_USDT_THRESHOLD_USD:
        vetoes_short.append(f"eth_usdt_mint_6h:{eth_usdt_in_6h/1e6:.0f}M")
        score_bull += 15

    # ── 3. Tron USDT mints (tron_state.json) ──────────────────────────────
    ts_ = jload("/var/lib/sygnif/tron_state.json")
    tron_usdt_in_6h = sum(m.get("amount_usd", 0) for m in (ts_.get("recent_mints") or [])
                           if m.get("token") == "USDT"
                           and in_window(m.get("ts", 0), TRON_USDT_WINDOW_S, now))
    signals["tron_usdt_6h_usd"] = round(tron_usdt_in_6h)
    if tron_usdt_in_6h >= TRON_USDT_THRESHOLD_USD:
        vetoes_short.append(f"tron_usdt_mint_6h:{tron_usdt_in_6h/1e6:.0f}M")
        boosts_long.append(f"tron_usdt_mint_6h:{tron_usdt_in_6h/1e6:.0f}M")
        score_bull += 20

    # ── 4. Multi-exchange liquidations (xchg_liq_state.json) ──────────────
    xls = jload("/var/lib/sygnif/xchg_liq_state.json")
    clusters = xls.get("recent_clusters") or []
    recent_clusters = [c for c in clusters
                       if in_window(c.get("ts", 0), LIQ_CLUSTER_WINDOW_S, now)]
    cluster_short = sum(1 for c in recent_clusters if c.get("side") == "SHORT_LIQ")
    cluster_long  = sum(1 for c in recent_clusters if c.get("side") == "LONG_LIQ")
    signals["liq_clusters_30m"] = {"short_liq": cluster_short, "long_liq": cluster_long}
    # SHORT_LIQ = shorts being liquidated = price moving UP (bullish for longs)
    # LONG_LIQ  = longs being liquidated = price moving DOWN (bullish for shorts)
    if cluster_short >= 1:
        boosts_long.append(f"liq_cluster_short:{cluster_short}")
        score_bull += 10
    if cluster_long >= 1:
        boosts_short.append(f"liq_cluster_long:{cluster_long}")
        score_bear += 10

    # ── 5. Market premium/basis (market_premium.json) ─────────────────────
    mp = jload("/var/lib/sygnif/market_premium.json")
    hist = mp.get("history") or []
    if hist:
        latest = hist[-1]
        cb_bn = latest.get("cb_bn_bps", 0) or 0
        bn_bb = latest.get("bn_bb_bps", 0) or 0
        signals["premium_cb_bn_bps"] = cb_bn
        signals["basis_bn_bb_bps"]   = bn_bb
        if cb_bn <= PREMIUM_CB_BEARISH_BPS:
            vetoes_long.append(f"cb_premium_negative:{cb_bn:.1f}bps")
            score_bear += 10
        if cb_bn >= PREMIUM_CB_BULLISH_BPS:
            vetoes_short.append(f"cb_premium_positive:{cb_bn:.1f}bps")
            score_bull += 10
        if bn_bb >= BASIS_BACKWARDATION_BPS:
            # Perp discount = backwardation = bullish (longs paid)
            boosts_long.append(f"backwardation:{bn_bb:.1f}bps")
            vetoes_short.append(f"backwardation:{bn_bb:.1f}bps")
            score_bull += 8
        elif bn_bb <= BASIS_CONTANGO_BPS:
            # Perp premium = contango = bearish (longs paying)
            boosts_short.append(f"contango:{bn_bb:.1f}bps")
            vetoes_long.append(f"contango:{bn_bb:.1f}bps")
            score_bear += 8

    # ── 6. Ecosystem — BTC dominance + stablecoin caps (ecosystem) ────────
    eco = jload("/var/lib/sygnif/ecosystem_state.json")
    dh = eco.get("dominance_history") or []
    if len(dh) >= 2:
        latest = dh[-1]
        # Find a snapshot ~4h ago for delta calculation
        prev = next((d for d in reversed(dh[:-1])
                      if (latest.get("ts", 0) - d.get("ts", 0)) >= 4*3600), None)
        signals["btc_dom_pct"] = latest.get("btc_dom")
        if prev:
            dom_delta = latest.get("btc_dom", 0) - prev.get("btc_dom", 0)
            signals["btc_dom_delta_4h_pp"] = round(dom_delta, 3)
            if dom_delta >= DOMINANCE_DELTA_PP:
                # BTC dominance rising = alts losing = BTC strength
                boosts_long.append(f"dom_rising:+{dom_delta:.2f}pp")
                score_bull += 5
            elif dom_delta <= -DOMINANCE_DELTA_PP:
                # Dominance falling = alts gaining = BTC weakness
                boosts_short.append(f"dom_falling:{dom_delta:+.2f}pp")
                score_bear += 5

    # ── 7. ML predict-loop (forecast topic via swarm) ─────────────────────
    # Lightweight: read latest from neurolinked_swarm_channel.json
    nl = jload("/home/ubuntu/SYGNIF/prediction_agent/neurolinked_swarm_channel.json")
    pl = (nl.get("extra") or {}).get("predict_loop") or {}
    ml_signal = pl.get("enhanced") or pl.get("target_side") or "unknown"
    signals["ml_predict_signal"] = ml_signal
    if ml_signal == "STRONG_BEARISH":
        vetoes_long.append("ml_strong_bearish")
        boosts_short.append("ml_strong_bearish")
        score_bear += 20
    elif ml_signal == "STRONG_BULLISH":
        vetoes_short.append("ml_strong_bullish")
        boosts_long.append("ml_strong_bullish")
        score_bull += 20


    # ── A. Chain mempool (pending whale txs) ──────────────────────────────
    cm = jload("/var/lib/sygnif/chain_mempool.json")
    pending_mempool = list(cm.values()) if isinstance(cm, dict) else []
    pending_unconfirmed = [w for w in pending_mempool
                            if isinstance(w, dict) and not w.get("confirmed", False)]
    signals["mempool_pending_whales"] = len(pending_unconfirmed)
    signals["mempool_pending_btc"] = round(sum(w.get("value_btc", 0)
                                                  for w in pending_unconfirmed), 1)
    if len(pending_unconfirmed) >= 3:
        boosts_long.append(f"mempool_pending:{len(pending_unconfirmed)}")
        score_bull += 5

    # ── B. EVM-extras (DEX large swaps + bridge flows) ────────────────────
    evex = jload("/var/lib/sygnif/evm_extras_state.json")
    dex_24h = sum(1 for e in (evex.get("recent_dex") or [])
                  if in_window(e.get("ts", 0), 86400, now))
    bridge_24h = sum(1 for e in (evex.get("recent_bridge") or [])
                     if in_window(e.get("ts", 0), 86400, now))
    bridge_in_to_wbtc_24h = sum(
        e.get("value_usd", 0) for e in (evex.get("recent_bridge") or [])
        if e.get("token") == "WBTC" and e.get("direction") == "outgoing"
        and in_window(e.get("ts", 0), 86400, now))
    signals["dex_swaps_24h"]   = dex_24h
    signals["bridge_flows_24h"] = bridge_24h
    if bridge_in_to_wbtc_24h > 5_000_000:
        boosts_long.append(f"wbtc_bridge_outflow:${bridge_in_to_wbtc_24h/1e6:.0f}M")
        score_bull += 5

    # ── C. Whale flow (Bybit-side aggregator current state) ───────────────
    wf = jload("/var/lib/sygnif/whale_flow.json")
    if wf:
        imb     = wf.get("whale_imbalance")
        n_buys  = wf.get("n_large_buys", 0)
        n_sells = wf.get("n_large_sells", 0)
        buy_usd  = wf.get("whale_buy_notional_usd", 0)
        sell_usd = wf.get("whale_sell_notional_usd", 0)
        signals["bybit_whale_imbalance"]   = imb
        signals["bybit_whale_n_buys"]      = n_buys
        signals["bybit_whale_n_sells"]     = n_sells
        signals["bybit_whale_buy_usd"]     = buy_usd
        signals["bybit_whale_sell_usd"]    = sell_usd
        if imb is not None:
            try: imb_f = float(imb)
            except: imb_f = None
            if imb_f is not None:
                if imb_f >= 0.65 and n_buys >= 2:
                    boosts_long.append(f"bybit_whale_imb:{imb_f:+.2f}")
                    score_bull += 8
                elif imb_f <= -0.65 and n_sells >= 2:
                    boosts_short.append(f"bybit_whale_imb:{imb_f:+.2f}")
                    score_bear += 8

    # ── D + E. ChartInspect macro (VIX, DXY, MSTR) ────────────────────────
    macro = macro_indicators()
    vix = macro.get("vix", {}).get("value")
    vix_chg = macro.get("vix", {}).get("change_pct") or 0
    dxy = macro.get("dxy", {}).get("value")
    dxy_chg = macro.get("dxy", {}).get("change_pct") or 0
    if vix is not None:
        signals["vix"] = vix
        signals["vix_change_pct"] = vix_chg
        # Rising VIX = risk-off = bearish for BTC
        if vix_chg >= 10:
            boosts_short.append(f"vix_spike:+{vix_chg:.1f}%")
            score_bear += 10
        elif vix_chg <= -10:
            boosts_long.append(f"vix_falling:{vix_chg:+.1f}%")
            score_bull += 5
    if dxy is not None:
        signals["dxy"] = dxy
        signals["dxy_change_pct"] = dxy_chg
        # Strong dollar (DXY rising) = bearish BTC
        if dxy_chg >= 0.5:
            boosts_short.append(f"dxy_rising:+{dxy_chg:.2f}%")
            score_bear += 8
        elif dxy_chg <= -0.5:
            boosts_long.append(f"dxy_weak:{dxy_chg:+.2f}%")
            score_bull += 8
    # MSTR purchases — any in last 7 days = strong bullish institutional flag
    mstr = macro.get("mstr_recent", [])
    if mstr:
        # date is ISO like "2026-05-11" — compare with today
        from datetime import date as _date
        recent_mstr = [p for p in mstr
                       if p.get("date") and (
                           (_date.today() - _date.fromisoformat(p["date"])).days <= 7)]
        if recent_mstr:
            total_usd = sum(p.get("total_usd", 0) or 0 for p in recent_mstr)
            signals["mstr_purchases_7d_usd"] = total_usd
            signals["mstr_purchases_7d_n"] = len(recent_mstr)
            boosts_long.append(f"mstr_buy_7d:${total_usd/1e6:.0f}M")
            vetoes_short.append(f"mstr_buy_7d:${total_usd/1e6:.0f}M")
            score_bull += 12

    # ── F. Microstructure (orderbook imbalance via log tail) ──────────────
    try:
        with open("/var/lib/log/sygnif/microstructure-feed.log") as f:
            lines = f.readlines()[-200:]
    except Exception:
        lines = []
    try:
        if not lines:
            with open("/var/log/sygnif/microstructure-feed.log") as f:
                lines = f.readlines()[-200:]
    except Exception:
        pass
    ob_imb_samples = []
    for line in lines[-30:]:
        if "orderbook" in line and "imb=" in line:
            import re
            m = re.search(r"imb=([+-]?[0-9.]+)", line)
            if m:
                try: ob_imb_samples.append(float(m.group(1)))
                except: pass
    if ob_imb_samples:
        avg_imb = sum(ob_imb_samples) / len(ob_imb_samples)
        signals["orderbook_imb_avg_recent"] = round(avg_imb, 3)
        # Bid-heavy (imb > 0) = buy-side pressure = bullish
        if avg_imb >= 0.3:
            boosts_long.append(f"ob_bid_heavy:{avg_imb:+.2f}")
            score_bull += 5
        elif avg_imb <= -0.3:
            boosts_short.append(f"ob_ask_heavy:{avg_imb:+.2f}")
            score_bear += 5

    # ── G. News-feed (parse /var/log/sygnif/news-feed.log directly) ───────
    # Daemon writes to NeuroLinked + logs; doesn't emit to swarm.db.
    # Parse last ~500 log lines for "posted TIER/category ... — headline" entries.
    news_high_6h = 0
    news_bearish_hits = 0
    news_bullish_hits = 0
    try:
        with open("/var/log/sygnif/news-feed.log") as f:
            lines = f.readlines()[-500:]
    except Exception:
        lines = []
    import re
    cutoff_str = time.strftime("%Y-%m-%d %H:", time.gmtime(now - 6*3600))
    posted_re = re.compile(r"(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}).*?posted\s+(\w+)/(\S+)\s+\S+\s+—\s+(.*)$")
    bearish_terms = ["war", "sanction", "ban", "hack", "collapse", "crash",
                     "escalat", "raid", "lawsuit", "fine", "default"]
    bullish_terms = ["approv", "etf inflow", "adoption", "stimulus", "rate cut",
                     "investment", "endorsement", "treasury bought"]
    for line in lines[::-1]:
        # Only look at posted lines within last 6h
        if "INFO posted" not in line:
            continue
        m = posted_re.search(line)
        if not m:
            # Fallback: looser match for "posted TIER/category ..."
            if "INFO posted high" in line:
                tier = "high"
                rest = line.split("INFO posted", 1)[1].strip()
                headline = rest
            else:
                continue
        else:
            tier = m.group(2)
            headline = m.group(4)
        if tier == "high":
            news_high_6h += 1
        h_low = (headline or "").lower()
        for t in bearish_terms:
            if t in h_low: news_bearish_hits += 1; break
        for t in bullish_terms:
            if t in h_low: news_bullish_hits += 1; break
    signals["news_high_tier_6h"]     = news_high_6h
    signals["news_bearish_hits_6h"]  = news_bearish_hits
    signals["news_bullish_hits_6h"]  = news_bullish_hits
    if news_bearish_hits >= 3:
        boosts_short.append(f"bearish_news:{news_bearish_hits}")
        score_bear += 5
    if news_bullish_hits >= 3:
        boosts_long.append(f"bullish_news:{news_bullish_hits}")
        score_bull += 5

    # ── H. Polymarket prediction-market (parse log for bitcoin markets) ───
    pm_btc_yes_probs = []
    pm_btc_highest_target = None
    try:
        with open("/var/log/sygnif/polymarket-feed.log") as f:
            pmlines = f.readlines()[-400:]
    except Exception:
        pmlines = []
    pm_re = re.compile(r"posted\s+bitcoin\s+yes=([0-9.]+)\s+liq=\$([\d.]+)([kKmM]?)\s+q=(.+?)$")
    seen_questions = set()
    for line in pmlines[::-1]:
        if "posted bitcoin" not in line:
            continue
        m = pm_re.search(line)
        if not m:
            continue
        try:
            yes = float(m.group(1))
            liq_v = float(m.group(2))
            mult = {"k":1e3,"K":1e3,"m":1e6,"M":1e6}.get(m.group(3), 1)
            liq = liq_v * mult
        except: continue
        q = m.group(4).strip()
        if q in seen_questions: continue
        seen_questions.add(q)
        # Only count markets with meaningful liquidity (≥$30k)
        if liq < 30_000: continue
        pm_btc_yes_probs.append({"q": q[:80], "yes": yes, "liq_usd": int(liq)})
        if len(pm_btc_yes_probs) >= 5: break
    signals["polymarket_btc_markets"] = pm_btc_yes_probs
    signals["polymarket_btc_n"] = len(pm_btc_yes_probs)
    # Aggregate sentiment: average yes-prob of BTC-up targets
    if pm_btc_yes_probs:
        avg_yes = sum(p["yes"] for p in pm_btc_yes_probs) / len(pm_btc_yes_probs)
        signals["polymarket_btc_avg_yes"] = round(avg_yes, 3)
        # Higher prob on "Will BTC hit $X" markets = market expects upside
        if avg_yes >= 0.25:
            boosts_long.append(f"polymarket_btc_avg:{avg_yes:.2f}")
            score_bull += 3
        elif avg_yes <= 0.10:
            boosts_short.append(f"polymarket_btc_avg:{avg_yes:.2f}")
            score_bear += 3

    # ── I. Hivemind option-implied probabilities ──────────────────────────
    # Read from hivemind log most recent emit
    try:
        with open("/var/log/sygnif/hivemind-feed.log") as f:
            lines = f.readlines()[-50:]
        for line in lines[::-1]:
            if "emit ok=True" in line and "spot=" in line:
                import re
                m = re.search(r"spot=(\d+).*?expiries=(\d+).*?head=(\S+)", line)
                if m:
                    signals["hivemind_spot"]   = int(m.group(1))
                    signals["hivemind_expiries"] = int(m.group(2))
                    signals["hivemind_head_exp"] = m.group(3)
                    break
    except Exception:
        pass

    # ── J. Agent commentary (latest DLP) ──────────────────────────────────
    comm = swarm_recent_topic("agent.commentary", since_s=2*3600, limit=1)
    if comm:
        ts, content, _ = comm[0]
        signals["latest_commentary_age_s"] = int(now - ts)
        signals["latest_commentary"] = (content or "")[:240]

    # ── K. Entity portfolio changes (Goldrush) ────────────────────────────
    ep = swarm_recent_topic("ecosystem.entity_portfolio", since_s=24*3600, limit=20)
    entity_deltas_24h = []
    for ts, content, meta in ep[:20]:
        try: m = json.loads(meta) if meta else {}
        except: continue
        delta_usd = m.get("delta_usd")
        if delta_usd and abs(delta_usd) > 5_000_000:
            entity_deltas_24h.append({
                "ts": ts, "entity": m.get("label", "?"),
                "delta_usd": delta_usd,
            })
    signals["entity_deltas_24h"] = entity_deltas_24h
    # Bullish if a known exchange-cold wallet SHRANK by big amount (withdrawal)
    # Bearish if it GREW big (deposits coming in for sale)
    for d in entity_deltas_24h:
        if "cold" in (d["entity"] or "").lower() and d["delta_usd"] < -10_000_000:
            boosts_long.append(f"cold_wallet_withdrew:${abs(d['delta_usd'])/1e6:.0f}M")
            score_bull += 8

    # ── L. Brain state (dopamine/serotonin = model meta-confidence) ───────
    brain = brain_live_state()
    if brain:
        nm = brain.get("neuromodulators", {}) or {}
        dop = nm.get("dopamine", 0.5)
        ser = nm.get("serotonin", 0.5)
        signals["brain_dopamine"] = round(dop, 3)
        signals["brain_serotonin"] = round(ser, 3)
        signals["brain_step_count"] = brain.get("step_count")
        signals["brain_steps_per_sec"] = brain.get("steps_per_second")
        # High dopamine + high serotonin = brain in "confident reward" state
        # Use as a tiebreaker — small confidence boost
        if dop >= 0.6 and ser >= 0.7:
            score_bull += 2  # mild
        elif dop <= 0.3:
            # Low dopamine = reward depleted, recent losses; bias toward caution
            score_bear += 2

    # ── 8. Compute scores + net directional ───────────────────────────────
    # Clamp to 0-100
    score_bull = min(100, score_bull)
    score_bear = min(100, score_bear)
    net_directional = score_bull - score_bear   # -100 to +100
    confidence = min(100, max(score_bull, score_bear))   # how strong the signal is

    return {
        "schema":               "sygnif.intel_summary.v1",
        "updated_at_utc":       dt.datetime.now(dt.timezone.utc).isoformat(),
        "updated_at_ts":        int(now),
        "scores": {
            "bullish":          score_bull,
            "bearish":          score_bear,
            "net_directional":  net_directional,
            "confidence":       confidence,
        },
        "vetoes_short":         vetoes_short,
        "vetoes_long":          vetoes_long,
        "boosts_short":         boosts_short,
        "boosts_long":          boosts_long,
        "active_signals":       signals,
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main() -> int:
    global _running
    print(f"=== sygnif_intel_aggregator started @ "
          f"{dt.datetime.now(dt.timezone.utc).isoformat()} ===", flush=True)
    print(f"  output:  {OUT_FILE}", flush=True)
    print(f"  poll:    {POLL_S}s", flush=True)

    def _sigterm(sig, frame):
        global _running
        print(f"  signal {sig}", flush=True)
        _running = False
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)

    last_run = 0.0
    while _running:
        now = time.time()
        if now - last_run >= POLL_S:
            try:
                summary = build_summary()
                OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp = OUT_FILE.with_suffix(OUT_FILE.suffix + ".tmp")
                tmp.write_text(json.dumps(summary, indent=2))
                os.replace(tmp, OUT_FILE)
                # Brief heartbeat
                s = summary["scores"]
                vs = len(summary["vetoes_short"]); vl = len(summary["vetoes_long"])
                bs = len(summary["boosts_short"]); bl = len(summary["boosts_long"])
                print(f"  [{int(now)}] bull={s['bullish']:>3d} bear={s['bearish']:>3d} "
                      f"net={s['net_directional']:>+4d}  "
                      f"V short:{vs} long:{vl}  B short:{bs} long:{bl}",
                      flush=True)
            except Exception as e:
                print(f"  ! build_summary err: {type(e).__name__}: {e}",
                      file=sys.stderr, flush=True)
            last_run = now
        time.sleep(2)

    return 0


if __name__ == "__main__":
    sys.exit(main())
