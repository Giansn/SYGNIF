#!/usr/bin/env python3
"""Full integration test: finance_agent → overseer → Plutus-3B."""
import sys
import os
import time
import requests

sys.path.insert(0, "/home/ubuntu/trade_overseer")
sys.path.insert(0, "/home/ubuntu/finance_agent")

# Start overseer HTTP server
import overseer
overseer.start_http_server()
time.sleep(1)
print("Overseer :8090 ready\n")

# Import finance_agent
import bot

# STEP 1: Market scan
print("=== STEP 1: Market scan ===")
tickers = bot.bybit_tickers()
pairs = []
exclude = {"USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDD", "USDP", "USDS", "USDE"}
for t in tickers:
    sym = t.get("symbol", "")
    if not sym.endswith("USDT"):
        continue
    base = sym.replace("USDT", "")
    if base in exclude or any(x in base for x in ("2L", "3L", "5L", "2S", "3S", "5S")):
        continue
    try:
        change = float(t.get("price24hPcnt", 0)) * 100
        turnover = float(t.get("turnover24h", 0))
        price = float(t.get("lastPrice", 0))
    except (ValueError, TypeError):
        continue
    if turnover < 1_000_000:
        continue
    pairs.append({"sym": base, "price": price, "change": change, "vol": turnover})

top_vol = sorted(pairs, key=lambda x: x["vol"], reverse=True)[:5]
gainers = sorted(pairs, key=lambda x: x["change"], reverse=True)[:3]
losers = sorted(pairs, key=lambda x: x["change"])[:3]
print(f"  {len(pairs)} pairs")
print(f"  Top volume: {', '.join(p['sym'] for p in top_vol)}")
print(f"  Gainers: {', '.join(p['sym'] + ' ' + format(p['change'], '+.1f') + '%' for p in gainers)}")
print(f"  Losers: {', '.join(p['sym'] + ' ' + format(p['change'], '+.1f') + '%' for p in losers)}")

# BTC TA
btc_df = bot.bybit_kline("BTCUSDT", "60", 200)
btc_ind = bot.calc_indicators(btc_df) if not btc_df.empty else {}
btc_ctx = ""
if btc_ind:
    btc_ctx = f"BTC: ${btc_ind['price']:,.0f}, {btc_ind['trend']}, RSI {btc_ind['rsi']:.0f}"
    print(f"  {btc_ctx}")

market_ctx = "\n".join(
    f"  {p['sym']}: ${p['price']:.4g} ({p['change']:+.1f}%) vol ${p['vol']/1e6:.0f}M"
    for p in top_vol
)

# STEP 2: Generate plays (Claude Haiku)
print("\n=== STEP 2: Claude Haiku -> plays ===")
prompt = (
    "You are a crypto strategist. Give exactly 3 plays from this data:\n\n"
    f"{market_ctx}\n{btc_ctx}\n\n"
    "Per play: **Play #N: [TICKER Name]**\n"
    "Type, Entry $price, TP $price, SL $price, timeframe.\n"
    "2 sentences max per play. Under 200 words total."
)

analysis = bot.claude_analyze(prompt, max_tokens=1000)
if analysis and len(analysis) > 50:
    print(analysis[:500])
else:
    print(f"  Claude returned: {analysis}")
    print("  (API key may not be set — using mock plays)")
    analysis = (
        "**Play #1: BTC Range Scalp**\n"
        "Type: Range Trade. Entry: $67,200, TP: $67,800, SL: $66,800. Timeframe: 12-24h.\n"
        "BTC consolidating near resistance, scalp the range.\n\n"
        "**Play #2: SOL Mean Reversion**\n"
        "Type: Mean Reversion. Entry: $79.50, TP: $83.00, SL: $77.00. Timeframe: 1-3 days.\n"
        "SOL oversold on daily, bounce expected.\n\n"
        "**Play #3: EDGE Momentum**\n"
        "Type: Momentum. Entry: $0.92, TP: $1.10, SL: $0.85. Timeframe: 1-2 days.\n"
        "High volume breakout, riding momentum."
    )

# STEP 3: POST to overseer
print("\n\n=== STEP 3: finance_agent -> overseer (POST /plays) ===")
r = requests.post(
    "http://127.0.0.1:8090/plays",
    json={"raw_text": analysis, "market_context": market_ctx},
    timeout=5,
)
print(f"  POST /plays: {r.status_code}")

# Verify cross-reference
import plays_store
import ft_client

plays = plays_store.load_plays()
trades = ft_client.get_all_trades()
matches = plays_store.match_trades_to_plays(trades, plays)
print(f"  Symbols extracted: {plays.get('symbols', [])}")
print(f"  Matched to {len(matches)} open trades:")
for m in matches:
    t = m["trade"]
    flags = "NEAR TP!" if m["approaching_tp"] else ("NEAR SL!" if m["approaching_sl"] else "tracking")
    print(f"    {t['pair']}[{t['instance'][0]}] -> {m['play_symbol']} [{flags}]")

# STEP 4: Plutus-3B evaluation
print("\n=== STEP 4: overseer -> Plutus-3B evaluation ===")
print("  Evaluating (30-90s)...")
start = time.time()
r = requests.post("http://127.0.0.1:8090/evaluate", timeout=120)
data = r.json()
elapsed = time.time() - start
print(f"  Done ({elapsed:.0f}s)\n")
print(data.get("commentary", "(empty)"))
