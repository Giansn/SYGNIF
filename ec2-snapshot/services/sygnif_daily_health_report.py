#!/usr/bin/env python3
"""sygnif_daily_health_report.py — comprehensive daily health snapshot.

Generates a structured Markdown report covering:
  • Trader status (services + errors)
  • PnL (realized/unrealized/24h delta)
  • Open positions
  • Recent trades (last 10 closes)
  • Learning progress (decision snapshots, training_pairs, gate_optimizer)
  • Bleedings detection (losses, drawdown, daily-loss circuit)
  • Market state (BTC, regime, IV, whale flow, bounce, options)
  • System health (disk, services, key file sizes)
  • Recommendations (gate adjustments, tier promotion, alerts)

Delivers to THREE channels:
  1. /var/lib/sygnif/daily-health-report.md (local file, stable path)
  2. GitHub gist (https URL the remote routine can fetch via WebFetch)
  3. Telegram via existing relay (user reads on phone)

Wired by sygnif-daily-health-report.timer (daily 06:55 UTC = 08:55 MESZ).
The remote "EC2 Review" routine runs at 07:00 UTC = 09:00 MESZ and reads
the gist URL.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import time
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap env
# ---------------------------------------------------------------------------
def _load_env(path: str) -> None:
    if not os.path.exists(path): return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env("/etc/sygnif/trader.env")

DB = "/var/lib/sygnif/swarm.db"
REPORT_FILE = pathlib.Path("/var/lib/sygnif/daily-health-report.md")
GIST_ID_FILE = pathlib.Path("/var/lib/sygnif/daily-health-gist-id.txt")

# ---------------------------------------------------------------------------
# Data collectors
# ---------------------------------------------------------------------------
def collect_services() -> dict:
    services = [
        "sygnif-trader", "sygnif-bybit-daemon", "sygnif-neurolinked",
        "sygnif-brain-insights", "sygnif-bybit-mcp", "sygnif-dlp",
        "sygnif-telegram-relay", "sygnif-bounce-watcher", "sygnif-whale-watcher",
        "sygnif-fast-reactor", "sygnif-news-feed", "sygnif-microstructure-feed",
        "sygnif-polymarket-feed", "sygnif-hivemind-feed", "sygnif-brain-context",
        "sygnif-trade-nl-publisher", "sygnif-bybit-nl-feed",
        "sygnif-perp-runner", "sygnif-swarm-predict-loop",
        "sygnif-funding-harvester",
    ]
    timers = [
        "sygnif-training-scanner.timer", "sygnif-standing-orders.timer",
        "sygnif-decision-joiner.timer", "sygnif-outcome-per-trade.timer",
        "sygnif-gate-optimizer.timer", "sygnif-drift-monitor.timer",
        "sygnif-challenger-report.timer", "sygnif-tier-audit.timer",
        "sygnif-whale-alignment-audit.timer", "sygnif-disk-janitor.timer",
        "sygnif-daily-health-report.timer", "sygnif-discovery.timer",
        "sygnif-predict.timer", "sygnif-swarm-x1-mirror.timer",
        "sygnif-journal-daily.timer", "sygnif-btc-1h-refresh.timer",
        "sygnif-btc01-finetune.timer",
    ]
    active = []
    inactive = []
    failed = []
    for name in services + timers:
        try:
            r = subprocess.run(["systemctl", "is-active", name],
                                capture_output=True, text=True, timeout=5)
            status = r.stdout.strip()
            if status == "active": active.append(name)
            elif status == "failed": failed.append(name)
            else: inactive.append(f"{name}({status})")
        except Exception:
            inactive.append(f"{name}(error)")
    return {"active": active, "inactive": inactive, "failed": failed,
            "total": len(services) + len(timers)}


def collect_wallet() -> dict:
    """Multi-asset wallet via sygnif_neurons.wallet.demo."""
    sys.path.insert(0, "/home/ubuntu/sygnif-agent-mirror")
    try:
        import sygnif_neurons as N
        r = N.run("wallet.demo", {})
        if not r.get("ok"): return {"error": r.get("error")}
        lst = ((r.get("data") or {}).get("result") or {}).get("list") or []
        if not lst: return {"error": "empty"}
        acct = lst[0]
        out = {
            "total_equity_usd":  float(acct.get("totalEquity") or 0),
            "available_usd":     float(acct.get("totalAvailableBalance") or 0),
            "wallet_balance_usd": float(acct.get("totalWalletBalance") or 0),
            "coins": [],
        }
        for c in (acct.get("coin") or []):
            bal = float(c.get("walletBalance") or 0)
            usd = float(c.get("usdValue") or 0)
            if bal == 0 and usd == 0: continue
            out["coins"].append({"coin": c.get("coin"), "balance": bal,
                                  "usd_value": usd})
        return out
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def collect_open_positions() -> list[dict]:
    sys.path.insert(0, "/home/ubuntu/sygnif-agent-mirror")
    try:
        import sygnif_neurons as N
        r = N.run("portfolio.demo", {})
        if not r.get("ok"): return []
        return (r.get("data") or {}).get("open") or []
    except Exception:
        return []


def collect_recent_closes(n: int = 15) -> list[dict]:
    out = []
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        for (created, meta_s) in c.execute(
            "SELECT created, meta FROM swarm_entries "
            "WHERE topic='trade.close' ORDER BY created DESC LIMIT ?",
            (n,)):
            try:
                m = json.loads(meta_s)
            except: continue
            out.append({
                "ts":     dt.datetime.fromtimestamp(created, tz=dt.timezone.utc).isoformat(),
                "symbol": m.get("symbol"),
                "side":   m.get("side"),
                "qty":    m.get("exec_qty"),
                "price":  m.get("exec_price"),
                "pnl":    m.get("closed_pnl"),
                "olid":   (m.get("order_link_id") or "")[:18],
            })
        c.close()
    except Exception: pass
    return out


def collect_24h_activity() -> dict:
    out = {}
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        cutoff = int(time.time()) - 86400
        cur = c.execute(
            "SELECT topic, COUNT(*) FROM swarm_entries "
            "WHERE created > ? GROUP BY topic ORDER BY 2 DESC LIMIT 30",
            (cutoff,))
        out["topic_counts"] = dict(cur.fetchall())

        # Recent outcomes for PnL
        cur = c.execute(
            "SELECT meta FROM swarm_entries WHERE topic='outcome.attributed' "
            "AND created > ?", (cutoff,))
        pnl_total = 0.0
        wins = 0; losses = 0
        for (m,) in cur.fetchall():
            try:
                d = json.loads(m)
                p = float(d.get("closed_pnl") or 0)
                pnl_total += p
                if p > 0: wins += 1
                elif p < 0: losses += 1
            except: pass
        out["realized_pnl_24h"] = round(pnl_total, 4)
        out["n_wins_24h"] = wins
        out["n_losses_24h"] = losses

        c.close()
    except Exception as e:
        out["error"] = str(e)
    return out


def collect_market_state() -> dict:
    """Pull live BTC + market microstructure from the snapshot pipeline."""
    sys.path.insert(0, "/home/ubuntu/sygnif-agent-mirror")
    out = {}
    try:
        from agent import market_features as MF
        ctx = MF.get_market_context("BTCUSDT")
        p = ctx.get("perp", {})
        ob = ctx.get("orderbook", {})
        o = ctx.get("options", {})
        oi = ctx.get("oi", {})
        out["btc_last"]               = p.get("last")
        out["btc_24h_pct"]            = (p.get("price_24h_pct") or 0) * 100
        out["btc_range_24h_pct"]      = p.get("range_24h_pct")
        out["btc_range_position"]    = p.get("price_pos_in_range")
        out["funding_bps_per_8h"]    = p.get("funding_bps_per_8h")
        out["funding_bps_annual"]    = p.get("funding_bps_annual")
        out["basis_bps"]              = p.get("basis_bps")
        out["oi_btc"]                 = p.get("open_interest_btc")
        out["oi_change_1h_pct"]       = oi.get("oi_change_1h_pct")
        out["oi_label"]               = oi.get("oi_label")
        out["spread_bps"]             = ob.get("spread_bps")
        out["depth_imbalance_top5"]   = ob.get("depth_imbalance_top5")
        out["atm_iv"]                 = o.get("atm_iv")
        out["implied_1d_move_pct"]    = o.get("implied_1d_move_pct")
        out["implied_1d_move_usd"]    = o.get("implied_1d_move_usd")
        out["skew_25d_iv"]            = o.get("skew_25d_iv")
        out["skew_label"]             = o.get("skew_label")
        out["put_call_oi_ratio"]      = o.get("put_call_oi_ratio")
        out["max_pain_strike"]        = o.get("max_pain_strike")
        out["gex_label"]              = o.get("gex_label")
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"

    # Bounce + whale (read from daemon JSON files — fresher than REST)
    for name, path in [("bounce", "/var/lib/sygnif/bounce_setup.json"),
                        ("whale",  "/var/lib/sygnif/whale_flow.json")]:
        try:
            with open(path) as f: d = json.load(f)
            if name == "bounce":
                out["bounce_active"] = d.get("active")
                out["bounce_direction"] = d.get("direction")
                out["bounce_magnitude_pct"] = d.get("magnitude_abs_pct")
            else:
                out["whale_imbalance"]    = d.get("whale_imbalance")
                out["whale_n_trades_15m"] = d.get("n_whale_trades")
                out["whale_buy_usd_15m"]  = d.get("whale_buy_notional_usd")
                out["whale_sell_usd_15m"] = d.get("whale_sell_notional_usd")
        except Exception: pass
    return out


def collect_learning_progress() -> dict:
    out = {}
    try:
        pairs_path = pathlib.Path("/var/lib/sygnif/training_pairs.ndjson")
        if pairs_path.exists():
            count = 0
            with pairs_path.open() as f:
                for _ in f: count += 1
            out["training_pairs_total"] = count
        # Today's per-day file
        day = dt.datetime.utcnow().strftime("%Y-%m-%d")
        day_path = pathlib.Path(f"/var/lib/sygnif/training_pairs_{day}.ndjson")
        if day_path.exists():
            count = 0
            with day_path.open() as f:
                for _ in f: count += 1
            out["training_pairs_today"] = count
    except Exception: pass

    # Latest audit + optimizer recommendations
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        for topic, key in [
            ("agent.review.tier_audit",        "tier_audit"),
            ("agent.review.gate_optimizer",    "gate_optimizer"),
            ("agent.review.challenger_diff",   "challenger"),
            ("agent.review.drift_monitor",     "drift_monitor"),
            ("agent.review.whale_alignment",   "whale_alignment"),
            ("agent.review.joiner_coverage",   "joiner"),
            ("agent.review.disk_janitor",      "disk_janitor"),
            ("agent.review.outcome_attribution", "outcome_attribution"),
        ]:
            row = c.execute(
                "SELECT datetime(created,'unixepoch'), content FROM swarm_entries "
                "WHERE topic=? ORDER BY created DESC LIMIT 1", (topic,)).fetchone()
            if row: out[key] = {"ts": row[0], "content": row[1][:200]}
        c.close()
    except Exception: pass

    # Policy state
    try:
        with open("/var/lib/sygnif/training_policy.json") as f:
            out["policy"] = json.load(f)
    except Exception: pass

    # Gate params + challenger diff
    try:
        with open("/var/lib/sygnif/gate_params.json") as f:
            out["gate_params"] = json.load(f).get("params", {})
        with open("/var/lib/sygnif/gate_params_challenger.json") as f:
            ch = json.load(f).get("params", {})
            diffs = {k: {"champion": out["gate_params"].get(k), "challenger": ch.get(k)}
                     for k in ch if out["gate_params"].get(k) != ch.get(k)}
            if diffs: out["challenger_diffs"] = diffs
    except Exception: pass

    # Circuit breaker
    cb = pathlib.Path("/var/lib/sygnif/circuit_breaker.json")
    if cb.exists():
        try:
            with cb.open() as f:
                out["circuit_breaker"] = json.load(f)
        except Exception: pass
    else:
        out["circuit_breaker"] = {"state": "ok"}

    return out


def collect_system_health() -> dict:
    out = {}
    try:
        u = shutil.disk_usage("/")
        out["disk_pct"] = int(round(u.used / u.total * 100))
        out["disk_free_gb"] = round(u.free / 1024**3, 1)
    except: pass
    try:
        with open("/proc/loadavg") as f:
            out["loadavg"] = f.read().split()[:3]
    except: pass
    try:
        with open("/proc/meminfo") as f:
            mi = {}
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1); mi[k] = v.strip()
            total_kb = int(mi.get("MemTotal", "0 kB").split()[0])
            avail_kb = int(mi.get("MemAvailable", "0 kB").split()[0])
            out["mem_used_pct"] = int(round((total_kb - avail_kb) / total_kb * 100)) if total_kb else 0
    except: pass
    return out


def find_bleedings(closes: list[dict], pnl_24h: float, dd_warn: float = -50) -> list[str]:
    bleedings = []
    if pnl_24h <= dd_warn:
        bleedings.append(f"🚨 24h realized PnL ${pnl_24h:+.2f} below ${dd_warn} threshold")
    # Find big-loss trades (>$5 single)
    big_losses = [c for c in closes if isinstance(c.get("pnl"), (int, float))
                  and float(c["pnl"]) < -5]
    if big_losses:
        bleedings.append(f"⚠️ {len(big_losses)} large-loss trades (each < -$5): "
                          + ", ".join(f"{b['symbol']} ${float(b['pnl']):+.2f}"
                                       for b in big_losses[:5]))
    # Consecutive losses (last 5)
    last_5_pnl = [float(c.get("pnl") or 0) for c in closes[:5]
                  if isinstance(c.get("pnl"), (int, float))]
    if last_5_pnl and len(last_5_pnl) == 5 and all(p < 0 for p in last_5_pnl):
        bleedings.append(f"🚨 5 consecutive losses: " +
                          ", ".join(f"${p:+.2f}" for p in last_5_pnl))
    return bleedings


def build_recommendations(data: dict) -> list[str]:
    recs = []
    sh = data.get("system_health", {})
    if (sh.get("disk_pct") or 0) >= 80:
        recs.append(f"⚠️ Disk at {sh['disk_pct']}% — janitor should clean; check /home/ubuntu/sygnif-agent-mirror/*.zip")
    if (sh.get("mem_used_pct") or 0) >= 85:
        recs.append(f"⚠️ Memory at {sh['mem_used_pct']}% — investigate")

    lp = data.get("learning_progress", {})
    if lp.get("circuit_breaker", {}).get("state") == "tripped":
        recs.append(f"🚨 Circuit breaker TRIPPED: {lp['circuit_breaker'].get('reason')}. "
                     "Manual reset: rm /var/lib/sygnif/circuit_breaker.json && "
                     "sudo systemctl restart sygnif-trader")
    if lp.get("challenger_diffs"):
        recs.append(f"💡 Gate-optimizer has {len(lp['challenger_diffs'])} pending proposal(s) — "
                     "review agent.review.challenger_diff swarm row")

    ta = lp.get("tier_audit", {}).get("content", "") if lp.get("tier_audit") else ""
    if "PROMOTE" in ta and "SYGNIF_TIER_FULL" in ta:
        recs.append(f"💡 Tier-audit recommends FULL tier promotion: {ta}")

    drift = lp.get("drift_monitor", {}).get("content", "") if lp.get("drift_monitor") else ""
    if "alert" in drift.lower():
        recs.append(f"⚠️ Drift monitor alert: {drift}")

    svc = data.get("services", {})
    if svc.get("failed"):
        recs.append(f"🚨 Failed services: {', '.join(svc['failed'])}")
    return recs


# ---------------------------------------------------------------------------
# Gainers (top market movers via CoinGecko, no auth)
# ---------------------------------------------------------------------------
def collect_gainers() -> dict:
    """Top 5 gainers + top 5 losers in last 24h among top-200 by mkt cap."""
    import urllib.request
    url = ("https://api.coingecko.com/api/v3/coins/markets"
           "?vs_currency=usd&order=market_cap_desc&per_page=200&page=1"
           "&price_change_percentage=24h")
    out = {"top_gainers": [], "top_losers": [], "btc_dom": None,
           "total_mcap_usd": None}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sygnif-health/1.0"})
        coins = json.loads(urllib.request.urlopen(req, timeout=10).read())
    except Exception as e:
        print(f"  gainers fetch failed: {e}", file=sys.stderr)
        return out
    valid = [c for c in coins if c.get("price_change_percentage_24h") is not None]
    valid.sort(key=lambda c: c["price_change_percentage_24h"], reverse=True)
    out["top_gainers"] = [
        {"symbol": c.get("symbol", "").upper(), "name": c.get("name", ""),
         "price": c.get("current_price"),
         "pct_24h": round(c.get("price_change_percentage_24h", 0), 1),
         "mcap_rank": c.get("market_cap_rank")}
        for c in valid[:5]
    ]
    out["top_losers"] = [
        {"symbol": c.get("symbol", "").upper(), "name": c.get("name", ""),
         "price": c.get("current_price"),
         "pct_24h": round(c.get("price_change_percentage_24h", 0), 1),
         "mcap_rank": c.get("market_cap_rank")}
        for c in valid[-5:][::-1]
    ]
    try:
        gurl = "https://api.coingecko.com/api/v3/global"
        req = urllib.request.Request(gurl, headers={"User-Agent": "sygnif-health/1.0"})
        g = json.loads(urllib.request.urlopen(req, timeout=8).read()).get("data", {})
        out["btc_dom"] = round((g.get("market_cap_percentage", {}) or {}).get("btc", 0), 2)
        out["total_mcap_usd"] = (g.get("total_market_cap", {}) or {}).get("usd")
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# On-chain intelligence summary (from chain-intel / evm / tron / xchg-liq / ecosystem state files)
# ---------------------------------------------------------------------------
def collect_onchain_intel() -> dict:
    out = {}
    now = time.time()
    since_24h = now - 86400

    try:
        s = json.loads(pathlib.Path("/var/lib/sygnif/chain_state.json").read_text())
        events = s.get("recent_events", []) or []
        last24 = [e for e in events if e.get("ts", 0) >= since_24h]
        out["btc_whale_events_24h"] = len(last24)
        out["btc_whale_btc_24h"] = round(sum(e.get("value_btc", 0) for e in last24), 1)
        out["btc_accumulation_to_cold_24h"] = sum(1 for e in last24 if e.get("category") == "ACCUMULATION_TO_COLD")
        out["btc_lth_spend_heavy_24h"] = sum(1 for e in last24 if "LTH_SPEND_HEAVY" in (e.get("flags") or []))
        out["btc_dormancy_breaks_24h"] = sum(1 for e in last24 if "DORMANCY_BREAK_5YR" in (e.get("flags") or []))
        out["btc_mempool_preconfirmed_24h"] = sum(1 for e in last24 if "MEMPOOL_PRE_CONFIRMED" in (e.get("flags") or []))
        out["btc_clusters_tracked"] = len(s.get("clusters", {}))
        out["btc_wallets_tracked"] = len(s.get("wallets", {}))
        out["btc_last_block"] = s.get("last_block_height")
    except Exception as e:
        out["btc_state_err"] = str(e)[:80]

    try:
        s = json.loads(pathlib.Path("/var/lib/sygnif/evm_state.json").read_text())
        mints = s.get("recent_mints", []) or []
        wbtc  = s.get("recent_wbtc", []) or []
        out["eth_usdt_mints_24h_usd"] = round(sum(m.get("amount_usd", 0) for m in mints if m.get("ts", 0) >= since_24h and m.get("token") == "USDT"), 0)
        out["eth_usdc_mints_24h_usd"] = round(sum(m.get("amount_usd", 0) for m in mints if m.get("ts", 0) >= since_24h and m.get("token") == "USDC"), 0)
        out["wbtc_events_24h"] = sum(1 for w in wbtc if w.get("ts", 0) >= since_24h)
    except Exception:
        pass

    try:
        s = json.loads(pathlib.Path("/var/lib/sygnif/tron_state.json").read_text())
        mints = s.get("recent_mints", []) or []
        out["tron_usdt_mints_24h_usd"] = round(sum(m.get("amount_usd", 0) for m in mints if m.get("ts", 0) >= since_24h and m.get("token") == "USDT"), 0)
        out["tron_mints_count_24h"]    = sum(1 for m in mints if m.get("ts", 0) >= since_24h)
    except Exception:
        pass

    try:
        s = json.loads(pathlib.Path("/var/lib/sygnif/xchg_liq_state.json").read_text())
        events = s.get("recent_events", []) or []
        clusters = s.get("recent_clusters", []) or []
        last24 = [e for e in events if e.get("ts", 0) >= since_24h]
        cl_24  = [c for c in clusters if c.get("ts", 0) >= since_24h]
        out["liq_events_24h"]    = len(last24)
        out["liq_clusters_24h"]  = len(cl_24)
        out["liq_total_usd_24h"] = round(sum(e.get("value_usd", 0) for e in last24), 0)
    except Exception:
        pass

    try:
        s = json.loads(pathlib.Path("/var/lib/sygnif/market_premium.json").read_text())
        h = s.get("history", []) or []
        if h:
            latest = h[-1]
            out["premium_cb_bn_bps"] = latest.get("cb_bn_bps")
            out["basis_bn_bb_bps"]   = latest.get("bn_bb_bps")
    except Exception:
        pass

    try:
        s = json.loads(pathlib.Path("/var/lib/sygnif/ecosystem_state.json").read_text())
        dh = s.get("dominance_history", []) or []
        sh = s.get("stablecoin_history", []) or []
        if dh:
            latest = dh[-1]
            out["btc_dom_pct"] = latest.get("btc_dom")
            out["total_mcap_trillion"] = round((latest.get("total_mcap", 0) or 0) / 1e12, 2)
        if sh:
            latest = sh[-1]
            chains = latest.get("chains", {}) or {}
            out["stablecoin_total_usd"]  = sum(c.get("total_usd", 0) for c in chains.values())
            out["stablecoin_chains_n"]   = len(chains)
    except Exception:
        pass

    try:
        s = json.loads(pathlib.Path("/var/lib/sygnif/evm_extras_state.json").read_text())
        dex = s.get("recent_dex", []) or []
        br  = s.get("recent_bridge", []) or []
        out["dex_swaps_24h"]    = sum(1 for e in dex if e.get("ts", 0) >= since_24h)
        out["bridge_flows_24h"] = sum(1 for e in br if e.get("ts", 0) >= since_24h)
    except Exception:
        pass

    return out


def build_report(data: dict) -> str:
    """CONDENSED daily recap — target ~2KB. Replaces the long-form report."""
    now = dt.datetime.now(tz=dt.timezone.utc)
    md = []
    md.append(f"# SYGNIF Daily Recap")
    md.append(f"_{now.strftime('%Y-%m-%d %H:%M')} UTC ({(now + dt.timedelta(hours=2)).strftime('%H:%M')} MESZ)_\n")

    w   = data.get("wallet", {})
    act = data.get("activity_24h", {})
    m   = data.get("market_state", {})
    svc = data.get("services", {})
    sh  = data.get("system_health", {})
    g   = data.get("gainers", {})
    oc  = data.get("onchain", {})
    lp  = data.get("learning_progress", {})

    eq      = w.get("total_equity_usd", 0)
    pnl_24h = act.get("realized_pnl_24h", 0)
    wins    = act.get("n_wins_24h", 0)
    losses  = act.get("n_losses_24h", 0)
    wr      = (wins / max(wins + losses, 1)) * 100
    open_n  = len(data.get("open_positions", []))
    closes  = data.get("recent_closes", []) or []
    pnls    = [float(c.get("pnl")) for c in closes if c.get("pnl") not in (None, "N/A")]
    best    = max(pnls) if pnls else 0
    worst   = min(pnls) if pnls else 0

    # === TL;DR ===
    md.append("## TL;DR")
    md.append(f"- Equity ${eq:,.0f} | 24h ${pnl_24h:+,.1f} | WR {wr:.0f}% ({wins}W/{losses}L) | Open {open_n}")
    btc_dom_val = oc.get("btc_dom_pct") or g.get("btc_dom") or 0
    mcap_t = oc.get("total_mcap_trillion") or (g.get("total_mcap_usd", 0) / 1e12 if g.get("total_mcap_usd") else "?")
    btc_24h = m.get('btc_24h_pct')
    btc_24h_str = f"{btc_24h:+.2f}%" if btc_24h is not None else "?"
    md.append(f"- BTC ${m.get('btc_last','?')} ({btc_24h_str}) | Dom {btc_dom_val:.2f}% | Mcap ${mcap_t}T")
    md.append(f"- Daemons {len(svc.get('active', []))}/{svc.get('total', 0)} active "
              f"({len(svc.get('failed', []))} fail, {len(svc.get('inactive', []))} off)")
    md.append("")

    # === Bleedings + Recs ===
    bleedings = data.get("bleedings", [])
    if bleedings:
        md.append("## Bleedings")
        for b in bleedings[:5]:
            md.append(f"- {b}")
        md.append("")
    recs = data.get("recommendations", [])
    if recs:
        md.append("## Recommendations")
        for r in recs[:4]:
            md.append(f"- {r}")
        md.append("")

    # === Trading 24h ===
    md.append("## Trading 24h")
    md.append(f"- Realized ${pnl_24h:+.2f} | best ${best:+.2f} | worst ${worst:+.2f}")
    pol = lp.get("policy", {})
    if pol:
        md.append(f"- Policy v{pol.get('version')} risk {pol.get('base_risk_pct',0)*100:.2f}% "
                  f"min_score {pol.get('min_score')} maxconc {pol.get('max_concurrent')}")
    tc = act.get("topic_counts", {})
    md.append(f"- Flow: snap {tc.get('decision.snapshot',0)} → exec {tc.get('decision.executed',0)} "
              f"→ close {tc.get('trade.close',0)} → outcome {tc.get('outcome.attributed',0)}")
    md.append("")

    # === Market ===
    md.append("## Market")
    md.append(f"- Range 24h: {m.get('btc_range_24h_pct','?')}% | pos {m.get('btc_range_position','?')}")
    md.append(f"- Funding {m.get('funding_bps_per_8h','?')}bps/8h | Basis {m.get('basis_bps','?')}bps | "
              f"OI {m.get('oi_btc','?')} BTC ({m.get('oi_label','?')})")
    md.append(f"- OB imb5 {m.get('depth_imbalance_top5','?')} | spread {m.get('spread_bps','?')}bps | "
              f"IV {m.get('atm_iv','?')} | P/C OI {m.get('put_call_oi_ratio','?')} | Max-pain ${m.get('max_pain_strike','?')}")
    cb_bn = oc.get("premium_cb_bn_bps"); bn_bb = oc.get("basis_bn_bb_bps")
    if cb_bn is not None or bn_bb is not None:
        cb_bn_s = f"{cb_bn:+.1f}" if cb_bn is not None else "?"
        bn_bb_s = f"{bn_bb:+.1f}" if bn_bb is not None else "?"
        md.append(f"- Premium cb→bn {cb_bn_s}bps | basis bn→bb {bn_bb_s}bps")
    md.append("")

    # === Gainers ===
    if g.get("top_gainers"):
        md.append("## Gainers / Losers 24h")
        for u in g["top_gainers"][:3]:
            md.append(f"- ↑ {u['symbol']:<6s} {u['pct_24h']:+.1f}% ${u.get('price',0):g} (rank {u.get('mcap_rank','?')})")
        for d in g["top_losers"][:3]:
            md.append(f"- ↓ {d['symbol']:<6s} {d['pct_24h']:+.1f}% ${d.get('price',0):g} (rank {d.get('mcap_rank','?')})")
        md.append("")

    # === On-chain intel ===
    md.append("## On-chain 24h")
    md.append(f"- BTC whales: {oc.get('btc_whale_events_24h','?')} events, {oc.get('btc_whale_btc_24h','?')} BTC")
    flags = []
    for k, lbl in [("btc_accumulation_to_cold_24h", "cold-accum"),
                    ("btc_lth_spend_heavy_24h", "LTH-heavy"),
                    ("btc_dormancy_breaks_24h", "dormancy-break"),
                    ("btc_mempool_preconfirmed_24h", "mempool-pre")]:
        if oc.get(k, 0):
            flags.append(f"{lbl}: {oc[k]}")
    if flags:
        md.append(f"- BTC flags: {' | '.join(flags)}")
    usdt_eth = oc.get("eth_usdt_mints_24h_usd", 0) or 0
    usdt_tr  = oc.get("tron_usdt_mints_24h_usd", 0) or 0
    usdc_eth = oc.get("eth_usdc_mints_24h_usd", 0) or 0
    md.append(f"- Mints 24h: USDT(ETH) ${usdt_eth/1e6:,.0f}M | "
              f"USDT(Tron) ${usdt_tr/1e6:,.0f}M | "
              f"USDC ${usdc_eth/1e6:,.0f}M")
    if oc.get("liq_events_24h") is not None:
        md.append(f"- Multi-exch liq: {oc.get('liq_events_24h',0)} events, "
                  f"${oc.get('liq_total_usd_24h',0)/1e6:.1f}M, clusters {oc.get('liq_clusters_24h',0)}")
    if oc.get("dex_swaps_24h", 0) or oc.get("bridge_flows_24h", 0):
        md.append(f"- DEX swaps: {oc.get('dex_swaps_24h',0)} | bridges: {oc.get('bridge_flows_24h',0)}")
    md.append("")

    # === Daemon health ===
    md.append("## Daemons")
    md.append(f"- Active {len(svc.get('active', []))}/{svc.get('total', 0)} | "
              f"disk {sh.get('disk_pct')}% free {sh.get('disk_free_gb')}GB | "
              f"mem {sh.get('mem_used_pct')}% | load {sh.get('loadavg',['?'])[0]}")
    if svc.get("failed"):
        md.append(f"- FAILED: {', '.join(svc['failed'])}")
    if svc.get("inactive"):
        inact = svc["inactive"][:6]
        more = len(svc["inactive"]) - 6
        md.append(f"- Inactive: {', '.join(inact)}" + (f" (+{more} more)" if more > 0 else ""))
    if oc.get("btc_last_block"):
        md.append(f"- chain-intel: block {oc['btc_last_block']} | "
                  f"{oc.get('btc_wallets_tracked',0)}w / {oc.get('btc_clusters_tracked',0)}c tracked")
    md.append("")

    # === Learning ===
    md.append("## Learning")
    cb = lp.get("circuit_breaker", {}) or {}
    md.append(f"- Training pairs: total {lp.get('training_pairs_total',0)} | "
              f"today +{lp.get('training_pairs_today',0)} | CB: {cb.get('state','?')}")
    if lp.get("challenger_diffs"):
        diffs = list(lp["challenger_diffs"].items())[:2]
        for k, v in diffs:
            md.append(f"- Gate Δ: {k}: {v['champion']}→{v['challenger']}")
    md.append("")

    # === Recent closes (compact, last 10) ===
    if closes:
        md.append("## Recent closes")
        md.append("| time | side | qty | PnL |")
        md.append("|---|---|---|---|")
        for c in closes[:10]:
            ts = c.get("ts","")[:16].replace("T"," ")
            pnl_raw = c.get("pnl")
            pnl_str = f"${float(pnl_raw):+.2f}" if pnl_raw not in (None, "N/A") else "—"
            md.append(f"| {ts} | {c.get('side','')} | {c.get('qty','')} | {pnl_str} |")
        md.append("")

    md.append("---")
    md.append(f"_Source: sygnif_daily_health_report.py on EC2 i-0cd5389584d70a7fc_")
    return "\n".join(md)


def _legacy_full_build_report(data: dict) -> str:
    now = dt.datetime.now(tz=dt.timezone.utc)
    md = []
    md.append(f"# SYGNIF Daily Health Report")
    md.append(f"_{now.isoformat()} UTC ({(now + dt.timedelta(hours=2)).strftime('%H:%M')} MESZ)_\n")

    # === Headline ===
    w = data.get("wallet", {})
    act = data.get("activity_24h", {})
    eq = w.get("total_equity_usd", 0)
    pnl_24h = act.get("realized_pnl_24h", 0)
    wins = act.get("n_wins_24h", 0); losses = act.get("n_losses_24h", 0)
    md.append("## TL;DR\n")
    md.append(f"- **Equity**: ${eq:,.2f} (multi-asset)")
    md.append(f"- **24h realized P&L**: ${pnl_24h:+.2f}  ({wins}W / {losses}L)")
    open_n = len(data.get("open_positions", []))
    md.append(f"- **Open positions**: {open_n}")
    svc = data.get("services", {})
    md.append(f"- **Services**: {len(svc.get('active', []))}/{svc.get('total', 0)} active "
              f"({len(svc.get('failed', []))} failed, {len(svc.get('inactive', []))} inactive)")
    sh = data.get("system_health", {})
    md.append(f"- **System**: disk {sh.get('disk_pct')}%  load {sh.get('loadavg', ['?'])[0]}  mem {sh.get('mem_used_pct')}%")
    md.append("")

    # === Bleedings (highest priority) ===
    bleedings = data.get("bleedings", [])
    if bleedings:
        md.append("## 🩸 Bleedings detected\n")
        for b in bleedings: md.append(f"- {b}")
    else:
        md.append("## ✅ No bleedings detected\n")
    md.append("")

    # === Recommendations ===
    recs = data.get("recommendations", [])
    if recs:
        md.append("## 🎯 Recommendations\n")
        for r in recs: md.append(f"- {r}")
    md.append("")

    # === PnL detail ===
    md.append("## P&L\n")
    md.append(f"- Realized 24h: ${pnl_24h:+.2f}")
    md.append(f"- Win rate: {wins/(wins+losses)*100:.0f}% ({wins}W/{losses}L)"
              if (wins + losses) > 0 else "- Win rate: N/A (no closed trades)")
    md.append(f"- Total demo equity: ${eq:,.2f}")
    md.append(f"- Coins:")
    for c in w.get("coins", []):
        md.append(f"  - {c['coin']}: {c['balance']:.4f} (${c['usd_value']:.2f})")
    md.append("")

    # === Open positions ===
    md.append("## Open positions\n")
    opens = data.get("open_positions", [])
    if not opens:
        md.append("- (none)")
    else:
        for p in opens:
            md.append(f"- {p.get('symbol')} {p.get('side')} qty={p.get('qty')} "
                      f"@${p.get('avg_price')} mark=${p.get('mark_price')} "
                      f"uPnL=${p.get('unrealized_pnl_usdc'):+.2f}")
    md.append("")

    # === Recent trades ===
    closes = data.get("recent_closes", [])
    if closes:
        md.append("## Recent closes (last 15)\n")
        md.append("| Time | Symbol | Side | Qty | Price | PnL | olid |")
        md.append("|---|---|---|---|---|---|---|")
        for c in closes:
            ts = c["ts"][:19]
            md.append(f"| {ts} | {c.get('symbol')} | {c.get('side')} | "
                       f"{c.get('qty')} | {c.get('price')} | "
                       f"{c.get('pnl') if c.get('pnl') is not None else 'N/A'} | "
                       f"{c.get('olid')} |")
        md.append("")

    # === Activity ===
    md.append("## 24h activity\n")
    tc = act.get("topic_counts", {})
    for t in ("decision.snapshot", "decision.executed", "trade.open", "trade.close",
               "outcome.attributed", "agent.bounce_alert", "agent.tier_promoted",
               "agent.circuit_breaker", "agent.disk_alert",
               "agent.review.tier_audit", "agent.review.gate_optimizer",
               "agent.review.drift_monitor", "agent.review.whale_alignment"):
        n = tc.get(t, 0)
        if n: md.append(f"- {t}: {n}")
    md.append("")

    # === Market state ===
    md.append("## Market state\n")
    m = data.get("market_state", {})
    md.append(f"- BTC: ${m.get('btc_last')}  24h {m.get('btc_24h_pct'):.2f}%" if m.get("btc_24h_pct") is not None else f"- BTC: ${m.get('btc_last')}")
    md.append(f"- Range: {m.get('btc_range_24h_pct')}%  position {m.get('btc_range_position')}")
    md.append(f"- Funding: {m.get('funding_bps_per_8h')}bps/8h ({m.get('funding_bps_annual')}bps annualized)")
    md.append(f"- Basis: {m.get('basis_bps')}bps")
    md.append(f"- OI: {m.get('oi_btc')} BTC  1h change {m.get('oi_change_1h_pct')}%  label: {m.get('oi_label')}")
    md.append(f"- Orderbook: spread {m.get('spread_bps')}bps  imbalance(top5) {m.get('depth_imbalance_top5')}")
    md.append(f"- Options: ATM IV {m.get('atm_iv')}  1d implied ±${m.get('implied_1d_move_usd')} ({m.get('implied_1d_move_pct')}%)")
    md.append(f"- Skew(25Δ): {m.get('skew_25d_iv')} = {m.get('skew_label')}")
    md.append(f"- P/C OI ratio: {m.get('put_call_oi_ratio')}")
    md.append(f"- Max-pain: ${m.get('max_pain_strike')}  Gamma: {m.get('gex_label')}")
    md.append(f"- Whale flow 15m: imbalance {m.get('whale_imbalance')}  "
              f"buys ${m.get('whale_buy_usd_15m',0)/1e6:.2f}M / sells ${m.get('whale_sell_usd_15m',0)/1e6:.2f}M  "
              f"n={m.get('whale_n_trades_15m')}")
    md.append(f"- Bounce: active={m.get('bounce_active')}  dir={m.get('bounce_direction')}  mag={m.get('bounce_magnitude_pct')}%")
    md.append("")

    # === Learning ===
    md.append("## Learning progress\n")
    lp = data.get("learning_progress", {})
    md.append(f"- Training pairs: total **{lp.get('training_pairs_total', 0)}**, "
              f"today **{lp.get('training_pairs_today', 0)}**")
    pol = lp.get("policy", {})
    if pol:
        md.append(f"- Active policy v{pol.get('version')}: "
                   f"min_score={pol.get('min_score')}  max_concur={pol.get('max_concurrent')}  "
                   f"risk={pol.get('base_risk_pct',0)*100:.2f}%  "
                   f"lev_mult={pol.get('leverage_mult')}")
    cb = lp.get("circuit_breaker", {})
    md.append(f"- Circuit breaker: {cb.get('state')}")
    if lp.get("challenger_diffs"):
        md.append(f"- Pending gate changes: {len(lp['challenger_diffs'])}")
        for k, v in list(lp["challenger_diffs"].items())[:5]:
            md.append(f"  - {k}: {v['champion']} → {v['challenger']}")
    md.append("\n### Latest audits\n")
    for key, label in [
        ("tier_audit", "Tier audit"),
        ("gate_optimizer", "Gate optimizer"),
        ("drift_monitor", "Drift monitor"),
        ("whale_alignment", "Whale alignment"),
        ("joiner", "Joiner coverage"),
        ("disk_janitor", "Disk janitor"),
        ("challenger", "Challenger diff"),
    ]:
        a = lp.get(key)
        if a: md.append(f"- **{label}** {a['ts']}: {a['content']}")
    md.append("")

    # === Services / system ===
    md.append("## System & services\n")
    md.append(f"- Disk: {sh.get('disk_pct')}%  free {sh.get('disk_free_gb')}GB")
    md.append(f"- Mem used: {sh.get('mem_used_pct')}%")
    md.append(f"- Load avg: {sh.get('loadavg')}")
    md.append(f"- Active: {len(svc.get('active', []))}/{svc.get('total', 0)}")
    if svc.get("failed"):
        md.append(f"- 🚨 Failed: {', '.join(svc['failed'])}")
    if svc.get("inactive"):
        md.append(f"- ⏸ Inactive: {', '.join(svc['inactive'][:10])}"
                   + (f" (+{len(svc['inactive'])-10} more)" if len(svc['inactive']) > 10 else ""))
    md.append("")

    md.append("---")
    md.append(f"_Report generated {now.isoformat()}_")
    md.append(f"_Source: sygnif_daily_health_report.py on EC2 i-0cd5389584d70a7fc_")
    return "\n".join(md)


# ---------------------------------------------------------------------------
# Delivery channels
# ---------------------------------------------------------------------------
def push_to_gist(md: str) -> str | None:
    """Upload (or update) a GitHub secret gist via gh CLI. Returns raw URL."""
    try:
        # Read existing gist ID if we have one
        gist_id = None
        if GIST_ID_FILE.exists():
            gist_id = GIST_ID_FILE.read_text().strip()

        # Write to tmp using the EXACT filename we want in the gist
        # (gh gist uses the source file's name as the gist filename).
        tmp = pathlib.Path("/tmp/daily-health-report.md")
        tmp.write_text(md)
        filename = tmp.name

        if gist_id:
            # Update existing — overwrite the same filename
            r = subprocess.run(
                ["gh", "gist", "edit", gist_id, "-f", filename, str(tmp)],
                capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                gist_id = None
            else:
                return _gist_raw_url(gist_id, filename)

        # Create new
        r = subprocess.run(
            ["gh", "gist", "create", "--desc", "SYGNIF daily health report",
             str(tmp)],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"  gist create failed: {r.stderr[:200]}", file=sys.stderr)
            return None
        url = r.stdout.strip().split("\n")[-1]
        gist_id = url.rstrip("/").rsplit("/", 1)[-1]
        GIST_ID_FILE.write_text(gist_id)
        return _gist_raw_url(gist_id, filename)
    except Exception as e:
        print(f"  gist push failed: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def _gist_raw_url(gist_id: str, filename: str) -> str:
    # The stable raw URL for the LATEST revision of a gist file
    return f"https://gist.githubusercontent.com/Giansn/{gist_id}/raw/{filename}"


def push_to_telegram(md: str) -> bool:
    """Use the existing sygnif-telegram-relay path — write to swarm topic
    agent.commentary and let the relay forward to Telegram."""
    try:
        sys.path.insert(0, "/home/ubuntu/sygnif-agent-mirror")
        import sygnif_neurons as N
        # Send a short summary (Telegram has 4096-char limit; full md may be longer)
        head_lines = md.split("\n")[:50]
        summary = "\n".join(head_lines)
        if len(summary) > 3800:
            summary = summary[:3800] + "\n... [truncated]"
        N.run("swarm.write", {
            "content": summary,
            "swarm_id":  "trading",
            "agent_id":  "sygnif-daily-health-report",
            "topic":     "agent.commentary",
            "tags":      ["daily-health", "report", "operator"],
            "meta":      {"template_id": "daily_health_v1",
                          "salience":    "noteworthy",
                          "full_length": len(md)},
        })
        return True
    except Exception as e:
        print(f"  telegram push failed: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print(f"=== sygnif_daily_health_report @ "
          f"{dt.datetime.utcnow().isoformat()}Z ===")

    # Collect
    print("  collecting wallet, positions, closes, activity, market, learning, system...")
    data = {
        "ts_utc":             dt.datetime.now(dt.timezone.utc).isoformat(),
        "wallet":             collect_wallet(),
        "open_positions":     collect_open_positions(),
        "recent_closes":      collect_recent_closes(10),
        "activity_24h":       collect_24h_activity(),
        "market_state":       collect_market_state(),
        "learning_progress":  collect_learning_progress(),
        "system_health":      collect_system_health(),
        "services":           collect_services(),
        "gainers":            collect_gainers(),
        "onchain":            collect_onchain_intel(),
    }
    # Derive
    data["bleedings"]        = find_bleedings(data["recent_closes"],
                                                data["activity_24h"].get("realized_pnl_24h", 0))
    data["recommendations"]  = build_recommendations(data)

    # Build report
    md = build_report(data)

    # Deliver
    print("  writing local file...")
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text(md)

    print("  pushing to gist...")
    gist_url = push_to_gist(md)
    if gist_url: print(f"    → {gist_url}")

    print("  pushing to telegram via swarm relay...")
    tg_ok = push_to_telegram(md)
    print(f"    → ok={tg_ok}")

    # Save delivery metadata
    pathlib.Path("/var/lib/sygnif/daily-health-report.json").write_text(
        json.dumps({"ts_utc": data["ts_utc"],
                    "gist_url": gist_url,
                    "telegram_ok": tg_ok,
                    "size_bytes": len(md),
                    "n_bleedings": len(data["bleedings"]),
                    "n_recommendations": len(data["recommendations"]),
                    "summary": {
                        "equity_usd": data["wallet"].get("total_equity_usd"),
                        "pnl_24h":    data["activity_24h"].get("realized_pnl_24h"),
                        "open_n":     len(data["open_positions"]),
                        "training_pairs_total": data["learning_progress"].get("training_pairs_total"),
                    }}, indent=2, default=str))

    print(f"\n  === report length: {len(md)} chars ===")
    print(f"  local: {REPORT_FILE}")
    if gist_url: print(f"  gist:  {gist_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
