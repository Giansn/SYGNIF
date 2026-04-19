#!/usr/bin/env bash
# Rebuild + restart **freqtrade-futures** (profile archived-main-traders) — compose runs BTC_Strategy_0_1.
# There is no separate freqtrade-btc-0-1 service anymore.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
docker compose --profile archived-main-traders up -d --build freqtrade-futures
docker compose --profile archived-main-traders restart freqtrade-futures
