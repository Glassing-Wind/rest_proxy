#!/bin/bash
# scripts/start_brain_server.sh
# ---------------------------------------------------------------------------
# Startup wrapper for brain_server.py (HTTP / Streamable MCP transport).
# Sources .env from the rest_proxy directory, then launches uvicorn.
# Designed to be called by the launchd plist.
# ---------------------------------------------------------------------------

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${LM_PROXY_PYTHON:-${LM_PROXY_INDEX_PYTHON:-}}"
if [[ -z "$PYTHON" ]]; then
    PYTHON="$(command -v python || command -v python3)"
fi
PORT="${BRAIN_SERVER_PORT:-8001}"

# Source .env so all LM_PROXY_* variables are available.
if [ -f "$REPO/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO/.env"
    set +a
fi

# Mark this as HTTP transport for the daemonized brain server.
export LM_PROXY_TRANSPORT=http

cd "$REPO"

exec "$PYTHON" -m uvicorn brain_server:app \
    --host 127.0.0.1 \
    --port "$PORT" \
    --workers 1 \
    --log-level info
