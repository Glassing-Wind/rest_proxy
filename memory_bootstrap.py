"""memory_bootstrap.py – Neo4j initialization wrapper for the memory layer.
(Formerly Postgres-specific, now redirects to graph_bootstrap.py).
"""

from __future__ import annotations

import os
import sys
from typing import Any
import graph_bootstrap

_ENABLE_PERSISTENCE = os.getenv("LM_PROXY_MEMORY_ENABLE_PERSISTENCE", "1").strip().lower() in {"1", "true", "yes", "on"}
_ENABLE_EMBEDDINGS = os.getenv("LM_PROXY_MEMORY_ENABLE_EMBEDDINGS", "0").strip().lower() in {"1", "true", "yes", "on"}
_ENABLE_DEBUG = os.getenv("LM_PROXY_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}

async def bootstrap_schema() -> bool:
    """
    Initialize the Neo4j GraphRAG database.
    Replaces the old Postgres schema creation.
    """
    if not _ENABLE_PERSISTENCE:
        return True
        
    try:
        await graph_bootstrap.init_graph_db()
        return True
    except Exception as exc:
        if _ENABLE_DEBUG:
            print(f"[lm-proxy:memory_bootstrap] bootstrap_failed error={exc}", file=sys.stderr)
        return False
