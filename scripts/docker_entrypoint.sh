#!/usr/bin/env bash
set -euo pipefail

# Bound startup waits. Database readiness/authentication is checked by bootstrap.
wait_for_service() {
  local label="$1" host="$2" port="$3"
  local deadline=$((SECONDS + ${LM_PROXY_STARTUP_TIMEOUT_SECONDS:-60}))
  until nc -z -w 2 "$host" "$port" 2>/dev/null; do
    if (( SECONDS >= deadline )); then
      echo "[entrypoint] Timed out waiting for ${label} at ${host}:${port}" >&2
      return 1
    fi
    sleep 1
  done
}

wait_for_service PostgreSQL "${PG_HOST:-postgres}" "${PG_PORT:-5432}"
wait_for_service Neo4j "${NEO4J_HOST:-neo4j}" "${NEO4J_PORT:-7687}"
if [[ "${LM_PROXY_MEMORY_ENABLE_REDIS:-0}" == "1" ]]; then
  wait_for_service Redis "${REDIS_HOST:-redis}" "${REDIS_PORT:-6379}"
fi

# An appliance cannot advertise readiness after schema initialization failed.
python mcp_server.py bootstrap
exec python -m uvicorn brain_server:app --host 0.0.0.0 --port 8000
