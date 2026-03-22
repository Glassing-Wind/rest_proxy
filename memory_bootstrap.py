"""memory_bootstrap.py – Idempotent Postgres schema creation for the memory layer.

Run this once at proxy startup when ENABLE_PERSISTENCE is set.
All CREATE TABLE / CREATE INDEX statements use IF NOT EXISTS so repeated
calls are safe.

Requires:
  - Postgres with the pgvector extension installed.
  - psycopg v3 (psycopg[binary] >= 3.1)
"""

from __future__ import annotations

import os
from typing import Any

_ENABLE_PERSISTENCE = os.getenv("LM_PROXY_MEMORY_ENABLE_PERSISTENCE", "1").strip().lower() in {"1", "true", "yes", "on"}
_ENABLE_EMBEDDINGS = os.getenv("LM_PROXY_MEMORY_ENABLE_EMBEDDINGS", "0").strip().lower() in {"1", "true", "yes", "on"}
_PG_DSN = os.getenv("LM_PROXY_PG_DSN", "")
_EMBEDDING_DIM = int(os.getenv("LM_PROXY_MEMORY_EMBEDDING_DIM", "768"))
_ENABLE_DEBUG = os.getenv("LM_PROXY_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}


def _debug(message: str, **fields: Any) -> None:
    if not _ENABLE_DEBUG:
        return
    import json
    payload = {"message": message}
    payload.update(fields)
    try:
        print(
            f"[lm-proxy:memory_bootstrap] {json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}",
            flush=True,
        )
    except Exception:
        print(f"[lm-proxy:memory_bootstrap] {message} {fields}", flush=True)


# DDL executed in order. Each statement is safe to run multiple times.
_SCHEMA_DDL = [
    # --- Core tables ---
    """
    CREATE TABLE IF NOT EXISTS conversation_turns (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id      TEXT NOT NULL,
        turn_index      INTEGER NOT NULL,
        role            TEXT NOT NULL,           -- user | assistant | tool
        content         TEXT NOT NULL,           -- full text (may be large)
        compact_content TEXT NOT NULL DEFAULT '', -- compact version for retrieval
        model           TEXT,
        tool_name       TEXT,
        tool_call_id    TEXT,
        metadata        JSONB NOT NULL DEFAULT '{}',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_turns_session_id
        ON conversation_turns (session_id, turn_index)
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_summaries (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id      TEXT NOT NULL,
        summary_text    TEXT NOT NULL,
        summary_type    TEXT NOT NULL DEFAULT 'rolling',  -- rolling | checkpoint
        metadata        JSONB NOT NULL DEFAULT '{}',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_summaries_session_id
        ON memory_summaries (session_id, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_outputs (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id      TEXT NOT NULL,
        tool_name       TEXT NOT NULL,
        tool_call_id    TEXT NOT NULL,
        raw_output      TEXT NOT NULL,
        compact_output  TEXT NOT NULL DEFAULT '',
        metadata        JSONB NOT NULL DEFAULT '{}',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_tool_outputs_session_id
        ON tool_outputs (session_id, created_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_checkpoints (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id      TEXT NOT NULL,
        working_memory  JSONB NOT NULL DEFAULT '{}',
        rolling_summary TEXT NOT NULL DEFAULT '',
        metadata        JSONB NOT NULL DEFAULT '{}',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_checkpoints_session_id
        ON memory_checkpoints (session_id, created_at DESC)
    """,
]

# Embedding table DDL (only if pgvector is available and embeddings enabled)
# search_vec is a generated column so inserts never need to supply it explicitly.
_EMBEDDINGS_DDL_TEMPLATE = """
    CREATE TABLE IF NOT EXISTS memory_embeddings (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id      TEXT NOT NULL,
        ref_id          UUID NOT NULL,           -- FK to turn/summary (not enforced for simplicity)
        ref_type        TEXT NOT NULL,           -- 'turn' | 'summary'
        compact_text    TEXT NOT NULL,
        embedding       vector({dim}) NOT NULL,
        search_vec      tsvector GENERATED ALWAYS AS
                            (to_tsvector('english', compact_text)) STORED,
        metadata        JSONB NOT NULL DEFAULT '{{}}',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
"""

_CODEBASE_EMBEDDINGS_DDL_TEMPLATE = """
    CREATE TABLE IF NOT EXISTS codebase_embeddings (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id      TEXT NOT NULL,
        file_path       TEXT NOT NULL,
        chunk_index     INTEGER NOT NULL,
        content         TEXT NOT NULL,
        embedding       vector({dim}) NOT NULL,
        search_vec      tsvector GENERATED ALWAYS AS
                            (to_tsvector('english', content)) STORED,
        metadata        JSONB NOT NULL DEFAULT '{{}}',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
"""

_EMBEDDINGS_INDEX_DDL = """
    CREATE INDEX IF NOT EXISTS idx_embeddings_session_hnsw
        ON memory_embeddings USING hnsw (embedding vector_cosine_ops)
"""

_CODEBASE_EMBEDDINGS_INDEX_DDL = """
    CREATE INDEX IF NOT EXISTS idx_codebase_embeddings_project_hnsw
        ON codebase_embeddings USING hnsw (embedding vector_cosine_ops)
"""

# GIN index for full-text keyword search
_EMBEDDINGS_FTS_INDEX_DDL = """
    CREATE INDEX IF NOT EXISTS idx_embeddings_search_vec
        ON memory_embeddings USING gin (search_vec)
"""

_CODEBASE_EMBEDDINGS_FTS_INDEX_DDL = """
    CREATE INDEX IF NOT EXISTS idx_codebase_embeddings_search_vec
        ON codebase_embeddings USING gin (search_vec)
"""

async def bootstrap_schema() -> bool:
    """
    Create all required tables and indexes if they don't exist.

    Returns True on success, False on any failure (non-fatal – proxy still works).
    """
    if not _ENABLE_PERSISTENCE:
        _debug("bootstrap_skipped_persistence_disabled")
        return False
    if not _PG_DSN:
        _debug("bootstrap_skipped_no_dsn")
        return False

    try:
        import psycopg  # psycopg v3
        async with await psycopg.AsyncConnection.connect(_PG_DSN, autocommit=True) as conn:

            # Enable pgvector if we need embeddings
            if _ENABLE_EMBEDDINGS:
                try:
                    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    _debug("pgvector_extension_enabled")
                except Exception as exc:
                    _debug("pgvector_extension_failed", error=str(exc))
                    # embeddings won't work but the rest will

            # Core schema
            for ddl in _SCHEMA_DDL:
                await conn.execute(ddl)

            # Embeddings table (only if extension available + enabled)
            if _ENABLE_EMBEDDINGS:
                try:
                    dim = _EMBEDDING_DIM
                    embed_ddl = _EMBEDDINGS_DDL_TEMPLATE.format(dim=dim)
                    codebase_embed_ddl = _CODEBASE_EMBEDDINGS_DDL_TEMPLATE.format(dim=dim)
                    await conn.execute(embed_ddl)
                    await conn.execute(codebase_embed_ddl)

                    # Migration: add search_vec to existing tables created without it.
                    # ADD COLUMN IF NOT EXISTS + generated expression is Postgres 12+ syntax.
                    try:
                        await conn.execute("""
                            ALTER TABLE memory_embeddings
                            ADD COLUMN IF NOT EXISTS search_vec tsvector
                            GENERATED ALWAYS AS (to_tsvector('english', compact_text)) STORED
                        """)
                    except Exception as mig_exc:
                        _debug("search_vec_migration_failed", error=str(mig_exc))

                    # Vector index (HNSW preferred, ivfflat fallback)
                    try:
                        await conn.execute(_EMBEDDINGS_INDEX_DDL)
                        await conn.execute(_CODEBASE_EMBEDDINGS_INDEX_DDL)
                    except Exception as idx_exc:
                        _debug("hnsw_index_failed", error=str(idx_exc))
                        try:
                            await conn.execute(
                                "CREATE INDEX IF NOT EXISTS idx_embeddings_session_ivf "
                                "ON memory_embeddings USING ivfflat (embedding vector_cosine_ops) "
                                "WITH (lists = 50)"
                            )
                            await conn.execute(
                                "CREATE INDEX IF NOT EXISTS idx_codebase_embeddings_project_ivf "
                                "ON codebase_embeddings USING ivfflat (embedding vector_cosine_ops) "
                                "WITH (lists = 50)"
                            )
                        except Exception:
                            pass

                    # GIN index for full-text keyword search arm
                    try:
                        await conn.execute(_EMBEDDINGS_FTS_INDEX_DDL)
                        await conn.execute(_CODEBASE_EMBEDDINGS_FTS_INDEX_DDL)
                    except Exception as gin_exc:
                        _debug("gin_index_failed", error=str(gin_exc))

                except Exception as emb_exc:
                    _debug("embeddings_table_failed", error=str(emb_exc))

        _debug("bootstrap_complete", persistence=True, embeddings=_ENABLE_EMBEDDINGS)
        return True

    except Exception as exc:
        _debug("bootstrap_failed", error=str(exc))
        return False
