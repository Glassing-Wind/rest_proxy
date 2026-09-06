"""memory/store_search.py — vector search helpers."""

from typing import Any, Dict, List

import graph_bootstrap
from memory import store_core


def _is_missing_vector_index_error(exc: Exception) -> bool:
    text = str(exc)
    return (
        "No such vector schema index" in text
        or "There is no such vector schema index" in text
    )


async def search_similar_memory(
    session_id: str,
    query_vector: List[float],
    k: int = 5,
    same_session_only: bool = True,
    query_text: str = "",
    global_search: bool = False,
) -> List[Dict[str, Any]]:
    """
    Hybrid retrieval: Neo4j vector search on MemoryEmbedding nodes.
    Supports global_search across all sessions/projects.
    """
    if (
        not store_core._ENABLE_EMBEDDINGS
        or not store_core._ENABLE_RETRIEVAL
        or not store_core._pool_available()
    ):
        return []

    if not isinstance(query_vector, list) or not query_vector:
        store_core._debug("graph_search_invalid_query_vector", session_id=session_id)
        return []

    try:
        # Note: We use the node index 'memory_embeddings_vector' created earlier
        cypher = """
        CALL db.index.vector.queryNodes('memory_embeddings_vector', $k, $query_vector)
        YIELD node, score
        WHERE ($global_search OR ($same_session OR node.session_id = $sid))
        RETURN node.ref_id as ref_id,
               node.ref_type as ref_type,
               node.text as compact_text,
               node.created_at as created_at,
               score as rrf_score
        ORDER BY score DESC
        LIMIT $k
        """
        driver = graph_bootstrap.get_driver()
        if not driver:
            return []

        results: List[Dict[str, Any]] = []
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            records = await store_core._neo4j_read(
                session,
                cypher,
                "search_similar_memory",
                query_vector=query_vector,
                k=k,
                global_search=global_search,
                same_session=not same_session_only,
                sid=session_id,
            )
            results = [dict(record) for record in records]

        store_core._debug(
            "graph_search_memory",
            session_id=session_id,
            k=k,
            found=len(results),
        )
        return results

    except Exception as exc:
        if _is_missing_vector_index_error(exc):
            store_core._debug(
                "graph_search_similar_skipped",
                session_id=session_id,
                reason="memory_embeddings_vector_missing",
            )
            return []
        store_core._debug(
            "graph_search_similar_error", session_id=session_id, error=str(exc)
        )
    return []


async def insert_codebase_embedding_graph(
    project_id: str,
    file_path: str,
    chunk_index: int,
    content: str,
    vector: List[float],
) -> bool:
    """Insert a codebase chunk into Neo4j with its vector embedding."""
    try:
        driver = graph_bootstrap.get_driver()
        if not driver:
            # Try to init if not already (safeguard)
            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()
            if not driver:
                return False

        # Generate a stable ID for the chunk
        chunk_id = f"{project_id}:{file_path}:{chunk_index}"
        # File ID: project_id:file:path (matches structural graph convention)
        file_id = f"{project_id}:file:{file_path}"

        cypher = """
        MERGE (chk:Chunk {id: $chk_id})
        SET chk.project_id = $pid,
            chk.file_path = $path,
            chk.chunk_index = $idx,
            chk.text = $text,
            chk.embedding = $vec
        WITH chk
        MATCH (f:File {id: $fid})
        MERGE (f)-[:HAS_CHUNK]->(chk)
        """

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await store_core._neo4j_write(
                session,
                cypher,
                "insert_codebase_embedding_graph",
                chk_id=chunk_id,
                pid=project_id,
                path=file_path,
                idx=chunk_index,
                text=content,
                vec=vector,
                fid=file_id,
            )
        store_core._debug(
            "neo4j_insert_chunk",
            project_id=project_id,
            file_path=file_path,
            chunk_index=chunk_index,
        )
        return True
    except Exception as exc:
        store_core._debug(
            "neo4j_insert_chunk_error",
            project_id=project_id,
            file_path=file_path,
            error=str(exc),
        )
        return False


async def search_codebase(
    project_id: str,
    query_vector: List[float],
    query_text: str = "",
    k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Hybrid retrieval: Postgres pgvector cosine ANN + BM25 tsvector, fused via RRF.
    Falls back to Neo4j native vector search if Postgres is unavailable.
    Results are augmented with Neo4j structural graph context.
    """
    if not store_core._ENABLE_EMBEDDINGS or not store_core._ENABLE_RETRIEVAL:
        return []
    if not isinstance(query_vector, list) or not query_vector:
        store_core._debug("search_codebase_invalid_vector", project_id=project_id)
        return []

    # ── Postgres primary path ─────────────────────────────────────────────────
    if store_core._pg_pool_available():
        try:
            vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"
            use_keyword = bool(query_text and query_text.strip())
            fetch = k * 4

            if use_keyword:
                sql = """
                    WITH semantic AS (
                        SELECT id, file_path, chunk_index, content, metadata, created_at,
                               ROW_NUMBER() OVER (ORDER BY embedding <=> %(vec)s::vector) AS rank
                        FROM codebase_embeddings
                        WHERE project_id = %(pid)s
                        LIMIT %(fetch)s
                    ),
                    keyword AS (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   ORDER BY ts_rank(search_vec,
                                       plainto_tsquery('english', %(qt)s)) DESC
                               ) AS rank
                        FROM codebase_embeddings
                        WHERE project_id = %(pid)s
                          AND search_vec @@ plainto_tsquery('english', %(qt)s)
                        LIMIT %(fetch)s
                    ),
                    fused AS (
                        SELECT s.file_path, s.chunk_index, s.content, s.metadata, s.created_at,
                               (1.0 / (60 + s.rank)
                                + COALESCE(1.0 / (60 + k_tbl.rank), 0.0)) AS rrf_score
                        FROM semantic s
                        LEFT JOIN keyword k_tbl ON s.id = k_tbl.id
                    )
                    SELECT file_path, chunk_index, content, metadata, created_at, rrf_score
                    FROM fused
                    ORDER BY rrf_score DESC
                    LIMIT %(k)s
                """
                params: Dict[str, Any] = {
                    "vec": vec_str,
                    "qt": query_text,
                    "pid": project_id,
                    "fetch": fetch,
                    "k": k,
                }
            else:
                sql = """
                    SELECT file_path, chunk_index, content, metadata, created_at,
                           (1.0 / (60 + ROW_NUMBER() OVER (
                               ORDER BY embedding <=> %(vec)s::vector
                           ))) AS rrf_score
                    FROM codebase_embeddings
                    WHERE project_id = %(pid)s
                    ORDER BY embedding <=> %(vec)s::vector
                    LIMIT %(k)s
                """
                params = {"vec": vec_str, "pid": project_id, "k": k}

            results: List[Dict[str, Any]] = []
            async with store_core._pg_pool.connection() as conn:  # type: ignore[union-attr]
                async with conn.cursor() as cur:
                    await cur.execute(sql, params)
                    async for row in cur:
                        results.append(
                            {
                                "file_path": row[0],
                                "chunk_index": row[1],
                                "content": row[2],
                                "metadata": row[3],
                                "created_at": row[4],
                                "rrf_score": float(row[5]),
                            }
                        )

            store_core._debug(
                "pg_search_codebase",
                project_id=project_id,
                k=k,
                keyword_arm=use_keyword,
                found=len(results),
            )
            return await _augment_with_graph_context(project_id, results)

        except Exception as exc:
            store_core._debug(
                "pg_search_codebase_error", project_id=project_id, error=str(exc)
            )
            # fall through to Neo4j

    # ── Neo4j fallback (when Postgres unavailable) ────────────────────────────
    try:
        driver = graph_bootstrap.get_driver()
        if not driver:
            return []
        cypher = """
        CALL db.index.vector.queryNodes('codebase_chunks_vector', $k, $vec)
        YIELD node, score
        WHERE node.project_id = $pid
        RETURN node.metadata AS metadata_json, node.text AS content,
               score AS rrf_score, '' AS file_path, 0 AS chunk_index
        """
        results = []
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as s:
            res = await s.run(cypher, k=k * 2, vec=query_vector, pid=project_id)
            async for record in res:
                results.append(dict(record))
        results = results[:k]
        store_core._debug(
            "neo4j_search_codebase_fallback", project_id=project_id, found=len(results)
        )
        return await _augment_with_graph_context(project_id, results)
    except Exception as exc:
        store_core._debug(
            "neo4j_search_codebase_error", project_id=project_id, error=str(exc)
        )
    return []


async def _augment_with_graph_context(
    project_id: str,
    results: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Expand semantic results with Neo4j structural graph context.

    For each retrieved file, looks up its symbols and their outgoing
    CALLS/INHERITS relationships and prepends them to the chunk content.
    File node IDs use the current format: "project_id:file:rel_path".
    """
    try:
        if not graph_bootstrap._NEO4J_ENABLED:
            return results
        driver = graph_bootstrap.get_driver()
        if not driver:
            return results

        # Current file node ID format: "project_id:file:rel_path"
        file_node_ids = list(
            set(
                f"{project_id}:file:{r['file_path']}"
                for r in results
                if r.get("file_path")
            )
        )
        if not file_node_ids:
            return results

        cypher = """
        UNWIND $file_ids AS fid
        MATCH (f:File {id: fid})-[:CONTAINS]->(node)
        OPTIONAL MATCH (node)-[rel:CALLS|INHERITS]->(target)
        RETURN f.filepath AS file_path,
               labels(node)[0] AS node_type,
               node.name AS node_name,
               type(rel) AS rel_type,
               target.name AS target_name
        """
        graph_data: Dict[str, Dict[str, list]] = {}
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as s:
            res = await s.run(cypher, file_ids=file_node_ids)
            async for record in res:
                path = record["file_path"]
                if not path:
                    continue
                graph_data.setdefault(path, {})
                key = f"[{record['node_type']}] {record['node_name']}"
                graph_data[path].setdefault(key, [])
                if record["rel_type"] and record["target_name"]:
                    graph_data[path][key].append(
                        f"{record['rel_type']} -> {record['target_name']}"
                    )

        for r in results:
            path = r.get("file_path", "")
            if path in graph_data and graph_data[path]:
                lines = [f"=== GraphRAG: {path} ==="]
                for node_key, rels in graph_data[path].items():
                    lines.append(f" * {node_key}")
                    for rel in set(rels):
                        lines.append(f"    - {rel}")
                lines.append("=" * 35)
                r["content"] = "\n".join(lines) + "\n\n" + r.get("content", "")

        return results

    except Exception as exc:
        store_core._debug("graph_rag_augmentation_error", error=str(exc))
        return results
