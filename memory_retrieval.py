"""memory_retrieval.py – Embedding provider interface and memory assembly for the proxy.

Responsibilities:
  1. Embedding provider abstraction (LM Studio/OpenAI endpoint or no-op stub).
  2. Assemble compact memory augmentation (rolling summary + structured state + recent turns
     + optional pgvector retrieved snippets) for future prompt injection.

All operations are best-effort and degrade gracefully when services are unavailable.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
from typing import Any, Dict, List, Optional

import httpx

from memory_types import AssembledMemory, _empty_working_memory

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_ENABLE_EMBEDDINGS = os.getenv("LM_PROXY_MEMORY_ENABLE_EMBEDDINGS", "0").strip().lower() in {"1", "true", "yes", "on"}
_ENABLE_RETRIEVAL = os.getenv("LM_PROXY_MEMORY_ENABLE_RETRIEVAL", "1").strip().lower() in {"1", "true", "yes", "on"}
_EMBEDDING_MODEL = os.getenv("LM_PROXY_MEMORY_EMBEDDING_MODEL", "").strip()
_EMBEDDING_BASE_URL = os.getenv(
    "LM_PROXY_MEMORY_EMBEDDING_BASE_URL",
    os.getenv("LM_BASE", "http://127.0.0.1:1234"),
).rstrip("/")
_RETRIEVAL_K = int(os.getenv("LM_PROXY_MEMORY_RETRIEVAL_K", "4"))
_EMBED_CACHE_MAX = int(os.getenv("LM_PROXY_MEMORY_EMBED_CACHE_SIZE", "256"))
# Recency decay: half-life in seconds for reranking (default 1 hour).
# Set to 0 to disable recency weighting.
_RECENCY_HALF_LIFE = float(os.getenv("LM_PROXY_MEMORY_RECENCY_HALF_LIFE", "3600"))
_ENABLE_DEBUG = os.getenv("LM_PROXY_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}

# In-process embedding cache: sha256(text) -> vector.
# FIFO eviction (dict insertion order). Max ~750 KB at 256 entries / 768-dim.
_embed_cache: Dict[str, List[float]] = {}


def _rerank_by_recency(hits: List[Dict[str, Any]]) -> List[str]:
    """
    Re-order retrieval hits by blending RRF score with a recency decay factor.

    Score = rrf_score * exp(-age_seconds / half_life_seconds)

    If a hit has no created_at or recency is disabled (_RECENCY_HALF_LIFE == 0),
    the rrf_score is used as-is (no decay applied).

    Returns compact_text strings ordered best-first.
    """
    if not hits:
        return []
    if _RECENCY_HALF_LIFE <= 0:
        return [h["compact_text"] for h in hits]

    now = datetime.datetime.now(tz=datetime.timezone.utc)
    # ln(2) / half_life gives the decay constant for exp decay
    decay_k = math.log(2) / _RECENCY_HALF_LIFE

    scored: List[tuple] = []
    for h in hits:
        rrf = h.get("rrf_score", 1.0)
        created_at = h.get("created_at")
        if created_at is not None:
            try:
                # psycopg returns timezone-aware datetimes; handle naive too
                if isinstance(created_at, datetime.datetime):
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=datetime.timezone.utc)
                    age_secs = max(0.0, (now - created_at).total_seconds())
                else:
                    age_secs = 0.0
                recency_weight = math.exp(-decay_k * age_secs)
            except Exception:
                recency_weight = 1.0
        else:
            recency_weight = 1.0
        scored.append((rrf * recency_weight, h["compact_text"]))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in scored]


def _debug(message: str, **fields: Any) -> None:
    if not _ENABLE_DEBUG:
        return
    payload = {"message": message}
    payload.update(fields)
    try:
        print(
            f"[lm-proxy:memory_retrieval] {json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}",
            flush=True,
        )
    except Exception:
        print(f"[lm-proxy:memory_retrieval] {message} {fields}", flush=True)


# ---------------------------------------------------------------------------
# Embedding provider
# ---------------------------------------------------------------------------

async def get_embedding(text: str) -> Optional[List[float]]:
    """
    Obtain an embedding vector for *text* using the configured provider.

    Results are cached in-process (FIFO, max _EMBED_CACHE_MAX entries) to avoid
    redundant HTTP calls for repeated text. Cache is keyed by sha256 of the input.

    Returns None if embeddings are disabled or the provider is unavailable.
    The caller must handle None gracefully.
    """
    if not _ENABLE_EMBEDDINGS or not _EMBEDDING_MODEL:
        return None
    if not text or not text.strip():
        return None

    # Cache lookup
    cache_key = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    if cache_key in _embed_cache:
        _debug("embedding_cache_hit", key=cache_key[:8])
        return _embed_cache[cache_key]

    try:
        payload = {"model": _EMBEDDING_MODEL, "input": text[:4000]}
        timeout = httpx.Timeout(20.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{_EMBEDDING_BASE_URL}/v1/embeddings", json=payload)
            if r.status_code >= 400:
                _debug("embedding_request_error", status=r.status_code, body=r.text[:200])
                return None
            data = r.json()
            embedding_data = data.get("data", [])
            if embedding_data and isinstance(embedding_data[0], dict):
                vec = embedding_data[0].get("embedding")
                if isinstance(vec, list) and vec:
                    # Populate cache; evict oldest entry if full (FIFO)
                    if len(_embed_cache) >= _EMBED_CACHE_MAX:
                        _embed_cache.pop(next(iter(_embed_cache)))
                    _embed_cache[cache_key] = vec
                    _debug("embedding_obtained", model=_EMBEDDING_MODEL, dim=len(vec), cache_size=len(_embed_cache))
                    return vec
    except Exception as exc:
        _debug("embedding_exception", error=str(exc))
    return None


# ---------------------------------------------------------------------------
# Memory assembly
# ---------------------------------------------------------------------------

def _format_working_memory(wm: Dict[str, Any]) -> str:
    """Render working memory dict as a compact structured text block."""
    if not wm:
        return ""
    lines: List[str] = ["## Working Memory"]
    for key in ("goal", "current_focus", "files_touched", "recent_errors",
                "decisions", "open_issues", "next_actions"):
        val = wm.get(key)
        if not val:
            continue
        if isinstance(val, list):
            if val:
                lines.append(f"**{key}**:")
                for item in val[:8]:  # cap each list
                    lines.append(f"  - {str(item)[:200]}")
        elif isinstance(val, str) and val.strip():
            lines.append(f"**{key}**: {val[:300]}")
    return "\n".join(lines)


def _format_recent_turns(turns: List[Dict[str, Any]], max_turns: int = 6) -> str:
    """Render recent turns as compact text."""
    if not turns:
        return ""
    lines = ["## Recent Conversation"]
    for t in turns[-max_turns:]:
        role = t.get("role", "?")
        content = str(t.get("content", ""))[:400]
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)


def _format_retrieved_snippets(snippets: List[str]) -> str:
    """Render retrieved memory snippets block."""
    if not snippets:
        return ""
    lines = ["## Relevant Memory"]
    for s in snippets[:3]:
        lines.append(f"- {s[:200]}")
    return "\n".join(lines)


async def assemble_memory(
    session_id: str,
    query_text: Optional[str] = None,
) -> AssembledMemory:
    """
    Assemble compact memory augmentation for *session_id*.

    - Pulls rolling summary and working memory from Redis (via memory_store).
    - Pulls recent turns from Redis.
    - Optionally retrieves similar snippets via pgvector.
    - Returns an AssembledMemory with an assembled_text field ready for prompt injection.

    This function swallows all errors to stay non-blocking.
    """
    from memory_store import (
        get_rolling_summary,
        get_recent_turns,
        get_session_state,
        search_similar_memory,
    )

    rolling_summary = ""
    working_memory: Dict[str, Any] = _empty_working_memory()
    recent_turns: List[Dict[str, Any]] = []
    retrieved_snippets: List[str] = []

    try:
        rolling_summary = await get_rolling_summary(session_id)
    except Exception as exc:
        _debug("assemble_rolling_summary_error", error=str(exc))

    try:
        state = await get_session_state(session_id)
        if state:
            working_memory = state
    except Exception as exc:
        _debug("assemble_session_state_error", error=str(exc))

    try:
        recent_turns = await get_recent_turns(session_id)
    except Exception as exc:
        _debug("assemble_recent_turns_error", error=str(exc))

    # Vector retrieval (optional) – hybrid semantic + keyword, then recency-reranked
    if _ENABLE_RETRIEVAL and _ENABLE_EMBEDDINGS and query_text:
        try:
            query_vec = await get_embedding(query_text)
            if query_vec:
                hits = await search_similar_memory(
                    session_id, query_vec, k=_RETRIEVAL_K, query_text=query_text
                )
                # Recency-weighted rerank; returns compact_text strings
                retrieved_snippets = _rerank_by_recency(hits)
                # --- NEW: deduplicate + cap retrieved snippets ---
                seen = set()
                deduped = []
                for s in retrieved_snippets:
                    key = s[:120]
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(s)

                # cap to top 3 to prevent prompt bloat
                retrieved_snippets = deduped[:3]
        except Exception as exc:
            _debug("assemble_retrieval_error", error=str(exc))

    # Build the assembled text block
    sections: List[str] = []
    if rolling_summary:
        sections.append(f"## Session Summary\n{rolling_summary[:600]}")
    wm_text = _format_working_memory(working_memory)
    if wm_text:
        sections.append(wm_text)
    turns_text = _format_recent_turns(recent_turns)
    if turns_text:
        sections.append(turns_text)
    snippets_text = _format_retrieved_snippets(retrieved_snippets)
    if snippets_text:
        sections.append(snippets_text)

    assembled_text = "\n\n".join(sections)

    _debug(
        "memory_assembled",
        session_id=session_id,
        has_summary=bool(rolling_summary),
        recent_turns=len(recent_turns),
        snippets=len(retrieved_snippets),
        assembled_chars=len(assembled_text),
    )

    return AssembledMemory(
        session_id=session_id,
        rolling_summary=rolling_summary,
        working_memory=working_memory,
        recent_turns=recent_turns,
        retrieved_snippets=retrieved_snippets,
        assembled_text=assembled_text,
    )
