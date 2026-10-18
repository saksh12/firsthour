#!/usr/bin/env bash
# Reset between demo runs: backend to IDLE, and a signed `reset` to the Pi
# (latch locked, alarm off, fusion cooldown cleared, display READY).
# The Pi picks it up on its next poll (~1 s). Run from anywhere:
#   bash scripts/demo_reset.sh
set -euo pipefail

ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
env_value() {
  # The env var wins; else the .env line, without its trailing "# comment".
  local v="${!1:-}"
  if [[ -z "$v" && -f "$ENV_FILE" ]]; then
    v="$(grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//')"
  fi
  printf '%s' "${v:-$2}"
}

BACKEND="$(env_value BACKEND_URL http://localhost:8000)"
SECRET="$(env_value SHARED_SECRET "")"
[[ -n "$SECRET" ]] || { echo "SHARED_SECRET is not set (.env)" >&2; exit 1; }

curl -fsS -X POST "${BACKEND%/}/demo/reset" -H "Authorization: Bearer $SECRET"
echo
