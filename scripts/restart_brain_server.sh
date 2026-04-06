#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATUS_SCRIPT="$ROOT/scripts/brain_server_status.sh"
START_SCRIPT="$ROOT/scripts/start_brain_server_daemon.sh"
STOP_SCRIPT="$ROOT/scripts/stop_brain_server.sh"

ONLY_IF_RUNNING=0
if [[ "${1:-}" == "--if-running" ]]; then
  ONLY_IF_RUNNING=1
fi

if "$STATUS_SCRIPT" >/dev/null 2>&1; then
  "$STOP_SCRIPT"
  sleep 1
  "$START_SCRIPT"
  exit 0
fi

if [[ "$ONLY_IF_RUNNING" -eq 1 ]]; then
  echo "brain_server not running; skipped restart"
  exit 0
fi

"$START_SCRIPT"
