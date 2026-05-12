#!/usr/bin/env python3
"""sygnif_institutional_search.py — Search for institutional BTC movement signatures.

Two-pronged approach:
  1. KNOWN ENTITY POLLING — check balance + recent activity of a curated list of
     publicly-known institutional, ETF custody, miner, and treasury addresses.
     Source: public disclosures, on-chain forensic firms, MicroStrategy SEC filings,
     ETF prospectuses. ADDRESSES ROTATE — labels degrade over time.

  2. PATTERN MATCHING — scan our own onchain_state.json registry for high-conviction
     COLD_ACCUMULATOR signatures (large balance, low spend, growing). These are
     proxies for institutional activity even when we can't put a name to them.

  3. ETF FLOW AGGREGATE — scrape Farside Investors public dashboard for daily
     ETF net inflows. No API needed, HTML parse.

Output: structured digest of institutional flow indicators.
"""
from __future__ import annotations

import json, pathlib, sys, time, urllib.parse, urllib.request, re
from collections import defaultdict

STATE = pathlib.Path("/var/lib/sygnif/onchain_state.json")
MEMPOOL = "https://mempool.space/api"
HEADERS = {"User-Agent": "sygnif-institutional/1.0"}


# ---------------------------------------------------------------------------
# CURATED INSTITUTIONAL ADDRESS LIST  (best-effort, see notes inline)
# Confidence: H = high (publicly disclosed), M = medium (forensic-attributed),
#             L = low (rumored / aging)
# ---------------------------------------------------------------------------
INSTITUTIONAL = [
    # --- MicroStrategy (Saylor) — public SEC disclosures ---
    ("3LjDcvFs73Wb8wEXcs9d5KQ8AbnEVdMrnY",  "MicroStrategy", "MSTR-acquisition", "M"),
    # --- BlackRock IBIT ETF — Coinbase Prime custody, multi-cluster ---
    ("bc1q7zyrumxk8jdlu4ts6h82q3uvavck98t8amsynt", "BlackRock IBIT", "ETF-cold (CB Prime)", "L"),
    # --- Coinbase Prime institutional cold (handles many ETF custodies) ---
    ("bc1q9aksx5kpfgtuue0dey6c7s9zd8aj6evzs2ftxe", "Coinbase Prime", "institutional-cold", "L"),
    # --- Grayscale GBTC custody (Coinbase) — known cluster (rotates) ---
    ("36n452uGq1x4afyKKX3PnGwJV5YsVjjvX9", "Grayscale GBTC", "ETF-cold (CB)", "L"),
    # --- Fidelity Digital Assets (FBTC custodian) ---
    ("bc1qhuv3dhpnm0wktasd3v0kt6e4aqfqsd0uhfdu7d", "Fidelity FBTC", "ETF-custody", "L"),
    # --- Gemini custody (HODL ETF, Winklevoss etc) ---
    ("bc1q8lqwjepnxntj7p8m25v9zlry7vlqcrn34v7v8z", "Gemini", "ETF-custody", "L"),
    # --- BitGo (multi-sig institutional custody) ---
    ("38UmuUqPCrFmQo4khkomQwZ4VbY2nZMJ67", "BitGo", "institutional", "L"),
    # --- Tether reserves (Bitfinex cold, partially Tether-backed) ---
    ("bc1qjasf9z3h7w3jspkhtgatgpyvvzgpa2wwd2lr0eh5tx44reyn2k7sfc27a4",
     "Bitfinex Cold (Tether-related)", "treasury", "H"),
    # --- Public BTC miners (treasury) ---
    ("1HQ3Go3ggs8pFnXuHVHRytPCq5fGG8Hbhx", "Marathon Digital", "miner-treasury", "L"),
    ("3HLkofbW5tDgZBkR3UFGdjcFK6dyVKpKZF", "Riot Platforms", "miner-treasury", "L"),
    # --- US Government seized coins (DOJ marshals) ---
    ("1ETh1xWf6vNoMQAxYwhq5UN4w5T9YyaH28", "US Government", "seizure-cold", "M"),
    # --- El Salvador sovereign BTC ---
    ("32ixEdVJWo3kmvJGMTZq5jAQVZZeuwnqzo", "El Salvador", "sovereign", "L"),
    # --- Mt.Gox trustee distributions (defunct but legacy big mover) ---
    ("1FeexV6bAHb8ybZjqQMjJrcCrHGW9sb6uF", "Mt.Gox Trustee", "legacy-cold", "M"),
]


def jget(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        body = urllib.request.urlopen(req, timeout=timeout).read()
        return json.loads(body)
    except Exception as e:
        return {"_err": f"{type(e).__name__}: {e}"}


def text_get(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", errors="ignore")
    except Exception as e:
        return None


def addr_summary(info):
    if not info or info.get("_err"):
        return None
    cs = info.get("chain_stats", {}) or {}
    ms = info.get("mempool_stats", {}) or {}
    funded = (cs.get("funded_txo_sum", 0) + ms.get("funded_txo_sum", 0)) / 1e8
    spent  = (cs.get("spent_txo_sum",  0) + ms.get("spent_txo_sum",  0)) / 1e8
    n_tx   = cs.get("tx_count", 0) + ms.get("tx_count", 0)
    return {"balance": funded - spent, "n_tx": n_tx,
            "total_received": funded, "total_sent": spent}


def fetch_recent_txs(addr, limit=10):
    """Pull last few txs to detect activity in last 24h."""
    r = jget(f"{MEMPOOL}/address/{addr}/txs")
    if isinstance(r, dict) and r.get("_err"):
        return []
    return r[:limit] if isinstance(r, list) else []


def fmt_btc(b):
    return f"{b:>10,.1f} BTC"


# ---------------------------------------------------------------------------
def main():
    print(f"=== INSTITUTIONAL FLOW SEARCH  @  {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")

    # ====== PART 1: KNOWN ENTITY POLLING ======
    print(f"━━━ 1. KNOWN INSTITUTIONAL ADDRESSES — public-disclosure tracking ━━━")
    print(f"  Note: confidence H/M/L. Many addresses rotate; H = SEC-filed / verified.")
    print(f"  {'entity':<35s} {'conf':<5s} {'balance':>12s} {'n_tx':>8s}  recent_active")
    print(f"  {'-'*35} {'-'*5} {'-'*12} {'-'*8}  {'-'*15}")

    now = int(time.time())
    active_24h = []
    for addr, entity, label, conf in INSTITUTIONAL:
        info = jget(f"{MEMPOOL}/address/{addr}")
        time.sleep(1.0)
        s = addr_summary(info)
        if s is None:
            print(f"  {entity[:35]:<35s} {conf:<5s} {'lookup-fail':>12s}")
            continue
        # Detect activity
        recent = fetch_recent_txs(addr, 5)
        time.sleep(1.0)
        last_active = None
        for t in recent:
            if t.get("status", {}).get("confirmed"):
                ts = t.get("status", {}).get("block_time", 0)
                if ts and (last_active is None or ts > last_active):
                    last_active = ts
        if last_active:
            age_h = (now - last_active) / 3600
            recent_str = (f"{age_h:.1f}h ago" if age_h < 48 else
                          f"{int(age_h/24)}d ago")
            if age_h < 24:
                active_24h.append((entity, label, conf, s, last_active, recent))
        else:
            recent_str = "—"
        print(f"  {entity[:35]:<35s} {conf:<5s} {fmt_btc(s['balance'])} "
              f"{s['n_tx']:>8,d}  {recent_str}")

    if active_24h:
        print(f"\n  >> {len(active_24h)} institutional address(es) ACTIVE in last 24h:")
        for entity, label, conf, s, last_ts, recent in active_24h:
            age_h = (now - last_ts) / 3600
            print(f"\n     [{entity}] ({label}, conf={conf})")
            print(f"        balance: {s['balance']:,.1f} BTC   n_tx: {s['n_tx']:,}")
            print(f"        last activity: {time.strftime('%H:%M:%S UTC %Y-%m-%d', time.gmtime(last_ts))}  ({age_h:.1f}h ago)")
            # Show direction of recent txs
            for t in recent[:3]:
                ts_t = t.get("status", {}).get("block_time", 0)
                if not ts_t or (now - ts_t)/3600 > 48: continue
                v_in = sum(o.get("value",0) for o in t.get("vin",[])
                            if (o.get("prevout") or {}).get("scriptpubkey_address") == addr) / 1e8
                v_out = sum(o.get("value",0) for o in t.get("vout",[])
                             if o.get("scriptpubkey_address") == addr) / 1e8
                net = v_out - v_in
                direction = ("RECEIVED" if net > 0 else "SENT")
                print(f"        {time.strftime('%H:%M', time.gmtime(ts_t))}  "
                      f"{direction} {abs(net):,.4f} BTC   tx={t.get('txid','?')[:16]}...")
    else:
        print(f"\n  >> No institutional addresses ACTIVE in last 24h (or rotation hides them)")

    # ====== PART 2: OUR REGISTRY — PATTERN-MATCHED INSTITUTIONAL PROXIES ======
    print(f"\n━━━ 2. PATTERN-DETECTED COLD ACCUMULATORS (from our oversight registry) ━━━")
    print(f"  These are unknown wallets exhibiting INSTITUTIONAL SIGNATURES:")
    print(f"  - large balance (>=500 BTC)")
    print(f"  - low transaction count (<=50)")
    print(f"  - little or no spending")
    print(f"  - recent activity (last 7 days)\n")

    if not STATE.exists():
        print("  (oversight state not found — watcher not running?)")
    else:
        state = json.loads(STATE.read_text())
        wallets = state.get("wallets", {}).values()
        institutional_proxies = []
        for w in wallets:
            bal   = w.get("balance_btc", 0) or 0
            n_tx  = w.get("n_tx", 0) or 0
            sent  = w.get("total_sent_btc", 0) or 0
            recv  = w.get("total_received_btc", 0) or 0
            if bal >= 500 and n_tx <= 50 and (recv == 0 or sent / max(recv, 1) < 0.05):
                institutional_proxies.append(w)
            elif w.get("tier") in ("COLD_ACCUMULATOR", "FRESH_COLD") and bal >= 100:
                institutional_proxies.append(w)
        institutional_proxies.sort(key=lambda w: -w.get("balance_btc", 0))

        if institutional_proxies:
            print(f"  {'tier':<18s} {'balance':>12s} {'n_tx':>6s}  addr (label)")
            for w in institutional_proxies[:15]:
                print(f"  {w.get('tier','?'):<18s} {w.get('balance_btc',0):>10,.1f} BTC "
                      f"{w.get('n_tx',0):>6,d}  {w.get('addr','?')[:18]}...  "
                      f"{w.get('label','')[:55]}")
        else:
            print(f"  (no high-conviction proxies in registry yet — watcher needs more time)")

    # ====== PART 3: ETF AGGREGATE FLOW (Farside dashboard, free HTML scrape) ======
    print(f"\n━━━ 3. ETF AGGREGATE FLOWS (Farside Investors, public dashboard) ━━━")
    farside = text_get("https://farside.co.uk/bitcoin-etf-flow-all-data/")
    if farside:
        # Looking for a recent total row like: "23-May-2026,...,Total"
        # The page is HTML — extract the latest row of the data table.
        # Be very conservative: just look for the most recent date line and totals.
        lines = farside.split("\n")
        # Find lines that look like data rows (start with date)
        date_pat = re.compile(r'(\d{1,2})[-/](\w{3})[-/](\d{4})')
        # Find Total figures
        total_pat = re.compile(r'Total Net Inflow.*?\$([+-]?[\d,]+\.?\d*)\s*(b|m|M|B)', re.IGNORECASE)
        m = total_pat.search(farside)
        if m:
            val = m.group(1).replace(",", "")
            unit = m.group(2).upper()
            print(f"  ETF cumulative net inflow (Farside-stated): ${val}{unit}")
        # Look for recent daily flow in the table
        # Farside structure varies; fallback: just report we got the page
        print(f"  (parsed {len(farside):,} chars from farside.co.uk)")
        # Find most recent date mentioned
        dates = date_pat.findall(farside)
        if dates:
            print(f"  latest dates seen: {[''.join([d[0],d[1],d[2]]) for d in dates[:5]]}")
    else:
        print(f"  (could not reach farside.co.uk — likely network/SSL or rate-limited)")

    # ====== PART 4: CROSS-CORRELATION ======
    print(f"\n━━━ 4. INTERPRETATION ━━━")
    if STATE.exists():
        state = json.loads(STATE.read_text())
        events = state.get("recent_events", [])
        cat_count = defaultdict(int)
        cat_btc = defaultdict(float)
        for e in events:
            cat_count[e.get("category","?")] += 1
            cat_btc[e.get("category","?")] += e.get("value_btc",0) or 0
        if events:
            print(f"  Visible on-chain flow categories (from our oversight):")
            for c, n in sorted(cat_count.items(), key=lambda x: -x[1]):
                print(f"    {c:<28s} count={n:>3d}  total={cat_btc[c]:>9,.1f} BTC "
                      f"(~${cat_btc[c]*81850/1e6:>6,.1f}M)")
            net = cat_btc.get("WITHDRAWAL_FROM_EXCHANGE",0) + cat_btc.get("ACCUMULATION_TO_COLD",0) \
                 - cat_btc.get("DEPOSIT_TO_EXCHANGE",0)
            print(f"\n  Net visible flow:   {net:+,.1f} BTC  (~${net*81850/1e6:+,.1f}M)")
            print(f"    (positive = bullish accumulation, negative = bearish distribution)")


if __name__ == "__main__":
    sys.exit(main() or 0)
