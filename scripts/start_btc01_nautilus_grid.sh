#!/usr/bin/env bash
# Start **nautilus-grid-btc01**: Nautilus GridMarketMaker on Bybit demo for **BTCUSDT-LINEAR** (btc-0-1 universe).
#
# Requires in **`.env`** / ``SYGNIF_SECRETS_ENV_FILE`` (same bundle — see ``.env.example`` next to ``BYBIT_DEMO_*``):
#   BYBIT_DEMO_API_KEY, BYBIT_DEMO_API_SECRET, NAUTILUS_GRID_MM_DEMO_ACK=YES
#
# Optional sizing (defaults in docker-compose.yml):
#   NAUTILUS_GRID_BTC01_TRADE_SIZE, NAUTILUS_GRID_BTC01_MAX_POSITION, NAUTILUS_GRID_BTC01_NUM_LEVELS,
#   NAUTILUS_GRID_BTC01_GRID_STEP_BPS, NAUTILUS_GRID_BTC01_REQUOTE_BPS
#
# **Do not** run against the same Bybit demo account as ``freqtrade-btc-0-1`` unless you accept conflicting orders.
# For always-on + reboot: ``COMPOSE_PROFILES=btc-grid-mm`` in ``.env`` and ``docker compose up -d`` from repo root.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! docker compose -f docker-compose.yml --profile btc-grid-mm config --services 2>/dev/null | grep -qx 'nautilus-grid-btc01'; then
  echo "Service nautilus-grid-btc01 not found (enable profile btc-grid-mm in docker-compose.yml)" >&2
  exit 1
fi

exec docker compose -f docker-compose.yml --profile btc-grid-mm up -d nautilus-grid-btc01
