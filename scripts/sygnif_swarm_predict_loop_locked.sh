#!/usr/bin/env bash
# **Single-instance** Swarm + predict-protocol loop (Bybit **API demo** orders when ACK is set).
#
# Runs ``swarm_auto_predict_protocol_loop.py --execute`` under ``flock`` so systemd ``Restart=always``
# does not stack duplicate venue writers.
#
# Prerequisites (typically via ``~/SYGNIF/.env`` + ``~/SYGNIF/swarm_operator.env`` loaded by systemd):
#   - ``SYGNIF_PREDICT_PROTOCOL_LOOP_ACK=YES`` (required for ``--execute``)
#   - ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET``
#
# Python: prefers ``$ROOT/.venv/bin/python3``, then ``research/nautilus_lab/.venv/bin/python``, else ``python3``.
#
# Lock override: ``SYGNIF_SWARM_PREDICT_LOOP_LOCK_FILE=/path/to.lock`` (default under ``/run/user/$UID/``).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
LOCK="${SYGNIF_SWARM_PREDICT_LOOP_LOCK_FILE:-/run/user/$(id -u)/sygnif-swarm-predict-loop.lock}"
mkdir -p "$(dirname "$LOCK")"

# Swarm ↔ fusion compatibility (safe defaults; override in .env / swarm_operator.env)
export SWARM_ORDER_BTC_FUTURE_FLAT_PASS="${SWARM_ORDER_BTC_FUTURE_FLAT_PASS:-1}"
export SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS="${SWARM_ORDER_BTC_FUTURE_VOTE_FLAT_PASS:-1}"
export SYGNIF_FUSION_BTC_FUTURE_ADAPT_WHEN_FLAT="${SYGNIF_FUSION_BTC_FUTURE_ADAPT_WHEN_FLAT:-1}"

PY=""
REPO_PARENT="$(cd "$ROOT/.." && pwd)"
for cand in "$ROOT/.venv/bin/python3" "$REPO_PARENT/.venv/bin/python3" "$ROOT/research/nautilus_lab/.venv/bin/python" "$(command -v python3)"; do
  if [[ -n "$cand" && -x "$cand" ]]; then
    PY="$cand"
    break
  fi
done
if [[ -z "$PY" ]]; then
  echo "sygnif_swarm_predict_loop_locked: no usable python (tried .venv, nautilus_lab/.venv, PATH)" >&2
  exit 2
fi

# Optional: SYGNIF_SWARM_RISK_PROFILE=demo_safe (or default) — passed through to launcher.
SWARM_AUTO_EXTRA=()
if [[ -n "${SYGNIF_SWARM_RISK_PROFILE:-}" ]]; then
  SWARM_AUTO_EXTRA=(--risk-profile "$SYGNIF_SWARM_RISK_PROFILE")
fi

# -n: if another process holds the lock, exit 0 (do not spin duplicate loops under systemd).
if ! flock -n "$LOCK" "$PY" "$ROOT/scripts/swarm_auto_predict_protocol_loop.py" "${SWARM_AUTO_EXTRA[@]}" --execute; then
  echo "sygnif_swarm_predict_loop_locked: loop already running (lock $LOCK) — exiting" >&2
  exit 0
fi
