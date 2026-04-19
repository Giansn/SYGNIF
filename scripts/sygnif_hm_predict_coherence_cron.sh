#!/usr/bin/env bash
# Cron helper: append one NDJSON line only when
#   flags.channel_vs_enhanced_mismatch OR flags.trees_vs_logreg == "conflict"
set -euo pipefail
REPO="${SYGNIF_REPO_ROOT:-$HOME/SYGNIF}"
PY="${PYTHON:-python3}"
LOG_DIR="${SYGNIF_COHERENCE_ALERT_LOG_DIR:-$HOME/.local/share/sygnif}"
LOG="${SYGNIF_COHERENCE_ALERT_LOG:-$LOG_DIR/hm_predict_coherence_alerts.log}"
mkdir -p "$LOG_DIR"

line="$(
  "$PY" "$REPO/scripts/sygnif_hm_predict_coherence.py" --json 2>/dev/null \
    | "$PY" -c "
import json, sys
from datetime import datetime, timezone
d = json.load(sys.stdin)
fl = d.get('flags') or {}
if fl.get('channel_vs_enhanced_mismatch') or fl.get('trees_vs_logreg') == 'conflict':
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    print(ts + ' ' + json.dumps(d, separators=(',', ':')), flush=True)
"
)" || true

if [[ -n "${line:-}" ]]; then
  printf '%s\n' "$line" >>"$LOG"
fi
