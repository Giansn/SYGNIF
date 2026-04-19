#!/usr/bin/env bash
# Strategy development protocol — keep ML + channel + TA + swarm + fusion + demo TP/SL in one loop.
# Same as ``swarm_sync_protocol.py`` with stable defaults for iterative strategy work.
#
#   ./scripts/strat_dev_protocol.sh
#   ./scripts/strat_dev_protocol.sh --quick
#   ./scripts/strat_dev_protocol.sh --no-tpsl
#
# Env (optional): SYGNIF_SWARM_TPSL_PROFILE, SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL, SYGNIF_SECRETS_ENV_FILE
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export SYGNIF_SWARM_TPSL_PROFILE="${SYGNIF_SWARM_TPSL_PROFILE:-reward_risk}"
export SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL="${SYGNIF_SWARM_BTC_FUTURE_AUTO_TPSL:-1}"
exec "${ROOT}/scripts/swarm_sync_protocol.py" "$@"
