#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATUS_SCRIPT="$ROOT_DIR/scripts/brain_server_status.sh"
START_SCRIPT="$ROOT_DIR/scripts/start_brain_server_daemon.sh"
RESTART_SCRIPT="$ROOT_DIR/scripts/restart_brain_server.sh"

MCP_URL="http://127.0.0.1:8001/mcp"
PROTO="2025-06-18"

ensure_server() {
  if ! "$STATUS_SCRIPT" >/dev/null 2>&1; then
    "$START_SCRIPT" >/dev/null
    wait_for_server
  fi
}

wait_for_server() {
  local attempts=20
  local delay=0.5
  local i
  for ((i=0; i<attempts; i++)); do
    if curl -fsS "http://127.0.0.1:8001/health" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay"
  done
  echo "stale-session check failed: brain_server did not come back after restart" >&2
  return 1
}

init_session() {
  curl -i -sS -X POST "$MCP_URL" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H "MCP-Protocol-Version: $PROTO" \
    --data "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"$PROTO\",\"capabilities\":{},\"clientInfo\":{\"name\":\"graphrag-restart-smoke\",\"version\":\"1.0\"}}}"
}

extract_session_id() {
  awk 'BEGIN{IGNORECASE=1} /^mcp-session-id:/ {print $2}' | tr -d '\r'
}

ensure_server

INIT_RESPONSE="$(init_session)"
SESSION_ID="$(printf '%s\n' "$INIT_RESPONSE" | extract_session_id)"

if [[ -z "$SESSION_ID" ]]; then
  echo "stale-session check failed: initialize did not return Mcp-Session-Id" >&2
  exit 1
fi

"$RESTART_SCRIPT" >/dev/null
wait_for_server

STALE_RESPONSE="$(
  curl -i -sS -X POST "$MCP_URL" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H "MCP-Protocol-Version: $PROTO" \
    -H "Mcp-Session-Id: $SESSION_ID" \
    --data "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\",\"params\":{}}"
)"

if ! printf '%s\n' "$STALE_RESPONSE" | grep -q '^HTTP/1.1 404'; then
  echo "stale-session check failed: expected 404 after restart" >&2
  printf '%s\n' "$STALE_RESPONSE" >&2
  exit 1
fi

echo "MCP stale-session restart check passed"
echo "- stale session id rejected after restart: $SESSION_ID"
