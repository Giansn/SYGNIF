#!/usr/bin/env bash
# Post-deploy / operator health sweep: HTTP endpoints + optional Docker ps.
# Run from repo root. Ports follow docker-compose.yml / INSTANCE_SETUP defaults.
#
# Exit code: non-zero if any **core** check fails (finance-agent, notification-handler).
# Freqtrade / overseer targets are warn-only when those containers are not running.
#
# Optional env: SYGNIF_FT_SPOT_PING_PORT (default 8181), SYGNIF_COMPOSE_CMD (default: docker compose -f docker-compose.yml)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE="${SYGNIF_COMPOSE_CMD:-docker compose -f docker-compose.yml}"
FT_SPOT="${SYGNIF_FT_SPOT_PING_PORT:-8181}"

pass=0
crit_fail=0

ok() {
  printf 'OK   %s\n' "$1"
  pass=$((pass + 1))
}

warn() {
  printf 'WARN %s — %s\n' "$1" "${2:-}"
}

fail_core() {
  printf 'FAIL %s — %s\n' "$1" "${2:-}"
  crit_fail=$((crit_fail + 1))
}

try_curl_core() {
  name=$1
  url=$2
  if curl -fsS --max-time 8 "$url" >/dev/null 2>&1; then
    ok "$name"
  else
    fail_core "$name" "$url"
  fi
}

try_curl_opt() {
  name=$1
  url=$2
  if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
    ok "$name"
  else
    warn "$name" "$url"
  fi
}

echo "=== Sygnif deploy health ($(date -u +%Y-%m-%dT%H:%MZ)) ==="
echo

try_curl_core "finance-agent /health" "http://127.0.0.1:8091/health"
try_curl_core "notification-handler GET /" "http://127.0.0.1:8089/"

try_curl_opt "trade-overseer /health" "http://127.0.0.1:8090/health"
try_curl_opt "trade-overseer /overview" "http://127.0.0.1:8090/overview"
try_curl_opt "freqtrade spot /api/v1/ping (:${FT_SPOT})" "http://127.0.0.1:${FT_SPOT}/api/v1/ping"
try_curl_opt "freqtrade futures /api/v1/ping (:8081)" "http://127.0.0.1:8081/api/v1/ping"
try_curl_opt "dashboard BTC01+Grid / (:8892)" "http://127.0.0.1:8892/"
try_curl_opt "cursor-agent-worker /healthz" "http://127.0.0.1:8093/healthz"

echo
if command -v docker >/dev/null 2>&1; then
  echo "=== docker compose ps ==="
  $COMPOSE ps -a 2>/dev/null | head -50 || echo "(compose ps failed)"
else
  echo "(docker not in PATH — skipped compose ps)"
fi

echo
echo "=== summary: ${pass} checks run, core failures: ${crit_fail} ==="
exit "$crit_fail"
