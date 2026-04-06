"""proxy_config.py — environment flags and optional memory imports."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

# ---------------------------------------------------------------------------
# Memory layer feature flags (loaded once; all optional)
# ---------------------------------------------------------------------------
_MEMORY_ENABLED = os.getenv("LM_PROXY_MEMORY_ENABLED", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_ENABLE_PERSISTENCE = os.getenv(
    "LM_PROXY_MEMORY_ENABLE_PERSISTENCE", "1"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_ENABLE_REDIS = os.getenv("LM_PROXY_MEMORY_ENABLE_REDIS", "1").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_ENABLE_EMBEDDINGS = os.getenv(
    "LM_PROXY_MEMORY_ENABLE_EMBEDDINGS", "0"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_MEMORY_SESSION_NAMESPACE = os.getenv("LM_PROXY_MEMORY_SESSION_NAMESPACE", "lmproxy")
# Memory injection into prompts: prepend rolling summary + trim old turns before forwarding.
_MEMORY_ENABLE_INJECT = os.getenv(
    "LM_PROXY_MEMORY_ENABLE_INJECT", "1"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_MEMORY_MAX_INJECT_TURNS = int(os.getenv("LM_PROXY_MEMORY_MAX_INJECT_TURNS", "10"))

# Interlink (Multi-Agent Messaging)
_INTERLINK_ENABLED = os.getenv("LM_PROXY_ENABLE_INTERLINK", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_INTERLINK_DSN = os.getenv("LM_PROXY_INTERLINK_DSN", os.getenv("LM_PROXY_PG_DSN", ""))
_INTERLINK_ID = os.getenv("LM_PROXY_INTERLINK_ID", os.path.basename(os.getcwd()))
_INTERLINK_TTL = int(os.getenv("LM_PROXY_INTERLINK_TTL", "86400"))


# Backward-compat flag; injection is now controlled by _MEMORY_ENABLED + _MEMORY_ENABLE_INJECT.
_MEMORY_MODE = os.getenv("LM_PROXY_MEMORY_MODE", "stateless").strip().lower()

# Conditionally import memory modules; keep failures non-fatal so the proxy
# still works even if optional dependencies are missing.
_memory_store = None
_memory_summary = None
_memory_retrieval = None
_memory_bootstrap = None
_skeleton_extractor = None

if _MEMORY_ENABLED:
    try:
        import memory.store as _memory_store  # type: ignore
        import memory.summary as _memory_summary  # type: ignore
        import memory.retrieval as _memory_retrieval  # type: ignore
        import memory.bootstrap as _memory_bootstrap  # type: ignore
        import memory.skeleton_extractor as _skeleton_extractor  # type: ignore
    except ImportError as _mem_import_err:
        print(
            f"[lm-proxy] memory_import_failed error={_mem_import_err}",
            file=sys.stderr,
            flush=True,
        )
        _memory_store = None
        _memory_summary = None
        _memory_retrieval = None
        _memory_bootstrap = None
        _skeleton_extractor = None


def get_env(name: str, default=None):
    return os.getenv(name, default)


LM_BASE = os.getenv("LM_BASE", "http://127.0.0.1:1234").rstrip("/")
OPENAI_BASE = f"{LM_BASE}/v1"
def _get_writable_path(env_var: str, default_rel: str) -> Path:
    env_val = os.getenv(env_var)
    if env_val:
        return Path(env_val)
    
    # Try current directory .runtime
    local_runtime = Path("./.runtime")
    try:
        local_runtime.mkdir(parents=True, exist_ok=True)
        # Test writability
        test_file = local_runtime / ".write_test"
        test_file.touch()
        test_file.unlink()
        return local_runtime / default_rel
    except (OSError, PermissionError):
        # Fallback to /tmp
        tmp_runtime = Path("/tmp/lm-proxy/.runtime")
        try:
            tmp_runtime.mkdir(parents=True, exist_ok=True)
            return tmp_runtime / default_rel
        except Exception:
            # Absolute fallback: just the filename in /tmp
            return Path("/tmp") / default_rel

STATE_FILE = _get_writable_path("LM_PROXY_STATE", "lm_proxy_state.json")
ENABLE_PROXY_FILTERING = os.getenv(
    "LM_PROXY_ENABLE_FILTERING", "true"
).strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
ENABLE_DEBUG_LOGGING = os.getenv("LM_PROXY_DEBUG", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DEBUG_LOG_PATH = _get_writable_path("LM_PROXY_DEBUG_LOG", "proxy_debug.log")
MODEL_ALIASES_ENV = os.getenv("LM_PROXY_MODEL_ALIASES", "").strip()
FALLBACK_MODEL = os.getenv("LM_PROXY_FALLBACK_MODEL", "").strip()
ENABLE_MODEL_VALIDATION = os.getenv(
    "LM_PROXY_VALIDATE_MODELS", "true"
).strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
USE_LOCAL_MODELS_FOR_V1 = os.getenv(
    "LM_PROXY_V1_MODELS_LOCAL", "false"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# Default context window size injected into stateful route payloads when not provided by client.
# Set to 0 to let LM Studio use its own default.
DEFAULT_CONTEXT_LENGTH = int(os.getenv("LM_PROXY_CONTEXT_LENGTH", "0"))
# When enabled, tool-using requests are translated to LM Studio's stateful /v1/responses
# endpoint instead of /v1/chat/completions, gaining server-side KV-cache continuity.
USE_RESPONSES_API = os.getenv("LM_PROXY_USE_RESPONSES_API", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# Number of consecutive identical tool calls required to trigger loop-break injection.
LOOP_DETECT_THRESHOLD = int(os.getenv("LM_PROXY_LOOP_DETECT_THRESHOLD", "3"))
