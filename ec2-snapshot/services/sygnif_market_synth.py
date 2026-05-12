"""Comprehensive market intelligence dump for synthesis."""
import json, sqlite3, time, urllib.request, pathlib
from collections import Counter, defaultdict

DB = "/var/lib/sygnif/swarm.db"
now = int(time.time())

def jget(url, headers=None, t=8):
    try:
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "sygnif"})
        return json.loads(urllib.request.urlopen(req, timeout=t).read())
    except Exception as e:
        return {"_err": str(e)}

def jload(p):
    try: return json.loads(pathlib.Path(p).read_text())
    except Exception: return {}

print("="*70)
print("MARKET INTELLIGENCE DUMP @ " + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
print("="*70)

# === 1. Current price + microstructure ===
print("\n[1] PRICE STRUCTURE (Bybit + cross-exchange)")
r = jget("https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT")
tic = ((r.get("result") or {}).get("list") or [{}])[0]
mark = float(tic.get("lastPrice", 0))
funding = float(tic.get("fundingRate", 0))*100
oi_usd = float(tic.get("openInterestValue", 0))/1e6
print(f"  bybit mark: ${mark:,.1f}")
print(f"  funding: {funding:+.4f}%   OI: ${oi_usd:,.1f}M   24h vol: ${float(tic.get('turnover24h',0))/1e9:.2f}B")
print(f"  24h range: ${float(tic.get('lowPrice24h',0)):,.0f} - ${float(tic.get('highPrice24h',0)):,.0f}")
print(f"  24h change: {float(tic.get('price24hPcnt',0))*100:+.2f}%")

# Cross-venue
cb = jget("https://api.exchange.coinbase.com/products/BTC-USD/ticker")
bn = jget("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
cb_p = float(cb.get("price", 0)) if "price" in cb else None
bn_p = float(bn.get("price", 0)) if "price" in bn else None
if cb_p and bn_p:
    print(f"  coinbase USD: ${cb_p:,.1f}  binance USDT: ${bn_p:,.1f}  bybit: ${mark:,.1f}")
    print(f"  cb→bn: {(cb_p-bn_p)/bn_p*10000:+.1f} bps")
    print(f"  bn→bb: {(bn_p-mark)/mark*10000:+.1f} bps (perp basis)")

# === 2. Chain-intel summary 24h ===
print("\n[2] BTC CHAIN INTELLIGENCE (24h)")
s = jload("/var/lib/sygnif/chain_state.json")
since = now - 86400
events = [e for e in (s.get("recent_events") or []) if e.get("ts", 0) >= since]
cat_btc = defaultdict(float)
flag_count = Counter()
for e in events:
    cat_btc[e.get("category","?")] += e.get("value_btc", 0) or 0
    for fl in (e.get("flags") or []):
        flag_count[fl] += 1
print(f"  wallets tracked: {len(s.get('wallets',{}))}")
print(f"  clusters tracked: {len(s.get('clusters',{}))}")
print(f"  events 24h: {len(events)}")
for cat, btc in sorted(cat_btc.items(), key=lambda x: -x[1]):
    if btc > 0:
        usd = btc * mark
        print(f"    {cat:<26} {btc:>9,.1f} BTC  ${usd/1e6:,.1f}M")
print(f"  flags fired 24h: {dict(flag_count.most_common())}")
net_bull = (cat_btc.get("WITHDRAWAL_FROM_EXCHANGE",0)
            + cat_btc.get("ACCUMULATION_TO_COLD",0))
net_bear = cat_btc.get("DEPOSIT_TO_EXCHANGE",0)
print(f"  NET visible flow: {net_bull-net_bear:+,.1f} BTC = ${(net_bull-net_bear)*mark/1e6:+,.1f}M")

# === 3. EVM mints + reserves 24h ===
print("\n[3] EVM SIGNALS 24h")
s = jload("/var/lib/sygnif/evm_state.json")
mints = [m for m in (s.get("recent_mints") or []) if m.get("ts", 0) >= since]
eth_usdt = sum(m.get("amount_usd",0) for m in mints if m.get("token")=="USDT")
eth_usdc = sum(m.get("amount_usd",0) for m in mints if m.get("token")=="USDC")
print(f"  ETH USDT mints: {sum(1 for m in mints if m.get('token')=='USDT')} events  ${eth_usdt/1e6:,.0f}M")
print(f"  ETH USDC mints: {sum(1 for m in mints if m.get('token')=='USDC')} events  ${eth_usdc/1e6:,.0f}M")
# Top reserves
resv = s.get("exchange_reserves") or {}
if resv:
    print("  top exchange wallet balances (USDT-denominated where >$100M):")
    flat = []
    for exch, info in resv.items():
        bals = info.get("balances", {})
        for tok, val in bals.items():
            usd_val = val if tok in ("USDT","USDC","DAI") else (val*mark if tok=="WBTC" else 0)
            if usd_val > 100_000_000:
                flat.append((exch, tok, val, usd_val))
    flat.sort(key=lambda x: -x[3])
    for exch, tok, val, usd in flat[:8]:
        print(f"    {exch:<14s} {tok:<5s} {val:>14,.0f}  (${usd/1e6:,.0f}M)")

# === 4. Tron USDT 24h ===
print("\n[4] TRON SIGNALS 24h")
s = jload("/var/lib/sygnif/tron_state.json")
tron_mints = [m for m in (s.get("recent_mints") or []) if m.get("ts", 0) >= since]
tron_usdt = sum(m.get("amount_usd",0) for m in tron_mints if m.get("token")=="USDT")
print(f"  Tron USDT mints: {len(tron_mints)} events  ${tron_usdt/1e6:,.0f}M")
# Top destinations
dest_ctr = Counter((m.get("to","")[:10], m.get("to_entity","unlabeled")) for m in tron_mints)
print(f"  top destinations:")
for (dest, entity), n in dest_ctr.most_common(5):
    total = sum(m.get("amount_usd",0) for m in tron_mints
                if m.get("to","").startswith(dest))
    print(f"    {dest}...  {entity:<30s} {n} mints  ${total/1e6:,.0f}M")

# === 5. Cross-exchange liquidations ===
print("\n[5] MULTI-EXCHANGE LIQUIDATIONS")
s = jload("/var/lib/sygnif/xchg_liq_state.json")
events = [e for e in (s.get("recent_events") or []) if e.get("ts", 0) >= since]
clusters = [c for c in (s.get("recent_clusters") or []) if c.get("ts", 0) >= since]
by_exch = Counter(e.get("exchange") for e in events)
long_val = sum(e.get("value_usd",0) for e in events if e.get("side") == "LONG_LIQ")
short_val = sum(e.get("value_usd",0) for e in events if e.get("side") == "SHORT_LIQ")
print(f"  events 24h: {len(events)}  clusters: {len(clusters)}")
print(f"  by exchange: {dict(by_exch)}")
print(f"  long-liq: ${long_val/1e6:,.1f}M  short-liq: ${short_val/1e6:,.1f}M")
print(f"  net: {'longs' if long_val > short_val else 'shorts'} got hit harder")

# === 6. Premium / basis trend ===
print("\n[6] PREMIUM / BASIS (latest 5 + 24h avg)")
s = jload("/var/lib/sygnif/market_premium.json")
hist = s.get("history") or []
last_5 = hist[-5:]
in24h = [h for h in hist if h.get("ts",0) >= since]
if in24h:
    avg_cb = sum(h.get("cb_bn_bps",0) for h in in24h)/len(in24h)
    avg_bb = sum(h.get("bn_bb_bps",0) for h in in24h)/len(in24h)
    print(f"  24h avg: cb→bn {avg_cb:+.2f} bps  |  bn→bb {avg_bb:+.2f} bps")
    print(f"  latest 5 readings:")
    for h in last_5:
        ts = time.strftime("%H:%M", time.gmtime(h.get("ts",0)))
        print(f"    {ts}  cb→bn {h.get('cb_bn_bps',0):+5.1f}  bn→bb {h.get('bn_bb_bps',0):+5.1f}")

# === 7. Ecosystem state ===
print("\n[7] ECOSYSTEM")
s = jload("/var/lib/sygnif/ecosystem_state.json")
dh = s.get("dominance_history") or []
if dh:
    latest = dh[-1]
    print(f"  BTC dominance: {latest.get('btc_dom')}%   ETH dom: {latest.get('eth_dom')}%")
    print(f"  total mcap: ${latest.get('total_mcap',0)/1e12:.2f}T")
    print(f"  24h mcap change: {latest.get('mcap_chg_24h_pct',0):+.2f}%")

sh = s.get("stablecoin_history") or []
if sh:
    latest = sh[-1]
    chains = latest.get("chains", {})
    print(f"  stablecoin supply by chain:")
    for chain, data in sorted(chains.items(), key=lambda x: -x[1].get("total_usd",0)):
        print(f"    {chain:<14s} ${data.get('total_usd',0)/1e9:>7,.1f}B")

ep = s.get("entity_portfolios") or {}
if ep:
    print(f"  entity portfolios (Goldrush):")
    for label, snap in sorted(ep.items(), key=lambda x: -x[1].get("total_usd",0)):
        usd = snap.get("total_usd", 0)
        if usd > 1e6:
            print(f"    {label:<32s} ${usd/1e6:,.1f}M  on {snap.get('chain')}")

# === 8. ChartInspect MSTR purchases ===
print("\n[8] CHARTINSPECT — MSTR purchases (most recent 5)")
ck = "ci_live_ea38afc04d3dbdaa1844a5fb640bb956d71767d4086c7b6e59bcf93c08177959"
r = jget("https://chartinspect.com/api/v1/economic/mstr-purchases?limit=5",
         {"X-API-KEY": ck})
for p in r.get("data", [])[:5]:
    print(f"    {p.get('date')}  +{p.get('btc_count'):,} BTC @ ${p.get('average_price'):,}  total ${p.get('total_purchase_price')/1e6:,.0f}M")

# === 9. Predict-loop signal ===
print("\n[9] PREDICT-LOOP ML SIGNAL (most recent + last 12)")
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = c.execute("SELECT created, content FROM swarm_entries WHERE topic='forecast' AND created>? ORDER BY created DESC LIMIT 12",(since,)).fetchall()
labels = []
last_signal = None
last_action = None
for ts, content in rows:
    try:
        d = json.loads(content) if content else {}
    except: d={}
    reg = (d.get("regime") or {}).get("label") if isinstance(d.get("regime"), dict) else d.get("regime")
    labels.append(reg)
    if last_signal is None:
        last_signal = d.get("signal")
        last_action = d.get("action")
print(f"  latest 12 regimes: {labels}")
print(f"  latest signal: {last_signal}  action: {last_action}")

# === 10. Recent trades + open positions ===
print("\n[10] TRADING STATE")
rows = c.execute("SELECT created, content, meta FROM swarm_entries WHERE topic='trader.heartbeat' ORDER BY created DESC LIMIT 1").fetchall()
for ts, content, meta in rows:
    print(f"  latest heartbeat: {content[:160]}")

# Open positions
rows = c.execute(
    "SELECT json_extract(meta,'$.symbol'), json_extract(meta,'$.side'), "
    "json_extract(meta,'$.size'), json_extract(meta,'$.avgPrice'), "
    "json_extract(meta,'$.markPrice'), json_extract(meta,'$.unrealisedPnl'), "
    "json_extract(meta,'$.stopLoss') "
    "FROM swarm_entries WHERE topic='position.snapshot' "
    "ORDER BY created DESC LIMIT 5").fetchall()
print("  position snapshots: (skipping, query topic differs)")

# Recent closes 24h
rows = c.execute("SELECT created, content, meta FROM swarm_entries "
                  "WHERE topic='trade.close' AND created>? "
                  "ORDER BY created DESC LIMIT 6",(since,)).fetchall()
print(f"  recent closes:")
for ts, content, meta in rows[:6]:
    try:
        m = json.loads(meta) if meta else {}
        pnl = m.get("closed_pnl", "?")
        sym = m.get("symbol", "?")
        side = m.get("side", "?")
        print(f"    {time.strftime('%H:%M', time.gmtime(ts))}  {sym} {side}  pnl={pnl}")
    except: pass

c.close()

# === 11. Trail daemon state ===
print("\n[11] TRAIL DAEMON")
try:
    with open("/var/log/sygnif/trailing-daemon.log") as f:
        lines = f.readlines()
    hbs = [l for l in lines if "[HB]" in l][-3:]
    ratchets = [l for l in lines if "[RATCHET]" in l][-2:]
    for l in hbs + ratchets:
        print(f"  {l.strip()[:120]}")
except Exception as e:
    print(f"  err: {e}")

print("\n" + "="*70)
print("END")
print("="*70)
