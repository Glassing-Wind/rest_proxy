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
        import memory_store as _memory_store  # type: ignore
        import memory_summary as _memory_summary  # type: ignore
        import memory_retrieval as _memory_retrieval  # type: ignore
        import memory_bootstrap as _memory_bootstrap  # type: ignore
        import skeleton_extractor as _skeleton_extractor  # type: ignore
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
STATE_FILE = Path(os.getenv("LM_PROXY_STATE", "./lm_proxy_state.json"))
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
