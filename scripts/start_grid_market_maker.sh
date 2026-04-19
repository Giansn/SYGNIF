#!/usr/bin/env bash
# Start GridMarketMaker backtest in order: PREPARE → RUN (inside nautilus-research if available).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAB="$ROOT/research/nautilus_lab"

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'nautilus-research'; then
  exec docker exec -w /lab/workspace nautilus-research python3 /lab/workspace/run_grid_market_maker_backtest.py "$@"
fi

NT_PY="${LAB}/.venv/bin/python"
if [[ -x "$NT_PY" ]] && "$NT_PY" -c 'import nautilus_trader' 2>/dev/null; then
  cd "$LAB"
  exec "$NT_PY" run_grid_market_maker_backtest.py "$@"
fi

if command -v python3 >/dev/null 2>&1 && python3 -c 'import nautilus_trader' 2>/dev/null; then
  cd "$LAB"
  exec python3 run_grid_market_maker_backtest.py "$@"
fi

echo "Neither docker container 'nautilus-research' nor a Python env with nautilus_trader is available." >&2
echo "Install: pip install -r $ROOT/research/nautilus_lab/requirements-bybit-demo-live.txt" >&2
echo "Or restore a Nautilus image from archive/freqtrade-btc-dock-2026-04-13/ and run a container named nautilus-research." >&2
exit 1
