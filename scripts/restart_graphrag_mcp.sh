#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/.runtime/graphrag_mcp_supervisor.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "GraphRAG MCP supervisor PID file not found: $PID_FILE" >&2
  echo "Make sure Codex is launching scripts/graphrag_mcp_supervisor.py." >&2
  exit 1
fi

PID="$(cat "$PID_FILE")"

if [[ -z "$PID" ]] || ! kill -0 "$PID" 2>/dev/null; then
  echo "GraphRAG MCP supervisor is not running (pid: ${PID:-missing})." >&2
  exit 1
fi

kill -USR1 "$PID"
echo "Requested GraphRAG MCP restart from supervisor pid $PID."
