import ccxt
import json

exchange = ccxt.bybit({'options': {'defaultType': 'spot'}})
tickers = exchange.fetch_tickers()

usdt_pairs = {k: v for k, v in tickers.items() if k.endswith('/USDT') and ':' not in k and v.get('percentage') is not None and v.get('quoteVolume', 0) > 100000}

gainers = sorted(usdt_pairs.items(), key=lambda x: x[1]['percentage'], reverse=True)[:5]
losers = sorted(usdt_pairs.items(), key=lambda x: x[1]['percentage'])[:5]
by_volume = sorted(usdt_pairs.items(), key=lambda x: x[1].get('quoteVolume', 0), reverse=True)[:20]

all_pairs = list(set([p[0] for p in gainers] + [p[0] for p in losers] + [p[0] for p in by_volume]))

print("=== TOP 5 GAINERS ===")
for name, t in gainers:
    print(f"  {name}: +{t['percentage']:.1f}%")
print("=== TOP 5 LOSERS ===")
for name, t in losers:
    print(f"  {name}: {t['percentage']:.1f}%")
print(f"\nTOTAL UNIQUE PAIRS: {len(all_pairs)}")
print(sorted(all_pairs))

config_path = '/freqtrade/user_data/config.json'
with open(config_path, 'r') as f:
    config = json.load(f)
config['exchange']['pair_whitelist'] = sorted(all_pairs)
config['pairlists'] = [{"method": "StaticPairList"}]
with open(config_path, 'w') as f:
    json.dump(config, f, indent=4)
print("✅ Config updated!")
