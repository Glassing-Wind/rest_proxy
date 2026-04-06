#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/.runtime/graphrag_mcp_supervisor.pid"
RESTART_BRAIN_SCRIPT="$ROOT/scripts/restart_brain_server.sh"

TARGET_ID="${1:-}" 
PID_FILES=()

if [[ -n "$TARGET_ID" ]]; then
  PF="$ROOT/.runtime/graphrag_mcp_supervisor_${TARGET_ID}.pid"
  if [[ -f "$PF" ]]; then
    PID_FILES+=("$PF")
  else
    echo "No PID file found for ID: $TARGET_ID ($PF)" >&2
    exit 1
  fi
else
  # Find all existing PID files (supervisors and proxy)
  for f in "$ROOT/.runtime"/graphrag_mcp_supervisor_*.pid "$ROOT/.runtime"/proxy.pid; do
    if [[ -f "$f" ]]; then PID_FILES+=("$f"); fi
  done
fi

if [[ ${#PID_FILES[@]} -eq 0 ]]; then
  echo "No active GraphRAG MCP or Proxy processes found in $ROOT/.runtime" >&2
  exit 0
fi

for pf in "${PID_FILES[@]}"; do
  PID="$(cat "$pf")"
  BN="$(basename "$pf" .pid)"
  if [[ "$BN" == "proxy" ]]; then
    TYPE="Proxy Endpoint"
    SIG="-TERM" # Proxy usually needs a restart to refresh code
  else
    TYPE="MCP Supervisor ($(echo "$BN" | sed 's/graphrag_mcp_supervisor_//'))"
    SIG="-USR1" # Supervisors handle in-place child restarts
  fi

  if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
    kill "$SIG" "$PID"
    echo "Requested GraphRAG $TYPE restart (pid: $PID)"
  else
    echo "$TYPE is not running (pid: ${PID:-missing})" >&2
  fi
done
# Completed multi-restart logic

if [[ -x "$RESTART_BRAIN_SCRIPT" ]]; then
  "$RESTART_BRAIN_SCRIPT" --if-running
fi
