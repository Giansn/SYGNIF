#!/usr/bin/env bash
# Swarm + channel + btc-specialist context, then **finance-agent** consulting (Cursor Task).
# Run from repo root; does not call the LLM — prints the canonical file paths and a prompt stub.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}:${ROOT}/finance_agent${PYTHONPATH:+:$PYTHONPATH}"

echo "=== Sygnif sync (runner/channel/TA → swarm → fusion → TP/SL) ==="
python3 "${ROOT}/scripts/swarm_sync_protocol.py" "$@"

echo ""
echo "=== Artefacts for finance-agent (Task subagent_type=finance-agent) ==="
PA="${PREDICTION_AGENT_DIR:-${ROOT}/prediction_agent}"
BD="${ROOT}/finance_agent/btc_specialist/data"
echo "  swarm_knowledge:  ${PA}/swarm_knowledge_output.json"
echo "  btc_prediction:     ${PA}/btc_prediction_output.json"
echo "  training_channel: ${PA}/training_channel_output.json"
echo "  fusion_sidecar:     ${PA}/swarm_nautilus_protocol_sidecar.json"
echo "  tpsl_last:          ${PA}/swarm_btc_future_tpsl_last.json"
echo "  btc_manifest:       ${BD}/manifest.json"
echo "  ta_snapshot:        ${BD}/btc_sygnif_ta_snapshot.json (if present)"
echo ""
echo "=== Prompt stub (paste into Cursor Task → finance-agent) ==="
cat <<EOF
Consult this Sygnif BTC setup using btc-specialist + swarm files:
- Read ${PA}/swarm_knowledge_output.json (votes, bf, conflict).
- Read ${PA}/training_channel_output.json (recognition probs, channel alignment).
- Read ${PA}/btc_prediction_output.json and ${PA}/swarm_nautilus_protocol_sidecar.json (fusion).
- Optional: ${BD}/manifest.json freshness; ${PA}/swarm_btc_future_tpsl_last.json for demo TP/SL.
Suggest risk/reward and parameter tweaks only (no promises). Prefer minimal loss, positive expectancy; reference SYGNIF_SWARM_TPSL_PROFILE=reward_risk and channel_adjust if relevant.
EOF
