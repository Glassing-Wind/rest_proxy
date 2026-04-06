"""memory_bootstrap.py – Initialize Postgres (pgvector) and Neo4j for the memory layer.

Schema:
  Postgres: conversation_turns, memory_embeddings, codebase_embeddings, tool_outputs, etc.
  Neo4j:    structural graph (files, symbols, relationships)
"""

from __future__ import annotations

import os
import sys
from typing import Any

import graph_bootstrap

_ENABLE_PERSISTENCE = os.getenv(
    "LM_PROXY_MEMORY_ENABLE_PERSISTENCE", "1"
).strip().lower() in {"1", "true", "yes", "on"}
_ENABLE_EMBEDDINGS = os.getenv(
    "LM_PROXY_MEMORY_ENABLE_EMBEDDINGS", "0"
).strip().lower() in {"1", "true", "yes", "on"}
_ENABLE_DEBUG = os.getenv("LM_PROXY_DEBUG", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_PG_DSN = os.getenv("LM_PROXY_PG_DSN", "")
_DIM = int(os.getenv("LM_PROXY_MEMORY_EMBEDDING_DIM", "768"))


def _debug(msg: str, **kw: Any) -> None:
    if _ENABLE_DEBUG:
        import json

        print(
            f"[lm-proxy:memory_bootstrap] {json.dumps({'message': msg, **kw})}",
            file=sys.stderr,
            flush=True,
        )


# ── Postgres schema DDL ───────────────────────────────────────────────────────

_CODEBASE_EMBEDDINGS_DDL = f"""
CREATE TABLE IF NOT EXISTS codebase_embeddings (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id    TEXT        UNIQUE,           -- stable ID: "project_id:version:rel_path:hash"
    project_id  TEXT        NOT NULL,
    file_path   TEXT        NOT NULL,
    ref_type    TEXT        NOT NULL DEFAULT 'code_chunk',
    chunk_index INTEGER     NOT NULL,
    content     TEXT        NOT NULL,
    embedding   vector({_DIM}) NOT NULL,
    search_vec  tsvector    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    metadata    JSONB       NOT NULL DEFAULT '{{}}',
    created_at  DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
)
"""

_CODEBASE_DDL_STEPS = [
    _CODEBASE_EMBEDDINGS_DDL,
    # HNSW index for ANN vector search
    "CREATE INDEX IF NOT EXISTS idx_ce_hnsw       ON codebase_embeddings USING hnsw (embedding vector_cosine_ops)",
    # GIN index for BM25 full-text search
    "CREATE INDEX IF NOT EXISTS idx_ce_fts        ON codebase_embeddings USING gin  (search_vec)",
    # Btree for project-scoped deletes / incremental re-index
    "CREATE INDEX IF NOT EXISTS idx_ce_project    ON codebase_embeddings (project_id, file_path)",
    # Unique index on chunk_id — required for ON CONFLICT (chunk_id) DO NOTHING
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ce_chunk_id ON codebase_embeddings (chunk_id) WHERE chunk_id IS NOT NULL",
]

# doc_embeddings — identical schema for external documentation (future)
_DOC_EMBEDDINGS_DDL = f"""
CREATE TABLE IF NOT EXISTS doc_embeddings (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    chunk_id    TEXT        UNIQUE,           -- stable ID: "source:url::idx"
    source      TEXT        NOT NULL,         -- library name / url root
    url         TEXT        NOT NULL,
    ref_type    TEXT        NOT NULL DEFAULT 'doc_chunk',
    chunk_index INTEGER     NOT NULL,
    content     TEXT        NOT NULL,
    embedding   vector({_DIM}) NOT NULL,
    search_vec  tsvector    GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    metadata    JSONB       NOT NULL DEFAULT '{{}}',
    created_at  DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
)
"""

_DOC_DDL_STEPS = [
    _DOC_EMBEDDINGS_DDL,
    "CREATE INDEX IF NOT EXISTS idx_de_hnsw    ON doc_embeddings USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX IF NOT EXISTS idx_de_fts     ON doc_embeddings USING gin  (search_vec)",
    "CREATE INDEX IF NOT EXISTS idx_de_source  ON doc_embeddings (source, url)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_de_chunk_id ON doc_embeddings (chunk_id) WHERE chunk_id IS NOT NULL",
]

# INTERLINK (Multi-Agent Messaging and Discovery)
_INTERLINK_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS agent_registry (
    agent_id          TEXT        PRIMARY KEY,
    workspace_id      TEXT        NOT NULL,
    workspace_path    TEXT        NOT NULL,
    last_seen         DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
    capabilities      TEXT[]      NOT NULL DEFAULT '{}',
    current_goal      TEXT,
    metadata          JSONB       NOT NULL DEFAULT '{}'
)
"""

_INTERLINK_MESSAGES_DDL = """
CREATE TABLE IF NOT EXISTS agent_messages (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    sender_id         TEXT        NOT NULL,
    receiver_id       TEXT,                   -- NULL means "broadcast" or "all"
    source_workspace  TEXT        NOT NULL,
    target_workspace  TEXT,                   -- optional filter
    interaction_type  TEXT        NOT NULL DEFAULT 'query', -- query, review, ping, sync
    session_id        TEXT,                   -- group related messages
    subject           TEXT,
    content           TEXT        NOT NULL,
    status            TEXT        NOT NULL DEFAULT 'unread', -- unread, read, archived
    metadata          JSONB       NOT NULL DEFAULT '{}',
    created_at        DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
)
"""

_INTERLINK_DDL_STEPS = [
    _INTERLINK_REGISTRY_DDL,
    _INTERLINK_MESSAGES_DDL,
    "CREATE INDEX IF NOT EXISTS idx_ar_last_seen ON agent_registry (last_seen)",
    "CREATE INDEX IF NOT EXISTS idx_am_receiver  ON agent_messages (receiver_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_am_created   ON agent_messages (created_at)",
]


async def _pg_bootstrap() -> bool:
    """Create pgvector extension and all tables/indexes."""
    if not _PG_DSN:
        _debug("pg_dsn_missing")
        return False
    try:
        from psycopg_pool import AsyncConnectionPool  # type: ignore

        pool = AsyncConnectionPool(_PG_DSN, min_size=1, max_size=2, open=False)
        await pool.open(wait=True, timeout=15)

        async with pool.connection() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            all_ddl = _CODEBASE_DDL_STEPS + _DOC_DDL_STEPS + _INTERLINK_DDL_STEPS
            for i, ddl in enumerate(all_ddl):
                try:
                    await conn.execute(f"SAVEPOINT sp_{i}")
                    await conn.execute(ddl)
                    await conn.execute(f"RELEASE SAVEPOINT sp_{i}")
                except Exception as e:
                    await conn.execute(f"ROLLBACK TO SAVEPOINT sp_{i}")
                    _debug("pg_ddl_warning", error=str(e)[:120])

        await pool.close()
        _debug("pg_bootstrap_ok")
        return True
    except Exception as exc:
        _debug("pg_bootstrap_failed", error=str(exc))
        return False


async def bootstrap_schema() -> bool:
    """Initialize Postgres schema + Neo4j GraphRAG database."""
    ok = True

    # 1. Postgres
    if _ENABLE_PERSISTENCE and _PG_DSN:
        pg_ok = await _pg_bootstrap()
        ok = ok and pg_ok

    # 2. Neo4j
    if _ENABLE_PERSISTENCE:
        try:
            await graph_bootstrap.init_graph_db()
        except Exception as exc:
            _debug("neo4j_bootstrap_failed", error=str(exc))
            ok = False

    return ok
