#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/_brain_server_common.sh"

RUNTIME_DIR="$(brain_server_runtime_dir)"
PID_FILE="$(brain_server_pid_file)"
LOCK_FILE="$(brain_server_lock_file)"
LOG_FILE="$(brain_server_log_file)"
START_SCRIPT="$ROOT/scripts/start_brain_server.sh"
PORT="$(brain_server_port)"
LOCK_DIR="${LOCK_FILE}.d"

mkdir -p "$RUNTIME_DIR"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "brain_server start already in progress"
  exit 1
fi

cleanup_lock() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup_lock EXIT

existing_pid="$(brain_server_read_pid_file)"
if brain_server_command_matches_pid "$existing_pid"; then
  echo "brain_server already running pid=$existing_pid"
  exit 0
fi

if existing_listener_pid="$(brain_server_find_listener_pid)"; then
  echo "$existing_listener_pid" > "$PID_FILE"
  echo "brain_server already running pid=$existing_listener_pid"
  exit 0
fi

if [[ -f "$PID_FILE" ]]; then
  rm -f "$PID_FILE"
fi

nohup "$START_SCRIPT" >>"$LOG_FILE" 2>&1 &
launcher_pid=$!
pid=""

for _ in {1..20}; do
  if listener_pid="$(brain_server_find_listener_pid)"; then
    pid="$listener_pid"
    break
  fi

  if ! kill -0 "$launcher_pid" 2>/dev/null; then
    break
  fi
  sleep 0.25
done

if [[ -z "$pid" ]]; then
  if brain_server_command_matches_pid "$launcher_pid"; then
    pid="$launcher_pid"
  else
    echo "brain_server failed to start; see $LOG_FILE" >&2
    exit 1
  fi
fi

echo "$pid" > "$PID_FILE"
echo "brain_server started pid=$pid port=$PORT log=$LOG_FILE"
