"""tools/code_intel.py — code intelligence tools (symbol analysis, visualization, file description)."""

import hashlib
import time
import os
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_symbol_context(project_path: str, symbol_name: str) -> str:
        """
        Single-call deep dive into a symbol: definition location, signature,
        what it calls, what calls it, and the actual source code chunk.

        Replaces the common 4-step pattern of:
          get_code_summary → find_callers → find_definitions → search_codebase

        Args:
            project_path: Absolute path to the project root.
            symbol_name:  Name of the function, class, or struct to inspect.
        """
        try:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            import graph_bootstrap

            _, _, _, _, proxy = get_memory_modules()

            _, _, _, _, proxy = get_memory_modules()

            _, _, _, _, proxy = get_memory_modules()

            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                r = await session.run(
                    """
                    MATCH (s {name: $name, project_id: $pid})
                    WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum OR s:EnumCase OR s:Method
                    OPTIONAL MATCH (s)<-[:CONTAINS]-(parent:File)
                    OPTIONAL MATCH (caller)-[:CALLS]->(s)
                    OPTIONAL MATCH (s)-[:CALLS]->(callee)
                    RETURN
                      labels(s)[0]  AS kind,
                      s.filepath    AS filepath,
                      s.start_line  AS start_line,
                      s.end_line    AS end_line,
                      s.signature   AS signature,
                      parent.filepath AS parent_file,
                      collect(DISTINCT {name: caller.name, file: caller.filepath,
                                        line: caller.start_line})[..10] AS callers,
                      collect(DISTINCT {name: callee.name, file: callee.filepath})[..10] AS callees
                    LIMIT 1
                """,
                    name=symbol_name,
                    pid=project_id,
                )
                rec = await r.single()

            if not rec:
                return f"Symbol '{symbol_name}' not found. Run index_workspace() first."

            out = [
                f"## `{symbol_name}` ({rec['kind']})",
                f"**File:** `{rec['filepath']}`  Lines {rec['start_line']}–{rec['end_line']}",
            ]
            if rec["signature"]:
                out.append(f"**Signature:** `{rec['signature']}`\n")

            callers = [c for c in (rec["callers"] or []) if c.get("name")]
            callees = [c for c in (rec["callees"] or []) if c.get("name")]

            if callers:
                out.append(f"**Called by** ({len(callers)}):")
                for c in callers:
                    line = f":{c['line']}" if c.get("line") else ""
                    out.append(f"  - `{c['name']}`{line}  in {c.get('file', '?')}")
            if callees:
                out.append(f"\n**Calls** ({len(callees)}):")
                for c in callees:
                    out.append(f"  - `{c['name']}`  in {c.get('file', '?')}")

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
                pass

            return "\n".join(out)
        except Exception as e:
            return f"Error getting symbol context: {str(e)}"

    @mcp.tool()
    async def get_call_chain(
        project_path: str, symbol_name: str, depth: int = 3, direction: str = "down"
    ) -> str:
        """
        Trace a call chain N hops deep from a starting symbol.

        Unlike get_symbol_context (single hop), this recursively follows
        CALLS edges to build a full call tree — ideal for understanding
        execution paths and gRPC handler flows.

        Args:
            project_path: Absolute path to the project root.
            symbol_name:  Starting symbol name.
            depth:        How many hops to follow (default 3, max 5).
            direction:    'down' (what this calls) or 'up' (what calls this).
        """
        try:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            depth = min(int(depth), 5)
            import graph_bootstrap

            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()

            resolve_cypher = """
                MATCH (s {project_id: $pid})
                WHERE s:Function OR s:Method OR s:Class OR s:Struct OR s:Trait OR s:Enum
                OPTIONAL MATCH (s)<-[:CALLS]-(caller)
                WITH s,
                     CASE
                       WHEN s.name = $name THEN 0
                       WHEN s.name ENDS WITH ('.' + $name) THEN 1
                       WHEN s.name STARTS WITH ($name + '(') THEN 2
                       WHEN s.name CONTAINS ('.' + $name + '(') THEN 3
                       WHEN s.signature IS NOT NULL AND s.signature CONTAINS $name THEN 4
                       ELSE 99
                     END AS rank,
                     count(DISTINCT caller) AS callers_in
                WHERE rank < 99
                RETURN elementId(s) AS eid, s.name AS name, s.filepath AS filepath, rank
                ORDER BY rank ASC, callers_in DESC, size(s.name) ASC
                LIMIT 5
            """

            resolved_name = symbol_name
            resolved_eid = None

            if direction == "up":
                hop_label = "caller"
                cypher = (
                    f"MATCH (start) WHERE elementId(start) = $eid "
                    f"MATCH path = (start)"
                    f"<-[:CALLS*1..{depth}]-(hop)"
                    " WHERE (hop:Function OR hop:Method OR hop:Class OR hop:Struct OR hop:Trait OR hop:Enum)"
                    " RETURN [n IN nodes(path) | n.name] AS chain,"
                    "        [n IN nodes(path) | n.filepath] AS files"
                    " LIMIT 40"
                )
            else:
                hop_label = "callee"
                cypher = (
                    f"MATCH (start) WHERE elementId(start) = $eid "
                    f"MATCH path = (start)"
                    f"-[:CALLS*1..{depth}]->(hop)"
                    " WHERE (hop:Function OR hop:Method OR hop:Class OR hop:Struct OR hop:Trait OR hop:Enum)"
                    " RETURN [n IN nodes(path) | n.name] AS chain,"
                    "        [n IN nodes(path) | n.filepath] AS files"
                    " LIMIT 40"
                )

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                res = await session.run(
                    resolve_cypher, name=symbol_name, pid=project_id
                )
                candidates = [rec async for rec in res]
                if not candidates:
                    return (
                        f"`{symbol_name}` not found or no {hop_label}s within {depth} hops.\n"
                        "Try a fully qualified name like `Type.method` or include argument labels."
                    )

                resolved_eid = candidates[0]["eid"]
                resolved_name = candidates[0]["name"] or symbol_name

                result = await session.run(cypher, eid=resolved_eid)
                rows = [rec async for rec in result]

            if not rows:
                return (
                    f"`{resolved_name}` resolved but no {hop_label}s within {depth} hops.\n"
                    "Make sure the project is indexed and Swift CALLS edges are available."
                )

            # Build tree from chain paths — deduplicate and indent by depth
            seen: set[str] = set()
            header_name = resolved_name or symbol_name
            out = [f"## Call chain: `{header_name}` ({direction}, depth={depth})\n"]
            if resolved_name and resolved_name != symbol_name:
                out.append(f"Resolved `{symbol_name}` → `{resolved_name}`\n")
            for rec in rows:
                chain = rec["chain"]
                files = rec["files"]
                for i in range(1, len(chain)):
                    key = "→".join(chain[: i + 1])
                    if key in seen:
                        continue
                    seen.add(key)
                    pad = "  " * i
                    name = chain[i]
                    fp = (files[i] or "").split("/")[-1] if files[i] else "?"
                    out.append(f"{pad}{'└─' if i > 1 else '  '} `{name}`  ({fp})")

            return "\n".join(out)
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
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            import graph_bootstrap

            _, _, _, _, proxy = get_memory_modules()

            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()

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
                result = await session.run(
                    cypher,
                    pid=project_id,
                    q=q,
                    kinds=kind_list,
                    limit=min(int(limit), 200),
                    type_kinds=type_kinds,
                    callable_kinds=callable_kinds,
                    timeout=float(os.getenv("LM_PROXY_NEO4J_READ_TIMEOUT", "3.0")),
                )
                rows = [rec async for rec in result]

            proxy.debug_log(
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
            try:
                _, _, _, _, proxy = get_memory_modules()
                proxy.debug_log("list_symbol_matches_error", error=str(e))
            except Exception:
                pass
            return f"Error listing symbols: {str(e)}"

    @mcp.tool()
    async def diagnose_symbol_query(
        project_path: str, query: str, kind: str = "Enum"
    ) -> str:
        """
        Diagnostic path for symbol query latency.

        Args:
            project_path: Absolute path to the project root.
            query:        Substring to match against symbol name/signature.
            kind:         Label to filter (default: Enum).
        """
        try:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            import graph_bootstrap

            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()

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
                result = await session.run(
                    cypher,
                    pid=project_id,
                    q=q,
                    kind=kind,
                    timeout=float(os.getenv("LM_PROXY_NEO4J_READ_TIMEOUT", "3.0")),
                )
                rec = await result.single()

            elapsed_ms = int((time.perf_counter() - start) * 1000)
            return (
                f"diagnose_symbol_query: kind={kind} query='{q}' "
                f"rows={rec['n'] if rec else 0} elapsed_ms={elapsed_ms}"
            )
        except Exception as e:
            return f"Error diagnosing symbol query: {str(e)}"

    @mcp.tool()
    async def get_code_importance(project_path: str) -> str:
        """
        Identify the most important files in a project using GDS PageRank on the
        CALLS graph. Files whose symbols are called by many important callers rank
        highest — this is architecturally meaningful, not just symbol density.

        Falls back to a heuristic (callers×3 + symbols) for projects that have not
        yet been indexed with the PageRank pass.

        Excludes test files automatically.

        Args:
            project_path: Absolute path to the project root.
        """
        try:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            import graph_bootstrap

            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()

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
            OPTIONAL MATCH (caller:File {project_id: $pid})-[:CALLS]->(cs)<-[:CONTAINS]-(f)
              WHERE caller <> f
            WITH f, sym_count, sym_examples, count(DISTINCT caller) AS callers_in
            WITH f.filepath AS file, sym_count, sym_examples,
                 callers_in * 3 + sym_count AS score, NULL AS top_pagerank
            WHERE score > 0
            ORDER BY score DESC LIMIT 15
            RETURN file, sym_count, sym_examples, top_pagerank, score
            """

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                result = await session.run(cypher_pr, pid=project_id)
                records = [r async for r in result]
                using_pagerank = bool(records)

                if not using_pagerank:
                    result = await session.run(cypher_fallback, pid=project_id)
                    records = [r async for r in result]

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
    async def get_code_communities(project_path: str) -> str:
        """
        Identify architectural clusters in the codebase by grouping files by their
        top-level directory. Shows the structure of the project at a glance.
        For each cluster, lists its most symbol-rich files.

        Run build_import_graph() first to enable real PageRank and
        Louvain community detection via get_code_importance and
        get_code_communities.

        Args:
            project_path: Absolute path to the project root.
        """
        try:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            import graph_bootstrap

            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()

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
                result = await session.run(cypher_louvain, pid=project_id)
                records = [r async for r in result]
                using_louvain = bool(records)

                if not using_louvain:
                    result = await session.run(cypher_dir, pid=project_id)
                    records = [r async for r in result]

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
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
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

            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                result = await session.run(cypher, fid=file_id, pid=project_id)
                related = []
                async for record in result:
                    related.append(
                        f"- {record['related_file']} (Strength: {record['shared_imports']})"
                    )
            if not related:
                return "No structurally related files found."
            return "Related Files:\n" + "\n".join(related)
        except Exception as e:
            return f"Error finding related files: {str(e)}"

    @mcp.tool()
    async def find_references(project_path: str | list[str], symbol_name: str) -> str:
        """
        Find all locations that reference a symbol — function calls, type usages,
        and any code chunk that mentions the name.

        Combines two sources:
        1. Neo4j [:CALLS] edges (precise call-site graph hits)
        2. Postgres full-text search over codebase_embeddings (catches type references,
           field accesses, generic bounds, and string literals that the graph misses)

        Use this before renaming or deleting a symbol to find every location that
        must be updated.

        Args:
            project_path: Absolute path to the project root (or list of paths).
            symbol_name:  Exact name of the symbol to find references for.
        """
        return await find_references_impl(project_path, symbol_name)

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
        import os as _os

        # Resolve absolute path
        if not project_path:
            abs_path = _os.path.abspath(file_path)
            file_path = abs_path  # use abs for display too
        else:
            abs_path = (
                _os.path.join(project_path, file_path)
                if not _os.path.isabs(file_path)
                else file_path
            )

        basename = _os.path.basename(abs_path)
        lines = [f"=== {file_path} ==="]

        # ── 1. ts-pack on-disk AST (works even for unindexed files) ──────────
        ts_symbols: list[str] = []
        try:
            import tree_sitter_language_pack as ts_pack

            if _os.path.exists(abs_path):
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                    code = fh.read()
                lang = ts_pack.detect_language(abs_path)
                if lang:
                    cfg = ts_pack.ProcessConfig(lang)
                    cfg.diagnostics = True
                    result = ts_pack.process(code, config=cfg)
                    error_count = (result.get("metrics") or {}).get("error_count", 0)
                    lang_label = f"  [{lang}]"
                    if error_count:
                        lang_label += f"  ⚠ {error_count} syntax error(s)"
                    lines.append(lang_label)

                    def _fmt(items: list, depth: int = 0) -> None:
                        pad = "  " * depth
                        for item in items:
                            name = item.get("name") or "?"
                            kind = item.get("kind") or ""
                            sig = item.get("signature") or ""
                            span = item.get("span") or {}
                            sl = (span.get("start_line") or 0) + 1
                            el = (span.get("end_line") or 0) + 1
                            loc = f"  L{sl}–{el}" if sl else ""
                            label = sig if sig else f"{kind} {name}"
                            ts_symbols.append(f"{pad}  {label}{loc}")
                            _fmt(item.get("children") or [], depth + 1)

                    _fmt(result.get("structure") or [])
        except Exception:
            pass

        # ── 2. Neo4j symbols (richer — includes signatures) ───────────────────
        # Skip if no project context
        use_syms = ts_symbols
        if project_path:
            try:
                project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
                rel_path = (
                    _os.path.relpath(abs_path, project_path)
                    if project_path
                    else file_path
                )
                file_id = f"{project_id}:file:{rel_path}"
                import graph_bootstrap

                await graph_bootstrap.init_graph_db()
                driver = graph_bootstrap.get_driver()
                sym_cypher = """
                MATCH (f:File {id: $fid})-[:CONTAINS]->(s)
                WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum OR s:Module
                RETURN labels(s)[0] AS kind, s.name AS name,
                       s.start_line AS start, s.end_line AS end,
                       s.signature AS sig
                ORDER BY s.start_line
                """
                neo_symbols: list[str] = []
                async with driver.session(
                    database=graph_bootstrap._NEO4J_DB
                ) as session:
                    result_q = await session.run(sym_cypher, fid=file_id)
                    async for rec in result_q:
                        loc = f":{rec['start']}-{rec['end']}" if rec["start"] else ""
                        sig = f"  →  {rec['sig']}" if rec["sig"] else ""
                        neo_symbols.append(f"  [{rec['kind']}] {rec['name']}{loc}{sig}")
                use_syms = neo_symbols or ts_symbols
            except Exception:
                use_syms = ts_symbols

        if use_syms:
            lines.append(f"Symbols ({len(use_syms)}):")
            lines.extend(use_syms[:40])
        else:
            lines.append("No symbols found.")

        # ── 3. Postgres semantic preview (only in full mode) ──────────────────
        if project_path:
            try:
                project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
                rel_path = _os.path.relpath(abs_path, project_path)
                memory_store, _, _, _, _ = get_memory_modules()
                await memory_store.open_pool()
                async with memory_store._pg_pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "SELECT content FROM codebase_embeddings "
                            "WHERE project_id = %s AND file_path = %s "
                            "ORDER BY chunk_index LIMIT 1",
                            (project_id, rel_path),
                        )
                        row = await cur.fetchone()
                    if row:
                        lines.append(f"\nFirst chunk preview:\n{row[0][:500].rstrip()}")
            except Exception:
                pass

        return "\n".join(lines)

        return "\n".join(lines)

    @mcp.tool()
    async def visualize_subgraph(project_path: str, symbol_name: str) -> str:
        """
        Generate a Mermaid diagram of a symbol's neighborhood in the structural graph.
        Shows the symbol's containing file, what it CALLS, what IMPORTS it, and
        sibling symbols in the same file — up to 2 hops.

        Args:
            project_path: Absolute path to the project root.
            symbol_name: Name of the symbol to visualize.
        """
        try:
            import re

            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            import graph_bootstrap

            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                focus_r = await session.run(
                    """
                    MATCH (n {name: $name, project_id: $pid})
                    WHERE n:Function OR n:Class OR n:Struct OR n:Enum OR n:Trait OR n:File
                    RETURN n.id AS id, labels(n)[0] AS kind, n.name AS name,
                           n.filepath AS fp, n.start_line AS sl
                    LIMIT 3
                """,
                    name=symbol_name,
                    pid=project_id,
                )
                focus_nodes = [dict(r) async for r in focus_r]
                if not focus_nodes:
                    return f"No symbol named '{symbol_name}' found in this project."
                focus = focus_nodes[0]
                focus_id = focus["id"]

                nbr_r = await session.run(
                    """
                    MATCH (n {id: $fid})
                     OPTIONAL MATCH (parent:File)-[:CONTAINS]->(n)
                     OPTIONAL MATCH (n)<-[:CALLS]-(caller)
                         WHERE caller:File OR caller:Function OR caller:Class OR caller:Method
                     OPTIONAL MATCH (n)<-[:IMPORTS]-(importer:File)
                    OPTIONAL MATCH (n)-[:CALLS]->(callee)
                        WHERE callee:Function OR callee:Class OR callee:Struct
                    RETURN
                      parent.id AS parent_id, parent.name AS parent_name, parent.filepath AS parent_fp,
                      collect(DISTINCT {id: caller.id, name: caller.name, fp: caller.filepath})[..6]  AS callers,
                      collect(DISTINCT {id: importer.id, name: importer.name, fp: importer.filepath})[..6] AS importers,
                      collect(DISTINCT {id: callee.id, name: callee.name, kind: labels(callee)[0],
                                        fp: callee.filepath})[..8] AS callees
                    LIMIT 1
                """,
                    fid=focus_id,
                )
                nbr = dict(await nbr_r.single() or {})

            node_counter = [0]
            node_map: dict = {}

            def mermaid_id(neo_id: str) -> str:
                if neo_id not in node_map:
                    node_counter[0] += 1
                    node_map[neo_id] = f"n{node_counter[0]}"
                return node_map[neo_id]

            def safe_label(text: str) -> str:
                return text.replace('"', "'")

            def short_fp(fp) -> str:
                if not fp:
                    return "?"
                parts = fp.split("/")
                return "/".join(parts[-2:]) if len(parts) > 1 else fp

            def node_shape(kind: str, mid: str, label: str) -> str:
                shapes = {
                    "Function": f'{mid}("{safe_label(label)}")',
                    "File": f'{mid}["{safe_label(label)}"]',
                    "Class": f'{mid}(("{safe_label(label)}"))',
                    "Struct": f'{mid}(("{safe_label(label)}"))',
                    "Enum": f'{mid}{{"{safe_label(label)}"}}',
                    "Trait": f'{mid}[/"{safe_label(label)}"/]',
                }
                return shapes.get(kind, f'{mid}["{safe_label(label)}"]')

            lines = ["graph LR"]
            lines += [
                "  classDef focus fill:#f4a261,stroke:#e76f51,color:#000",
                "  classDef file  fill:#264653,stroke:#2a9d8f,color:#fff",
                "  classDef func  fill:#2a9d8f,stroke:#264653,color:#fff",
                "  classDef cls   fill:#457b9d,stroke:#1d3557,color:#fff",
            ]
            fmid = mermaid_id(focus_id)
            focus_label = f"{focus['name']}\n({short_fp(focus.get('fp'))}:{focus.get('sl') or '?'})"
            lines.append(f"  {node_shape(focus['kind'], fmid, focus_label)}")
            lines.append(f"  class {fmid} focus")

            if nbr.get("parent_id"):
                pmid = mermaid_id(nbr["parent_id"])
                plabel = short_fp(nbr.get("parent_fp")) or nbr.get("parent_name", "?")
                lines.append(f"  {node_shape('File', pmid, plabel)}")
                lines.append(f"  class {pmid} file")
                lines.append(f"  {pmid} -->|contains| {fmid}")

            for callee in nbr.get("callees") or []:
                if not callee.get("id"):
                    continue
                cmid = mermaid_id(callee["id"])
                clabel = f"{callee['name']}\n({short_fp(callee.get('fp'))})"
                ckind = callee.get("kind", "Function")
                lines.append(f"  {node_shape(ckind, cmid, clabel)}")
                lines.append(
                    f"  class {cmid} {'func' if ckind == 'Function' else 'cls'}"
                )
                lines.append(f"  {fmid} -->|calls| {cmid}")

            for imp in nbr.get("importers") or []:
                if not imp.get("id"):
                    continue
                imid = mermaid_id(imp["id"])
                ilabel = short_fp(imp.get("fp")) or imp.get("name", "?")
                lines.append(f"  {node_shape('File', imid, ilabel)}")
                lines.append(f"  class {imid} file")
                lines.append(f"  {imid} -->|imports| {fmid}")

            for caller in nbr.get("callers") or []:
                if not caller.get("id") or caller.get("id") == nbr.get("parent_id"):
                    continue
                amid = mermaid_id(caller["id"])
                alabel = short_fp(caller.get("fp")) or caller.get("name", "?")
                lines.append(f"  {node_shape('File', amid, alabel)}")
                lines.append(f"  class {amid} file")
                lines.append(f"  {amid} -->|calls| {fmid}")

            if len(lines) <= 5:
                return f"No relationships found for '{symbol_name}'."
            return "```mermaid\n" + "\n".join(lines) + "\n```"
        except Exception as e:
            return f"Error visualizing subgraph: {str(e)}"


async def find_references_impl(project_path: str | list[str], symbol_name: str) -> str:
    """Implementation of find_references shared by tool and test runner."""
    import hashlib

    try:
        if isinstance(project_path, str):
            project_paths = [project_path]
        else:
            project_paths = project_path

        pids = [hashlib.md5(p.encode()).hexdigest()[:12] for p in project_paths]

        import graph_bootstrap

        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()

        # 1. Graph References (Neo4j)
        graph_refs = []
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            r = await session.run(
                """
                MATCH (target {name: $name})
                WHERE target.project_id IN $pids
                MATCH (caller:Node)-[:CALLS]->(target)
                MATCH (f:File)-[:CONTAINS]->(caller)
                RETURN f.filepath AS fp, caller.start_line AS sl, caller.name AS cn, target.project_id AS tpid
            """,
                name=symbol_name,
                pids=pids,
            )
            async for rec in r:
                graph_refs.append(
                    f"- {rec['fp']}:{rec['sl']} ({rec['cn']}) [Project: {rec['tpid']}]"
                )

        # 2. Semantic/Literal References (Postgres)
        semantic_refs = []
        from _helpers import get_memory_modules

        memory_store, _, _, _, _ = get_memory_modules()
        await memory_store.open_pool()
        async with memory_store._pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT file_path, metadata->>'start_line' as start_line, project_id, content
                    FROM codebase_embeddings
                    WHERE project_id = ANY(%s)
                      AND (content ILIKE %s OR content ~ %s)
                    LIMIT 30
                """,
                    (pids, f"%{symbol_name}%", f"\\b{symbol_name}\\b"),
                )
                async for row in cur:
                    fp, sl, pid, text = row
                    if any(fp in gr for gr in graph_refs):
                        continue
                    preview = text.strip().splitlines()[0][:80]
                    semantic_refs.append(
                        f"- {fp}:{sl} (semantic) [Project: {pid}]  >> {preview}..."
                    )

        res = []
        if graph_refs:
            res.append(
                f"### Functional References (Graph)\n"
                + "\n".join(sorted(list(set(graph_refs))))
            )
        if semantic_refs:
            res.append(
                f"### Mentions & Type Usages (Semantic)\n"
                + "\n".join(sorted(list(set(semantic_refs))))
            )

        if not res:
            return f"No references found for '{symbol_name}' in the specified projects."

        return "\n\n".join(res)
    except Exception as e:
        return f"Error finding references: {str(e)}"
