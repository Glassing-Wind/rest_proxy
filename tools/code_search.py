"""tools/code_search.py — codebase search and cross-reference tools."""

import hashlib
import json
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def search_codebase(
        project_paths: list,
        query: str,
        k: int = 5,
        include_metadata: bool = False,
        languages: list | None = None,
        min_imports: int = 0,
        min_symbols: int = 0,
        require_diagnostics: bool = False,
        require_context: bool = False,
        include_paths: list | None = None,
        exclude_paths: list | None = None,
    ) -> str:
        """
        Perform a hybrid semantic search over one or more codebases simultaneously.
        Results are ranked by relevance using RRF (vector + full-text).

        Pass a single-element list for single-project search, or multiple paths
        for cross-project search — results are merged and annotated with their
        source project in the multi-project case.

        Args:
            project_paths: List of absolute paths to project roots to search across.
            query: Natural language or code snippet to search for.
            k: Total number of results to return (default 5).
            include_metadata: Show metadata lines in results (default False).
            languages: Optional allowlist of languages to include.
            min_imports: Require at least N file imports in metadata.
            min_symbols: Require at least N file symbols in metadata.
            require_diagnostics: Only return chunks with diagnostics.
            require_context: Only return chunks with a non-empty context_path.
            include_paths: Optional list of glob patterns to include (file_path).
            exclude_paths: Optional list of glob patterns to exclude (file_path).
        """
        try:
            import asyncio
            import fnmatch
            from embedding_service import get_embedding_service

            memory_store, _, _, _, _ = get_memory_modules()

            if not project_paths:
                return "Error: provide at least one project path."

            multi = len(project_paths) > 1

            svc = get_embedding_service()
            vecs = await svc.embed_batch_async([query])
            query_vector = vecs[0]
            if not query_vector:
                return "Error: Could not generate embedding for query."

            vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"
            fetch = min(k * 10, 150)

            await memory_store.open_pool()

            pid_to_name: dict[str, str] = {}
            for p in project_paths:
                pid = hashlib.md5(p.encode()).hexdigest()[:12]
                pid_to_name[pid] = p.rstrip("/").split("/")[-1]

            def _format_meta(meta: dict) -> list[str]:
                if not isinstance(meta, dict):
                    return []
                parts: list[str] = []
                language = meta.get("language")
                if language:
                    parts.append(f"lang={language}")
                imports = meta.get("file_imports")
                if isinstance(imports, list) and imports:
                    parts.append(f"imports={len(imports)}")
                symbols = meta.get("file_symbols")
                if isinstance(symbols, list) and symbols:
                    parts.append(f"symbols={len(symbols)}")
                node_types = meta.get("node_types")
                if isinstance(node_types, list) and node_types:
                    parts.append(f"node_types={len(node_types)}")
                diagnostics = meta.get("file_diagnostics") or {}
                if isinstance(diagnostics, dict):
                    diag_count = diagnostics.get("count")
                    if isinstance(diag_count, int) and diag_count > 0:
                        parts.append(f"diagnostics={diag_count}")
                metrics = meta.get("file_metrics") or {}
                if isinstance(metrics, dict):
                    lines = metrics.get("total_lines")
                    if isinstance(lines, int):
                        parts.append(f"lines={lines}")
                ctx = meta.get("context_path")
                ctx_line = ""
                if isinstance(ctx, list) and ctx:
                    ctx_line = "context=" + " > ".join(str(c) for c in ctx[:6])
                out = []
                if parts:
                    out.append("meta: " + ", ".join(parts))
                if ctx_line:
                    out.append(ctx_line)
                return out

            async def _search_project(pid: str) -> list[dict]:
                async with memory_store._pg_pool.connection() as conn:
                    await conn.execute("BEGIN")
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "SET LOCAL hnsw.ef_search = 100"
                        )  # resets when connection returns to pool
                        await cur.execute(
                            "SET LOCAL hnsw.iterative_scan = relaxed_order"
                        )  # auto-expands past project_id filter
                        await cur.execute(
                            """\
                            WITH semantic AS (
                                SELECT file_path, chunk_index, content, project_id, metadata,
                                       ROW_NUMBER() OVER (
                                           ORDER BY embedding <=> %(vec)s::vector
                                       ) AS sem_rank
                                FROM codebase_embeddings
                                WHERE project_id = %(pid)s
                                LIMIT %(fetch)s
                            ),
                            keyword AS (
                                SELECT file_path, chunk_index,
                                       ROW_NUMBER() OVER (
                                           ORDER BY ts_rank(search_vec,
                                               websearch_to_tsquery('english', %(qt)s)) DESC
                                       ) AS kw_rank
                                FROM codebase_embeddings
                                WHERE project_id = %(pid)s
                                  AND search_vec @@ websearch_to_tsquery('english', %(qt)s)
                                LIMIT %(fetch)s
                            )
                            SELECT s.file_path, s.chunk_index, s.content, s.project_id, s.metadata,
                                   (2.0/(60+s.sem_rank)
                                    + COALESCE(1.0/(60+k.kw_rank), 0.0)) AS rrf
                            FROM semantic s
                            LEFT JOIN keyword k
                              ON s.file_path = k.file_path
                              AND s.chunk_index = k.chunk_index
                            ORDER BY rrf DESC
                            LIMIT %(fetch)s
                        """,
                            {"vec": vec_str, "pid": pid, "qt": query, "fetch": fetch},
                        )
                        rows = await cur.fetchall()
                        return [
                            {
                                "file_path": r[0],
                                "chunk_index": r[1],
                                "content": r[2],
                                "project_id": r[3],
                                "metadata": r[4],
                                "rrf": r[5],
                            }
                            for r in rows
                        ]

            def _meta_score(meta: dict) -> int:
                if not isinstance(meta, dict):
                    return 0
                score = 0
                for key in (
                    "file_imports",
                    "file_symbols",
                    "node_types",
                    "file_metrics",
                    "file_diagnostics",
                    "context_path",
                ):
                    val = meta.get(key)
                    if isinstance(val, list) and val:
                        score += 1
                    elif isinstance(val, dict) and val:
                        score += 1
                return score

            def _passes_filters(meta: dict) -> bool:
                if not isinstance(meta, dict):
                    return False
                if languages:
                    lang = meta.get("language")
                    if not lang or lang not in languages:
                        return False
                if min_imports > 0:
                    imports = meta.get("file_imports")
                    if not isinstance(imports, list) or len(imports) < min_imports:
                        return False
                if min_symbols > 0:
                    symbols = meta.get("file_symbols")
                    if not isinstance(symbols, list) or len(symbols) < min_symbols:
                        return False
                if require_diagnostics:
                    diagnostics = meta.get("file_diagnostics") or {}
                    if (
                        not isinstance(diagnostics, dict)
                        or diagnostics.get("count", 0) <= 0
                    ):
                        return False
                if require_context:
                    ctx = meta.get("context_path")
                    if not isinstance(ctx, list) or not ctx:
                        return False
                return True

            def _path_allowed(file_path: str) -> bool:
                if not file_path:
                    return True
                if include_paths:
                    if not any(
                        fnmatch.fnmatch(file_path, pat) for pat in include_paths
                    ):
                        return False
                if exclude_paths:
                    if any(fnmatch.fnmatch(file_path, pat) for pat in exclude_paths):
                        return False
                return True

            all_results: list[dict] = []
            batch = await asyncio.gather(*[_search_project(pid) for pid in pid_to_name])
            for chunk in batch:
                all_results.extend(chunk)

            if not all_results:
                projects = ", ".join(f"'{n}'" for n in pid_to_name.values())
                return f"No matching code found in {projects}.\nEnsure projects are indexed with index_workspace()."

            filters_active = any(
                [
                    languages,
                    min_imports > 0,
                    min_symbols > 0,
                    require_diagnostics,
                    require_context,
                    include_paths,
                    exclude_paths,
                ]
            )
            if filters_active:
                include_metadata = True

            if include_metadata:
                for r in all_results:
                    meta = r.get("metadata")
                    if isinstance(meta, str):
                        try:
                            meta = json.loads(meta)
                        except Exception:
                            meta = {}
                    r_meta = meta if isinstance(meta, dict) else {}
                    r["_meta"] = r_meta
                    r["meta_score"] = _meta_score(r_meta)
                if filters_active:
                    all_results = [
                        r
                        for r in all_results
                        if _passes_filters(r.get("_meta", {}))
                        and _path_allowed(r.get("file_path", ""))
                    ]
                all_results.sort(
                    key=lambda r: (r["rrf"], r.get("meta_score", 0)), reverse=True
                )
            else:
                all_results.sort(key=lambda r: r["rrf"], reverse=True)
            top = all_results[:k]

            lines = []
            if multi:
                lines.append(
                    f"Cross-project search: '{query}'  ({len(pid_to_name)} projects)\n"
                )

            for i, r in enumerate(top, 1):
                proj = pid_to_name.get(r["project_id"], r["project_id"])
                if multi:
                    lines.append(
                        f"[{i}] [{proj}] {r['file_path']}  (score: {r['rrf']:.4f})"
                    )
                else:
                    lines.append(f"--- {r['file_path']} (Score: {r['rrf']:.4f}) ---")
                if include_metadata:
                    meta = r.get("_meta")
                    if not isinstance(meta, dict):
                        meta = r.get("metadata")
                        if isinstance(meta, str):
                            try:
                                meta = json.loads(meta)
                            except Exception:
                                meta = {}
                    lines.extend(_format_meta(meta if isinstance(meta, dict) else {}))
                lines.append(r["content"].strip())
                lines.append("")

            return "\n".join(lines)
        except Exception as e:
            return f"Error searching codebase: {str(e)}"

    @mcp.tool()
    async def trace_symbol_cross_project(
        symbol_name: str,
        source_project: str,
        target_project: str,
    ) -> str:
        """
        Trace a symbol from its definition in one project to its usages in another.

        Ideal for gRPC-style workflows: find where a service, message, or function
        is *defined* server-side and how it is *called or referenced* client-side.

        Combines two sources per side:
        - Neo4j structural graph (definition location, call-edges)
        - Postgres semantic search (text occurrences in chunks)

        Args:
            symbol_name:     Exact name of the symbol to trace (e.g. 'GenerateImageRequest').
            source_project:  Absolute path to the project where the symbol is defined.
            target_project:  Absolute path to the project that consumes/calls the symbol.
        """
        try:
            import asyncio
            from embedding_service import get_embedding_service

            memory_store, _, _, _, _ = get_memory_modules()

            src_id = hashlib.md5(source_project.encode()).hexdigest()[:12]
            tgt_id = hashlib.md5(target_project.encode()).hexdigest()[:12]
            src_name = source_project.rstrip("/").split("/")[-1]
            tgt_name = target_project.rstrip("/").split("/")[-1]

            import graph_bootstrap

            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()

            # ── 1. Definition in source project ──────────────────────────────
            definition: dict = {}
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                r = await session.run(
                    """
                    MATCH (s {name: $name, project_id: $pid})
                    WHERE s:Function OR s:Class OR s:Struct OR s:Trait
                       OR s:Enum OR s:Method OR s:Protocol
                    OPTIONAL MATCH (s)<-[:CONTAINS]-(f:File)
                    RETURN labels(s)[0]  AS kind,
                           s.filepath    AS filepath,
                           s.start_line  AS start_line,
                           s.end_line    AS end_line,
                           s.signature   AS signature
                    LIMIT 1
                """,
                    name=symbol_name,
                    pid=src_id,
                )
                rec = await r.single()
                if rec:
                    definition = dict(rec)

            # ── 2. Call-graph usages in target project ────────────────────────
            graph_usages: list[str] = []
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                r2 = await session.run(
                    """
                    MATCH (target {name: $name})
                    WHERE target:Function OR target:Class OR target:Struct
                       OR target:Method   OR target:Trait OR target:Protocol
                    MATCH (caller {project_id: $tpid})-[:CALLS]->(target)
                    RETURN DISTINCT
                           caller.name      AS caller_name,
                           caller.filepath  AS caller_file,
                           caller.start_line AS caller_line,
                           labels(caller)[0] AS caller_kind
                    ORDER BY caller.filepath, caller.start_line
                    LIMIT 20
                """,
                    name=symbol_name,
                    tpid=tgt_id,
                )
                async for rec in r2:
                    name = rec["caller_name"] or "(file scope)"
                    fp = rec["caller_file"] or "?"
                    line = f":{rec['caller_line']}" if rec["caller_line"] else ""
                    graph_usages.append(
                        f"  {name}{line}  [{rec['caller_kind'] or 'Node'}]  in {fp}"
                    )

            # ── 3. Semantic text hits in target project ───────────────────────
            sem_usages: list[str] = []
            await memory_store.open_pool()

            svc = get_embedding_service()
            vecs = await svc.embed_batch_async([symbol_name])
            query_vector = vecs[0]

            async def _fetch_semantic():
                vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"
                async with memory_store._pg_pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            WITH sem AS (
                                SELECT file_path, chunk_index, content,
                                       ROW_NUMBER() OVER (
                                           ORDER BY embedding <=> %(vec)s::vector
                                       ) AS sem_rank
                                FROM codebase_embeddings
                                WHERE project_id = %(pid)s
                                LIMIT 40
                            ),
                            kw AS (
                                SELECT file_path, chunk_index,
                                       ROW_NUMBER() OVER (
                                           ORDER BY ts_rank(search_vec,
                                               plainto_tsquery('english', %(qt)s)) DESC
                                       ) AS kw_rank
                                FROM codebase_embeddings
                                WHERE project_id = %(pid)s
                                  AND content ILIKE %(ilike)s
                                LIMIT 40
                            )
                            SELECT s.file_path, s.chunk_index, s.content,
                                   (1.0/(60+s.sem_rank) + COALESCE(1.0/(60+k.kw_rank), 0.0)) AS rrf
                            FROM sem s LEFT JOIN kw k
                              ON s.file_path = k.file_path AND s.chunk_index = k.chunk_index
                            WHERE s.content ILIKE %(ilike)s
                            ORDER BY rrf DESC LIMIT 5
                        """,
                            {
                                "vec": vec_str,
                                "pid": tgt_id,
                                "qt": symbol_name,
                                "ilike": f"%{symbol_name}%",
                            },
                        )
                        return await cur.fetchall()

            async def _fetch_src_preview():
                if not definition.get("filepath"):
                    return []
                async with memory_store._pg_pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            SELECT content FROM codebase_embeddings
                            WHERE project_id = %s AND file_path = %s
                            ORDER BY chunk_index LIMIT 2
                        """,
                            (src_id, definition["filepath"]),
                        )
                        return await cur.fetchall()

            sem_rows, src_rows = await asyncio.gather(
                _fetch_semantic(), _fetch_src_preview()
            )

            for fp, idx, content, rrf in sem_rows:
                sem_usages.append(
                    f"  [chunk {idx}]  {fp}  (score: {rrf:.4f})\n"
                    f"    {content[:200].strip().replace(chr(10), ' ')}…"
                )

            # ── 4. Assemble output ────────────────────────────────────────────
            lines = [
                f"## Cross-project trace: `{symbol_name}`",
                f"   {src_name}  →  {tgt_name}",
                "",
            ]

            if definition:
                lines += [
                    f"### Definition  [{src_name}]",
                    f"  Kind:      {definition.get('kind', '?')}",
                    f"  File:      {definition.get('filepath', '?')}  "
                    f"L{definition.get('start_line', '?')}–{definition.get('end_line', '?')}",
                ]
                if definition.get("signature"):
                    lines.append(f"  Signature: {definition['signature']}")
                if src_rows:
                    preview = "\n".join(r[0][:400] for r in src_rows)
                    lines += ["", f"```\n{preview.strip()}\n```"]
            else:
                lines.append(f"⚠️  `{symbol_name}` not found in Neo4j for [{src_name}].")
                lines.append(
                    "   (Symbol may be in an un-indexed file or a different casing.)"
                )

            lines += ["", f"### Usages  [{tgt_name}]"]

            if graph_usages:
                lines.append(f"**Call-graph hits** ({len(graph_usages)}):")
                lines.extend(graph_usages)
            else:
                lines.append("  No direct call-graph edges found.")

            if sem_usages:
                lines += ["", f"**Semantic text hits** ({len(sem_usages)}):"]
                lines.extend(sem_usages)
            else:
                lines.append("  No semantic text hits found.")

            if not graph_usages and not sem_usages:
                lines += [
                    "",
                    f"💡 `{symbol_name}` appears to be defined in [{src_name}] but not yet referenced in [{tgt_name}].",
                    f"   Try `search_multi_project` with a broader semantic query.",
                ]

            return "\n".join(lines)
        except Exception as e:
            return f"Error tracing cross-project symbol: {str(e)}"

    @mcp.tool()
    async def query_graph(cypher_query: str) -> str:
        """
        Execute a raw Cypher query on the Neo4j structural graph.
        Useful for complex relationship analysis.

        Args:
            cypher_query: The Cypher query string.
        """
        try:
            import graph_bootstrap

            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()
            if not driver:
                return "Error: Could not connect to Neo4j."
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                result = await session.run(cypher_query)
                data = []
                async for record in result:
                    data.append(record.data())
            if not data:
                return "No results found."
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error querying graph: {str(e)}"

    @mcp.tool()
    async def find_definitions(symbol_name: str) -> str:
        """
        Search for the definition of a class, function, or struct across ALL indexed projects.
        Ideal for cross-project dependency discovery.

        Args:
            symbol_name: Name of the symbol to find.
        """
        try:
            import graph_bootstrap

            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()
            cypher = """
            MATCH (n)
            WHERE (n:Class OR n:Function) AND n.name = $name
            RETURN n.project_id AS project, n.filepath AS file,
                   n.start_line AS line, labels(n)[0] AS type
            """
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                result = await session.run(cypher, name=symbol_name)
                output = [f"Found {symbol_name} in the following locations:"]
                async for record in result:
                    loc = record["file"] or "unknown"
                    line = record["line"]
                    loc_str = f"{loc}:{line}" if line is not None else loc
                    output.append(
                        f"- [{record['type']}] Project: {record['project']}, File: {loc_str}"
                    )
            if len(output) == 1:
                return f"Symbol '{symbol_name}' not found in any indexed project."
            return "\n".join(output)
        except Exception as e:
            return f"Error finding definition: {str(e)}"
