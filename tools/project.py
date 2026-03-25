"""tools/project.py — project health, overview, import graph, and usage guide tools."""
import hashlib
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_graph_usage_guide() -> str:
        """
        Returns a comprehensive guide on how to best use this GraphRAG MCP.
        Ideal for 'onboarding' new agents or refreshing search strategies.

        Covers importance metrics, community detection, health checks, and global memory.
        """
        return """
# GraphRAG MCP Usage Guide (Self-Documentation)

This MCP provides a unified structural and semantic interface for your codebases.
Architecture: **Postgres/pgvector** handles semantic chunk storage and RRF hybrid search;
**Neo4j** handles the structural graph (files, symbols, call relationships).

### Recommended Workflow for New Projects:
1. **Onboarding**: Call `index_workspace(project_path)` — returns immediately with a `job_id`.
   - Monitor progress: `get_index_status(job_id)` — shows phase status and recent log lines.
   - You can continue working while indexing runs in the background.
2. **Health + Overview**: Call `get_project_overview(project_path)` — health, architecture clusters, and key files in one shot.
3. **Initial Discovery**: Use `get_code_importance(project_path)` for PageRank-central files.
4. **Memory Recall**: Use `search_memory(session_id, query, global_search=True)` for cross-project context.
5. **Cross-Project Search**: Use `find_definitions(symbol_name)` to locate symbols across all projects.
6. **Modular Understanding**: Use `get_code_communities(project_path)` for Louvain-clustered module groups.
7. **Targeted Search**: Use `search_codebase(project_path, query)` — Postgres RRF hybrid (cosine + BM25).
8. **Deep Dive**: Use `get_symbol_context(project_path, symbol_name)` — definition + callers + callees + source.

### Background Indexing:
- `index_workspace(project_path)` → non-blocking, returns `job_id` in ~1s
- `get_index_status(job_id)` → poll for `RUNNING` / `DONE` / `FAILED` + last 20 log lines

### Documentation Research (llms.txt → Crawl4AI → Postgres):
- `research_documentation(topic, query)` → web search for relevant doc URLs (checks llms.txt)
- `download_documentation(urls, topic)` → non-blocking crawl + embed + index into `doc_embeddings`
- `search_documentation(query, topic?)` → RRF hybrid search over indexed docs with source URL

### Pro Tips:
- **Git State**: `git_summary(project_path)` → branch, status, recent commits at a glance.
- **Exact Text Search**: `grep_codebase(project_path, pattern)` → ripgrep with file:line context.
- **Symbol Deep Dive**: `get_symbol_context(project_path, symbol)` → replaces 4-step lookup pattern.
- **All References**: `find_references(project_path, symbol)` → graph hits + Postgres text scan.
- **Graph Visualization**: `visualize_subgraph(project_path, symbol_name)` → Mermaid relationship diagram.
- **Related Files**: `get_related_files(project_path, file_path)` → structural neighbors.
- **Raw Graph**: `query_graph(cypher)` for arbitrary Neo4j Cypher queries.
"""

    @mcp.tool()
    async def get_project_overview(project_path: str) -> str:
        """
        Single-call project onboarding summary. Combines health, architecture
        clusters, and most important files into one synthesized view.
        Use this as the FIRST tool when starting work on an unfamiliar codebase.

        Args:
            project_path: Absolute path to the project root.
        """
        try:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            import graph_bootstrap
            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                r = await session.run("MATCH (f:File {project_id:$p}) RETURN count(f) AS files", p=project_id)
                rec = await r.single()
                n_files = rec["files"] if rec else 0

                r2 = await session.run(
                    "MATCH (s {project_id:$p}) WHERE s:Function OR s:Class OR s:Struct "
                    "RETURN count(s) AS syms", p=project_id)
                rec2 = await r2.single()
                n_syms = rec2["syms"] if rec2 else 0

                r3 = await session.run("""
                    MATCH (f:File {project_id: $p})
                    WITH f, CASE WHEN f.filepath CONTAINS '/'
                         THEN split(f.filepath, '/')[0] ELSE '(root)' END AS top_dir
                    OPTIONAL MATCH (f)-[:CONTAINS]->(s)
                    WHERE s:Function OR s:Class OR s:Struct
                    WITH top_dir, count(s) AS syms, count(f) AS files
                    ORDER BY syms DESC LIMIT 6
                    RETURN top_dir, files, syms
                """, p=project_id)
                dirs = []
                async for rec in r3:
                    dirs.append(f"  📂 {rec['top_dir']}/  ({rec['files']} files, {rec['syms']} symbols)")

                r4 = await session.run("""
                    MATCH (f:File {project_id: $p})-[:CONTAINS]->(s)
                    WHERE (s:Function OR s:Class OR s:Struct)
                      AND NOT f.filepath CONTAINS 'test'
                      AND NOT f.filepath CONTAINS 'spec'
                    WITH f.filepath AS fp, count(s) AS n, collect(DISTINCT s.name)[..3] AS ex
                    ORDER BY n DESC LIMIT 5
                    RETURN fp, n, ex
                """, p=project_id)
                key_files = []
                async for rec in r4:
                    ex = ", ".join(e for e in rec["ex"] if e)
                    key_files.append(f"  - {rec['fp']}  ({rec['n']} symbols: {ex})")

                memory_store, _, _, _, _ = get_memory_modules()
                await memory_store.open_pool()
                async with memory_store._pg_pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "SELECT count(*) FROM codebase_embeddings WHERE project_id=%s",
                            (project_id,))
                        row = await cur.fetchone()
                    n_chunks = row[0] if row else 0

            lines = [
                f"# Project Overview: {project_path.split('/')[-1]}",
                "", "## Health",
                f"  Files indexed:   {n_files}",
                f"  Symbols:         {n_syms}",
                f"  Semantic chunks: {n_chunks}",
                "", "## Architecture (top-level directories by symbol density)",
            ] + dirs + [
                "", "## Key Files (most symbol-dense, non-test)",
            ] + key_files + [
                "", "## Next steps",
                f"  - search_codebase('{project_path}', '<your question>') — semantic search",
                f"  - describe_file('{project_path}', '<file_path>') — deep-dive a specific file",
                f"  - get_code_importance('{project_path}') — full ranked file list",
            ]
            return "\n".join(lines)
        except Exception as e:
            return f"Error generating project overview: {str(e)}"

    @mcp.tool()
    async def build_import_graph(project_path: str) -> str:
        """
        Build file-level IMPORTS edges in Neo4j by resolving Import nodes.

        Reads every Import node already in the graph, attempts to match the
        import source to a File node in the same project, and writes
        (source_file)-[:IMPORTS]->(target_file) relationships.

        Supports: Python (from X import Y / import X),
                  JavaScript/TypeScript (import from './X'),
                  Rust (use crate::X / mod X).

        Run this once after index_workspace to enable real PageRank and
        Louvain community detection via get_code_importance and
        get_code_communities.

        Args:
            project_path: Absolute path to the project root.
        """
        try:
            import re
            from pathlib import PurePosixPath
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            import graph_bootstrap
            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                r = await session.run("MATCH (f:File {project_id:$p}) RETURN f.filepath AS fp, f.id AS fid", p=project_id)
                files: dict[str, str] = {}
                async for rec in r:
                    files[rec["fp"]] = rec["fid"]

                stems: dict[str, list[str]] = {}
                for fp in files:
                    stem = PurePosixPath(fp).stem
                    stems.setdefault(stem, []).append(fp)

                r2 = await session.run(
                    "MATCH (f:File {project_id:$p})-[:CONTAINS]->(imp:Import) "
                    "RETURN f.id AS src_fid, f.filepath AS src_fp, imp.source AS src_text",
                    p=project_id)
                imports = []
                async for rec in r2:
                    imports.append((rec["src_fid"], rec["src_fp"], rec["src_text"] or ""))

            # Build Swift SPM module → file mapping
            # SPM convention: Libraries/ModuleName/Sources/**/*.swift  → module "ModuleName"
            swift_module_map: dict[str, list[str]] = {}
            for fp in files:
                parts = fp.replace("\\", "/").split("/")
                if "Sources" in parts:
                    src_idx = parts.index("Sources")
                    if src_idx > 0:
                        mod_name = parts[src_idx - 1]
                        swift_module_map.setdefault(mod_name, []).append(fp)

            def resolve(src_fp: str, src_text: str) -> str | None:
                ext = PurePosixPath(src_fp).suffix.lstrip(".")
                src_dir = str(PurePosixPath(src_fp).parent)
                if ext in ("py", ""):
                    m = re.match(r"from\s+([\w.]+)\s+import", src_text) or re.match(r"import\s+([\w.]+)", src_text)
                    if m:
                        mod = m.group(1).split(".")[0]
                        for fp in [f"{src_dir}/{mod}.py", f"{mod}.py",
                                   f"{src_dir}/{mod}/__init__.py", f"{mod}/__init__.py"]:
                            fp = fp.lstrip("./")
                            if fp in files:
                                return fp
                        if mod in stems and len(stems[mod]) == 1:
                            return stems[mod][0]
                elif ext in ("js", "ts", "jsx", "tsx", "mjs", "cjs"):
                    m = re.search(r"from\s+[\x27\x22]([^\x27\x22]+)[\x27\x22]", src_text)
                    if m:
                        imp = m.group(1)
                        if imp.startswith("."):
                            import posixpath
                            base = posixpath.normpath(posixpath.join(src_dir, imp)).lstrip('/')
                            for suf in ("", ".js", ".ts", ".jsx", ".tsx", "/index.js", "/index.ts"):
                                candidate = (base + suf).lstrip("/")
                                if candidate in files:
                                    return candidate
                elif ext == "rs":
                    m = re.match(r"(?:use|mod)\s+(?:crate::)?([\w:]+)", src_text)
                    if m:
                        mod = m.group(1).split("::")[0]
                        for fp in [f"{src_dir}/{mod}.rs", f"{src_dir}/{mod}/mod.rs",
                                   f"src/{mod}.rs", f"src/{mod}/mod.rs"]:
                            fp = fp.lstrip("./")
                            if fp in files:
                                return fp
                elif ext == "swift":
                    # Swift uses bare module imports: `import ModuleName`
                    # In SPM layout: Libraries/ModuleName/Sources/**/*.swift
                    # Resolve the module name to any file in that module's Sources dir.
                    m = re.match(r"import\s+(\w+)", src_text)
                    if m:
                        mod = m.group(1)
                        # Find files whose path contains /Sources/ and whose
                        # module directory (parent of Sources/) matches the name.
                        candidates = swift_module_map.get(mod, [])
                        if candidates:
                            # Prefer a file in the same sub-library if possible
                            return candidates[0]
                return None

            edges: list[tuple[str, str]] = []
            for src_fid, src_fp, src_text in imports:
                tgt_fp = resolve(src_fp, src_text)
                if tgt_fp and tgt_fp != src_fp:
                    edges.append((src_fid, files[tgt_fp]))

            # ── Swift on-disk supplement ──────────────────────────────────────
            # The Rust indexer doesn't emit Import nodes for most Swift source
            # files. Scan .swift files directly from disk instead.
            import os as _os
            SWIFT_IMPORT_RE = re.compile(
                r"""^(?:@testable\s+)?import\s+(\w+)""", re.MULTILINE
            )
            SYSTEM_MODS = frozenset({
                "Foundation", "Swift", "Dispatch", "Darwin", "Combine",
                "XCTest", "UIKit", "AppKit", "SwiftUI", "CoreFoundation",
                "ObjectiveC", "GRPCCore", "Logging", "NIO", "NIOSSL",
                "NIOCore", "Atomics", "Crypto",
            })
            swift_edges_added = 0
            for rel_fp, src_fid in list(files.items()):
                if not rel_fp.endswith(".swift"):
                    continue
                abs_fp = _os.path.join(project_path, rel_fp)
                try:
                    with open(abs_fp, "r", encoding="utf-8", errors="ignore") as fh:
                        source = fh.read()
                except OSError:
                    continue
                for m in SWIFT_IMPORT_RE.finditer(source):
                    mod = m.group(1)
                    if mod in SYSTEM_MODS:
                        continue
                    candidates = swift_module_map.get(mod, [])
                    for tgt_fp in candidates[:1]:   # representative file per module
                        if tgt_fp != rel_fp:
                            edges.append((src_fid, files[tgt_fp]))
                            swift_edges_added += 1


            if not edges:
                return "No resolvable imports found — IMPORTS graph not built."

            edges = list(set(edges))
            BATCH = 500
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                await session.run("MATCH (a:File {project_id:$p})-[r:IMPORTS]->() DELETE r", p=project_id)
                for i in range(0, len(edges), BATCH):
                    batch = [{"src": s, "tgt": t} for s, t in edges[i:i+BATCH]]
                    await session.run("""
                        UNWIND $batch AS edge
                        MATCH (a:File {id: edge.src})
                        MATCH (b:File {id: edge.tgt})
                        MERGE (a)-[:IMPORTS]->(b)
                    """, batch=batch)

            return (
                f"IMPORTS graph built for {project_path.split('/')[-1]}:\n"
                f"  {len(imports)} import statements scanned\n"
                f"  {len(edges)} resolved IMPORTS edges written to Neo4j\n"
                f"\nPageRank and community detection are now meaningful.\n"
                f"Re-run get_code_importance() and get_code_communities() for upgraded results."
            )
        except Exception as e:
            return f"Error building import graph: {str(e)}"
