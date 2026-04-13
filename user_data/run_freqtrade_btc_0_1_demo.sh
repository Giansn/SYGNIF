#!/bin/sh
set -eu
python3 /freqtrade/user_data/bybit_ccxt_demo_patch.py
python3 /freqtrade/user_data/apply_bybit_demo_to_btc_0_1_config.py
exec /home/ftuser/.local/bin/freqtrade trade \
  --logfile /freqtrade/user_data/logs/freqtrade-btc-0-1.log \
  --db-url sqlite:////freqtrade/user_data/tradesv3-btc01-bybit-demo.sqlite \
  --config /tmp/config_btc_0_1_runtime.json \
  --strategy BTC_Strategy_0_1
