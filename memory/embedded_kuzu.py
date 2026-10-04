"""Experimental Kuzu graph adapter, not a supported Neo4j replacement.

Existing query compatibility, rollback and concurrent-session parity are unproven.
Upstream Kuzu is archived; engine selection remains open. No latency claim is made.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import kuzu
except ImportError:
    kuzu = None  # type: ignore

from proxy.logging import debug_log

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KUZU_DB_PATH = str(REPO_ROOT / ".runtime" / "kuzu_graph.db")


class KuzuResult:
    """Wrapper around Kùzu QueryResult to match Neo4j's AsyncResult interface."""

    def __init__(self, data: List[Dict[str, Any]]):
        self._data = data

    async def data(self) -> List[Dict[str, Any]]:
        return self._data

    async def consume(self) -> None:
        pass


class KuzuSession:
    """Async session wrapper around a synchronous Kùzu connection."""

    def __init__(self, conn: "kuzu.Connection", driver: "KuzuGraphDriver"):
        self._conn = conn
        self._driver = driver

    async def __aenter__(self) -> "KuzuSession":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        return False

    async def run(self, cypher: str, **params: Any) -> KuzuResult:
        """Execute a Cypher query in a background thread and return KuzuResult."""
        return await asyncio.to_thread(self._sync_run, cypher, params)

    def _sync_run(self, cypher: str, params: Dict[str, Any]) -> KuzuResult:
        # Strip Neo4j-specific SHOW commands or pragmas if present
        clean_cypher = cypher.strip()
        if clean_cypher.upper().startswith("SHOW "):
            return KuzuResult([])

        # Filter None values or empty parameters
        kuzu_params = {k: v for k, v in params.items() if v is not None}
        try:
            if kuzu_params:
                res = self._conn.execute(clean_cypher, parameters=kuzu_params)
            else:
                res = self._conn.execute(clean_cypher)

            if not res.get_column_names():
                return KuzuResult([])

            # Convert Apache Arrow table to Python list of dicts
            arrow_table = res.get_as_arrow()
            raw_rows = arrow_table.to_pylist()

            # Normalize column keys (e.g., 'fn.name' -> 'name' or keep alias)
            normalized_rows: List[Dict[str, Any]] = []
            for row in raw_rows:
                norm = {}
                for k, v in row.items():
                    # If column is qualified like 's.name', also expose 'name'
                    norm[k] = v
                    if "." in k:
                        short_k = k.split(".", 1)[1]
                        if short_k not in norm:
                            norm[short_k] = v
                normalized_rows.append(norm)

            return KuzuResult(normalized_rows)
        except Exception as exc:
            debug_log("kuzu_query_error", cypher=clean_cypher[:200], error=str(exc))
            raise

    async def execute_read(self, tx_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await tx_fn(self, *args, **kwargs)

    async def execute_write(self, tx_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await tx_fn(self, *args, **kwargs)


class KuzuGraphDriver:
    """Embedded property graph driver mimicking Neo4j's AsyncGraphDatabase Driver."""

    def __init__(self, db_path: str | None = None):
        if kuzu is None:
            raise RuntimeError(
                "Kùzu is not installed. Run 'pip install kuzu' to enable embedded graph storage."
            )
        self.db_path = db_path or os.getenv("LM_PROXY_KUZU_PATH", DEFAULT_KUZU_DB_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Initialize Kùzu database and connection
        self._db = kuzu.Database(self.db_path)
        self._conn = kuzu.Connection(self._db)
        self._init_schema()

    def _init_schema(self) -> None:
        """Create foundational node and relationship tables for code graphs."""
        node_tables = [
            "CREATE NODE TABLE IF NOT EXISTS File(id STRING, path STRING, project_id STRING, PRIMARY KEY (id))",
            "CREATE NODE TABLE IF NOT EXISTS Function(id STRING, name STRING, signature STRING, start_line INT64, end_line INT64, qualified_name STRING, project_id STRING, PRIMARY KEY (id))",
            "CREATE NODE TABLE IF NOT EXISTS Class(id STRING, name STRING, start_line INT64, end_line INT64, qualified_name STRING, project_id STRING, PRIMARY KEY (id))",
            "CREATE NODE TABLE IF NOT EXISTS Struct(id STRING, name STRING, start_line INT64, end_line INT64, qualified_name STRING, project_id STRING, PRIMARY KEY (id))",
            "CREATE NODE TABLE IF NOT EXISTS Trait(id STRING, name STRING, start_line INT64, end_line INT64, project_id STRING, PRIMARY KEY (id))",
            "CREATE NODE TABLE IF NOT EXISTS Module(id STRING, name STRING, project_id STRING, PRIMARY KEY (id))",
            "CREATE NODE TABLE IF NOT EXISTS Project(id STRING, name STRING, root_path STRING, PRIMARY KEY (id))",
            "CREATE NODE TABLE IF NOT EXISTS Session(id STRING, created_at INT64, PRIMARY KEY (id))",
            "CREATE NODE TABLE IF NOT EXISTS MemoryTurn(id STRING, content STRING, role STRING, PRIMARY KEY (id))",
        ]
        for stmt in node_tables:
            try:
                self._conn.execute(stmt)
            except Exception as exc:
                debug_log("kuzu_schema_init_warn", stmt=stmt[:50], error=str(exc))

        rel_tables = [
            "CREATE REL TABLE IF NOT EXISTS CONTAINS(FROM File TO Function, FROM File TO Class, FROM File TO Struct, FROM File TO Trait)",
            "CREATE REL TABLE IF NOT EXISTS CALLS(FROM Function TO Function, FROM Class TO Function)",
            "CREATE REL TABLE IF NOT EXISTS IMPORTS(FROM File TO Module, FROM File TO File)",
            "CREATE REL TABLE IF NOT EXISTS EXTENDS(FROM Class TO Class, FROM Struct TO Trait)",
            "CREATE REL TABLE IF NOT EXISTS IMPLEMENTS(FROM Class TO Trait, FROM Struct TO Trait)",
            "CREATE REL TABLE IF NOT EXISTS DEFINED_IN_FILE(FROM Function TO File, FROM Class TO File, FROM Struct TO File)",
            "CREATE REL TABLE IF NOT EXISTS REFERENCES_PROJECT(FROM Project TO Project)",
        ]
        for stmt in rel_tables:
            try:
                self._conn.execute(stmt)
            except Exception as exc:
                debug_log("kuzu_schema_init_warn", stmt=stmt[:50], error=str(exc))

    def session(self, database: str | None = None) -> KuzuSession:
        """Return a session for executing queries."""
        return KuzuSession(self._conn, self)

    async def verify_connectivity(self) -> None:
        """Check embedded database connectivity (always ready)."""
        pass

    async def close(self) -> None:
        """Close driver connections."""
        pass


_embedded_driver: Optional[KuzuGraphDriver] = None


def get_embedded_kuzu_driver(db_path: str | None = None) -> KuzuGraphDriver:
    global _embedded_driver
    if _embedded_driver is None:
        _embedded_driver = KuzuGraphDriver(db_path)
    return _embedded_driver
