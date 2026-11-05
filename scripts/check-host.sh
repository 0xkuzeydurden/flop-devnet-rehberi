#!/usr/bin/env bash
# Read local host information. Does not install, pull images or start containers.
set -euo pipefail

flop_failures=0
printf 'FLOP miner host checks (read-only)\n'
printf 'OS: %s / %s\n' "$(uname -s)" "$(uname -m)"
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  printf 'FAIL: this miner path requires Linux x86_64. A Mac can be the remote controller.\n'
  flop_failures=$((flop_failures + 1))
fi
for flop_command in docker nvidia-smi python3 curl jq openssl git; do
  if ! command -v "$flop_command" >/dev/null 2>&1; then
    printf 'MISSING: %s\n' "$flop_command"
    flop_failures=$((flop_failures + 1))
  fi
done
if command -v docker >/dev/null 2>&1; then
  if ! docker compose version; then
    flop_failures=$((flop_failures + 1))
  fi
  if ! docker info --format 'Docker server: {{.ServerVersion}}'; then
    printf 'FAIL: Docker daemon is unavailable to this account.\n'
    flop_failures=$((flop_failures + 1))
  fi
fi
if command -v nvidia-smi >/dev/null 2>&1; then
  if ! nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv; then
    flop_failures=$((flop_failures + 1))
  fi
fi
df -h .
printf 'Containers were not started. GPU-in-container, exact model and TOPLOC still need acceptance checks.\n'
if ((flop_failures > 0)); then
  printf 'STOP: %s host requirement(s) failed.\n' "$flop_failures"
  exit 1
fi
printf 'Local prerequisites found. This is not calibration or paid-work readiness.\n'
