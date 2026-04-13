#!/usr/bin/env bash
# Start **nautilus-btc-testnet**: Nautilus `TradingNode` + `SygnifBtcBarNodeStrategy` on **Bybit testnet**
# (`run_sygnif_btc_trading_node.py --testnet`). Default: bars only (no orders).
#
# Requires in secrets `.env` / `SYGNIF_SECRETS_ENV_FILE`:
#   BYBIT_TESTNET_API_KEY, BYBIT_TESTNET_API_SECRET  (https://testnet.bybit.com/)
#
# For **post-only limit probes** on testnet, set `NAUTILUS_SYGNIF_NODE_EXEC_ACK=YES` and use a compose override
# to append `--exec-order-qty 0.001` (or run `docker compose run` with extra args).
#
# **Nightly engine:** the public GHCR image `ghcr.io/nautechsystems/nautilus-trader:nightly` is not anonymously
# pullable. To try a newer wheel, rebuild `docker/Dockerfile.nautilus_research` with e.g.
#   RUN pip install --no-cache-dir -U --pre "nautilus_trader>=1.225,<2"
# or install inside a one-off container before starting.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! docker compose -f docker-compose.yml --profile btc-testnet config --services 2>/dev/null | grep -qx 'nautilus-btc-testnet'; then
  echo "Service nautilus-btc-testnet not found (check docker-compose.yml profile btc-testnet)" >&2
  exit 1
fi

exec docker compose -f docker-compose.yml --profile btc-testnet up -d --build nautilus-btc-testnet
