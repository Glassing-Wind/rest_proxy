"""proxy/handlers_memory.py — session/memory helpers."""

import hashlib
import json
from typing import Any, Dict, List, Optional

from proxy.config import (
    _MEMORY_ENABLED,
    _MEMORY_EMBEDDINGS_ENABLED,
    _MEMORY_MAX_INJECT_TURNS,
    _MEMORY_INJECT_ENABLED,
    _MEMORY_MODE,
    _MEMORY_PERSIST_ENABLED,
    _MEMORY_REDIS_ENABLED,
    _MEMORY_SESSION_NAMESPACE,
    _memory_retrieval,
    _memory_store,
    _memory_summary,
    _skeleton_extractor,
)
from proxy.logging import debug_log, stable_json
from proxy.state import STATE, save_state
from proxy.handlers_utils import content_to_text, history_key, _truncate_text


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
        debug_log(
            "session_id_project_scoped",
            project_path=project_path,
            session_id=session_id,
        )
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
    if _MEMORY_MODE == "off":
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
        assistant_turn: Dict[str, Any] = {
            "role": "assistant",
            "content": assistant_text[:500],
        }
        if tool_calls:
            assistant_turn["tool_calls"] = tool_calls

        # --- Redis: append recent turns ---
        if _MEMORY_REDIS_ENABLED:
            new_turns = [user_turn, assistant_turn]
            for t in new_turns:
                await _memory_store.append_recent_turn(session_id, t)

            # Update rolling summary
            try:
                prev_summary = await _memory_store.get_rolling_summary(session_id)
                new_summary = await _memory_summary.update_rolling_summary(
                    prev_summary, new_turns
                )
                await _memory_store.set_rolling_summary(session_id, new_summary)
            except Exception as _sum_exc:
                debug_log("memory_summary_update_error", error=str(_sum_exc))

        # --- Postgres: persist turns durably ---
        if _MEMORY_PERSIST_ENABLED:
            # Determine turn index (approximate; use timestamp-based ordering)
            turn_count = len(
                [
                    m
                    for m in messages
                    if isinstance(m, dict) and m.get("role") != "system"
                ]
            )

            user_compact = _memory_summary.compact_turn_content("user", user_content)

            asst_content = assistant_text
            if tool_calls:
                asst_content += "\n" + stable_json(tool_calls)
            asst_compact = _memory_summary.compact_turn_content(
                "assistant", asst_content
            )
            turn_ids = await _memory_store.insert_turns_batch(
                [
                    {
                        "session_id": session_id,
                        "turn_index": turn_count,
                        "role": "user",
                        "content": user_content,
                        "compact_content": user_compact,
                        "model": model,
                    },
                    {
                        "session_id": session_id,
                        "turn_index": turn_count + 1,
                        "role": "assistant",
                        "content": asst_content,
                        "compact_content": asst_compact,
                        "model": model,
                    },
                ]
            )
            user_turn_id = turn_ids[0] if len(turn_ids) > 0 else None
            asst_turn_id = turn_ids[1] if len(turn_ids) > 1 else None

            # Persist summary snapshot periodically (every call; lightweight since text is small)
            if _MEMORY_REDIS_ENABLED:
                try:
                    current_summary = await _memory_store.get_rolling_summary(
                        session_id
                    )
                    if current_summary:
                        await _memory_store.insert_summary(
                            session_id=session_id,
                            summary_text=current_summary,
                            summary_type="rolling",
                        )
                except Exception:
                    pass

            # --- Embed compact turn text and store vectors for hybrid retrieval ---
            if _MEMORY_EMBEDDINGS_ENABLED and _memory_retrieval is not None:
                embedding_rows = []
                for ref_id, ref_role, compact_text in [
                    (user_turn_id, "user", user_compact),
                    (asst_turn_id, "assistant", asst_compact),
                ]:
                    if not ref_id or not compact_text:
                        continue
                    try:
                        vec = await _memory_retrieval.get_embedding(compact_text)
                        if vec:
                            embedding_rows.append(
                                {
                                    "session_id": session_id,
                                    "ref_id": ref_id,
                                    "ref_type": "turn",
                                    "compact_text": compact_text,
                                    "vector": vec,
                                    "metadata": {"role": ref_role},
                                }
                            )
                    except Exception as _emb_exc:
                        debug_log(
                            "memory_embed_turn_error",
                            role=ref_role,
                            error=str(_emb_exc),
                        )
                if embedding_rows:
                    try:
                        await _memory_store.insert_memory_embeddings_batch(
                            embedding_rows
                        )
                    except Exception as _emb_batch_exc:
                        debug_log(
                            "memory_embed_turn_batch_error",
                            error=str(_emb_batch_exc),
                        )

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
    if not _MEMORY_INJECT_ENABLED:
        return messages
    if _memory_retrieval is None:
        return messages
    try:
        last_user = next(
            (
                m.get("content", "")
                for m in reversed(messages)
                if isinstance(m, dict) and m.get("role") == "user"
            ),
            "",
        )
        assembled = await _memory_retrieval.assemble_memory(
            session_id,
            query_text=last_user[:400] if isinstance(last_user, str) else "",
        )
        rolling_summary = assembled.assembled_text.strip() if assembled else ""
        # --- NEW: adaptive memory budget ---
        total_len = len(rolling_summary) + sum(
            len(str(m.get("content", ""))) for m in messages
        )

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
        system_msgs = [
            dict(m)
            for m in messages
            if isinstance(m, dict) and m.get("role") == "system"
        ]
        non_system = [
            m for m in messages if isinstance(m, dict) and m.get("role") != "system"
        ]

        # Inject memory block into system prompt (or create one)
        tag_block_parts = []

        # Assist/full modes inject only bounded summary + recent turns by default.
        if formatted_summary:
            tag_block_parts.append(
                f"<planner_context>\n{formatted_summary}\n</planner_context>"
            )
        if recent_turns_text:
            tag_block_parts.append(
                f"<coder_context>\n{recent_turns_text}\n</coder_context>"
            )

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
                    recent_files_hint = "Recently inspected files:\n" + "\n".join(
                        f"- {f}" for f in list(files)[:5]
                    )
        except Exception as _rf_exc:
            debug_log("memory_recent_files_error", error=str(_rf_exc))

        if _MEMORY_MODE == "full" and recent_files_hint:
            tag_block += f"\n\n<recent_files>\n{recent_files_hint}\n</recent_files>"

        # --- NEW: Structural Skeletons ---
        # If we have recent files, check if we have parsed API skeletons for them in Redis.
        skeletons_text = ""
        try:
            if _memory_store is not None and files:
                skeleton_parts = []
                for f in list(files)[:5]:  # cap at 5 recent files
                    file_hash = hashlib.sha256(f.encode("utf-8")).hexdigest()
                    skel = await _memory_store.get_file_skeleton(session_id, file_hash)
                    if skel:
                        skeleton_parts.append(f"--- {f} ---\n{skel}")

                if skeleton_parts:
                    skeletons_text = "API Skeletons of recent files:\n\n" + "\n\n".join(
                        skeleton_parts
                    )
        except Exception as _sk_exc:
            debug_log("memory_recent_skeletons_error", error=str(_sk_exc))

        if _MEMORY_MODE == "full" and skeletons_text:
            tag_block += f"\n\n<api_skeletons>\n{skeletons_text}\n</api_skeletons>"

        # Anti-loop instructions always accompany the memory block.
        if tag_block:
            instruction_lines = [
                "Instructions:",
                "- Exploration is allowed, but avoid rereading the same files or same sections without new purpose.",
                "- Prefer making progress over repeated exploration loops.",
            ]
            if _MEMORY_MODE == "full":
                instruction_lines.insert(
                    1,
                    "- Use <recent_files> as a soft reminder of files already explored.",
                )
            inject_instructions = "\n".join(instruction_lines) + "\n\n"
            tag_block = f"{tag_block}\n\n{inject_instructions}"
        if tag_block:
            if system_msgs:
                existing = system_msgs[0].get("content", "")
                system_msgs[0]["content"] = (
                    (
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
                    )
                    if existing
                    else (
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
                )
            else:
                system_msgs = [
                    {
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
                        ),
                    }
                ]

        # Trim history: keep only the last N non-system messages
        if len(non_system) > _MEMORY_MAX_INJECT_TURNS:
            trimmed = len(non_system) - _MEMORY_MAX_INJECT_TURNS
            non_system = non_system[-_MEMORY_MAX_INJECT_TURNS:]
            debug_log(
                "memory_inject_trimmed",
                dropped_turns=trimmed,
                kept_turns=len(non_system),
            )

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
        lines = raw_content.split("\n", 10)
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
            file_hash = hashlib.sha256(file_path.encode("utf-8")).hexdigest()
            await _memory_store.set_file_skeleton(session_id, file_hash, skel)
            debug_log(
                "skeleton_extracted_in_bg", file_path=file_path, skeleton_len=len(skel)
            )
    except Exception as e:
        debug_log("skeleton_extraction_failed", error=str(e))
