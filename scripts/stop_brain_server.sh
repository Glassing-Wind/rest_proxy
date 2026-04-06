#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/_brain_server_common.sh"

PID_FILE="$(brain_server_pid_file)"

pid="$(brain_server_read_pid_file)"

if brain_server_command_matches_pid "$pid"; then
  kill "$pid"
  for _ in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$PID_FILE"
      echo "brain_server stopped pid=$pid"
      exit 0
    fi
    sleep 0.25
  done
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "brain_server killed pid=$pid"
  exit 0
fi

if fallback_pid="$(brain_server_find_listener_pid)"; then
  kill "$fallback_pid"
  echo "brain_server stopped pid=$fallback_pid (discovered via listener)"
  rm -f "$PID_FILE"
  exit 0
fi

rm -f "$PID_FILE"
echo "brain_server already stopped"
