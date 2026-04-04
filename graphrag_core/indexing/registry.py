"""Indexing registry helpers (local + Neo4j)."""

from __future__ import annotations

import os
import time
from neo4j import unit_of_work

from graphrag_core.config import load_env
from graphrag_core.indexing import watcher as index_watcher

load_env()

_TX_TIMEOUT = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
_TX_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
_TX_METADATA_BASE = {"source": "lm_proxy", "tool": "indexing"}


async def record_indexed_project(
    project_path: str,
    project_id: str,
    file_count: int | None = None,
    source_url: str | None = None,
    source_type: str | None = None,
) -> None:
    abs_path = os.path.abspath(project_path)
    try:
        data = index_watcher.load_indexed_projects()
        data[project_id] = {
            "project_path": abs_path,
            "last_indexed": time.time(),
        }
        index_watcher.save_indexed_projects(data)
    except Exception:
        pass

    try:
        import graph_bootstrap

        if not graph_bootstrap._NEO4J_ENABLED:
            return
        driver = await graph_bootstrap.require_driver()
        now = time.time()
        effective_type = source_type or ("git" if source_url else "local")
        name = os.path.basename(abs_path)

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            tx_metadata = dict(_TX_METADATA_BASE)
            tx_metadata["op"] = "update_project_indexed"
            if _TX_OP_PREFIX:
                tx_metadata["op"] = f"{_TX_OP_PREFIX}.{tx_metadata['op']}"

            @unit_of_work(timeout=_TX_TIMEOUT, metadata=tx_metadata)
            async def _tx(tx):
                result = await tx.run(
                    """
                    MERGE (p:Project {id: $id})
                    SET p.project_path = $path,
                        p.name = $name,
                        p.last_indexed = $ts,
                        p.source_type = $source_type,
                        p.source_url = $source_url,
                        p.file_count = $file_count
                    """,
                    id=project_id,
                    path=abs_path,
                    name=name,
                    ts=now,
                    source_type=effective_type,
                    source_url=source_url,
                    file_count=file_count,
                )
                await result.consume()

            if hasattr(session, "execute_write"):
                await session.execute_write(_tx)
            else:
                await _tx(session)
    except Exception:
        return

