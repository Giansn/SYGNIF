#!/bin/sh
set -eu
python3 /freqtrade/user_data/apply_bybit_demo_to_btc_spot_config.py
exec freqtrade trade \
  --logfile /freqtrade/user_data/logs/freqtrade-btc-spot.log \
  --db-url sqlite:////freqtrade/user_data/tradesv3-btc-spot.sqlite \
  --config /tmp/config_btc_spot_runtime.json \
  --strategy SygnifStrategy
