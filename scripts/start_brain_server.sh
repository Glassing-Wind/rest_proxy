#!/bin/bash
# scripts/start_brain_server.sh
# ---------------------------------------------------------------------------
# Startup wrapper for brain_server.py (HTTP / Streamable MCP transport).
# Sources .env from the rest_proxy directory, then launches uvicorn.
# Designed to be called by the launchd plist.
# ---------------------------------------------------------------------------

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${BRAIN_SERVER_PORT:-8001}"

# Source .env so all LM_PROXY_* variables are available.
if [ -f "$REPO/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO/.env"
    set +a
fi

pick_python() {
    local candidate=""
    local -a candidates=(
        "${LM_PROXY_PYTHON:-}"
        "${LM_PROXY_INDEX_PYTHON:-}"
        "/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python"
        "$(command -v python3 2>/dev/null || true)"
        "$(command -v python 2>/dev/null || true)"
    )

    for candidate in "${candidates[@]}"; do
        if [[ -z "$candidate" ]] || [[ ! -x "$candidate" ]]; then
            continue
        fi
        if "$candidate" -c "import uvicorn" >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

PYTHON="$(pick_python || true)"
if [[ -z "$PYTHON" ]]; then
    echo "Could not find a Python interpreter with uvicorn installed." >&2
    exit 1
fi

# Mark this as HTTP transport for the daemonized brain server.
export LM_PROXY_TRANSPORT=http

cd "$REPO"

exec "$PYTHON" -m uvicorn brain_server:app \
    --host 127.0.0.1 \
    --port "$PORT" \
    --workers 1 \
    --log-level info
