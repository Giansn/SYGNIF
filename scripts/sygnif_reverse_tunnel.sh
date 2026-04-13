#!/usr/bin/env bash
# Persistent reverse SSH tunnel: remote host:port → local Sygnif HTTP (default BTC Terminal :8891).
# Run under systemd (see systemd/sygnif-reverse-tunnel.service). Requires outbound SSH to a gateway
# you control; on the gateway, set GatewayPorts / PermitOpen as needed for public bind.
set -euo pipefail

if [[ -z "${SYGNIF_REVERSE_TUNNEL_GATEWAY:-}" ]]; then
  echo "sygnif_reverse_tunnel: set SYGNIF_REVERSE_TUNNEL_GATEWAY=user@gateway.example.com in .env" >&2
  exit 1
fi

REMOTE_BIND="${SYGNIF_REVERSE_TUNNEL_REMOTE_BIND:-127.0.0.1}"
REMOTE_PORT="${SYGNIF_REVERSE_TUNNEL_REMOTE_PORT:-19891}"
LOCAL_HOST="${SYGNIF_REVERSE_TUNNEL_LOCAL_HOST:-127.0.0.1}"
LOCAL_PORT="${SYGNIF_REVERSE_TUNNEL_LOCAL_PORT:-8891}"

SSH_OPTS=(
  -N
  -o ExitOnForwardFailure=yes
  -o ServerAliveInterval="${SYGNIF_REVERSE_TUNNEL_SERVER_ALIVE:-30}"
  -o ServerAliveCountMax="${SYGNIF_REVERSE_TUNNEL_SERVER_ALIVE_MAX:-4}"
  -o StrictHostKeyChecking="${SYGNIF_REVERSE_TUNNEL_STRICT_HOSTKEY:-accept-new}"
  -o BatchMode=yes
)

if [[ -n "${SYGNIF_REVERSE_TUNNEL_IDENTITY_FILE:-}" ]]; then
  SSH_OPTS+=( -o IdentitiesOnly=yes -i "$SYGNIF_REVERSE_TUNNEL_IDENTITY_FILE" )
fi

# -R [bind_address:]port:host:hostport
SPEC="${REMOTE_BIND}:${REMOTE_PORT}:${LOCAL_HOST}:${LOCAL_PORT}"
echo "sygnif_reverse_tunnel: -R ${SPEC} → ${SYGNIF_REVERSE_TUNNEL_GATEWAY}" >&2
exec ssh "${SSH_OPTS[@]}" -R "${SPEC}" "$SYGNIF_REVERSE_TUNNEL_GATEWAY"
