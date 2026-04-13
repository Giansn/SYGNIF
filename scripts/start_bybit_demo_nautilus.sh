#!/usr/bin/env bash
# Run Bybit demo TradingNode smoke (quotes only) inside nautilus-research, or locally if nautilus_trader is installed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAB="$ROOT/research/nautilus_lab"

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'nautilus-research'; then
  exec docker exec -w /lab/workspace -e "PYTHONPATH=/lab/workspace" nautilus-research \
    python3 /lab/workspace/run_bybit_demo_trading_node.py "$@"
fi

if command -v python3 >/dev/null 2>&1 && python3 -c 'import nautilus_trader' 2>/dev/null; then
  export PYTHONPATH="${LAB}${PYTHONPATH:+:$PYTHONPATH}"
  cd "$LAB"
  exec python3 run_bybit_demo_trading_node.py "$@"
fi

echo "Need docker container 'nautilus-research' or local nautilus_trader. See research/nautilus_lab/README.md § Bybit demo live." >&2
exit 1
