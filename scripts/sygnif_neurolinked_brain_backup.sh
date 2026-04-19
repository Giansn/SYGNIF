#!/usr/bin/env bash
# Persist NeuroLinked to disk (optional HTTP POST) then archive brain_state + BTC ML JSON.
#
# Env:
#   SYGNIF_REPO                     — default ~/SYGNIF
#   SYGNIF_BRAIN_BACKUP_DIR         — default ~/.local/share/sygnif-agent/brain_backups
#   SYGNIF_NEUROLINKED_SAVE_URL     — default http://127.0.0.1:8889/api/brain/save
#   SYGNIF_NEUROLINKED_BACKUP_TRIGGER_SAVE — 0 to skip curl save (default 1)
#   SYGNIF_BRAIN_BACKUP_KEEP        — keep newest N tarballs (default 120)
#
# Install: see cron/crontab.txt (every 15m recommended).
set -euo pipefail

REPO="${SYGNIF_REPO:-$HOME/SYGNIF}"
TS="$(date -u +%Y%m%d_%H%M%S)"
BACKUP_ROOT="${SYGNIF_BRAIN_BACKUP_DIR:-$HOME/.local/share/sygnif-agent/brain_backups}"
mkdir -p "$BACKUP_ROOT"

NL_SAVE="${SYGNIF_NEUROLINKED_SAVE_URL:-http://127.0.0.1:8889/api/brain/save}"
DO_SAVE="${SYGNIF_NEUROLINKED_BACKUP_TRIGGER_SAVE:-1}"
if [[ "$DO_SAVE" == "1" || "$DO_SAVE" == "yes" ]]; then
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -m 45 -X POST "$NL_SAVE" >/dev/null 2>&1 || true
  fi
fi

if [[ ! -d "$REPO/third_party/neurolinked/brain_state" ]]; then
  echo "warn: no brain_state dir at $REPO/third_party/neurolinked/brain_state" >&2
fi

OUT="${BACKUP_ROOT}/brain_btc_${TS}.tar.gz"
parts=( third_party/neurolinked/brain_state )
for rel in \
  prediction_agent/btc_prediction_output.json \
  prediction_agent/btc_24h_movement_prediction.json \
  prediction_agent/training_channel_output.json \
  prediction_agent/swarm_knowledge_output.json \
  prediction_agent/neurolinked_swarm_channel.json \
  prediction_agent/swarm_btc_vector.json \
  prediction_agent/swarm_btc_synth.json \
  prediction_agent/btc_macro_train_output.json
do
  [[ -e "$REPO/$rel" ]] && parts+=( "$rel" )
done

cd "$REPO"
tar czf "$OUT" --exclude='third_party/neurolinked/brain_state/backups' "${parts[@]}"
sz="$(wc -c <"$OUT" | tr -d ' ')"
echo "backup_ok ${OUT} (${sz} bytes)"

KEEP="${SYGNIF_BRAIN_BACKUP_KEEP:-120}"
shopt -s nullglob
arr=( "${BACKUP_ROOT}"/brain_btc_*.tar.gz )
if ((${#arr[@]} > KEEP)); then
  printf '%s\n' "${arr[@]}" | sort -r | tail -n +"$((KEEP + 1))" | xargs -r rm -f
fi
