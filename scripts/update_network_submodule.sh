#!/usr/bin/env bash
# Update the Network git submodule (https://github.com/Giansn/Network) to latest remote main.
#
# Usage (from anywhere):
#   ./scripts/update_network_submodule.sh
# From repo root after update, if network/ changed:
#   git add network && git commit -m "chore(network): bump submodule" && git push

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f "$ROOT/.gitmodules" ]] || ! grep -q 'submodule "network"' "$ROOT/.gitmodules" 2>/dev/null; then
  echo "update_network_submodule: no network submodule in $ROOT" >&2
  exit 1
fi

git submodule sync -- network
git submodule update --init --remote --merge network

echo "--- network submodule ---"
git -C "$ROOT/network" fetch origin
git -C "$ROOT/network" log -1 --oneline
git -C "$ROOT/network" status -sb

if ! git diff --quiet network 2>/dev/null || ! git diff --cached --quiet network 2>/dev/null; then
  echo ""
  echo "Parent repo shows updated submodule pointer. Commit with:"
  echo "  git add network && git commit -m 'chore(network): bump submodule'"
fi
