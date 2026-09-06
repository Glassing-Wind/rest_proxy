#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT_DIR"

PYTHON_BIN="${LM_PROXY_PYTHON:-${LM_PROXY_INDEX_PYTHON:-}}"
if [[ -z "$PYTHON_BIN" ]] && [[ -x "/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python" ]]; then
  PYTHON_BIN="/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python"
fi
if [[ -z "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python || command -v python3)"
fi

TMP_REQ="$(mktemp)"
trap 'rm -f "$TMP_REQ"' EXIT
BASELINE_FILE="security/pip-audit-baseline.txt"

# pip-audit cannot resolve local/editable or VCS requirements into registry
# advisories reliably. The pinned VCS revision is covered by the dedicated
# ts-pack build and contract jobs.
grep -vE '^[[:space:]]*(-e[[:space:]]|[^#]+[[:space:]]+@[[:space:]]+(file|git\+[^:]+)://)' requirements.txt > "$TMP_REQ"

AUDIT_ARGS=()
if [[ -f "$BASELINE_FILE" ]]; then
  while IFS= read -r advisory_id; do
    [[ -z "$advisory_id" || "$advisory_id" == \#* ]] && continue
    AUDIT_ARGS+=(--ignore-vuln "$advisory_id")
  done < "$BASELINE_FILE"
fi

"$PYTHON_BIN" -m pip_audit -r "$TMP_REQ" --skip-editable "${AUDIT_ARGS[@]}"
