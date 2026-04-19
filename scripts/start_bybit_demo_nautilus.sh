#!/usr/bin/env bash
# Run Bybit demo TradingNode smoke (quotes only). Prefers local venv with nautilus_trader; optional docker exec if a container named nautilus-research is running.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAB="$ROOT/research/nautilus_lab"

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'nautilus-research'; then
  exec docker exec -w /lab/workspace -e "PYTHONPATH=/lab/workspace" nautilus-research \
    python3 /lab/workspace/run_bybit_demo_trading_node.py "$@"
fi

NT_PY="${LAB}/.venv/bin/python"
if [[ -x "$NT_PY" ]] && "$NT_PY" -c 'import nautilus_trader' 2>/dev/null; then
  export PYTHONPATH="${LAB}${PYTHONPATH:+:$PYTHONPATH}"
  cd "$LAB"
  exec "$NT_PY" run_bybit_demo_trading_node.py "$@"
fi

if command -v python3 >/dev/null 2>&1 && python3 -c 'import nautilus_trader' 2>/dev/null; then
  export PYTHONPATH="${LAB}${PYTHONPATH:+:$PYTHONPATH}"
  cd "$LAB"
  exec python3 run_bybit_demo_trading_node.py "$@"
fi

echo "Need research/nautilus_lab/.venv (python3 -m venv .venv && pip install -r requirements-bybit-demo-live.txt), system nautilus_trader, or a running container named nautilus-research." >&2
exit 1
