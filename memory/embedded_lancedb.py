"""Legacy standalone LanceDB feasibility adapter; not the run-published backend.

New owned publication uses memory.embedded_lance_runs.LanceRunStore.
This legacy probe adapter is not routed through memory/store or the indexing pipeline. This adapter does not
construct an ANN index or implement hybrid RRF; backend parity remains unproven.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import lancedb
    import pyarrow as pa
except ImportError:
    lancedb = None  # type: ignore
    pa = None  # type: ignore


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LANCEDB_DIR = str(REPO_ROOT / ".runtime" / "lancedb")
TABLE_NAME = "codebase_chunks"


class LanceVectorStore:
    """Embedded vector and document database backed by LanceDB and Apache Arrow."""

    def __init__(self, db_dir: str | None = None):
        if lancedb is None:
            raise RuntimeError(
                "LanceDB is not installed. Run 'pip install lancedb pyarrow' to enable embedded vector storage."
            )
        self.db_dir = db_dir or os.getenv("LM_PROXY_LANCEDB_PATH", DEFAULT_LANCEDB_DIR)
        Path(self.db_dir).mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(self.db_dir)
        self._table = self._init_table()

    def _init_table(self):
        """Open existing table or initialize with standard schema."""
        table_list = self._db.list_tables()
        if TABLE_NAME in table_list:
            return self._db.open_table(TABLE_NAME)
        
        # Define foundational Apache Arrow schema for codebase chunks
        dim = int(os.getenv("LM_PROXY_MEMORY_EMBEDDING_DIM", "768"))
        schema = pa.schema([
            ("id", pa.string()),
            ("project_id", pa.string()),
            ("file_path", pa.string()),
            ("chunk_index", pa.int32()),
            ("content", pa.string()),
            ("vector", pa.list_(pa.float32(), dim)),
            ("metadata", pa.string()),
        ])
        return self._db.create_table(TABLE_NAME, schema=schema)

    async def upsert_chunks(self, chunks: List[Dict[str, Any]]) -> int:
        """Add or overwrite chunks in a background thread."""
        return await asyncio.to_thread(self._sync_upsert_chunks, chunks)

    def _sync_upsert_chunks(self, chunks: List[Dict[str, Any]]) -> int:
        if not chunks:
            return 0
        formatted = []
        for c in chunks:
            meta = c.get("metadata", {})
            meta_str = meta if isinstance(meta, str) else json.dumps(meta)
            formatted.append({
                "id": str(c.get("id") or f"{c.get('project_id')}:{c.get('file_path')}:{c.get('chunk_index')}"),
                "project_id": str(c.get("project_id", "")),
                "file_path": str(c.get("file_path", "")),
                "chunk_index": int(c.get("chunk_index", 0)),
                "content": str(c.get("content", "")),
                "vector": list(c.get("vector") or c.get("embedding") or []),
                "metadata": meta_str,
            })

        # Merge insert by id
        try:
            self._table.merge_insert("id") \
                .when_matched_update_all() \
                .when_not_matched_insert_all() \
                .execute(formatted)
        except Exception:
            # Fallback to direct add if merge is unsupported
            self._table.add(formatted)

        try:
            self._table.create_fts_index("content", replace=True)
        except Exception:
            pass

        return len(formatted)

    async def search_vector(
        self,
        query_vector: List[float],
        project_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search similar code chunks using cosine vector distance."""
        return await asyncio.to_thread(self._sync_search_vector, query_vector, project_id, limit)

    def _sync_search_vector(
        self,
        query_vector: List[float],
        project_id: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        query = self._table.search(query_vector).metric("cosine").limit(limit)
        if project_id:
            query = query.where(f"project_id = '{project_id}'")
        results = query.to_list()
        for r in results:
            if isinstance(r.get("metadata"), str):
                try:
                    r["metadata"] = json.loads(r["metadata"])
                except Exception:
                    pass
        return results

    async def search_text(
        self,
        query_text: str,
        project_id: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search code chunks using keyword/FTS search."""
        return await asyncio.to_thread(self._sync_search_text, query_text, project_id, limit)

    def _sync_search_text(
        self,
        query_text: str,
        project_id: Optional[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        try:
            query = self._table.search(query_text).limit(limit)
            if project_id:
                query = query.where(f"project_id = '{project_id}'")
            results = query.to_list()
            if not results:
                # Fallback to substring query if FTS returns empty
                filter_clause = f"project_id = '{project_id}'" if project_id else None
                fallback_q = self._table.search()
                if filter_clause:
                    fallback_q = fallback_q.where(filter_clause)
                all_candidates = fallback_q.to_list()
                results = [c for c in all_candidates if query_text.lower() in c.get("content", "").lower()][:limit]
            for r in results:
                if isinstance(r.get("metadata"), str):
                    try:
                        r["metadata"] = json.loads(r["metadata"])
                    except Exception:
                        pass
            return results
        except Exception:
            return []

    async def get_chunks_for_file(self, project_id: str, file_path: str) -> List[Dict[str, Any]]:
        """Retrieve all stored chunks for a given file ordered by chunk_index."""
        return await asyncio.to_thread(self._sync_get_chunks_for_file, project_id, file_path)

    def _sync_get_chunks_for_file(self, project_id: str, file_path: str) -> List[Dict[str, Any]]:
        query = f"project_id = '{project_id}' AND file_path = '{file_path}'"
        results = self._table.search().where(query).to_list()
        results.sort(key=lambda x: x.get("chunk_index", 0))
        for r in results:
            if isinstance(r.get("metadata"), str):
                try:
                    r["metadata"] = json.loads(r["metadata"])
                except Exception:
                    pass
        return results

    async def count(self, project_id: Optional[str] = None) -> int:
        return await asyncio.to_thread(self._sync_count, project_id)

    def _sync_count(self, project_id: Optional[str]) -> int:
        if project_id:
            return len(self._table.search().where(f"project_id = '{project_id}'").to_list())
        return self._table.count_rows()

    async def delete_project(self, project_id: str) -> None:
        """Remove all chunks associated with a project."""
        await asyncio.to_thread(self._sync_delete_project, project_id)

    def _sync_delete_project(self, project_id: str) -> None:
        self._table.delete(f"project_id = '{project_id}'")


_embedded_vector_store: Optional[LanceVectorStore] = None


def get_embedded_vector_store(db_dir: str | None = None) -> LanceVectorStore:
    global _embedded_vector_store
    if _embedded_vector_store is None:
        _embedded_vector_store = LanceVectorStore(db_dir)
    return _embedded_vector_store
