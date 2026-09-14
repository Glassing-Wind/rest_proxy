#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATUS_SCRIPT="$ROOT_DIR/scripts/brain_server_status.sh"
START_SCRIPT="$ROOT_DIR/scripts/start_brain_server_daemon.sh"
RESTART_SCRIPT="$ROOT_DIR/scripts/restart_brain_server.sh"

PORT="${BRAIN_SERVER_PORT:-8001}"
BASE_URL="${BRAIN_SERVER_BASE_URL:-http://127.0.0.1:$PORT}"
MCP_URL="$BASE_URL/mcp"
PROTO="2025-06-18"

ensure_server() {
  if ! "$STATUS_SCRIPT" >/dev/null 2>&1; then
    "$START_SCRIPT" >/dev/null
    wait_for_server
  fi
}

wait_for_server() {
  local attempts=120
  local delay=0.5
  local i
  for ((i=0; i<attempts; i++)); do
    if curl -fsS "$BASE_URL/health" >/dev/null 2>&1; then
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

extract_header() {
  local header_name="$1"
  awk -v wanted="$header_name" '
    tolower($1) == tolower(wanted ":") && value == "" {value=$2}
    END {print value}
  ' | tr -d '\r'
}

assert_http_status() {
  local response="$1"
  local expected="$2"
  if ! grep -Eq "^HTTP/[0-9.]+ $expected([[:space:]]|$)" <<< "$response"; then
    echo "stale-session check failed: expected HTTP $expected" >&2
    printf '%s\n' "$response" >&2
    exit 1
  fi
}

ensure_server

INIT_RESPONSE="$(init_session)"
SESSION_ID="$(printf '%s\n' "$INIT_RESPONSE" | extract_session_id)"
OLD_BOOT_ID="$(printf '%s\n' "$INIT_RESPONSE" | extract_header 'x-graphrag-boot-id')"

if [[ -z "$SESSION_ID" || -z "$OLD_BOOT_ID" ]]; then
  echo "stale-session check failed: initialize did not return session and boot identity headers" >&2
  exit 1
fi

"$RESTART_SCRIPT" >/dev/null
wait_for_server

NEW_BOOT_ID="$(curl -i -sS "$BASE_URL/health" | extract_header 'x-graphrag-boot-id')"
if [[ -z "$NEW_BOOT_ID" || "$NEW_BOOT_ID" == "$OLD_BOOT_ID" ]]; then
  echo "stale-session check failed: restart did not rotate boot identity" >&2
  echo "old_boot=$OLD_BOOT_ID new_boot=${NEW_BOOT_ID:-<missing>}" >&2
  exit 1
fi

STALE_RESPONSE="$(
  curl -i -sS -X POST "$MCP_URL" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H "MCP-Protocol-Version: $PROTO" \
    -H "Mcp-Session-Id: $SESSION_ID" \
    --data "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\",\"params\":{}}"
)"

assert_http_status "$STALE_RESPONSE" 404
STALE_BOOT_ID="$(printf '%s\n' "$STALE_RESPONSE" | extract_header 'x-graphrag-boot-id')"
STALE_KNOWN="$(printf '%s\n' "$STALE_RESPONSE" | extract_header 'x-graphrag-session-known')"
if [[ "$STALE_BOOT_ID" != "$NEW_BOOT_ID" || "$STALE_KNOWN" != "0" ]]; then
  echo "stale-session check failed: rejection was not attributed to the new boot as an unknown session" >&2
  echo "new_boot=$NEW_BOOT_ID response_boot=${STALE_BOOT_ID:-<missing>} session_known=${STALE_KNOWN:-<missing>}" >&2
  exit 1
fi

FRESH_RESPONSE="$(init_session)"
FRESH_SESSION_ID="$(printf '%s\n' "$FRESH_RESPONSE" | extract_session_id)"
FRESH_BOOT_ID="$(printf '%s\n' "$FRESH_RESPONSE" | extract_header 'x-graphrag-boot-id')"
if [[ -z "$FRESH_SESSION_ID" || "$FRESH_SESSION_ID" == "$SESSION_ID" || "$FRESH_BOOT_ID" != "$NEW_BOOT_ID" ]]; then
  echo "stale-session check failed: fresh session recovery did not bind to the new boot" >&2
  exit 1
fi

FRESH_LIST_RESPONSE="$(
  curl -i -sS -X POST "$MCP_URL" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -H "MCP-Protocol-Version: $PROTO" \
    -H "Mcp-Session-Id: $FRESH_SESSION_ID" \
    --data '{"jsonrpc":"2.0","id":3,"method":"tools/list","params":{}}'
)"
assert_http_status "$FRESH_LIST_RESPONSE" 200
FRESH_KNOWN="$(printf '%s\n' "$FRESH_LIST_RESPONSE" | extract_header 'x-graphrag-session-known')"
if [[ "$FRESH_KNOWN" != "1" ]]; then
  echo "stale-session check failed: fresh session was not recognized after restart" >&2
  exit 1
fi

curl -sS -X DELETE "$MCP_URL" \
  -H "MCP-Protocol-Version: $PROTO" \
  -H "Mcp-Session-Id: $FRESH_SESSION_ID" >/dev/null

echo "MCP stale-session restart check passed"
echo "- stale session id rejected after restart: $SESSION_ID"
echo "- boot identity rotated: $OLD_BOOT_ID -> $NEW_BOOT_ID"
echo "- fresh session recovered after restart: $FRESH_SESSION_ID"
