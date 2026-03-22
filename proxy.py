from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse


load_dotenv()


# ---------------------------------------------------------------------------
# Memory layer feature flags (loaded once; all optional)
# ---------------------------------------------------------------------------
_MEMORY_ENABLED = os.getenv("LM_PROXY_MEMORY_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
_MEMORY_ENABLE_PERSISTENCE = os.getenv("LM_PROXY_MEMORY_ENABLE_PERSISTENCE", "1").strip().lower() in {"1", "true", "yes", "on"}
_MEMORY_ENABLE_REDIS = os.getenv("LM_PROXY_MEMORY_ENABLE_REDIS", "1").strip().lower() in {"1", "true", "yes", "on"}
_MEMORY_ENABLE_EMBEDDINGS = os.getenv("LM_PROXY_MEMORY_ENABLE_EMBEDDINGS", "0").strip().lower() in {"1", "true", "yes", "on"}
_MEMORY_SESSION_NAMESPACE = os.getenv("LM_PROXY_MEMORY_SESSION_NAMESPACE", "lmproxy")
# Memory injection into prompts: prepend rolling summary + trim old turns before forwarding.
_MEMORY_ENABLE_INJECT = os.getenv("LM_PROXY_MEMORY_ENABLE_INJECT", "1").strip().lower() in {"1", "true", "yes", "on"}
_MEMORY_MAX_INJECT_TURNS = int(os.getenv("LM_PROXY_MEMORY_MAX_INJECT_TURNS", "10"))

# Add memory mode flag
# Deprecated: _MEMORY_MODE was previously used to gate injection (stateless/hybrid/stateful).
# Injection is now always active when _MEMORY_ENABLED and _MEMORY_ENABLE_INJECT are set.
# This var is kept for backward-compat but no longer changes behaviour.
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
        import memory_store as _memory_store          # type: ignore
        import memory_summary as _memory_summary      # type: ignore
        import memory_retrieval as _memory_retrieval  # type: ignore
        import memory_bootstrap as _memory_bootstrap  # type: ignore
        import skeleton_extractor as _skeleton_extractor  # type: ignore
    except ImportError as _mem_import_err:
        # Memory modules not available; proxy runs normally without them.
        print(f"[lm-proxy] memory_import_failed error={_mem_import_err}", flush=True)
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
ENABLE_PROXY_FILTERING = os.getenv("LM_PROXY_ENABLE_FILTERING", "true").strip().lower() not in {"0", "false", "no", "off"}
ENABLE_DEBUG_LOGGING = os.getenv("LM_PROXY_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}
MODEL_ALIASES_ENV = os.getenv("LM_PROXY_MODEL_ALIASES", "").strip()
FALLBACK_MODEL = os.getenv("LM_PROXY_FALLBACK_MODEL", "").strip()
ENABLE_MODEL_VALIDATION = os.getenv("LM_PROXY_VALIDATE_MODELS", "true").strip().lower() not in {"0", "false", "no", "off"}
USE_LOCAL_MODELS_FOR_V1 = os.getenv("LM_PROXY_V1_MODELS_LOCAL", "false").strip().lower() in {"1", "true", "yes", "on"}
# Default context window size injected into stateful route payloads when not provided by client.
# Set to 0 to let LM Studio use its own default.
DEFAULT_CONTEXT_LENGTH = int(os.getenv("LM_PROXY_CONTEXT_LENGTH", "0"))
# When enabled, tool-using requests are translated to LM Studio's stateful /v1/responses
# endpoint instead of /v1/chat/completions, gaining server-side KV-cache continuity.
USE_RESPONSES_API = os.getenv("LM_PROXY_USE_RESPONSES_API", "0").strip().lower() in {"1", "true", "yes", "on"}
# Number of consecutive identical tool calls required to trigger loop-break injection.
LOOP_DETECT_THRESHOLD = int(os.getenv("LM_PROXY_LOOP_DETECT_THRESHOLD", "3"))

app = FastAPI(title="LM Studio Stateful Chat Proxy")


@app.on_event("startup")
async def _startup_event() -> None:
    """Run idempotent schema bootstrap and open the Postgres connection pool on startup."""
    if _MEMORY_ENABLED and _memory_store is not None and _MEMORY_ENABLE_PERSISTENCE:
        # Open connection pool first so bootstrap (and all later inserts) can use it.
        try:
            await _memory_store.open_pool()
        except Exception as _pool_exc:
            print(f"[lm-proxy] memory_pool_error error={_pool_exc}", flush=True)

    if _MEMORY_ENABLED and _memory_bootstrap is not None and _MEMORY_ENABLE_PERSISTENCE:
        try:
            ok = await _memory_bootstrap.bootstrap_schema()
            if ok:
                print("[lm-proxy] memory_bootstrap_ok", flush=True)
        except Exception as _bs_exc:
            print(f"[lm-proxy] memory_bootstrap_error error={_bs_exc}", flush=True)

# history-hash -> LM Studio response id
STATE: Dict[str, str] = {}


def load_state() -> None:
    global STATE
    if STATE_FILE.exists():
        try:
            STATE = json.loads(STATE_FILE.read_text())
            debug_log("state_loaded", entries=len(STATE), state_file=str(STATE_FILE))
        except Exception:
            STATE = {}
            debug_log("state_load_failed", state_file=str(STATE_FILE))


def save_state() -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(STATE, indent=2, sort_keys=True))
    tmp.replace(STATE_FILE)
    debug_log("state_saved", entries=len(STATE), state_file=str(STATE_FILE))


def debug_log(message: str, **fields: Any) -> None:
    if not ENABLE_DEBUG_LOGGING:
        return

    payload = {"message": message}
    payload.update(fields)
    try:
        log_str = f"[lm-proxy] {stable_json(payload)}"
        print(log_str, flush=True)
        with open("proxy_debug.log", "a") as f:
            f.write(log_str + "\n")
    except Exception as e:
        log_str = f"[lm-proxy] {message} {fields} - Exception: {e}"
        print(log_str, flush=True)
        with open("proxy_debug.log", "a") as f:
            f.write(log_str + "\n")


load_state()


def stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- Model aliasing and fallback helpers
def parse_model_aliases() -> Dict[str, str]:
    if not MODEL_ALIASES_ENV:
        return {}
    try:
        parsed = json.loads(MODEL_ALIASES_ENV)
    except Exception:
        debug_log("model_alias_parse_failed", raw_value=MODEL_ALIASES_ENV)
        return {}

    if not isinstance(parsed, dict):
        debug_log("model_alias_parse_failed", raw_value=MODEL_ALIASES_ENV)
        return {}

    aliases: Dict[str, str] = {}
    for key, value in parsed.items():
        if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip():
            aliases[key.strip()] = value.strip()
    return aliases


def extract_model_keys(models_payload: Any) -> List[str]:
    if not isinstance(models_payload, dict):
        return []
    models = models_payload.get("models")
    if not isinstance(models, list):
        return []

    keys: List[str] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if isinstance(key, str) and key:
            keys.append(key)
    return keys


async def fetch_lmstudio_models() -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{LM_BASE}/api/v1/models")
        r.raise_for_status()
        return r.json()


def resolve_model_name(requested_model: str, available_keys: List[str]) -> str:
    aliases = parse_model_aliases()
    if requested_model in aliases:
        mapped = aliases[requested_model]
        debug_log("model_alias_applied", requested_model=requested_model, mapped_model=mapped)
        requested_model = mapped

    if not ENABLE_MODEL_VALIDATION:
        return requested_model

    if requested_model in available_keys:
        return requested_model

    if FALLBACK_MODEL and FALLBACK_MODEL in available_keys:
        debug_log(
            "model_missing_using_fallback",
            requested_model=requested_model,
            fallback_model=FALLBACK_MODEL,
        )
        return FALLBACK_MODEL

    return requested_model


def is_insufficient_resource_error_text(detail_text: str) -> bool:
    text = detail_text.lower()
    return (
        "insufficient system resources" in text
        or "would likely overload your system" in text
        or "model loading was stopped" in text
    )


def history_key(messages: List[Dict[str, Any]]) -> str:
    return hashlib.sha256(stable_json(messages).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Memory session ID helpers
# ---------------------------------------------------------------------------

def _derive_session_id(body: Dict[str, Any], messages: List[Dict[str, Any]]) -> str:
    """
    Derive a stable session ID for memory keying.

    Priority:
      1. Explicit 'session_id' or 'x_session_id' key in the request body.
      2. Project path extracted from the system prompt (e.g. the cwd OpenCode
         embeds).  This makes every conversation about the same project reuse
         the same memory bucket across sessions.
      3. Stable hash of the system prompt + first user message – fallback for
         conversations that don't advertise a project path.

    The ID is namespaced so it is safe to share a Redis / Postgres cluster.
    """
    import re as _re

    # 1. Caller-supplied session id
    explicit = body.get("session_id") or body.get("x_session_id")
    if isinstance(explicit, str) and explicit.strip():
        return f"{_MEMORY_SESSION_NAMESPACE}:{explicit.strip()}"

    # Collect system prompt text and first user message
    system_parts: List[str] = []
    first_user: str = ""
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "system":
            system_parts.append(content_to_text(msg.get("content", "")))
        elif role == "user" and not first_user:
            first_user = content_to_text(msg.get("content", ""))[:200]

    full_system = "\n".join(system_parts)

    # 2. Try to extract a stable project path from the system prompt.
    #    OpenCode embeds lines like:
    #      "Current working directory: /Users/.../MyProject"
    #      "cwd: /Users/.../MyProject"
    #      or bare absolute paths in context sections.
    project_path: str = ""

    # Look for labelled cwd first (most reliable)
    cwd_match = _re.search(
        r"(?:current[_\s]working[_\s]directory|cwd|working[_\s]directory)\s*[:\-]\s*(/[^\s\n,;\"'<>]+)",
        full_system,
        _re.IGNORECASE,
    )
    if cwd_match:
        project_path = cwd_match.group(1).rstrip("/")

    # Fallback: find the longest /Users/... or /home/... path in the system prompt
    if not project_path:
        candidates = _re.findall(r"(/(?:Users|home)/[^\s\n,;\"'<>]{4,})", full_system)
        if candidates:
            # Pick the longest match (most specific path)
            project_path = max(candidates, key=len).rstrip("/")
            # Strip down to likely project root (≤ 5 path components)
            parts = project_path.split("/")
            if len(parts) > 6:
                project_path = "/".join(parts[:6])

    if project_path:
        safe = _re.sub(r"[^a-zA-Z0-9_\-]", "_", project_path)[:80]
        session_id = f"{_MEMORY_SESSION_NAMESPACE}:project:{safe}"
        debug_log("session_id_project_scoped", project_path=project_path, session_id=session_id)
        return session_id

    # 3. Fallback: hash of system prompt + first user message
    seed = "|".join(system_parts) + "|" + first_user
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return f"{_MEMORY_SESSION_NAMESPACE}:{digest}"


async def _persist_memory_best_effort(
    session_id: str,
    model: str,
    messages: List[Dict[str, Any]],
    assistant_text: str,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Persist user turn + assistant turn to Redis + Postgres, update rolling summary.
    Errors are caught and logged; this function must never raise.
    """
    if not _MEMORY_ENABLED:
        return
    if _memory_store is None or _memory_summary is None:
        return

    # We only persist if it's the very first turn or if we are in a stateless fallback.
    if len(messages) > 1 and STATE.get(history_key(messages[:-1])):
        return

    try:
        # --- Identify user turn (last user message) ---
        user_content = ""
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                user_content = content_to_text(msg.get("content", ""))
                break

        # --- Build turn dicts for Redis ---
        user_turn = {"role": "user", "content": user_content[:500]}
        assistant_turn: Dict[str, Any] = {"role": "assistant", "content": assistant_text[:500]}
        if tool_calls:
            assistant_turn["tool_calls"] = tool_calls

        # --- Redis: append recent turns ---
        if _MEMORY_ENABLE_REDIS:
            new_turns = [user_turn, assistant_turn]
            for t in new_turns:
                await _memory_store.append_recent_turn(session_id, t)

            # Update rolling summary
            try:
                prev_summary = await _memory_store.get_rolling_summary(session_id)
                new_summary = await _memory_summary.update_rolling_summary(prev_summary, new_turns)
                await _memory_store.set_rolling_summary(session_id, new_summary)
            except Exception as _sum_exc:
                debug_log("memory_summary_update_error", error=str(_sum_exc))

        # --- Postgres: persist turns durably ---
        if _MEMORY_ENABLE_PERSISTENCE:
            # Determine turn index (approximate; use timestamp-based ordering)
            turn_count = len([m for m in messages if isinstance(m, dict) and m.get("role") != "system"])

            user_compact = _memory_summary.compact_turn_content("user", user_content)
            user_turn_id = await _memory_store.insert_turn(
                session_id=session_id,
                turn_index=turn_count,
                role="user",
                content=user_content,
                compact_content=user_compact,
                model=model,
            )

            asst_content = assistant_text
            if tool_calls:
                asst_content += "\n" + stable_json(tool_calls)
            asst_compact = _memory_summary.compact_turn_content("assistant", asst_content)
            asst_turn_id = await _memory_store.insert_turn(
                session_id=session_id,
                turn_index=turn_count + 1,
                role="assistant",
                content=asst_content,
                compact_content=asst_compact,
                model=model,
            )

            # Persist summary snapshot periodically (every call; lightweight since text is small)
            if _MEMORY_ENABLE_REDIS:
                try:
                    current_summary = await _memory_store.get_rolling_summary(session_id)
                    if current_summary:
                        await _memory_store.insert_summary(
                            session_id=session_id,
                            summary_text=current_summary,
                            summary_type="rolling",
                        )
                except Exception:
                    pass

            # --- Embed compact turn text and store vectors for hybrid retrieval ---
            if _MEMORY_ENABLE_EMBEDDINGS and _memory_retrieval is not None:
                for ref_id, ref_role, compact_text in [
                    (user_turn_id, "user",      user_compact),
                    (asst_turn_id, "assistant", asst_compact),
                ]:
                    if not ref_id or not compact_text:
                        continue
                    try:
                        vec = await _memory_retrieval.get_embedding(compact_text)
                        if vec:
                            await _memory_store.insert_embedding(
                                session_id=session_id,
                                ref_id=ref_id,
                                ref_type="turn",
                                compact_text=compact_text,
                                vector=vec,
                                metadata={"role": ref_role},
                            )
                    except Exception as _emb_exc:
                        debug_log("memory_embed_turn_error", role=ref_role, error=str(_emb_exc))

        debug_log("memory_persisted", session_id=session_id, model=model)


    except Exception as exc:
        debug_log("memory_persist_error", session_id=session_id, error=str(exc))


async def _inject_memory_into_messages(
    session_id: str,
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Prepend the rolling memory summary into the system prompt and trim old
    non-system messages to keep context size bounded.

    Behaviour:
    - Calls assemble_memory() to get the compact summary + working memory text.
    - Wraps it in <memory>...</memory> tags and prepends to the first system
      message (or inserts a synthetic system message if none exists).
    - Keeps only the last _MEMORY_MAX_INJECT_TURNS non-system messages.
    - Returns messages unchanged (with best-effort error handling) if anything fails.
    """
    if _memory_retrieval is None:
        return messages
    try:
        last_user = next(
            (m.get("content", "") for m in reversed(messages)
             if isinstance(m, dict) and m.get("role") == "user"),
            "",
        )
        assembled = await _memory_retrieval.assemble_memory(
            session_id,
            query_text=last_user[:400] if isinstance(last_user, str) else "",
        )
        rolling_summary = assembled.assembled_text.strip() if assembled else ""
        # --- NEW: adaptive memory budget ---
        total_len = len(rolling_summary) + sum(len(str(m.get("content", ""))) for m in messages)

        if total_len > 12000:
            rolling_summary = _truncate_text(rolling_summary, 1200)
        elif total_len > 8000:
            rolling_summary = _truncate_text(rolling_summary, 1600)
        else:
            rolling_summary = _truncate_text(rolling_summary, 2000)
        formatted_summary = (
            _memory_summary.format_summary_for_prompt(rolling_summary)
            if _memory_summary is not None
            else rolling_summary
        )

        # 1. Recent turns (last N turns)
        recent_turns_text = ""
        recent_turns: List[Dict[str, Any]] = []
        try:
            if _memory_store is not None:
                # memory_store.get_recent_turns() does its own capping
                recent_turns = await _memory_store.get_recent_turns(session_id)
                if recent_turns:
                    parts = []
                    for t in recent_turns:
                        role = t.get("role", "unknown")
                        content = _truncate_text(str(t.get("content", "")), 500)
                        parts.append(f"{role.upper()}: {content}")
                    recent_turns_text = "\n".join(parts)
        except Exception as _rt_exc:
            debug_log("memory_recent_turns_error", error=str(_rt_exc))

        # Split messages into system and non-system
        system_msgs = [dict(m) for m in messages if isinstance(m, dict) and m.get("role") == "system"]
        non_system = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]

        # Inject memory block into system prompt (or create one)
        tag_block_parts = []

        # Always inject: summary + recent-turns context (no mode gate).
        if formatted_summary:
            tag_block_parts.append(f"<planner_context>\n{formatted_summary}\n</planner_context>")
        if recent_turns_text:
            tag_block_parts.append(f"<coder_context>\n{recent_turns_text}\n</coder_context>")

        tag_block = "\n\n".join(tag_block_parts)

        # Recent-files hint: soft memory of files already explored this session.
        recent_files_hint = ""
        files: set[str] = set()
        try:
            if recent_turns:
                for t in recent_turns:
                    content = str(t.get("content", ""))
                    for part in content.split():
                        if "." in part and "/" in part:
                            files.add(part.strip(".,:;()[]"))
                if files:
                    recent_files_hint = "Recently inspected files:\n" + "\n".join(f"- {f}" for f in list(files)[:5])
        except Exception as _rf_exc:
            debug_log("memory_recent_files_error", error=str(_rf_exc))

        if recent_files_hint:
            tag_block += f"\n\n<recent_files>\n{recent_files_hint}\n</recent_files>"

        # --- NEW: Structural Skeletons ---
        # If we have recent files, check if we have parsed API skeletons for them in Redis.
        skeletons_text = ""
        try:
            if _memory_store is not None and files:
                skeleton_parts = []
                for f in list(files)[:5]: # cap at 5 recent files
                    file_hash = hashlib.sha256(f.encode('utf-8')).hexdigest()
                    skel = await _memory_store.get_file_skeleton(session_id, file_hash)
                    if skel:
                        skeleton_parts.append(f"--- {f} ---\n{skel}")
                
                if skeleton_parts:
                    skeletons_text = "API Skeletons of recent files:\n\n" + "\n\n".join(skeleton_parts)
        except Exception as _sk_exc:
            debug_log("memory_recent_skeletons_error", error=str(_sk_exc))

        if skeletons_text:
            tag_block += f"\n\n<api_skeletons>\n{skeletons_text}\n</api_skeletons>"

        # Anti-loop instructions always accompany the memory block.
        if tag_block:
            inject_instructions = (
                "Instructions:\n"
                "- Use <recent_files> as a soft reminder of files already explored.\n"
                "- Exploration is allowed, but avoid rereading the same files or same sections without new purpose.\n"
                "- Prefer making progress over repeated exploration loops.\n\n"
            )
            tag_block = f"{tag_block}\n\n{inject_instructions}"
        if tag_block:
            if system_msgs:
                existing = system_msgs[0].get("content", "")
                system_msgs[0]["content"] = (
                    f"{tag_block}\n\n"
                    "Instructions:\n"
                    "First, silently plan using <planner_context>:\n"
                    "- Understand goals, decisions, and next steps\n"
                    "- Do NOT output the plan explicitly\n\n"
                    "Then execute using <coder_context>:\n"
                    "- Focus on exact file edits, tool calls, and current task\n"
                    "- Be precise and avoid unnecessary exploration\n\n"
                    "Rules:\n"
                    "- Exploration of the codebase is allowed when needed to understand context\n"
                    "- Do not repeatedly read the same file or the same file sections without new purpose\n"
                    "- If you already inspected a file, only revisit it if you need different sections or new information\n"
                    "- Do not search repeatedly for the same missing symbols if already checked\n"
                    "- If a required file or symbol is missing, assume it should be created\n"
                    "- Prefer making progress over repeated exploration loops\n\n"
                    f"{existing}"
                ) if existing else (
                    f"{tag_block}\n\n"
                    "Instructions:\n"
                    "First, silently plan using <planner_context>:\n"
                    "- Understand goals, decisions, and next steps\n"
                    "- Do NOT output the plan explicitly\n\n"
                    "Then execute using <coder_context>:\n"
                    "- Focus on exact file edits, tool calls, and current task\n"
                    "- Be precise and avoid unnecessary exploration\n\n"
                    "Rules:\n"
                    "- Exploration of the codebase is allowed when needed to understand context\n"
                    "- Do not repeatedly read the same file or the same file sections without new purpose\n"
                    "- If you already inspected a file, only revisit it if you need different sections or new information\n"
                    "- Do not search repeatedly for the same missing symbols if already checked\n"
                    "- If a required file or symbol is missing, assume it should be created\n"
                    "- Prefer making progress over repeated exploration loops\n\n"
                )
            else:
                system_msgs = [{
                    "role": "system",
                    "content": (
                        f"{tag_block}\n\n"
                        "Instructions:\n"
                        "First, silently plan using <planner_context>:\n"
                        "- Understand goals, decisions, and next steps\n"
                        "- Do NOT output the plan explicitly\n\n"
                        "Then execute using <coder_context>:\n"
                        "- Focus on exact file edits, tool calls, and current task\n"
                        "- Be precise and avoid unnecessary exploration\n\n"
                        "Rules:\n"
                        "- Exploration of the codebase is allowed when needed to understand context\n"
                        "- Do not repeatedly read the same file or the same file sections without new purpose\n"
                        "- If you already inspected a file, only revisit it if you need different sections or new information\n"
                        "- Do not search repeatedly for the same missing symbols if already checked\n"
                        "- If a required file or symbol is missing, assume it should be created\n"
                        "- Prefer making progress over repeated exploration loops\n\n"
                    )
                }]

        # Trim history: keep only the last N non-system messages
        if len(non_system) > _MEMORY_MAX_INJECT_TURNS:
            trimmed = len(non_system) - _MEMORY_MAX_INJECT_TURNS
            non_system = non_system[-_MEMORY_MAX_INJECT_TURNS:]
            debug_log("memory_inject_trimmed", dropped_turns=trimmed, kept_turns=len(non_system))

        result = system_msgs + non_system
        debug_log(
            "memory_injected",
            session_id=session_id,
            memory_chars=len(formatted_summary),
            final_message_count=len(result),
        )
        return result

    except Exception as exc:
        debug_log("memory_inject_error", session_id=session_id, error=str(exc))
        return messages  # fall back to unmodified messages


# --- Hybrid routing: OpenAI tool call detection
def request_uses_openai_tools(body: Dict[str, Any], messages: List[Dict[str, Any]]) -> bool:
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        return True

    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool":
            return True
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            return True

    return False


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text" and "text" in item:
                    parts.append(str(item["text"]))
                elif item.get("type") == "input_text" and "text" in item:
                    parts.append(str(item["text"]))
                elif "text" in item:
                    parts.append(str(item["text"]))
                else:
                    parts.append(stable_json(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return stable_json(content)


# --- Proxy-side message filtering helpers
def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head - 13
    if tail < 0:
        tail = 0
    return f"{text[:head]}\n\n[...snip...]\n\n{text[-tail:] if tail else ''}"


def _detect_and_break_tool_loop(
    messages: List[Dict[str, Any]],
    threshold: int = 3,
) -> List[Dict[str, Any]]:
    """Detect consecutive duplicate tool calls and inject a loop-breaking message.

    Scans the assistant messages in *messages* for a run of identical
    (tool_name, arguments) pairs at the tail of the conversation.  When the
    run length meets or exceeds *threshold*, appends a synthetic 'user'
    message that explains the loop and tells the model to change approach.

    Returns the original list unchanged when no loop is detected, or a new
    list with the warning appended.
    """
    if not messages or threshold < 2:
        return messages

    # Collect (tool_name, arguments_str) for every assistant tool-call message.
    call_records: List[tuple] = []  # (message_index, (name, args))
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        tcs = msg.get("tool_calls")
        if not isinstance(tcs, list) or not tcs:
            continue
        tc = tcs[0]  # check first (usually only) tool call in the turn
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function", {})
        key = (fn.get("name", ""), fn.get("arguments", ""))
        call_records.append((i, key))

    if len(call_records) < threshold:
        return messages

    # Count how long the run of identical keys is at the tail.
    last_key = call_records[-1][1]
    run = 1
    for _, key in reversed(call_records[:-1]):
        if key == last_key:
            run += 1
        else:
            break

    if run < threshold:
        return messages

    # Build a human-readable summary of the repeated call.
    tool_name, tool_args = last_key
    try:
        args_dict = json.loads(tool_args) if tool_args else {}
        args_summary = ", ".join(
            f"{k}={v!r}" for k, v in list(args_dict.items())[:5]
        )
    except Exception:
        args_summary = tool_args[:120] if tool_args else ""

    print(
        f"[lm-proxy] tool_loop_detected tool={tool_name!r} run={run} args={args_summary[:80]!r}",
        flush=True,
    )

    warning = (
        f"[PROXY LOOP ALERT] You have called `{tool_name}({args_summary})` "
        f"{run} consecutive times with identical arguments and received the same "
        f"result every time. You MUST change your approach:\n"
        f"  - If you are reading a file, increment the offset to the value shown "
        f"in the last result (e.g., if the result says 'Use offset=2763', call "
        f"read with offset=2763 — NOT {args_summary}).\n"
        f"  - If no further reading is needed, synthesise what you have learned "
        f"and respond to the user directly.\n"
        f"Do NOT repeat the same call again."
    )

    new_messages = list(messages)
    new_messages.append({"role": "user", "content": warning})
    return new_messages


# ---------------------------------------------------------------------------
# Tool definition compaction
# ---------------------------------------------------------------------------

# Set to "0" to disable tool description compaction.
_COMPACT_TOOL_DEFINITIONS = os.getenv("LM_PROXY_COMPACT_TOOL_DEFINITIONS", "1").strip().lower() not in {"0", "false", "no", "off"}

# Max chars to keep from each tool description (first N chars ≈ first sentence).
_TOOL_DESC_MAX_CHARS = int(os.getenv("LM_PROXY_TOOL_DESC_MAX_CHARS", "120"))
# Parameter-level description max chars (per param).
_PARAM_DESC_MAX_CHARS = int(os.getenv("LM_PROXY_PARAM_DESC_MAX_CHARS", "80"))
# Schema keys that add noise without helping the model pick arguments.
_SCHEMA_NOISE_KEYS = frozenset({"$schema", "additionalProperties"})


def _compact_tool_definitions(tools: Any) -> Any:
    """Strip verbose text from tool definitions before forwarding to LM Studio.

    Reduces token cost by:
    - Truncating long tool descriptions to the first sentence / first N chars.
    - Truncating per-parameter descriptions similarly.
    - Removing schema noise keys ($schema, additionalProperties) that LM Studio
      doesn't need and that waste tokens.

    Returns the original value unchanged if compaction is disabled or the input
    is not a list.
    """
    if not _COMPACT_TOOL_DEFINITIONS or not isinstance(tools, list):
        return tools

    compacted: List[Any] = []
    for tool in tools:
        if not isinstance(tool, dict):
            compacted.append(tool)
            continue
        tool_copy = dict(tool)
        fn = tool_copy.get("function")
        if not isinstance(fn, dict):
            compacted.append(tool_copy)
            continue
        fn_copy = dict(fn)

        # Truncate top-level description to first sentence / max chars.
        desc = fn_copy.get("description", "")
        if isinstance(desc, str) and len(desc) > _TOOL_DESC_MAX_CHARS:
            # Try to cut at first sentence boundary.
            for sep in ("\n", ". ", "! ", "? "):
                idx = desc.find(sep)
                if 0 < idx <= _TOOL_DESC_MAX_CHARS:
                    desc = desc[: idx + 1].rstrip()
                    break
            else:
                desc = desc[:_TOOL_DESC_MAX_CHARS].rstrip() + "…"
            fn_copy["description"] = desc

        # Compact parameters schema.
        params = fn_copy.get("parameters")
        if isinstance(params, dict):
            params_copy = {k: v for k, v in params.items() if k not in _SCHEMA_NOISE_KEYS}
            props = params_copy.get("properties")
            if isinstance(props, dict):
                new_props: Dict[str, Any] = {}
                for pname, pval in props.items():
                    if not isinstance(pval, dict):
                        new_props[pname] = pval
                        continue
                    p_copy = dict(pval)
                    pdesc = p_copy.get("description", "")
                    if isinstance(pdesc, str) and len(pdesc) > _PARAM_DESC_MAX_CHARS:
                        for sep in ("\n", ". ", "! ", "? "):
                            idx = pdesc.find(sep)
                            if 0 < idx <= _PARAM_DESC_MAX_CHARS:
                                pdesc = pdesc[: idx + 1].rstrip()
                                break
                        else:
                            pdesc = pdesc[:_PARAM_DESC_MAX_CHARS].rstrip() + "…"
                        p_copy["description"] = pdesc
                    new_props[pname] = p_copy
                params_copy["properties"] = new_props
            fn_copy["parameters"] = params_copy

        tool_copy["function"] = fn_copy
        compacted.append(tool_copy)

    original_chars = sum(len(json.dumps(t)) for t in tools)
    compacted_chars = sum(len(json.dumps(t)) for t in compacted)
    if original_chars != compacted_chars:
        print(
            f"[lm-proxy] tool_defs_compacted tools={len(compacted)}"
            f" before={original_chars} after={compacted_chars}"
            f" saved={original_chars - compacted_chars}",
            flush=True,
        )
    return compacted


def compact_message_content(content: Any, max_chars: int) -> Any:
    if isinstance(content, str):
        return _truncate_text(content, max_chars)

    if isinstance(content, list):
        compacted: List[Any] = []
        for item in content:
            if isinstance(item, dict):
                item_copy = dict(item)

                if isinstance(item_copy.get("text"), str):
                    item_copy["text"] = _truncate_text(item_copy["text"], max_chars)

                if isinstance(item_copy.get("content"), str):
                    item_copy["content"] = _truncate_text(item_copy["content"], max_chars)

                image_url = item_copy.get("image_url")
                if (
                    isinstance(image_url, dict)
                    and isinstance(image_url.get("url"), str)
                    and image_url["url"].startswith("data:")
                ):
                    image_url = dict(image_url)
                    image_url["url"] = _truncate_text(
                        image_url["url"], min(len(image_url["url"]), max_chars)
                    )
                    item_copy["image_url"] = image_url

                compacted.append(item_copy)
            elif isinstance(item, str):
                compacted.append(_truncate_text(item, max_chars))
            else:
                compacted.append(item)
        return compacted

    return content


async def _extract_and_cache_skeleton_bg(session_id: str, raw_content: str) -> None:
    """
    Background task: Look for evidence of a file read in the tool output,
    extract its API skeleton, and cache it to Redis.
    """
    if _memory_store is None or _skeleton_extractor is None:
        return
        
    try:
        # Basic heuristic: if it looks like a file path is mentioned near the start, extract it.
        # Often tools output something like: "Read file: /path/to/script.swift\n\nimport Foundation..."
        lines = raw_content.split("\\n", 10)
        file_path = "unknown.txt"
        
        # Try to infer the file type from the content or the first lines.
        # OpenCode `read_file` usually prints the path.
        for line in lines:
            if "/" in line and "." in line:
                parts = line.split()
                for p in parts:
                    if "/" in p and "." in p:
                        file_path = p.strip("',`\":[]()")
                        break
        
        # SourceKitten only works if the filename ends in .swift
        # or we explicitly guess it's swift. If it has import Foundation, it's swift.
        if "import Foundation" in raw_content or "import SwiftUI" in raw_content:
            file_path = "inferred.swift"
            
        skel = _skeleton_extractor.extract_skeleton(raw_content, file_path)
        if skel and len(skel) > 10:
            file_hash = hashlib.sha256(file_path.encode('utf-8')).hexdigest()
            await _memory_store.set_file_skeleton(session_id, file_hash, skel)
            debug_log("skeleton_extracted_in_bg", file_path=file_path, skeleton_len=len(skel))
    except Exception as e:
        debug_log("skeleton_extraction_failed", error=str(e))


def _is_filter_bypassed(messages: List[Dict[str, Any]]) -> bool:
    if not messages:
        return True
    return False

def filter_messages_for_proxy(session_id: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if _is_filter_bypassed(messages):
        debug_log("filter_bypassed_empty_messages")
        return messages

    if not ENABLE_PROXY_FILTERING:
        debug_log("filter_bypassed_disabled", message_count=len(messages))
        return messages

    max_recent_messages = int(os.getenv("LM_PROXY_MAX_RECENT_MESSAGES", "40"))
    max_old_message_chars = int(os.getenv("LM_PROXY_MAX_OLD_MESSAGE_CHARS", "4000"))
    max_recent_message_chars = int(os.getenv("LM_PROXY_MAX_RECENT_MESSAGE_CHARS", "12000"))
    max_system_prompt_chars = int(os.getenv("LM_PROXY_MAX_SYSTEM_PROMPT_CHARS", "10000"))
    max_tool_message_chars = int(os.getenv("LM_PROXY_MAX_TOOL_MESSAGE_CHARS", "200000"))
    _OVERRIDE_MAX_TOKENS = int(os.getenv("LM_PROXY_OVERRIDE_MAX_TOKENS", "1280000"))

    filtered: List[Dict[str, Any]] = []
    non_system_indices = [
        i for i, msg in enumerate(messages)
        if isinstance(msg, dict) and msg.get("role") != "system"
    ]
    recent_non_system = set(non_system_indices[-max_recent_messages:])

    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            continue

        role = message.get("role")
        msg_copy = dict(message)

        if role == "system":
            # Always keep system messages, but truncate if they are too long.
            msg_copy["content"] = _truncate_text(
                content_to_text(message.get("content", "")),
                max_system_prompt_chars
            )
            filtered.append(msg_copy)
            continue

        if role == "tool":
            raw = message.get("content", "")
            if isinstance(raw, str):
                # Trigger skeleton extraction in the background if it's a file read response
                if len(raw) > 50 and "\n" in raw:
                    asyncio.create_task(_extract_and_cache_skeleton_bg(session_id, raw))

            if isinstance(raw, str) and len(raw) > max_tool_message_chars:
                # Head-only: never split code in the middle with [...snip...].
                # The model should use the offset parameter to read further.
                raw = raw[:max_tool_message_chars].rstrip() + (
                    "\n[PROXY: output truncated at"
                    f" {max_tool_message_chars} chars."
                    " Use a smaller limit or a higher offset to read further.]"
                )
            elif not isinstance(raw, str):
                raw = compact_message_content(raw, max_tool_message_chars)
            msg_copy["content"] = raw
            filtered.append(msg_copy)
            continue

        max_chars = max_recent_message_chars if i in recent_non_system else max_old_message_chars
        msg_copy["content"] = compact_message_content(message.get("content", ""), max_chars)
        filtered.append(msg_copy)

    debug_log(
        "filter_applied",
        original_count=len(messages),
        filtered_count=len(filtered),
        max_recent_messages=max_recent_messages,
        max_old_message_chars=max_old_message_chars,
        max_recent_message_chars=max_recent_message_chars,
        max_system_prompt_chars=max_system_prompt_chars,
        max_tool_message_chars=max_tool_message_chars,
    )
    return filtered


def normalize_input_content(content: Any) -> str | List[Dict[str, Any]]:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return content_to_text(content)

    normalized: List[Dict[str, Any]] = []
    text_parts: List[str] = []

    def flush_text_parts() -> None:
        nonlocal text_parts, normalized
        if text_parts:
            normalized.append({"type": "message", "content": "\n".join(text_parts)})
            text_parts = []

    for item in content:
        if isinstance(item, str):
            text_parts.append(item)
            continue

        if not isinstance(item, dict):
            text_parts.append(str(item))
            continue

        item_type = item.get("type")

        if item_type in {"text", "input_text"}:
            text = item.get("text") or item.get("content")
            if isinstance(text, str):
                text_parts.append(text)
            else:
                text_parts.append(content_to_text(item))
            continue

        if item_type == "image_url":
            image_url = item.get("image_url")
            if isinstance(image_url, dict):
                url = image_url.get("url")
            else:
                url = image_url
            if isinstance(url, str) and url.startswith("data:"):
                flush_text_parts()
                normalized.append({"type": "image", "data_url": url})
                continue
            text_parts.append(content_to_text(item))
            continue

        if item_type == "image":
            data_url = item.get("data_url")
            if isinstance(data_url, str):
                flush_text_parts()
                normalized.append({"type": "image", "data_url": data_url})
                continue
            text_parts.append(content_to_text(item))
            continue

        text_parts.append(content_to_text(item))

    flush_text_parts()

    if not normalized:
        return ""
    if len(normalized) == 1 and normalized[0].get("type") == "message":
        return normalized[0]["content"]
    return normalized


def extract_tool_calls_from_api_chat_output(output: Any) -> List[Dict[str, Any]]:
    if not isinstance(output, list):
        return []

    tool_calls: List[Dict[str, Any]] = []
    for index, item in enumerate(output):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "invalid_tool_call":
            debug_log(
                "invalid_tool_call_output",
                reason=item.get("reason"),
                metadata=item.get("metadata"),
            )
            continue
        if item.get("type") != "tool_call":
            continue

        tool_name = item.get("tool")
        if not isinstance(tool_name, str) or not tool_name:
            continue

        arguments = item.get("arguments", {})
        if not isinstance(arguments, str):
            arguments = stable_json(arguments)

        tool_calls.append(
            {
                "id": f"call_{index}_{tool_name}",
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": arguments,
                },
            }
        )

    return tool_calls


def stats_to_usage(stats: Any) -> Dict[str, Any]:
    if not isinstance(stats, dict):
        return {}

    prompt_tokens = stats.get("input_tokens", 0)
    completion_tokens = stats.get("total_output_tokens", 0)
    if not isinstance(prompt_tokens, int):
        prompt_tokens = int(prompt_tokens or 0)
    if not isinstance(completion_tokens, int):
        completion_tokens = int(completion_tokens or 0)

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


# --- Helper: extract system prompt and last actionable message
def extract_system_prompt(messages: List[Dict[str, Any]]) -> Optional[str]:
    parts: List[str] = []
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            text = content_to_text(message.get("content", ""))
            if text:
                parts.append(text)
    if not parts:
        return None
    return "\n\n".join(parts)


def get_last_non_system_message(messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") != "system":
            return message
    raise HTTPException(status_code=400, detail="No non-system message found in request")


def extract_text_from_api_chat_output(output: Any) -> str:
    if isinstance(output, str):
        return output
    if not isinstance(output, list):
        return ""

    parts: List[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue

        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for sub in content:
                if isinstance(sub, dict):
                    text = sub.get("text") or sub.get("content")
                    if isinstance(text, str):
                        parts.append(text)
                elif isinstance(sub, str):
                    parts.append(sub)
    return "".join(parts)



def build_chat_completion_response(
    model: str,
    resp: Dict[str, Any],
    assistant_text: str,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    message: Dict[str, Any] = {
        "role": "assistant",
        "content": assistant_text if assistant_text else "",
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": f"chatcmpl_proxy_{resp.get('response_id', resp.get('id', 'unknown'))}",
        "object": "chat.completion",
        "created": resp.get("created_at", resp.get("created", 0)),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": stats_to_usage(resp.get("stats", {})),
    }



def _extract_new_messages_for_responses_api(
    messages: List[Dict[str, Any]],
) -> tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Split messages into system_prompt and the new input items since the last assistant turn.

    Returns (system_prompt, input_items) where input_items are formatted for /v1/responses:
    - user text        -> {"role": "user", "content": [...]}
    - tool result      -> {"type": "function_call_output", "call_id": "...", "output": "..."}
    """
    system_prompt: Optional[str] = None
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "system":
            system_prompt = m.get("content", "") or system_prompt

    # Find the index of the last assistant message (with tool_calls = end of last model turn)
    last_asst_idx = -1
    for i, m in enumerate(messages):
        if isinstance(m, dict) and m.get("role") == "assistant":
            last_asst_idx = i

    # New messages: everything after the last assistant turn (tool results + new user msg)
    raw_new = messages[last_asst_idx + 1:] if last_asst_idx >= 0 else [
        m for m in messages if isinstance(m, dict) and m.get("role") not in ("system", "assistant")
    ]

    input_items: List[Dict[str, Any]] = []
    for m in raw_new:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "tool":
            # Convert tool result to function_call_output
            input_items.append({
                "type": "function_call_output",
                "call_id": m.get("tool_call_id", "unknown"),
                "output": str(m.get("content", "")),
            })
        elif role == "user":
            content = m.get("content", "")
            if isinstance(content, str):
                input_items.append({"role": "user", "content": content})
            elif isinstance(content, list):
                # Pass through content array as-is (images etc.)
                input_items.append({"role": "user", "content": content})

    return system_prompt, input_items


def _responses_output_to_chat_completion(
    resp: Dict[str, Any], model: str
) -> Dict[str, Any]:
    """Translate a /v1/responses response body to OpenAI chat.completion format."""
    output = resp.get("output", [])
    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "message":
            for part in item.get("content", []):
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text_parts.append(part.get("text", ""))
                elif isinstance(part, str):
                    text_parts.append(part)
        elif item_type == "function_call":
            tool_calls.append({
                "id": item.get("call_id") or item.get("id") or f"call_{len(tool_calls)}",
                "type": "function",
                "function": {
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", ""),
                },
            })
        elif item_type == "reasoning":
            pass  # reasoning is internal; don't expose to client

    text = "".join(text_parts)
    message: Dict[str, Any] = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    # Map usage fields
    raw_usage = resp.get("usage", {}) or {}
    usage = {
        "prompt_tokens": raw_usage.get("input_tokens", 0),
        "completion_tokens": raw_usage.get("output_tokens", 0),
        "total_tokens": raw_usage.get("total_tokens", 0),
    }

    return {
        "id": resp.get("id", "chatcmpl_responses"),
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": usage,
        # Pass the response_id through so callers can inspect it.
        "x_lmstudio_response_id": resp.get("id"),
    }


async def stream_openai_compatible_response(
    url: str, payload: Dict[str, Any], messages: List[Dict[str, Any]] | None = None,
    original_body: Dict[str, Any] = None, fallback_func=None
) -> Any:
    """
    Generic streaming helper that pipes an OpenAI-compatible stream from an upstream URL.
    Optionally updates the global STATE with the resulting response_id for continuity.
    """
    timeout = httpx.Timeout(900.0, connect=30.0)
    client = httpx.AsyncClient(timeout=timeout)
    req = client.build_request("POST", url, json=payload)
    
    try:
        r = await client.send(req, stream=True)
    except Exception as exc:
        await client.aclose()
        if fallback_func and original_body is not None:
            debug_log("streaming_failed_fallback", error=str(exc))
            return await fallback_func(original_body)
        raise HTTPException(status_code=500, detail=str(exc))
        
    if r.status_code >= 400:
        detail = await r.aread()
        err_text = detail.decode("utf-8", errors="replace")
        await r.aclose()
        await client.aclose()
        if fallback_func and original_body is not None:
            debug_log("streaming_rejected_fallback", status=r.status_code, error=err_text)
            return await fallback_func(original_body)
        raise HTTPException(r.status_code, err_text)

    async def event_generator():
        last_id = None
        accumulated_content = ""
        current_tool_call_index = -1
        try:
            is_responses_api = url.endswith("/responses")
            async for line in r.aiter_lines():
                if not line:
                    continue
                    
                if not is_responses_api:
                    # Standard OpenAI stream pass-through
                    if messages and line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str and data_str != "[DONE]":
                            try:
                                data = json.loads(data_str)
                                if not last_id:
                                    last_id = data.get("id") or data.get("response_id")
                                
                                choices = data.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    if "content" in delta:
                                        accumulated_content += delta["content"]
                                    if choices[0].get("finish_reason"):
                                        debug_log("stream_finished", reason=choices[0].get("finish_reason"))
                            except (json.JSONDecodeError, KeyError):
                                pass

                    yield f"{line}\n\n"
                else:
                    # Translate /v1/responses SSE stream to OpenAI chat completions SSE chunk
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str and data_str != "[DONE]":
                            try:
                                data = json.loads(data_str)
                                evt_type = data.get("type")
                                
                                if evt_type == "response.created":
                                    if not last_id:
                                        last_id = data.get("response", {}).get("id", "resp_unk")
                                    # yield initial role block
                                    chunk = {
                                        "id": last_id,
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": payload.get("model", "unknown"),
                                        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]
                                    }
                                    yield f"data: {json.dumps(chunk)}\n\n"
                                    
                                elif evt_type == "response.output_text.delta":
                                    text_delta = data.get("delta", "")
                                    accumulated_content += text_delta
                                    chunk = {
                                        "id": last_id or "resp_unk",
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": payload.get("model", "unknown"),
                                        "choices": [{"index": 0, "delta": {"content": text_delta}, "finish_reason": None}]
                                    }
                                    yield f"data: {json.dumps(chunk)}\n\n"
                                    
                                elif evt_type == "tool_call.start":
                                    current_tool_call_index += 1
                                    call_id = f"call_{uuid.uuid4().hex[:12]}"
                                    tool_name = data.get("tool", "unknown")
                                    chunk = {
                                        "id": last_id or "resp_unk",
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": payload.get("model", "unknown"),
                                        "choices": [{"index": 0, "delta": {
                                            "tool_calls": [{
                                                "index": current_tool_call_index,
                                                "id": call_id,
                                                "type": "function",
                                                "function": {"name": tool_name, "arguments": ""}
                                            }]
                                        }, "finish_reason": None}]
                                    }
                                    yield f"data: {json.dumps(chunk)}\n\n"
                                    
                                elif evt_type == "tool_call.arguments":
                                    args_obj = data.get("arguments", {})
                                    args_str = json.dumps(args_obj) if isinstance(args_obj, dict) else str(args_obj)
                                    chunk = {
                                        "id": last_id or "resp_unk",
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": payload.get("model", "unknown"),
                                        "choices": [{"index": 0, "delta": {
                                            "tool_calls": [{
                                                "index": max(0, current_tool_call_index),
                                                "function": {"arguments": args_str}
                                            }]
                                        }, "finish_reason": None}]
                                    }
                                    yield f"data: {json.dumps(chunk)}\n\n"
                                    
                                elif evt_type == "response.completed":
                                    chunk = {
                                        "id": last_id or "resp_unk",
                                        "object": "chat.completion.chunk",
                                        "created": int(time.time()),
                                        "model": payload.get("model", "unknown"),
                                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                                    }
                                    yield f"data: {json.dumps(chunk)}\n\n"
                                    yield "data: [DONE]\n\n"
                            except (json.JSONDecodeError, KeyError):
                                pass
        except Exception as exc:
            debug_log("streaming_failed", error=str(exc))
            err_dict = {"error": {"message": str(exc), "type": "proxy_stream_error"}}
            yield f"data: {json.dumps(err_dict)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            await r.aclose()
            await client.aclose()

        # Save state if we captured an ID and we have the history context
        if last_id and messages:
            asst_msg = {"role": "assistant", "content": accumulated_content}
            STATE[history_key(messages + [asst_msg])] = last_id
            # Fire-and-forget save
            asyncio.create_task(asyncio.to_thread(save_state))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def forward_responses_api_completion(body: Dict[str, Any], history_messages: Optional[List[Dict[str, Any]]] = None) -> Any:
    """
    Forward a chat/completions request to LM Studio's /v1/responses endpoint.
    This endpoint is stateful (via previous_response_id) and OpenAI-compatible (messages/tools).
    """
    messages: List[Dict[str, Any]] = body.get("messages", [])
    
    debug_log("dumping_raw_messages", first_message=messages[0] if messages else None, last_message=messages[-1] if messages else None)
    
    # Use history_messages if provided, otherwise default to the current messages array.
    # This prevents mutations (like tool-calling blocks) from breaking the history_key.
    tracking_messages = history_messages if history_messages is not None else messages
    
    model: str = body.get("model", "")
    stream = bool(body.get("stream", False))
    timeout = httpx.Timeout(900.0, connect=30.0)

    # Look up stored response_id for this session to maintain continuity
    # We MUST use the base history_messages (original raw) if provided for lookup,
    # because that's what we used to SAVE the key in the previous Turn.
    prev_messages = tracking_messages[:-1] if len(tracking_messages) > 1 else []
    hk = history_key(prev_messages)
    prev_response_id = STATE.get(hk)
    
    # If the previous turn fell back to stateless, it has a chatcmpl_ ID. We must ignore it.
    if prev_response_id and not prev_response_id.startswith("resp_"):
        prev_response_id = None

    # If we have a prev_response_id, we only need to send the latest message.
    # Otherwise (e.g., Turn 1 or dropped session), we send the full history to initialize a new state.
    if prev_response_id:
        last_msg = messages[-1] if messages else {}
        payload_input = [last_msg] if last_msg else []
    else:
        payload_input = messages

    instructions = messages[0].get("content", "") if messages and messages[0].get("role") == "system" else ""

    payload = {
        "model": model,
        "input": payload_input,
        "stream": stream,
    }
    
    if "tools" in body:
        payload["tools"] = body["tools"]
    if "tool_choice" in body:
        tc = body["tool_choice"]
        if isinstance(tc, dict):
            # LM Studio only supports string tool_choice like "auto", "required", "none"
            payload["tool_choice"] = "required" if tc else "auto"
        else:
            payload["tool_choice"] = tc
    
    # Only send instructions if non-empty and only on the FIRST turn (no previous ID)
    if instructions and not prev_response_id:
        payload["instructions"] = instructions
    
    if prev_response_id:
        payload["previous_response_id"] = prev_response_id

    if ENABLE_DEBUG_LOGGING:
        debug_log(
            "responses_api_request",
            model=model,
            stream=stream,
            has_previous_response_id=bool(prev_response_id),
            message_count=len(tracking_messages),
        )

    if stream:
        # Use existing streaming helper but target the /v1/responses endpoint
        return await stream_openai_compatible_response(
            url=f"{OPENAI_BASE}/responses", 
            payload=payload, 
            messages=messages,
            original_body=body,
            fallback_func=forward_openai_chat_completion
        )

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.post(f"{OPENAI_BASE}/responses", json=payload)
            r.raise_for_status()
            resp = r.json()
        except httpx.HTTPStatusError as exc:
            try:
                err_json = exc.response.json()
                if "error" not in err_json or not isinstance(err_json["error"], dict):
                    err_json = {"error": {"message": exc.response.text, "type": "proxy_error"}}
            except Exception:
                err_json = {"error": {"message": exc.response.text, "type": "proxy_error"}}
                
            debug_log("responses_api_failed", error=err_json)
            # Fall back to stateless chat/completions if responses API rejects the payload
            return await forward_openai_chat_completion(body)
        except Exception as exc:
            debug_log("responses_api_failed", error=str(exc))
            # Fall back to stateless chat/completions on other errors
            return await forward_openai_chat_completion(body)

    # Extract the assistant message and new response_id for state tracking
    new_response_id = resp.get("id")
    
    # Handle both OpenAI 'choices' and LM Studio 'output' formats
    choices = resp.get("choices", [])
    output = resp.get("output", [])
    
    asst_msg = {}
    finish_reason = None
    
    if choices:
        asst_msg = choices[0].get("message", {})
        finish_reason = choices[0].get("finish_reason")
    elif output:
        # LM Studio v1 /v1/responses format
        last_out = output[-1] if output else {}
        finish_reason = last_out.get("status")
        # Extract text from the content array if present
        content_items = last_out.get("content", [])
        text_content = "".join([i.get("text", "") for i in content_items if i.get("type") == "output_text"])
        asst_msg = {"role": "assistant", "content": text_content}
    
    if new_response_id and asst_msg:
        hk_save = history_key(tracking_messages + [asst_msg])
        STATE[hk_save] = new_response_id
        save_state()

    debug_log(
        "responses_api_success",
        new_response_id=new_response_id,
        finish_reason=finish_reason,
    )

    # Always return standard OpenAI format even if backend is stateful Responses API
    if (output or new_response_id) and not choices:
        # Map back to OpenAI choices format for the client
        resp["choices"] = [
            {
                "index": 0,
                "message": asst_msg,
                "finish_reason": finish_reason,
            }
        ]

    return JSONResponse(resp)


async def forward_openai_chat_completion(body: Dict[str, Any]) -> Any:

    timeout = httpx.Timeout(900.0, connect=30.0)
    
    # Flatten structured tool_choice because LM Studio universally rejects objects natively.
    # We use "auto" instead of "required" to give the model breathing room to output plain text 
    # if it needs to, preventing "toxic stops" mid-sentence caused by grammar enforcers.
    if "tool_choice" in body:
        tc = body["tool_choice"]
        if isinstance(tc, dict):
            body["tool_choice"] = "auto"
            
    # Stop stripping moved to chat_completions for global coverage

    stream = bool(body.get("stream", False))

    # stream_options stripping moved to top-level route

    debug_log(
        "route_openai_chat_completions",
        stream=stream,
        has_tools=isinstance(body.get("tools"), list) and bool(body.get("tools")),
        message_count=len(body.get("messages", [])) if isinstance(body.get("messages"), list) else None,
        model=body.get("model"),
    )

    if stream:
        return await stream_openai_compatible_response(
            f"{OPENAI_BASE}/chat/completions", body, body.get("messages", [])
        )

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{OPENAI_BASE}/chat/completions", json=body)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        response_json = r.json()

    # --- Stateful tracking for the stateless route ---
    # This allows the *next* turn to use the stateful Responses API
    sid = response_json.get("id")
    choices = response_json.get("choices", [])
    if sid and choices:
        m = choices[0].get("message", {})
        STATE[history_key(body.get("messages", []) + [m])] = sid
        save_state()

    # --- Always-on: log usage stats for monitoring ---
    usage = response_json.get("usage", {})
    if isinstance(usage, dict):
        debug_log(
            "openai_completion_stats",
            model=body.get("model"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )
        # Log a warning when context is getting full (>80 % of a common 32k window).
        # Adjust threshold via LM_PROXY_CONTEXT_WARN_TOKENS (default 25600 = 80% of 32k).
        warn_threshold = int(os.getenv("LM_PROXY_CONTEXT_WARN_TOKENS", "25600"))
        pt = usage.get("prompt_tokens") or 0
        if isinstance(pt, int) and pt > warn_threshold:
            print(
                f"[lm-proxy] context_pressure model={body.get('model')} prompt_tokens={pt}",
                flush=True,
            )

    # --- Item 1: Detect and recover from invalid / malformed tool calls ---
    # When LM Studio produces incomplete JSON for tool arguments (context overflow),
    # OpenCode sees `undefined` for content/filePath and loops forever.
    # Instead, return a synthetic assistant message asking the model to retry.
    choices = response_json.get("choices", [])
    if isinstance(choices, list) and choices:
        first_choice = choices[0]
        if isinstance(first_choice, dict):
            msg = first_choice.get("message", {})
            tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else None

            # --- NEW: Intercept Codebase Search Tool ---
            if isinstance(tool_calls, list) and len(tool_calls) == 1:
                tc = tool_calls[0]
                fn = tc.get("function", {})
                if fn.get("name") == "codebase_search":
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                        query = args.get("query", "")
                        
                        debug_log("intercepting_codebase_search", query=query)
                        
                        # 1. Get embedding
                        query_vec = None
                        if _memory_retrieval and query:
                            query_vec = await _memory_retrieval.get_embedding(query)
                            
                        # 2. Run search
                        results_text = "No results found."
                        if query_vec and _memory_store:
                            # We need a project ID. In OpenCode, the first system prompt usually has the workspace path.
                            # We'll try to infer it from the first few messages, or just use a hash of the session ID.
                            session_id = _derive_session_id(body, body.get("messages", []))
                            project_id_guess = session_id.split("-")[0] if "-" in session_id else session_id
                            
                            hits = await _memory_store.search_codebase(
                                project_id=project_id_guess,
                                query_vector=query_vec,
                                query_text=query,
                                k=5
                            )
                            
                            if hits:
                                parts = ["Here are the results from the codebase search. Please review them carefully to answer the user's request. DO NOT truncate your response. List all relevant findings:\n"]
                                for hit in hits:
                                    parts.append(f"```python\n// File: {hit['file_path']}\n{hit['content']}\n```")
                                results_text = "\n\n".join(parts) + "\n\nSearch complete. Please output your analysis now."
                                
                        # 3. Append the tool call and response to the messages array
                        new_body = dict(body)
                        new_messages = list(new_body.get("messages", []))
                        
                        # Add the assistant's tool call message
                        new_messages.append({
                            "role": "assistant",
                            "content": "I need to search the codebase to answer this.",
                            "tool_calls": [tc]
                        })
                        
                        # Add the tool's response message
                        new_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "name": "codebase_search",
                            "content": results_text
                        })
                        
                        new_body["messages"] = new_messages
                        
                        # Preserve token limits so the model doesn't truncate the post-search explanation
                        if "max_tokens" in body:
                            new_body["max_tokens"] = body["max_tokens"]
                        if "max_output_tokens" in body:
                            new_body["max_output_tokens"] = body["max_output_tokens"]
                        
                        # 4. Recursively call the LLM to get the *actual* answer
                        debug_log("codebase_search_returning_to_llm", hits=len(hits) if "hits" in locals() and hits else 0)
                        return await forward_openai_chat_completion(new_body)
                        
                    except Exception as _search_exc:
                        debug_log("codebase_search_failed", error=str(_search_exc))

            # --- NEW: loop guard (per-session) ---
            session_key = _derive_session_id(body, body.get("messages", []))

            if not hasattr(app.state, "tool_call_history"):
                app.state.tool_call_history = {}

            history = app.state.tool_call_history.setdefault(session_key, [])

            if isinstance(tool_calls, list) and tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    sig = str(fn.get("name")) + str(fn.get("arguments"))

                    history.append(sig)
                    # keep last 6
                    if len(history) > 6:
                        del history[:-6]

                    if history.count(sig) >= 3:
                        debug_log("loop_guard_triggered", tool=fn.get("name"))

                        return JSONResponse({
                            "id": "chatcmpl_loop_guard",
                            "object": "chat.completion",
                            "created": 0,
                            "model": body.get("model", ""),
                            "choices": [{
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "Repeated identical tool calls detected. Try a different approach."
                                },
                                "finish_reason": "stop",
                            }],
                        })
            if isinstance(tool_calls, list) and tool_calls:
                bad_calls = []
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function", {})
                    args = fn.get("arguments") if isinstance(fn, dict) else None
                    if args is None:
                        bad_calls.append(tc.get("id", "?"))
                        continue
                    try:
                        json.loads(args)
                    except (json.JSONDecodeError, TypeError):
                        bad_calls.append(tc.get("id", "?"))

                if bad_calls:
                    print(
                        f"[lm-proxy] invalid_tool_call_args model={body.get('model')} bad_ids={bad_calls}",
                        flush=True,
                    )
                    # Replace the malformed tool_calls response with a plain text
                    # recovery message so the client can retry cleanly.
                    recovery_msg = (
                        "I attempted a tool call but produced invalid arguments "
                        "(likely due to context length pressure). "
                        "Please retry the operation with explicit string values for all required parameters."
                    )
                    recovery_response = {
                        "id": response_json.get("id", "chatcmpl_recovery"),
                        "object": "chat.completion",
                        "created": response_json.get("created", 0),
                        "model": response_json.get("model", body.get("model", "")),
                        "choices": [{
                            "index": 0,
                            "message": {"role": "assistant", "content": recovery_msg},
                            "finish_reason": "stop",
                        }],
                        "usage": usage,
                    }
                    return JSONResponse(recovery_response)

    return JSONResponse(response_json)




def build_local_llm_models(payload: Any) -> List[Dict[str, Any]]:
    raw_models = payload.get("models", []) if isinstance(payload, dict) else []

    models: List[Dict[str, Any]] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "llm":
            continue

        quantization = item.get("quantization")
        quantization_name = None
        if isinstance(quantization, dict):
            name = quantization.get("name")
            if isinstance(name, str) and name:
                quantization_name = name

        capabilities = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
        display_name = item.get("display_name")
        key = item.get("key")

        if not isinstance(key, str) or not key:
            continue

        if not isinstance(display_name, str) or not display_name:
            display_name = key

        models.append(
            {
                "id": key,
                "name": display_name,
                "publisher": item.get("publisher") if isinstance(item.get("publisher"), str) else None,
                "params": item.get("params_string") if isinstance(item.get("params_string"), str) else None,
                "format": item.get("format") if isinstance(item.get("format"), str) else None,
                "quantization": quantization_name,
                "context_length": item.get("max_context_length"),
                "vision": bool(capabilities.get("vision", False)),
                "tool_use": bool(capabilities.get("trained_for_tool_use", False)),
                "loaded": bool(item.get("loaded_instances")),
            }
        )

    return models


@app.get("/v1/models")
async def list_models() -> Any:
    if USE_LOCAL_MODELS_FOR_V1:
        payload = await fetch_lmstudio_models()
        local_models = build_local_llm_models(payload)
        data = [
            {
                "id": item["id"],
                "object": "model",
                "created": 0,
                "owned_by": item["publisher"] or "lmstudio",
                "x_display_name": item["name"],
                "x_params": item["params"],
                "x_format": item["format"],
                "x_quantization": item["quantization"],
                "x_context_length": item["context_length"],
                "x_vision": item["vision"],
                "x_tool_use": item["tool_use"],
                "x_loaded": item["loaded"],
            }
            for item in local_models
        ]
        return JSONResponse({"object": "list", "data": data})

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{OPENAI_BASE}/models")
        r.raise_for_status()
        return JSONResponse(r.json())


@app.get("/api/v1/models")
async def list_stateful_models() -> Any:
    return JSONResponse(await fetch_lmstudio_models())


@app.get("/v1/local-models")
async def list_local_llm_models() -> Any:
    payload = await fetch_lmstudio_models()
    return JSONResponse({"models": build_local_llm_models(payload)})


# --- Lightweight debug endpoints
@app.get("/health")
async def health() -> Any:
    return JSONResponse(
        {
            "ok": True,
            "lm_base": LM_BASE,
            "openai_base": OPENAI_BASE,
            "filtering_enabled": ENABLE_PROXY_FILTERING,
            "debug_logging_enabled": ENABLE_DEBUG_LOGGING,
            "v1_models_local_enabled": USE_LOCAL_MODELS_FOR_V1,
            "state_entries": len(STATE),
            "state_file": str(STATE_FILE),
        }
    )


@app.get("/debug/state")
async def debug_state() -> Any:
    if not ENABLE_DEBUG_LOGGING:
        raise HTTPException(
            status_code=404,
            detail="Debug state endpoint is disabled. Set LM_PROXY_DEBUG=true to enable it.",
        )

    sample_keys = list(STATE.keys())[-10:]
    return JSONResponse(
        {
            "entries": len(STATE),
            "state_file": str(STATE_FILE),
            "sample_keys": sample_keys,
            "state_file": str(STATE_FILE),
        }
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    body = await request.json()

    # OpenAI clients often send stream_options even for non-streaming calls.
    # We strip it here so the messages hash remains consistent for history_key.
    if "stream_options" in body:
        body = dict(body)
        body.pop("stream_options", None)

    # Log and strip `stop` tokens to see what OpenCode is sending
    if "stop" in body:
        debug_log("intercepted_stop_tokens", stop=body["stop"])
        body = dict(body)
        body.pop("stop", None)

    requested_model = body["model"]
    raw_messages = body["messages"]
    requested_stream = bool(body.get("stream", False))

    debug_log("dumping_raw_messages", first_message=raw_messages[0] if raw_messages else None, last_message=raw_messages[-1] if raw_messages else None)

    available_model_keys: List[str] = []
    if ENABLE_MODEL_VALIDATION or MODEL_ALIASES_ENV or FALLBACK_MODEL:
        try:
            models_payload = await fetch_lmstudio_models()
            available_model_keys = extract_model_keys(models_payload)
        except Exception as exc:
            debug_log("model_list_fetch_failed", error=str(exc))

    model = resolve_model_name(requested_model, available_model_keys)
    if model != requested_model:
        body = dict(body)
        body["model"] = model

    debug_log(
        "request_received",
        model=model,
        requested_model=requested_model,
        stream=requested_stream,
        raw_message_count=len(raw_messages) if isinstance(raw_messages, list) else None,
        has_tools=isinstance(body.get("tools"), list) and bool(body.get("tools")),
        has_store_override="store" in body,
    )

    # --- GLOBAL INJECTION: Ensure codebase_search is always available ---
    if isinstance(body.get("tools"), list) or body.get("tools") is None:
        body = dict(body)
        if "tools" not in body or body["tools"] is None:
            body["tools"] = []

        existing_tool_names = [
            t.get("function", {}).get("name")
            for t in body["tools"]
            if t.get("type") == "function"
        ]
        if "codebase_search" not in existing_tool_names:
            body["tools"].append(
                {
                    "type": "function",
                    "function": {
                        "name": "codebase_search",
                        "description": "Searches the entire codebase for specific logic, implementations, or usages based on natural language. Use this to find where a variable/class is defined or used, or to understand the project architecture.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "What you are looking for. E.g. 'Where is the API URL configured?'",
                                }
                            },
                            "required": ["query"],
                        },
                    },
                }
            )
            body["tools"] = _compact_tool_definitions(body["tools"])
            debug_log("global_tool_injection", tool="codebase_search")

    uses_openai_tools = request_uses_openai_tools(body, raw_messages)
    stream = requested_stream and not uses_openai_tools
    debug_log(
        "route_decision",
        uses_openai_tools=uses_openai_tools,
        requested_stream=requested_stream,
        effective_stream=stream,
    )
    if uses_openai_tools:
        body = dict(body)

        # Apply filtering/injection for tools Turn
        _tool_session_id = _derive_session_id(body, raw_messages)
        filtered_for_tools = filter_messages_for_proxy(_tool_session_id, raw_messages)

        if (
            _MEMORY_ENABLED
            and _MEMORY_ENABLE_INJECT
            and _memory_retrieval is not None
            and not body.get("previous_response_id")
        ):
            try:
                filtered_for_tools = await _inject_memory_into_messages(
                    _tool_session_id, filtered_for_tools
                )
            except Exception as _inj_exc:
                debug_log("memory_inject_failed", error=str(_inj_exc))

        if LOOP_DETECT_THRESHOLD > 0:
            filtered_for_tools = _detect_and_break_tool_loop(
                filtered_for_tools, threshold=LOOP_DETECT_THRESHOLD
            )

        body["messages"] = filtered_for_tools
        debug_log(
            "openai_tool_route_messages_ready",
            raw_message_count=len(raw_messages),
            filtered_message_count=len(filtered_for_tools),
            filtering_applied=True,
        )

        if not body.get("stream") and "stream_options" in body:
            body.pop("stream_options", None)

        return await forward_responses_api_completion(body, history_messages=raw_messages)

    # Use the refactored stateful Response API route for all conversation turns
    return await forward_responses_api_completion(body, history_messages=raw_messages)


@app.post("/v1/embeddings")
async def embeddings(body: Dict[str, Any]) -> Any:
    # Pass through embeddings request to LM Studio's /v1/embeddings.
    timeout = httpx.Timeout(60.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{OPENAI_BASE}/embeddings", json=body)
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.text)
        return JSONResponse(r.json())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)