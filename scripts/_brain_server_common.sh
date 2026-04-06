#!/usr/bin/env bash

brain_server_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd
}

brain_server_runtime_dir() {
  printf '%s/.runtime\n' "$(brain_server_root)"
}

brain_server_pid_file() {
  printf '%s/brain_server.pid\n' "$(brain_server_runtime_dir)"
}

brain_server_lock_file() {
  printf '%s/brain_server.lock\n' "$(brain_server_runtime_dir)"
}

brain_server_log_file() {
  printf '%s/brain_server.log\n' "$(brain_server_runtime_dir)"
}

brain_server_port() {
  printf '%s\n' "${BRAIN_SERVER_PORT:-8001}"
}

brain_server_command_matches_pid() {
  local pid="$1"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1

  local cmd
  cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ "$cmd" == *"uvicorn"* ]] && [[ "$cmd" == *"brain_server:app"* ]]
}

brain_server_find_listener_pid() {
  local port
  port="$(brain_server_port)"
  local pid

  while IFS= read -r pid; do
    if brain_server_command_matches_pid "$pid"; then
      printf '%s\n' "$pid"
      return 0
    fi
  done < <(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)

  return 1
}

brain_server_read_pid_file() {
  local pid_file
  pid_file="$(brain_server_pid_file)"

  if [[ -f "$pid_file" ]]; then
    tr -d '[:space:]' < "$pid_file"
  fi
}
