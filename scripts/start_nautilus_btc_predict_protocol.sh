#!/usr/bin/env bash
# **Canonical Sygnif BTC demo + live-ML entry point** (see research/nautilus_lab/README.md § BTC predict protocol).
# **Orders:** Nautilus ``TradingNode`` only (``SygnifBtcBarNodeStrategy.submit_order`` → Bybit demo exec).
# Do not use Freqtrade ``/forceenter`` for this stack unless you run a separate Freqtrade bot on purpose.
# Sygnif **BTC predict protocol**: ``run_sygnif_btc_trading_node.py`` with ``--live-predict`` (in-process
# RF/XGB/LogReg on 5m bars + ``nautilus_enhanced_consensus``), optional **sidecar** JSON from the bundled
# sink, and **post-only** demo **BUY** limits when the live signal is bullish.
#
# Requires ``BYBIT_DEMO_API_KEY`` / ``BYBIT_DEMO_API_SECRET`` (e.g. from repo ``.env``).
# Refuses orders unless ``NAUTILUS_SYGNIF_NODE_EXEC_ACK=YES`` (set below by default).
#
# Run the bundled sink first so ``nautilus_strategy_signal.json`` exists when sidecar gate is on:
#   bash research/nautilus_lab/run_nautilus_bundled.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAB="$ROOT/research/nautilus_lab"
NT_PY="${LAB}/.venv/bin/python"
if [[ ! -x "$NT_PY" ]]; then
  echo "Missing ${NT_PY}. Run: cd ${LAB} && python3 -m venv .venv && pip install -r requirements-bybit-demo-live.txt" >&2
  exit 1
fi

# Do not ``source`` arbitrary operator .env files (non-assignment lines break ``set -e``).
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

export PYTHONPATH="${ROOT}/prediction_agent:${LAB}${PYTHONPATH:+:$PYTHONPATH}"

: "${NAUTILUS_SYGNIF_NODE_EXEC_ACK:=YES}"
export NAUTILUS_SYGNIF_NODE_EXEC_ACK
export NAUTILUS_SYGNIF_NODE_EXEC_ADAPTIVE="${NAUTILUS_SYGNIF_NODE_EXEC_ADAPTIVE:-1}"
export NAUTILUS_SYGNIF_NODE_SIDECAR_GATE="${NAUTILUS_SYGNIF_NODE_SIDECAR_GATE:-1}"
# Per-order USDT notional cap (adaptive sizing + LiveRiskEngine when supported). Override or clear in .env.
export NAUTILUS_SYGNIF_NODE_RISK_MAX_NOTIONAL_USDT="${NAUTILUS_SYGNIF_NODE_RISK_MAX_NOTIONAL_USDT:-5000}"

_DATA="${ROOT}/finance_agent/btc_specialist/data"
export NAUTILUS_BTC_OHLCV_DIR="${NAUTILUS_BTC_OHLCV_DIR:-$_DATA}"
export NAUTILUS_SYGNIF_NODE_LIVE_DATA_DIR="${NAUTILUS_SYGNIF_NODE_LIVE_DATA_DIR:-$NAUTILUS_BTC_OHLCV_DIR}"

BAR_MIN="${NAUTILUS_SYGNIF_BAR_MINUTES:-5}"
OFF_BPS="${NAUTILUS_SYGNIF_EXEC_OFFSET_BPS:-150}"
MAX_ORD="${NAUTILUS_SYGNIF_EXEC_MAX_ORDERS:-12}"
MAX_BARS="${NAUTILUS_SYGNIF_MAX_BARS:-0}"

exec "$NT_PY" "$LAB/run_sygnif_btc_trading_node.py" \
  --live-predict \
  --exec-adaptive \
  --bar-minutes "$BAR_MIN" \
  --exec-offset-bps "$OFF_BPS" \
  --exec-max-orders "$MAX_ORD" \
  --max-bars "$MAX_BARS" \
  "$@"
