"""tools/code_intel/core.py — code intelligence tools (symbol analysis, visualization, file description)."""

import time
import os
from neo4j import unit_of_work
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules, get_project_id
from proxy.logging import debug_log
from ts_diagnostics import normalize_ts_pack_result
from tools.brain.code_intel import file_describe
from tools.brain.code_intel import references
from tools.brain.code_intel import symbol_graph


def register(mcp: FastMCP) -> None:

    _TX_TIMEOUT = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
    _TX_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
    _TX_METADATA_BASE = {"source": "lm_proxy", "tool": "code_intel"}

    async def _execute_read(
        session,
        cypher: str,
        timeout: float | None = None,
        op: str | None = None,
        **params,
    ):
        metadata = dict(_TX_METADATA_BASE)
        op_value = op or "read"
        if _TX_OP_PREFIX:
            op_value = f"{_TX_OP_PREFIX}.{op_value}"
        metadata["op"] = op_value

        @unit_of_work(timeout=timeout or _TX_TIMEOUT, metadata=metadata)
        async def _tx(tx):
            result = await tx.run(cypher, **params)
            return await result.data()

        if hasattr(session, "execute_read"):
            return await session.execute_read(_tx)
        return await _tx(session)

    @mcp.tool()
    async def get_symbol_context(
        workspace_id: str, symbol_name: str, include_source_preview: bool = True
    ) -> str:
        """
        Single-call deep dive into a symbol: definition location, signature,
        what it calls, what calls it, and the actual source code chunk.

        Replaces the common 4-step pattern of:
          get_code_summary → find_callers → find_definitions → search_codebase

        Args:
            workspace_id: Logical workspace name or absolute project path.
            symbol_name:  Name of the function, class, or struct to inspect.
        """
        try:
            project_id = get_project_id(workspace_id)
            import graph_bootstrap

            _, _, _, _, proxy = get_memory_modules()

            driver = await graph_bootstrap.require_driver()

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await _execute_read(
                    session,
                    symbol_graph.SYMBOL_CONTEXT_CYPHER,
                    name=symbol_name,
                    pid=project_id,
                    op="get_symbol_context",
                )
                rec = records[0] if records else None

            if not rec:
                return f"Symbol '{symbol_name}' not found. Run index_workspace() first."

            out = symbol_graph.format_symbol_context(rec, symbol_name)

            if include_source_preview:
                # 1. Try Postgres (Brain/Central fallback) - This works remotely!
                try:
                    memory_store, _, _, _, _ = get_memory_modules()
                    await memory_store.open_pool()
                    async with memory_store._pg_pool.connection() as conn:
                        async with conn.cursor() as cur:
                            await cur.execute(
                                """
                                SELECT content FROM codebase_embeddings
                                WHERE  project_id = %s AND file_path = %s
                                ORDER  BY chunk_index LIMIT 2
                            """,
                                (project_id, rec["filepath"]),
                            )
                            rows = await cur.fetchall()
                            if rows:
                                src = "\n\n".join(r[0][:600] for r in rows)
                                out += [f"\n**Source preview:**\n```\n{src}\n```"]
                except Exception:
                    # 2. Try Local Filesystem (Hands fallback) - only if workspace_id is actually a local path
                    if os.path.exists(workspace_id):
                        try:
                            abs_path = os.path.join(workspace_id, rec["filepath"])
                            if os.path.exists(abs_path):
                                with open(
                                    abs_path, "r", encoding="utf-8", errors="ignore"
                                ) as fh:
                                    lines_list = fh.read().splitlines()
                                start_line = max(1, int(rec["start_line"] or 1))
                                end_line = max(start_line, int(rec["end_line"] or start_line))
                                snippet = "\n".join(lines_list[start_line - 1 : end_line])
                                if snippet.strip():
                                    out += [f"\n**Source preview:**\n```ts\n{snippet}\n```"]
                        except Exception:
                            pass

            return "\n".join(out)
        except Exception as e:
            return f"Error getting symbol context: {str(e)}"

    @mcp.tool()
    async def get_call_chain(
        workspace_id: str,
        symbol_name: str,
        depth: int = 3,
        direction: str = "down",
        file_path: str | None = None,
        signature: str | None = None,
    ) -> str:
        """
        Trace a call chain N hops deep from a starting symbol.

        Unlike get_symbol_context (single hop), this recursively follows
        CALLS edges to build a full call tree — ideal for understanding
        execution paths and gRPC handler flows.

        Args:
            workspace_id: Logical workspace name or absolute project path.
            symbol_name:  Starting symbol name.
            depth:        How many hops to follow (default 3, max 5).
            direction:    'down' (what this calls) or 'up' (what calls this).
            file_path:    Optional file path to disambiguate symbols.
            signature:    Optional signature substring to disambiguate symbols.
        """
        try:
            project_id = get_project_id(workspace_id)
            depth = min(int(depth), 5)
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            normalized_file_path = symbol_graph.normalize_query_file_path(
                workspace_id, file_path
            )

            normalized_signature = (
                signature.strip() if isinstance(signature, str) else None
            )
            if not normalized_signature:
                normalized_signature = None

            resolved_name = symbol_name
            resolved_eid = None
            hop_label = "caller" if direction == "up" else "callee"

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                candidates = await _execute_read(
                    session,
                    symbol_graph.CALL_CHAIN_RESOLVE_CYPHER,
                    name=symbol_name,
                    pid=project_id,
                    file_path=normalized_file_path,
                    signature=normalized_signature,
                    op="get_call_chain_resolve",
                )
                if not candidates:
                    return (
                        f"`{symbol_name}` not found or no {hop_label}s within {depth} hops.\n"
                        "Try a fully qualified name like `Type.method` or include argument labels."
                    )

                picked = symbol_graph.pick_call_chain_candidate(
                    candidates,
                    normalized_file_path=normalized_file_path,
                    normalized_signature=normalized_signature,
                )

                resolved_eid = picked["eid"]
                resolved_name = (
                    picked.get("qualified_name") or picked.get("name") or symbol_name
                )
                resolved_filepath = picked.get("filepath") or ""
                hop_label, cypher = symbol_graph.build_call_chain_path_cypher(
                    direction,
                    depth,
                    is_backend_root=symbol_graph.is_backend_filepath(resolved_filepath),
                )
                rows = await _execute_read(
                    session, cypher, eid=resolved_eid, op="get_call_chain"
                )

            if not rows:
                return (
                    f"`{resolved_name}` resolved but no {hop_label}s within {depth} hops.\n"
                    "Make sure the project is indexed and Swift CALLS edges are available."
                )

            return symbol_graph.format_call_chain_rows(
                rows,
                resolved_name=resolved_name,
                symbol_name=symbol_name,
                direction=direction,
                depth=depth,
            )
        except Exception as e:
            return f"Error tracing call chain: {str(e)}"

    @mcp.tool()
    async def list_symbol_matches(
        project_path: str, query: str, limit: int = 30, kinds: list | None = None
    ) -> str:
        """
        List symbols whose name/signature contains a query string.

        Args:
            project_path: Absolute path to the project root.
            query:        Substring to match against symbol name/signature.
            limit:        Max results to return (default 30).
        kinds:        Optional list of kinds to include (Function, Class, Enum, EnumCase, Protocol, Extension, etc.).
        """
        try:
            project_id = get_project_id(project_path)
            import graph_bootstrap

            _, _, _, _, proxy = get_memory_modules()

            driver = await graph_bootstrap.require_driver()

            q = (query or "").strip()
            if not q:
                return "Query is empty. Provide a symbol name substring to match."

            kind_set = {
                "Function",
                "Method",
                "Class",
                "Struct",
                "Trait",
                "Enum",
                "EnumCase",
                "Protocol",
                "Extension",
                "TypeAlias",
                "AssociatedType",
            }
            kind_list = []
            if kinds:
                for k in kinds:
                    if isinstance(k, str) and k in kind_set:
                        kind_list.append(k)
            if not kind_list:
                kind_list = sorted(kind_set)

            type_kinds = [
                "Class",
                "Struct",
                "Enum",
                "EnumCase",
                "Protocol",
                "Extension",
                "TypeAlias",
                "AssociatedType",
                "Trait",
            ]
            callable_kinds = ["Function", "Method"]

            cypher = """
                MATCH (s:Node {project_id: $pid})
                WHERE any(k in $kinds WHERE k IN labels(s))
                AND (
                  s.name CONTAINS $q
                  OR (s.qualified_name IS NOT NULL AND s.qualified_name CONTAINS $q)
                  OR (s.signature IS NOT NULL AND s.signature CONTAINS $q)
                )
                AND (size(s.name) > 2 OR size($q) <= 2)
                RETURN labels(s) AS kinds, s.name AS name, s.qualified_name AS qualified_name,
                       s.signature AS signature, s.filepath AS filepath,
                       CASE WHEN s.name CONTAINS $q THEN 0 ELSE 1 END AS name_match,
                       CASE
                         WHEN any(k in $type_kinds WHERE k IN labels(s)) THEN 0
                         WHEN any(k in $callable_kinds WHERE k IN labels(s)) THEN 1
                         ELSE 2
                       END AS kind_rank
                ORDER BY name_match ASC, kind_rank ASC, size(s.name) ASC
                LIMIT $limit
            """

            start = time.perf_counter()
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                rows = await _execute_read(
                    session,
                    cypher,
                    pid=project_id,
                    q=q,
                    kinds=kind_list,
                    limit=min(int(limit), 200),
                    type_kinds=type_kinds,
                    callable_kinds=callable_kinds,
                    timeout=float(os.getenv("LM_PROXY_NEO4J_READ_TIMEOUT", "3.0")),
                    op="list_symbol_matches",
                )

            debug_log(
                "list_symbol_matches",
                query=q,
                project_id=project_id,
                rows=len(rows),
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )

            if not rows:
                return f"No symbol matches for '{q}'."

            lines = [f"Symbol matches for '{q}':", ""]
            for rec in rows:
                sig = rec.get("signature") or ""
                sig = sig.strip().replace("\n", " ")
                sig = f" — {sig}" if sig else ""
                qn = rec.get("qualified_name") or ""
                qn = f" ({qn})" if qn and qn != rec.get("name") else ""
                kinds = rec.get("kinds") or []
                kind = next(
                    (k for k in kinds if k in kind_set), kinds[0] if kinds else "Node"
                )
                lines.append(
                    f"- {rec.get('name')}{qn} ({kind})  {rec.get('filepath')}{sig}"
                )
            return "\n".join(lines)
        except Exception as e:
            debug_log("list_symbol_matches_error", error=str(e))
            return f"Error listing symbols: {str(e)}"

    @mcp.tool()
    async def diagnose_symbol_query(
        project_path: str, query: str, kind: str = "Enum"
    ) -> str:
        """
        Diagnostic path for symbol query latency.

        Args:
            project_path: Absolute path or logical workspace identifier.
            query:        Substring to match against symbol name/signature.
            kind:         Label to filter (default: Enum).
        """
        try:
            project_id = get_project_id(project_path)
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            q = (query or "").strip()
            if not q:
                return "Query is empty. Provide a symbol name substring to match."

            cypher = """
                MATCH (s:Node {project_id: $pid})
                WHERE $kind IN labels(s)
                AND (
                  s.name CONTAINS $q
                  OR (s.qualified_name IS NOT NULL AND s.qualified_name CONTAINS $q)
                  OR (s.signature IS NOT NULL AND s.signature CONTAINS $q)
                )
                RETURN count(s) AS n
            """

            start = time.perf_counter()
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await _execute_read(
                    session,
                    cypher,
                    pid=project_id,
                    q=q,
                    kind=kind,
                    timeout=float(os.getenv("LM_PROXY_NEO4J_READ_TIMEOUT", "3.0")),
                    op="diagnose_symbol_query",
                )
                rec = records[0] if records else None

            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return (
                f"diagnose_symbol_query: kind={kind} query='{q}' "
                f"rows={rec['n'] if rec else 0} elapsed_ms={elapsed_ms}"
            )
        except Exception as e:
            return f"Error diagnosing symbol query: {str(e)}"

    @mcp.tool()
    async def get_code_importance(workspace_id: str) -> str:
        """
        Identify the most important files in a project using GDS PageRank on the
        CALLS graph. Files whose symbols are called by many important callers rank
        highest — this is architecturally meaningful, not just symbol density.

        Falls back to a heuristic (callers×3 + symbols) for projects that have not
        yet been indexed with the PageRank pass.

        Excludes test files automatically.

        Args:
            workspace_id: Logical workspace name or absolute project path.
        """
        try:
            project_id = get_project_id(workspace_id)
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            # Primary query: use GDS PageRank if available
            cypher_pr = """
            MATCH (f:File {project_id: $pid})
            WHERE f.pagerank IS NOT NULL
              AND NOT f.filepath CONTAINS 'test'
              AND NOT f.filepath CONTAINS 'Test'
              AND NOT f.filepath CONTAINS 'spec'
              AND NOT f.filepath CONTAINS 'vendor'
            OPTIONAL MATCH (f)-[:CONTAINS]->(s)
              WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum
            WITH f, count(s) AS sym_count, collect(DISTINCT s.name)[..4] AS sym_examples
            RETURN f.filepath AS file, sym_count, sym_examples,
                   f.pagerank      AS top_pagerank,
                   f.pagerank_sum  AS score,
                   f.betweenness   AS betweenness,
                   coalesce(f.isolated, false) AS isolated
            ORDER BY score DESC LIMIT 15
            """

            # Fallback query: heuristic for un-ranked projects
            cypher_fallback = """
            MATCH (f:File {project_id: $pid})
            WHERE NOT f.filepath CONTAINS 'test'
              AND NOT f.filepath CONTAINS 'Test'
              AND NOT f.filepath CONTAINS 'spec'
              AND NOT f.filepath CONTAINS 'vendor'
            OPTIONAL MATCH (f)-[:CONTAINS]->(s)
              WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum
            WITH f, count(s) AS sym_count, collect(DISTINCT s.name)[..4] AS sym_examples
            OPTIONAL MATCH (caller:File {project_id: $pid})-[:CALLS|CALLS_INFERRED]->(cs)<-[:CONTAINS]-(f)
              WHERE caller <> f
            WITH f, sym_count, sym_examples, count(DISTINCT caller) AS callers_in
            WITH f.filepath AS file, sym_count, sym_examples,
                 callers_in * 3 + sym_count AS score, NULL AS top_pagerank
            WHERE score > 0
            ORDER BY score DESC LIMIT 15
            RETURN file, sym_count, sym_examples, top_pagerank, score
            """

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await _execute_read(
                    session,
                    cypher_pr,
                    pid=project_id,
                    op="get_code_importance_pr",
                )
                using_pagerank = bool(records)

                if not using_pagerank:
                    records = await _execute_read(
                        session,
                        cypher_fallback,
                        pid=project_id,
                        op="get_code_importance_fallback",
                    )

            scoring_method = (
                "GDS PageRank (CALLS graph)"
                if using_pagerank
                else "heuristic (callers×3 + symbols)"
            )
            output = [f"Most important files [{scoring_method}, test/vendor excluded]:"]
            for record in records:
                examples = (
                    ", ".join(record["sym_examples"]) if record["sym_examples"] else "—"
                )
                pr_str = (
                    f"  pr:{record['top_pagerank']:.4f}"
                    if record["top_pagerank"]
                    else ""
                )
                output.append(
                    f"- {record['file']}"
                    f"  [score:{record['score'] or 0:.4f}{pr_str}  symbols:{record['sym_count'] or 0}"
                    + (
                        f"  bridge:{record['betweenness']:.1f}"
                        if record.get("betweenness")
                        else ""
                    )
                    + ("  ⚠️isolated" if record.get("isolated") else "")
                    + f"]  ({examples})"
                )
            if len(output) == 1:
                return "No importance metrics found (ensure project is indexed)."
            return "\n".join(output)
        except Exception as e:
            return f"Error calculating code importance: {str(e)}"

    @mcp.tool()
    async def get_code_communities(workspace_id: str) -> str:
        """
        Identify architectural clusters in the codebase by grouping files by their
        top-level directory. Shows the structure of the project at a glance.
        For each cluster, lists its most symbol-rich files.

        Run build_import_graph() first to enable real PageRank and
        Louvain community detection via get_code_importance and
        get_code_communities.

        Args:
            workspace_id: Logical workspace name or absolute project path.
        """
        try:
            project_id = get_project_id(workspace_id)
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            # Primary: Louvain topology-based communities
            cypher_louvain = """
            MATCH (f:File {project_id: $pid})
            WHERE f.louvainCommunity IS NOT NULL
              AND NOT f.filepath CONTAINS 'test'
              AND NOT f.filepath CONTAINS 'vendor'
            OPTIONAL MATCH (f)-[:CONTAINS]->(s)
            WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum
            WITH f, f.louvainCommunity AS comm, count(s) AS sym_count
            WITH comm,
                 collect(f.filepath)[..5] AS top_files,
                 sum(sym_count)           AS total_syms,
                 count(f)                 AS file_count
            WHERE file_count > 0
            ORDER BY total_syms DESC
            RETURN comm, file_count, total_syms, top_files
            """

            # Fallback: directory grouping
            cypher_dir = """
            MATCH (f:File {project_id: $pid})
            WITH f, CASE WHEN f.filepath CONTAINS '/'
                         THEN split(f.filepath, '/')[0]
                         ELSE '(root)' END AS top_dir
            OPTIONAL MATCH (f)-[:CONTAINS]->(s)
            WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum
            WITH top_dir, f.filepath AS fp, count(s) AS sym_count
            ORDER BY top_dir, sym_count DESC
            WITH top_dir, collect(fp)[..5] AS top_files, sum(sym_count) AS total_syms,
                 count(fp) AS file_count
            ORDER BY total_syms DESC
            RETURN NULL AS comm, top_dir AS dominant_dir, file_count, total_syms, top_files
            """

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await _execute_read(
                    session,
                    cypher_louvain,
                    pid=project_id,
                    op="get_code_communities_louvain",
                )
                using_louvain = bool(records)

                if not using_louvain:
                    records = await _execute_read(
                        session,
                        cypher_dir,
                        pid=project_id,
                        op="get_code_communities_dir",
                    )

            method = (
                "GDS Louvain (topology)" if using_louvain else "top-level directory"
            )
            output = [f"Architectural clusters [{method}]:"]
            for record in records:
                if using_louvain:
                    comm_label = f"cluster #{record['comm']}"
                else:
                    comm_label = record["dominant_dir"]
                output.append(
                    f"\n\U0001f4e6 {comm_label}"
                    f"  ({record['file_count']} files, {record['total_syms']} symbols)"
                    f"\n   Top files: {', '.join(record['top_files'])}"
                )
            if len(output) == 1:
                return "No communities found (ensure project is indexed)."
            return "\n".join(output)
        except Exception as e:
            return f"Error identifying code communities: {str(e)}"

    @mcp.tool()
    async def get_related_files(project_path: str, file_path: str) -> str:
        """
        Find files that are structurally related to the target file.

        Args:
            project_path: Absolute path to the project root.
            file_path: Relative path to the file in the project.
        """
        try:
            project_id = get_project_id(project_path)
            file_id = f"{project_id}:file:{file_path}"
            cypher = """
            MATCH (f1:File {id: $fid})-[:CONTAINS]->(imp1:Import)
            WITH f1, collect(imp1.source) AS my_imports
            MATCH (f2:File {project_id: $pid})-[:CONTAINS]->(imp2:Import)
            WHERE f2 <> f1 AND imp2.source IN my_imports
            WITH f2.filepath AS related_file, count(imp2) AS shared_imports
            ORDER BY shared_imports DESC LIMIT 10
            RETURN related_file, shared_imports
            """
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                related = []
                records = await _execute_read(
                    session,
                    cypher,
                    fid=file_id,
                    pid=project_id,
                    op="get_related_files",
                )
                for record in records:
                    related.append(
                        f"- {record['related_file']} (Strength: {record['shared_imports']})"
                    )
            if related:
                return "Related Files:\n" + "\n".join(related)

            # Fallback: semantic co-mentions based on top symbols in the file
            symbol_query = """
                MATCH (f:File {id: $fid})-[:CONTAINS]->(s)
                WHERE s.name IS NOT NULL
                  AND (s:Function OR s:Method OR s:Class OR s:Struct OR s:Trait
                       OR s:Enum OR s:Protocol OR s:Extension OR s:TypeAlias OR s:AssociatedType)
                RETURN s.name AS name
                LIMIT 10
            """

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await _execute_read(
                    session,
                    symbol_query,
                    fid=file_id,
                    op="get_related_files_symbols",
                )
                symbols = [rec["name"] for rec in records if rec.get("name")]

            symbols = [s for s in symbols if isinstance(s, str) and s.strip()]
            if not symbols:
                return "No structurally related files found."

            memory_store, _, _, _, _ = get_memory_modules()
            await memory_store.open_pool()
            async with memory_store._pg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    ors = " OR ".join(["content ILIKE %s"] * len(symbols))
                    sql = (
                        "SELECT file_path, count(*) AS hits "
                        "FROM codebase_embeddings "
                        "WHERE project_id = %s AND file_path <> %s AND (" + ors + ") "
                        "GROUP BY file_path "
                        "ORDER BY hits DESC "
                        "LIMIT 10"
                    )
                    params = [project_id, file_path] + [f"%{s}%" for s in symbols]
                    await cur.execute(sql, params)
                    rows = await cur.fetchall()

            if not rows:
                return "No structurally related files found."

            output = ["Related Files (semantic co-mentions):"]
            for fp, hits in rows:
                output.append(f"- {fp} (Mentions: {hits})")
            return "\n".join(output)
        except Exception as e:
            return f"Error finding related files: {str(e)}"

    @mcp.tool()
    async def find_references(workspace_id: str | list[str], symbol_name: str) -> str:
        """
        Find all locations that reference a symbol — function calls, type usages,
        and any code chunk that mentions the name.

        Combines two sources:
        1. Neo4j [:CALLS|CALLS_INFERRED] edges (precise + inferred call graph hits)
        2. Postgres full-text search over codebase_embeddings (catches type references,
           field accesses, generic bounds, and string literals that the graph misses)

        Use this before renaming or deleting a symbol to find every location that
        must be updated.

        Args:
            workspace_id:  Logical workspace name or absolute project path (or list).
            symbol_name:  Exact name of the symbol to find references for.
        """
        return await references.find_references_impl(workspace_id, symbol_name)

    @mcp.tool()
    async def describe_file(project_path: str, file_path: str) -> str:
        """
        Generate a structural and semantic description of a single file.
        Returns its symbol list (functions, classes, structs) and a preview
        of its most representative semantic chunk. Much faster than reading the
        raw file for orientation.

        Replaces get_file_outline — works in two modes:
        1. Fast outline (no index required):
               describe_file("", "/abs/path/to/file.swift")
               describe_file("", "relative/path.swift")   ← relative to cwd
        2. Full description (ts-pack + Neo4j + Postgres semantic preview):
               describe_file("/project/root", "relative/path.swift")

        Args:
            project_path: Absolute path to project root, or "" for abs-path-only mode.
            file_path:    Relative path within project, or absolute path when project_path="".
        """
        return await file_describe.describe_file_impl(
            project_path=project_path,
            file_path=file_path,
            execute_read=_execute_read,
        )

    @mcp.tool()
    async def visualize_subgraph(workspace_id: str, symbol_name: str) -> str:
        """
        Generate a Mermaid diagram of a symbol's neighborhood in the structural graph.
        Shows the symbol's containing file, what it CALLS, what IMPORTS it, and
        sibling symbols in the same file — up to 2 hops.

        Args:
            workspace_id: Logical workspace name or absolute project path.
            symbol_name: Name of the symbol to visualize.
        """
        try:
            project_id = get_project_id(workspace_id)
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                focus_nodes = await _execute_read(
                    session,
                    symbol_graph.VISUALIZE_SUBGRAPH_FOCUS_CYPHER,
                    name=symbol_name,
                    pid=project_id,
                    op="visualize_subgraph_focus",
                )
                if not focus_nodes:
                    return f"No symbol named '{symbol_name}' found in this project."
                focus = focus_nodes[0]
                focus_id = focus["id"]

                nbr_rows = await _execute_read(
                    session,
                    symbol_graph.VISUALIZE_SUBGRAPH_NEIGHBORS_CYPHER,
                    fid=focus_id,
                    op="visualize_subgraph_neighbors",
                )
                nbr = dict(nbr_rows[0]) if nbr_rows else {}
            rendered = symbol_graph.render_subgraph_mermaid(focus, nbr)
            if not rendered:
                return f"No relationships found for '{symbol_name}'."
            return rendered
        except Exception as e:
            return f"Error visualizing subgraph: {str(e)}"
