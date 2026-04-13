#!/usr/bin/env bash
# Start GridMarketMaker on Bybit **demo** (Nautilus). Requires NAUTILUS_GRID_MM_DEMO_ACK=YES + BYBIT_DEMO_*.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAB="$ROOT/research/nautilus_lab"

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'nautilus-research'; then
  exec docker exec -w /lab/workspace -e "PYTHONPATH=/lab/workspace" nautilus-research \
    python3 /lab/workspace/run_bybit_demo_grid_market_maker.py "$@"
fi

if command -v python3 >/dev/null 2>&1 && python3 -c 'import nautilus_trader' 2>/dev/null; then
  export PYTHONPATH="${LAB}${PYTHONPATH:+:$PYTHONPATH}"
  cd "$LAB"
  exec python3 run_bybit_demo_grid_market_maker.py "$@"
fi

echo "Need 'nautilus-research' or local nautilus_trader. See research/nautilus_lab/README.md (grid live demo)." >&2
exit 1
