#!/usr/bin/env bash
# Verify SSM agent is present, enabled on boot, and running.
# Exit 0 = OK, 1 = problems.
set -euo pipefail

UNIT="snap.amazon-ssm-agent.amazon-ssm-agent.service"
ROLE_URL="http://169.254.169.254/latest/meta-data/iam/security-credentials/"

echo "=== SSM agent health ==="
if snap list amazon-ssm-agent &>/dev/null; then
  snap list amazon-ssm-agent | tail -1
else
  echo "ERROR: amazon-ssm-agent snap not installed"
  exit 1
fi

if systemctl is-enabled "$UNIT" &>/dev/null; then
  echo "systemd enable: OK ($UNIT)"
else
  echo "WARN: $UNIT not enabled — run: sudo snap start --enable amazon-ssm-agent"
  exit 1
fi

if systemctl is-active "$UNIT" &>/dev/null; then
  echo "systemd active: OK"
else
  echo "ERROR: $UNIT not active — run: sudo systemctl start $UNIT"
  exit 1
fi

echo "=== IAM role (instance metadata) ==="
if curl -s --max-time 2 "$ROLE_URL" | grep -q .; then
  curl -s --max-time 2 "$ROLE_URL"
  echo ""
else
  echo "WARN: no IAM role visible from metadata (SSM may still work if IMDSv2 required)"
fi

echo "=== OK — SSM should work after reboot ==="
exit 0
