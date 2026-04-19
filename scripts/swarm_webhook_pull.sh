#!/usr/bin/env bash
# Load ``SYGNIF_SWARM_WEBHOOK_TOKEN`` from the same env files as docker-compose (secrets first, then repo .env),
# then call finance-agent ``GET /sygnif/swarm`` (or ``POST`` with ``--persist``). Does not print the token.
set -euo pipefail

REPO="${SYGNIF_REPO:-$HOME/SYGNIF}"
SEC="${SYGNIF_SECRETS_ENV_FILE:-$HOME/xrp_claude_bot/.env}"
LOCAL_ENV="$REPO/.env"
HOST="${SWARM_WEBHOOK_HOST:-127.0.0.1}"
PORT="${FINANCE_AGENT_HTTP_PORT:-8091}"
PATH_URL="${SWARM_WEBHOOK_PATH:-/sygnif/swarm}"

_load_token() {
  SYGNIF_SWARM_WEBHOOK_TOKEN=""
  for f in "$SEC" "$LOCAL_ENV"; do
    [[ -f "$f" ]] || continue
    if line=$(grep '^SYGNIF_SWARM_WEBHOOK_TOKEN=' "$f" 2>/dev/null | tail -1); then
      SYGNIF_SWARM_WEBHOOK_TOKEN="${line#SYGNIF_SWARM_WEBHOOK_TOKEN=}"
      SYGNIF_SWARM_WEBHOOK_TOKEN="${SYGNIF_SWARM_WEBHOOK_TOKEN%$'\r'}"
    fi
  done
  if [[ -z "${SYGNIF_SWARM_WEBHOOK_TOKEN}" ]]; then
    echo "error: SYGNIF_SWARM_WEBHOOK_TOKEN not set in $SEC or $LOCAL_ENV" >&2
    echo "  add: SYGNIF_SWARM_WEBHOOK_TOKEN=\$(openssl rand -hex 32)" >&2
    exit 1
  fi
}

usage() {
  echo "usage: $0 [--persist] [--path /webhook/swarm] [--host 127.0.0.1] [--port 8091]" >&2
  exit 2
}

PERSIST=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --persist) PERSIST=1; shift ;;
    --path)
      PATH_URL="${2:?}"
      shift 2
      ;;
    --host)
      HOST="${2:?}"
      shift 2
      ;;
    --port)
      PORT="${2:?}"
      shift 2
      ;;
    -h|--help) usage ;;
    *) echo "unknown arg: $1" >&2; usage ;;
  esac
done

_load_token
URL="http://${HOST}:${PORT}${PATH_URL}"

if [[ "$PERSIST" -eq 1 ]]; then
  exec curl -sS -X POST \
    -H "Authorization: Bearer ${SYGNIF_SWARM_WEBHOOK_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"persist":true}' \
    "$URL"
else
  exec curl -sS \
    -H "Authorization: Bearer ${SYGNIF_SWARM_WEBHOOK_TOKEN}" \
    "$URL"
fi
