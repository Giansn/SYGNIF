#!/usr/bin/env bash
# Nautilus ExecTester on Bybit **demo** (orders). Requires NAUTILUS_EXEC_TESTER_DEMO_ACK=YES + BYBIT_DEMO_*.
# Compose no longer ships a nautilus-research container — use a local venv with nautilus_trader.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAB="$ROOT/research/nautilus_lab"
NT_PY="${LAB}/.venv/bin/python"

if [[ -x "$NT_PY" ]] && "$NT_PY" -c 'import nautilus_trader' 2>/dev/null; then
  export PYTHONPATH="${LAB}${PYTHONPATH:+:$PYTHONPATH}"
  cd "$LAB"
  exec "$NT_PY" run_bybit_demo_exec_tester.py "$@"
fi

if command -v python3 >/dev/null 2>&1 && python3 -c 'import nautilus_trader' 2>/dev/null; then
  export PYTHONPATH="${LAB}${PYTHONPATH:+:$PYTHONPATH}"
  cd "$LAB"
  exec python3 run_bybit_demo_exec_tester.py "$@"
fi

echo "Need research/nautilus_lab/.venv or system nautilus_trader (pip install -r research/nautilus_lab/requirements-bybit-demo-live.txt)." >&2
exit 1
