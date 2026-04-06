#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/_brain_server_common.sh"

PID_FILE="$(brain_server_pid_file)"
PORT="$(brain_server_port)"
pid="$(brain_server_read_pid_file)"

if brain_server_command_matches_pid "$pid"; then
  echo "brain_server running pid=$pid port=$PORT"
  exit 0
fi

if listener_pid="$(brain_server_find_listener_pid)"; then
  echo "brain_server running pid=$listener_pid port=$PORT (pid file refreshed)"
  echo "$listener_pid" > "$PID_FILE"
  exit 0
fi

echo "brain_server stopped"
exit 1
