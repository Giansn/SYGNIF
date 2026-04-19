#!/usr/bin/env bash
# Start **btc_predict_protocol_loop.py** in the background with Bybit **demo** keys merged like
# ``start_nautilus_btc_predict_protocol.sh`` (xrp_claude_bot/.env then SYGNIF/.env).
#
# If your shell errors on ``_parse_usage`` / bash completion, run:
#   env -i HOME=$HOME PATH=/usr/bin:/bin:/usr/local/bin ROOT=/path/to/SYGNIF bash --noprofile --norc scripts/start_btc_predict_protocol_loop.sh
#
# Logs: research/nautilus_lab/btc_predict_protocol_loop.log
# PID:  research/nautilus_lab/btc_predict_protocol_loop.pid
#
# Requires: SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES for --execute (set below or export before run).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAB="$ROOT/research/nautilus_lab"
NT_PY="${LAB}/.venv/bin/python"
LOG="${LAB}/btc_predict_protocol_loop.log"
PIDFILE="${LAB}/btc_predict_protocol_loop.pid"

if [[ ! -x "$NT_PY" ]]; then
  echo "Missing ${NT_PY}. Run: cd ${LAB} && python3 -m venv .venv && pip install -r requirements-bybit-demo-live.txt" >&2
  exit 1
fi

_export_bybit_demo_from_envfiles() {
  ROOT="$1" python3 - <<'PY'
import os
import re
from pathlib import Path

root = Path(os.environ["ROOT"])
files = [
    Path(os.environ.get("SYGNIF_SECRETS_ENV_FILE", "")).expanduser()
    if os.environ.get("SYGNIF_SECRETS_ENV_FILE")
    else Path.home() / "xrp_claude_bot" / ".env",
    Path(os.environ.get("SYGNIF_ENV_FILE", str(root / ".env"))).expanduser(),
]
pat = re.compile(r"^(BYBIT_DEMO_API_(?:KEY|SECRET))=(.*)$")
seen = set()
for fp in files:
    if not fp.is_file():
        continue
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = pat.match(line)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip().strip('"').strip("'")
        if k in seen:
            continue
        seen.add(k)
        v_esc = v.replace("'", "'\"'\"'")
        print(f"export {k}='{v_esc}'")
PY
}
eval "$(_export_bybit_demo_from_envfiles "$ROOT")"

if [[ -f "${SYGNIF_ENV_FILE:-$ROOT/.env}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${SYGNIF_ENV_FILE:-$ROOT/.env}"
  set +a
fi

export PYTHONPATH="${ROOT}/prediction_agent${PYTHONPATH:+:$PYTHONPATH}"
: "${SYGNIF_PREDICT_PROTOCOL_LOOP_ACK:=YES}"
export SYGNIF_PREDICT_PROTOCOL_LOOP_ACK

if [[ -f "$PIDFILE" ]]; then
  old="$(cat "$PIDFILE" 2>/dev/null || true)"
  if [[ -n "$old" ]] && kill -0 "$old" 2>/dev/null; then
    echo "Already running (PID $old). Stop with: kill $old" >&2
    exit 1
  fi
fi

nohup "$NT_PY" "$ROOT/scripts/btc_predict_protocol_loop.py" --execute >>"$LOG" 2>&1 &
echo $! >"$PIDFILE"
echo "Started btc_predict_protocol_loop.py PID=$(cat "$PIDFILE") log=$LOG"
