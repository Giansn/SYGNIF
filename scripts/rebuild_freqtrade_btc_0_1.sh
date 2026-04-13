#!/usr/bin/env bash
# Rebuild (patches) + ensure BTC 0.1 paper trader is up; restart so volume-mounted
# user_data/strategies/*.py is reloaded (image alone may stay cached).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
docker compose --profile btc-0-1 up -d --build freqtrade-btc-0-1
docker compose restart freqtrade-btc-0-1
