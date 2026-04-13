#!/bin/sh
# Back-compat — canonical feed is ``bybit_nautilus_spot_btc_training_feed.py`` (see docker-compose.btc-nautilus-research.yml).
exec python3 /lab/workspace/bybit_nautilus_spot_btc_training_feed.py --loop
