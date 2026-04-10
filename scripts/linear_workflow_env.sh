#!/usr/bin/env bash
# Source linear workflow URLs into the environment (Sygnif linear pipeline order).
#
# Usage:
#   set -a && source scripts/linear_workflow_env.sh && set +a
#
# Optional: scripts/linear_workflow.env (gitignored) overrides example defaults.
#   cp scripts/linear_workflow.env.example scripts/linear_workflow.env

# Intentionally no `set -e` here — safe to `source` from an interactive shell.

_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
_ENV_USER="$_ROOT/scripts/linear_workflow.env"
_ENV_EX="$_ROOT/scripts/linear_workflow.env.example"

if [[ -f "$_ENV_USER" ]]; then
  # shellcheck source=/dev/null
  source "$_ENV_USER"
elif [[ -f "$_ENV_EX" ]]; then
  # shellcheck source=/dev/null
  source "$_ENV_EX"
else
  echo "linear_workflow_env: missing $_ENV_EX" >&2
  return 1 2>/dev/null || exit 1
fi
