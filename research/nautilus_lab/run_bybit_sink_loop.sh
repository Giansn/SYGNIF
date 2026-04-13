#!/bin/sh
# Back-compat — canonical feed is ``bybit_nautilus_spot_btc_training_feed.py`` (``docker-compose.yml`` profile ``btc-nautilus``).
exec python3 /lab/workspace/bybit_nautilus_spot_btc_training_feed.py --loop
