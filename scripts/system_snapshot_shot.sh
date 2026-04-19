#!/usr/bin/env bash
# Capture PNG of user_data/system_snapshot.html (refreshes JSON+HTML first).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "system_snapshot_shot.sh: missing $PY — use repo .venv" >&2
  exit 2
fi
exec "$PY" "${ROOT}/scripts/system_snapshot_shot.py" "$@"
