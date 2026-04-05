"""tools/search/cross_project.py — cross-project symbol tracing."""

from mcp.server.fastmcp import FastMCP

from _helpers import get_memory_modules, get_project_id
from tools.search import core as search_core


def register(mcp: FastMCP) -> None:

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

            src_id = get_project_id(source_project)
            tgt_id = get_project_id(target_project)
            src_name = source_project.rstrip("/").split("/")[-1]
            tgt_name = target_project.rstrip("/").split("/")[-1]

            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            # ── 1. Definition in source project ──────────────────────────────
            definition: dict = {}
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await search_core._execute_read(
                    session,
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
                    op="trace_symbol_definition",
                )
                if records:
                    definition = dict(records[0])

            # ── 2. Call-graph usages in target project ────────────────────────
            graph_usages: list[str] = []
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await search_core._execute_read(
                    session,
                    """
                    MATCH (target {name: $name})
                    WHERE target:Function OR target:Class OR target:Struct
                       OR target:Method   OR target:Trait OR target:Protocol
                    MATCH (caller {project_id: $tpid})-[:CALLS|CALLS_INFERRED]->(target)
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
                    op="trace_symbol_graph_usages",
                )
                for rec in records:
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
