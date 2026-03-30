"""tools/project.py — project health, overview, import graph, and usage guide tools."""

import os
import hashlib
import threading
import asyncio
import time
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules


_SYMBOL_GRAPH_LOCK = threading.Lock()

_GRAPH_WRITE_CONCURRENCY = max(
    1, int(os.getenv("LM_PROXY_GRAPH_WRITE_CONCURRENCY", "2"))
)
_NEO4J_READ_TIMEOUT_S = float(os.getenv("LM_PROXY_NEO4J_READ_TIMEOUT", "3.0"))
_NEO4J_WRITE_TIMEOUT_S = float(os.getenv("LM_PROXY_NEO4J_WRITE_TIMEOUT", "15.0"))
_NEO4J_GRAPH_BUILD_BATCH = max(50, int(os.getenv("LM_PROXY_GRAPH_BUILD_BATCH", "500")))

_WRITE_SEM = asyncio.Semaphore(_GRAPH_WRITE_CONCURRENCY)
_GRAPH_BUILD_QUEUE: asyncio.Queue | None = None
_GRAPH_BUILD_WORKER: asyncio.Task | None = None
_GRAPH_BUILD_LOCK = asyncio.Lock()

_METRICS_LOCK = threading.Lock()
_METRICS_MAX = 200
_METRICS: list[dict[str, object]] = []


def _debug_log(message: str, **fields: object) -> None:
    try:
        _, _, _, _, proxy = get_memory_modules()
        proxy.debug_log(message, **fields)
    except Exception:
        return


def _record_metric(event: str, **fields: object) -> None:
    with _METRICS_LOCK:
        _METRICS.append({"event": event, "ts": time.time(), **fields})
        if len(_METRICS) > _METRICS_MAX:
            del _METRICS[: len(_METRICS) - _METRICS_MAX]


def _summarize_batches(event: str, limit: int = 50) -> tuple[int, int, int]:
    with _METRICS_LOCK:
        recent = [m for m in _METRICS if m.get("event") == event][-limit:]
    if not recent:
        return 0, 0, 0
    durations = [int(m.get("elapsed_ms", 0) or 0) for m in recent]
    return len(durations), int(sum(durations) / len(durations)), max(durations)


def get_last_graph_build_metric() -> dict[str, object] | None:
    with _METRICS_LOCK:
        for entry in reversed(_METRICS):
            if entry.get("event") == "graph_build_done":
                return dict(entry)
    return None


async def _ensure_graph_build_worker() -> None:
    global _GRAPH_BUILD_QUEUE, _GRAPH_BUILD_WORKER
    async with _GRAPH_BUILD_LOCK:
        if _GRAPH_BUILD_QUEUE is None:
            _GRAPH_BUILD_QUEUE = asyncio.Queue()
        if _GRAPH_BUILD_WORKER is None or _GRAPH_BUILD_WORKER.done():
            _GRAPH_BUILD_WORKER = asyncio.create_task(_graph_build_worker())


async def enqueue_graph_build(
    project_path: str, run_imports: bool = True, run_symbols: bool = True
) -> None:
    await _ensure_graph_build_worker()
    assert _GRAPH_BUILD_QUEUE is not None
    await _GRAPH_BUILD_QUEUE.put((project_path, run_imports, run_symbols))


async def _graph_build_worker() -> None:
    assert _GRAPH_BUILD_QUEUE is not None
    while True:
        item = await _GRAPH_BUILD_QUEUE.get()
        if item is None:
            _GRAPH_BUILD_QUEUE.task_done()
            return
        project_path, run_imports, run_symbols = item
        _debug_log(
            "graph_build_start",
            project_path=project_path,
            imports=run_imports,
            symbols=run_symbols,
        )
        _record_metric(
            "graph_build_start",
            project_path=project_path,
            imports=run_imports,
            symbols=run_symbols,
        )
        start = time.perf_counter()
        try:
            if run_imports:
                await _build_import_graph_impl(project_path)
                await asyncio.sleep(0)
            if run_symbols:
                await _build_symbol_import_export_graph_impl(project_path)
        except Exception as exc:
            _debug_log("graph_build_error", project_path=project_path, error=str(exc))
            _record_metric(
                "graph_build_error", project_path=project_path, error=str(exc)
            )
        else:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            _debug_log(
                "graph_build_done",
                project_path=project_path,
                elapsed_ms=elapsed_ms,
            )
            _record_metric(
                "graph_build_done",
                project_path=project_path,
                elapsed_ms=elapsed_ms,
            )
        finally:
            _GRAPH_BUILD_QUEUE.task_done()


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_graph_usage_guide() -> str:
        """
        Returns a comprehensive guide on how to best use this GraphRAG MCP.
        Ideal for 'onboarding' new agents or refreshing search strategies.

        Covers importance metrics, community detection, health checks, and global memory.
        """
        return """
# GraphRAG MCP Tool Usage Guide

### Recommended Workflow:
1. `index_workspace(project_path)` → returns immediately with a `job_id`
2. `get_index_status(job_id)` → poll `RUNNING` / `DONE` / `FAILED` + recent logs
3. `get_project_overview(project_path)` → health, architecture clusters, key files
4. `get_code_importance(project_path)` → PageRank-ranked files (requires Neo4j GDS for PageRank)
5. `search_codebase([project_path], query, include_metadata=False, languages?, min_imports=0, min_symbols=0, require_diagnostics=False, require_context=False, include_paths?, exclude_paths?)` → semantic search + metadata filters
6. `get_symbol_context(project_path, symbol_name)` → definition + callers + callees + source

### Call Graph Traversal:
- `get_call_chain(project_path, symbol_name, depth=3, direction='down', file_path=None, signature=None)` → trace CALLS N hops
- `get_call_chain(project_path, symbol_name, depth=3, direction='up', file_path=None, signature=None)` → trace callers N hops

### File & Symbol Inspection (no indexing required):
- `describe_file("", "/abs/path/file.swift")` → fast outline
- `describe_file(project_path, "rel/path")` → symbols + semantic preview
- `list_symbol_matches(project_path, query, limit=30)` → name/signature substring matches (supports EnumCase)
- `extract_function_body(file_path, symbol_name)` → exact source by AST
- `extract_class_interface(file_path, class_name)` → public method signatures
- `find_symbol_usages(file_path, symbol_name)` → intra-file usages by AST
- `swift_doc_lookup(file_path, symbol_name)` → SourceKitten docs + location

### Documentation Tools:
- `research_and_index(topic, query, max_urls=5)` → search + crawl + index in one call
- `research_documentation(topic, query)` → web search for doc URLs
- `download_documentation(urls, topic)` → crawl + embed + index
- `search_documentation(query, topic?)` → RRF hybrid search over indexed docs
- `delete_documentation(topic, url_patterns?, allow_all=False, dry_run=False)` → delete doc chunks
- `list_documentation_sources(topic?, limit=20)` → list topics or domains
- `llms.txt` is validated; invalid links fall back to sitemap discovery

Doc indexing tips:
- Prefer llms.txt or direct markdown URLs for clean ingestion
- Keep topics narrow; avoid indexing test/demo topics you won't query
- `search_documentation` without a topic excludes sources containing test/tmp/scratch/demo/sample/experimental/staging/draft

### Graph & Search Utilities:
- `git_summary(project_path)` → branch + status + recent commits
- `get_changed_symbols(project_path, since='HEAD~1')` → which functions changed
- `grep_codebase(project_path, pattern)` → exact text search (ripgrep)
- `find_references(project_path, symbol_name)` → graph + text references
- `get_related_files(project_path, file_path)` → structural neighbors
- `visualize_subgraph(project_path, symbol_name)` → Mermaid subgraph
- `query_graph(cypher)` → raw Neo4j Cypher
- `trace_symbol_cross_project(symbol, source_project, target_project)` → cross-project trace
- `get_test_coverage_for(project_path, file_path)` → tests that cover a file
- `get_symbol_imports_summary(project_path, limit=20)` → summarize IMPORTS_SYMBOL edges
- `get_symbol_exports_summary(project_path, limit=20, include_paths?, exclude_paths?, symbol_prefix?)` → summarize EXPORTS_SYMBOL edges
- `get_language_pack_status()` → available vs manifest languages (auto-download status)

### Memory Tools:
- `search_memory(session_id, query, global_search=True)` → cross-project recall
- `list_memories(session_id, include_global=True)` → audit stored memories
- `add_memory(session_id, text)` → persist a durable note
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
                r = await session.run(
                    "MATCH (f:File {project_id:$p}) RETURN count(f) AS files",
                    p=project_id,
                )
                rec = await r.single()
                n_files = rec["files"] if rec else 0

                r2 = await session.run(
                    "MATCH (s {project_id:$p}) WHERE s:Function OR s:Class OR s:Struct "
                    "RETURN count(s) AS syms",
                    p=project_id,
                )
                rec2 = await r2.single()
                n_syms = rec2["syms"] if rec2 else 0

                r3 = await session.run(
                    """
                    MATCH (f:File {project_id: $p})
                    WITH f, CASE WHEN f.filepath CONTAINS '/'
                         THEN split(f.filepath, '/')[0] ELSE '(root)' END AS top_dir
                    OPTIONAL MATCH (f)-[:CONTAINS]->(s)
                    WHERE s:Function OR s:Class OR s:Struct
                    WITH top_dir, count(s) AS syms, count(f) AS files
                    ORDER BY syms DESC LIMIT 6
                    RETURN top_dir, files, syms
                """,
                    p=project_id,
                )
                dirs = []
                async for rec in r3:
                    dirs.append(
                        f"  📂 {rec['top_dir']}/  ({rec['files']} files, {rec['syms']} symbols)"
                    )

                r4 = await session.run(
                    """
                    MATCH (f:File {project_id: $p})-[:CONTAINS]->(s)
                    WHERE (s:Function OR s:Class OR s:Struct)
                      AND NOT f.filepath CONTAINS 'test'
                      AND NOT f.filepath CONTAINS 'spec'
                    WITH f.filepath AS fp, count(s) AS n, collect(DISTINCT s.name)[..3] AS ex
                    ORDER BY n DESC LIMIT 5
                    RETURN fp, n, ex
                """,
                    p=project_id,
                )
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
                            (project_id,),
                        )
                        row = await cur.fetchone()
                    n_chunks = row[0] if row else 0

            lines = (
                [
                    f"# Project Overview: {project_path.split('/')[-1]}",
                    "",
                    "## Health",
                    f"  Files indexed:   {n_files}",
                    f"  Symbols:         {n_syms}",
                    f"  Semantic chunks: {n_chunks}",
                    "",
                    "## Architecture (top-level directories by symbol density)",
                ]
                + dirs
                + [
                    "",
                    "## Key Files (most symbol-dense, non-test)",
                ]
                + key_files
                + [
                    "",
                    "## Next steps",
                    f"  - search_codebase('{project_path}', '<your question>') — semantic search",
                    f"  - describe_file('{project_path}', '<file_path>') — deep-dive a specific file",
                    f"  - get_code_importance('{project_path}') — full ranked file list",
                ]
            )
            return "\n".join(lines)
        except Exception as e:
            return f"Error generating project overview: {str(e)}"

    @mcp.tool()
    async def get_graph_build_metrics(limit: int = 50) -> str:
        """
        Summarize recent graph-build batch timings and last build duration.

        Args:
            limit: Number of recent batches to summarize (default 50).
        """
        limit = max(1, min(int(limit), 200))
        ib_count, ib_avg, ib_max = _summarize_batches("import_graph_batch", limit)
        si_count, si_avg, si_max = _summarize_batches("symbol_import_batch", limit)
        se_count, se_avg, se_max = _summarize_batches("symbol_export_batch", limit)

        with _METRICS_LOCK:
            last_build = next(
                (m for m in reversed(_METRICS) if m.get("event") == "graph_build_done"),
                None,
            )
            recent = _METRICS[-10:]

        lines = ["# Graph build metrics"]
        if last_build:
            lines.append(
                "Last build: "
                f"project={last_build.get('project_path')} "
                f"elapsed_ms={last_build.get('elapsed_ms')}"
            )
        else:
            lines.append("Last build: none")

        lines.append(
            f"import_graph_batch: count={ib_count} avg_ms={ib_avg} max_ms={ib_max}"
        )
        lines.append(
            f"symbol_import_batch: count={si_count} avg_ms={si_avg} max_ms={si_max}"
        )
        lines.append(
            f"symbol_export_batch: count={se_count} avg_ms={se_avg} max_ms={se_max}"
        )

        lines.append("Recent events:")
        for entry in recent:
            evt = entry.get("event")
            elapsed = entry.get("elapsed_ms")
            proj = entry.get("project_path") or entry.get("project_id")
            if elapsed is not None:
                lines.append(f"- {evt} {proj} elapsed_ms={elapsed}")
            else:
                lines.append(f"- {evt} {proj}")

        return "\n".join(lines)

    @mcp.tool()
    async def get_language_pack_status() -> str:
        """
        Show available vs manifest languages for tree-sitter-language-pack.
        """
        try:
            import tree_sitter_language_pack as ts_pack

            auto_dl = os.getenv("LM_PROXY_TS_PACK_AUTO_DOWNLOAD", "1")
            cache_dir = os.getenv("LM_PROXY_TS_PACK_CACHE_DIR")
            if cache_dir:
                try:
                    ts_pack.init({"cache_dir": cache_dir})
                except Exception:
                    pass
            available = sorted(ts_pack.available_languages())
            try:
                manifest = sorted(ts_pack.manifest_languages())
            except Exception:
                manifest = []

            missing = [lang for lang in manifest if lang not in available]
            lines = ["# Language pack status"]
            lines.append(f"Auto-download: {auto_dl}")
            if cache_dir:
                lines.append(f"Cache dir: {cache_dir}")
            lines.append(f"Available languages: {len(available)}")
            lines.append(f"Manifest languages: {len(manifest)}")
            if missing:
                lines.append(f"Missing languages: {len(missing)}")
                lines.append("Missing sample: " + ", ".join(missing[:20]))
            else:
                lines.append("Missing languages: none")
            return "\n".join(lines)
        except Exception as e:
            return f"Error reading language pack status: {str(e)}"

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
        return await _build_import_graph_impl(project_path)

    @mcp.tool()
    async def get_symbol_imports_summary(project_path: str, limit: int = 20) -> str:
        """
        Summarize symbol-level import edges (IMPORTS_SYMBOL) for a project.

        Args:
            project_path: Absolute path to the project root.
            limit: Max rows to return per section (default 20).
        """
        try:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            import graph_bootstrap

            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()

            limit = max(1, min(int(limit), 100))

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                r1 = await session.run(
                    """
                    MATCH (f:File {project_id:$p})-[:IMPORTS_SYMBOL]->(s)
                    RETURN s.name AS symbol, count(*) AS n
                    ORDER BY n DESC
                    LIMIT $limit
                    """,
                    p=project_id,
                    limit=limit,
                )
                top_symbols = []
                async for rec in r1:
                    top_symbols.append((rec["symbol"], rec["n"]))

                r2 = await session.run(
                    """
                    MATCH (f:File {project_id:$p})-[:IMPORTS_SYMBOL]->(s)
                    WITH f.filepath AS file, count(*) AS n, collect(DISTINCT s.name) AS symbols
                    ORDER BY n DESC
                    LIMIT $limit
                    RETURN file, n, symbols
                    """,
                    p=project_id,
                    limit=limit,
                )
                top_files = []
                async for rec in r2:
                    top_files.append((rec["file"], rec["n"], rec["symbols"]))

            if not top_symbols and not top_files:
                return "No IMPORTS_SYMBOL edges found."

            lines = [f"# Symbol import summary: {project_path.split('/')[-1]}", ""]
            if top_symbols:
                lines.append("## Top imported symbols")
                for name, n in top_symbols:
                    lines.append(f"- {name}  ({n})")
                lines.append("")
            if top_files:
                lines.append("## Files with most symbol imports")
                for file, n, symbols in top_files[:limit]:
                    sample = ", ".join(symbols[:6])
                    lines.append(f"- {file}  ({n})  [{sample}]")
            return "\n".join(lines)
        except Exception as e:
            return f"Error summarizing symbol imports: {str(e)}"

    @mcp.tool()
    async def get_symbol_exports_summary(
        project_path: str,
        limit: int = 20,
        include_paths: list | None = None,
        exclude_paths: list | None = None,
        symbol_prefix: str = "",
    ) -> str:
        """
        Summarize symbol-level export edges (EXPORTS_SYMBOL) for a project.

        Args:
            project_path: Absolute path to the project root.
            limit: Max rows to return per section (default 20).
            include_paths: Optional list of glob patterns to include (file_path).
            exclude_paths: Optional list of glob patterns to exclude (file_path).
            symbol_prefix: Optional prefix to filter exported symbols.
        """
        try:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            import graph_bootstrap
            import fnmatch

            await graph_bootstrap.init_graph_db()
            driver = graph_bootstrap.get_driver()

            limit = max(1, min(int(limit), 100))

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                r1 = await session.run(
                    """
                    MATCH (f:File {project_id:$p})-[:EXPORTS_SYMBOL]->(s)
                    RETURN s.name AS symbol, count(*) AS n
                    ORDER BY n DESC
                    LIMIT $limit
                    """,
                    p=project_id,
                    limit=limit,
                )
                top_symbols = []
                async for rec in r1:
                    name = rec["symbol"]
                    if (
                        symbol_prefix
                        and isinstance(name, str)
                        and not name.startswith(symbol_prefix)
                    ):
                        continue
                    top_symbols.append((name, rec["n"]))

                fetch_limit = limit
                if include_paths or exclude_paths or symbol_prefix:
                    fetch_limit = min(limit * 10, 200)

                r2 = await session.run(
                    """
                    MATCH (f:File {project_id:$p})-[:EXPORTS_SYMBOL]->(s)
                    WITH f.filepath AS file, count(*) AS n, collect(DISTINCT s.name) AS symbols
                    ORDER BY n DESC
                    LIMIT $limit
                    RETURN file, n, symbols
                    """,
                    p=project_id,
                    limit=fetch_limit,
                )
                top_files = []
                async for rec in r2:
                    file = rec["file"]
                    if include_paths:
                        if not any(fnmatch.fnmatch(file, pat) for pat in include_paths):
                            continue
                    if exclude_paths:
                        if any(fnmatch.fnmatch(file, pat) for pat in exclude_paths):
                            continue
                    symbols = rec["symbols"]
                    if symbol_prefix:
                        symbols = [
                            s
                            for s in symbols
                            if isinstance(s, str) and s.startswith(symbol_prefix)
                        ]
                        if not symbols:
                            continue
                    top_files.append((file, rec["n"], symbols))

            if not top_symbols and not top_files:
                return "No EXPORTS_SYMBOL edges found."

            lines = [f"# Symbol export summary: {project_path.split('/')[-1]}", ""]
            if top_symbols:
                lines.append("## Top exported symbols")
                for name, n in top_symbols:
                    lines.append(f"- {name}  ({n})")
                lines.append("")
            if top_files:
                lines.append("## Files with most symbol exports")
                for file, n, symbols in top_files:
                    sample = ", ".join(symbols[:6])
                    lines.append(f"- {file}  ({n})  [{sample}]")
            return "\n".join(lines)
        except Exception as e:
            return f"Error summarizing symbol exports: {str(e)}"


async def _build_import_graph_impl(project_path: str) -> str:
    """Module-level implementation callable from _jobs.py post-index hook."""
    try:
        _debug_log("import_graph_start", project_path=project_path)
        start = time.perf_counter()
        import re
        from pathlib import PurePosixPath
        import asyncio

        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        import graph_bootstrap

        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            r = await session.run(
                "MATCH (f:File {project_id:$p}) RETURN f.filepath AS fp, f.id AS fid",
                p=project_id,
                timeout=_NEO4J_READ_TIMEOUT_S,
            )
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
                p=project_id,
                timeout=_NEO4J_READ_TIMEOUT_S,
            )
            imports = []
            async for rec in r2:
                imports.append((rec["src_fid"], rec["src_fp"], rec["src_text"] or ""))

        # Build Swift SPM module → file mapping
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
                m = re.match(r"from\s+([\w.]+)\s+import", src_text) or re.match(
                    r"import\s+([\w.]+)", src_text
                )
                if m:
                    mod = m.group(1).split(".")[0]
                    for fp in [
                        f"{src_dir}/{mod}.py",
                        f"{mod}.py",
                        f"{src_dir}/{mod}/__init__.py",
                        f"{mod}/__init__.py",
                    ]:
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

                        base = posixpath.normpath(posixpath.join(src_dir, imp)).lstrip(
                            "/"
                        )
                        for suf in (
                            "",
                            ".js",
                            ".ts",
                            ".jsx",
                            ".tsx",
                            "/index.js",
                            "/index.ts",
                        ):
                            candidate = (base + suf).lstrip("/")
                            if candidate in files:
                                return candidate
            elif ext == "rs":
                m = re.match(r"(?:use|mod)\s+(?:crate::)?([\w:]+)", src_text)
                if m:
                    mod = m.group(1).split("::")[0]
                    for fp in [
                        f"{src_dir}/{mod}.rs",
                        f"{src_dir}/{mod}/mod.rs",
                        f"src/{mod}.rs",
                        f"src/{mod}/mod.rs",
                    ]:
                        fp = fp.lstrip("./")
                        if fp in files:
                            return fp
            elif ext == "swift":
                m = re.match(r"import\s+(\w+)", src_text)
                if m:
                    mod = m.group(1)
                    candidates = swift_module_map.get(mod, [])
                    if candidates:
                        return candidates[0]
            return None

        edges: list[tuple[str, str]] = []
        for src_fid, src_fp, src_text in imports:
            tgt_fp = resolve(src_fp, src_text)
            if tgt_fp and tgt_fp != src_fp:
                edges.append((src_fid, files[tgt_fp]))

        # Swift on-disk supplement
        SWIFT_IMPORT_RE = re.compile(
            r"""^(?:@testable\s+)?import\s+(\w+)""", re.MULTILINE
        )
        SYSTEM_MODS = frozenset(
            {
                "Foundation",
                "Swift",
                "Dispatch",
                "Darwin",
                "Combine",
                "UIKit",
                "AppKit",
                "SwiftUI",
                "XCTest",
                "os",
                "simd",
                "CoreFoundation",
                "ObjectiveC",
                "CoreGraphics",
                "QuartzCore",
                "Metal",
                "MetalKit",
                "MetalPerformanceShaders",
                "Accelerate",
                "CoreML",
                "CreateML",
                "Vision",
                "NaturalLanguage",
                "AVFoundation",
                "CoreVideo",
                "CoreImage",
                "CoreData",
                "SystemConfiguration",
                "Network",
                "Logging",
                "NIO",
                "NIOHTTP1",
                "NIOHTTP2",
                "NIOSSL",
                "GRPC",
                "SwiftProtobuf",
                "Atomics",
                "ConcurrencyExtras",
                "ArgumentParser",
            }
        )
        swift_files = {fp: fid for fp, fid in files.items() if fp.endswith(".swift")}

        def _scan_swift_import_edges() -> list[tuple[str, str]]:
            swift_edges: list[tuple[str, str]] = []
            for src_fp, src_fid in swift_files.items():
                abs_src = (
                    os.path.join(project_path, src_fp)
                    if not os.path.isabs(src_fp)
                    else src_fp
                )
                try:
                    content = open(
                        abs_src, "r", encoding="utf-8", errors="ignore"
                    ).read()
                except OSError:
                    continue
                for m in SWIFT_IMPORT_RE.finditer(content):
                    mod = m.group(1)
                    if mod in SYSTEM_MODS:
                        continue
                    candidates = swift_module_map.get(mod, [])
                    for tgt_fp in candidates:
                        if tgt_fp != src_fp and tgt_fp in files:
                            swift_edges.append((src_fid, files[tgt_fp]))
            return swift_edges

        if swift_files:
            edges.extend(await asyncio.to_thread(_scan_swift_import_edges))

        if not edges:
            return "No resolvable imports found — IMPORTS graph not built."

        edges = list(set(edges))
        BATCH = _NEO4J_GRAPH_BUILD_BATCH
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await session.run(
                "MATCH (a:File {project_id:$p})-[r:IMPORTS]->() DELETE r",
                p=project_id,
                timeout=_NEO4J_WRITE_TIMEOUT_S,
            )
            for i in range(0, len(edges), BATCH):
                batch = [{"src": s, "tgt": t} for s, t in edges[i : i + BATCH]]
                t0 = time.perf_counter()
                async with _WRITE_SEM:
                    await session.run(
                        """
                        UNWIND $batch AS edge
                        MATCH (a:File {id: edge.src})
                        MATCH (b:File {id: edge.tgt})
                        MERGE (a)-[:IMPORTS]->(b)
                    """,
                        batch=batch,
                        timeout=_NEO4J_WRITE_TIMEOUT_S,
                    )
                _debug_log(
                    "import_graph_batch",
                    project_id=project_id,
                    batch=i // BATCH,
                    batch_size=len(batch),
                    elapsed_ms=int((time.perf_counter() - t0) * 1000),
                )
                _record_metric(
                    "import_graph_batch",
                    project_id=project_id,
                    batch=i // BATCH,
                    batch_size=len(batch),
                    elapsed_ms=int((time.perf_counter() - t0) * 1000),
                )
                await asyncio.sleep(0)

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        _debug_log(
            "import_graph_done",
            project_path=project_path,
            imports=len(imports),
            edges=len(edges),
            elapsed_ms=elapsed_ms,
        )
        _record_metric(
            "import_graph_done",
            project_path=project_path,
            imports=len(imports),
            edges=len(edges),
            elapsed_ms=elapsed_ms,
        )
        return (
            f"IMPORTS graph built for {project_path.split('/')[-1]}:\n"
            f"  {len(imports)} import statements scanned\n"
            f"  {len(edges)} resolved IMPORTS edges written to Neo4j\n"
            f"\nPageRank and community detection are now meaningful.\n"
            f"Re-run get_code_importance() and get_code_communities() for upgraded results."
        )
    except Exception as e:
        return f"Error building import graph: {str(e)}"


async def _build_symbol_import_export_graph_impl(project_path: str) -> str:
    """Build symbol-level IMPORTS/EXPORTS edges using Import nodes and chunk metadata."""
    try:
        _debug_log("symbol_graph_start", project_path=project_path)
        start = time.perf_counter()
        import re
        import asyncio

        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        import graph_bootstrap

        memory_store, _, _, _, _ = get_memory_modules()

        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()
        await memory_store.open_pool()
        if not memory_store._pg_pool_available():
            return "Symbol import/export graph skipped: Postgres not available"

        # Collect symbols by file
        symbols_by_file: dict[str, dict[str, str]] = {}
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            r = await session.run(
                """
                MATCH (f:File {project_id:$p})-[:CONTAINS]->(s)
                WHERE s:Function OR s:Class OR s:Struct OR s:Enum OR s:Trait OR s:Method OR s:Protocol
                RETURN f.filepath AS fp, s.name AS name, s.id AS sid
                """,
                p=project_id,
                timeout=_NEO4J_READ_TIMEOUT_S,
            )
            async for rec in r:
                fp = rec["fp"]
                name = rec["name"]
                sid = rec["sid"]
                if not fp or not name or not sid:
                    continue
                symbols_by_file.setdefault(fp, {})[name] = sid

        # Load file exports from Postgres metadata
        exports_by_file: dict[str, list[str]] = {}
        async with memory_store._pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT file_path, metadata->'file_exports' AS exports
                    FROM codebase_embeddings
                    WHERE project_id = %s AND metadata ? 'file_exports'
                    """,
                    (project_id,),
                )
                async for row in cur:
                    fp = row[0]
                    exports = row[1] or []
                    names: list[str] = []
                    for item in exports or []:
                        if isinstance(item, dict) and item.get("name"):
                            names.append(item.get("name"))
                        elif isinstance(item, str):
                            names.append(item)
                    if names:
                        exports_by_file[fp] = list(dict.fromkeys(names))

        # Ensure tool registration functions are treated as exports.
        for fp, symbols in symbols_by_file.items():
            if not fp.startswith("tools/"):
                continue
            names = exports_by_file.get(fp, [])
            if "register" in symbols and "register" not in names:
                names.append("register")
            if fp.endswith("tools/__init__.py") and "register_all" in symbols:
                if "register_all" not in names:
                    names.append("register_all")
            if names:
                exports_by_file[fp] = names

        def _parse_imported_names(ext: str, src_text: str) -> list[str]:
            src_text = src_text.strip()
            names: list[str] = []
            if ext == "py":
                m = re.match(r"from\s+[\w.]+\s+import\s+(.+)", src_text)
                if m:
                    block = m.group(1).strip()
                    block = block.strip("()")
                    for part in block.split(","):
                        name = part.strip()
                        if not name or name == "*":
                            continue
                        if " as " in name:
                            name = name.split(" as ", 1)[0].strip()
                        names.append(name)
            elif ext in ("js", "ts", "jsx", "tsx", "mjs", "cjs"):
                # import Default from 'x'
                m_default = re.match(r"import\s+([\w$]+)\s+from\s+['\"]", src_text)
                if m_default:
                    names.append(m_default.group(1))
                # import Default, {a as b, c} from 'x'
                m_both = re.match(r"import\s+([\w$]+)\s*,\s*\{([^}]+)\}", src_text)
                if m_both:
                    names.append(m_both.group(1))
                    block = m_both.group(2)
                    for part in block.split(","):
                        name = part.strip()
                        if not name:
                            continue
                        if " as " in name:
                            name = name.split(" as ", 1)[0].strip()
                        names.append(name)
                # import {a as b, c} from 'x'
                m = re.search(r"import\s+\{([^}]+)\}", src_text)
                if m:
                    block = m.group(1)
                    for part in block.split(","):
                        name = part.strip()
                        if not name:
                            continue
                        if " as " in name:
                            name = name.split(" as ", 1)[0].strip()
                        names.append(name)
            return list(dict.fromkeys(names))

        # Reuse Import nodes to resolve file edges and symbol edges
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            r2 = await session.run(
                """
                MATCH (f:File {project_id:$p})-[:CONTAINS]->(imp:Import)
                RETURN f.id AS src_fid, f.filepath AS src_fp, imp.source AS src_text
                """,
                p=project_id,
                timeout=_NEO4J_READ_TIMEOUT_S,
            )
            imports = []
            async for rec in r2:
                imports.append((rec["src_fid"], rec["src_fp"], rec["src_text"] or ""))

        # Resolve file imports using same logic as build_import_graph
        files: dict[str, str] = {}
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            rfiles = await session.run(
                "MATCH (f:File {project_id:$p}) RETURN f.filepath AS fp, f.id AS fid",
                p=project_id,
                timeout=_NEO4J_READ_TIMEOUT_S,
            )
            async for rec in rfiles:
                files[rec["fp"]] = rec["fid"]

        stems: dict[str, list[str]] = {}
        for fp in files:
            stem = os.path.splitext(os.path.basename(fp))[0]
            stems.setdefault(stem, []).append(fp)

        def resolve(src_fp: str, src_text: str) -> str | None:
            ext = os.path.splitext(src_fp)[1].lstrip(".")
            src_dir = os.path.dirname(src_fp)
            if ext in ("py", ""):
                m = re.match(r"from\s+([\w.]+)\s+import", src_text) or re.match(
                    r"import\s+([\w.]+)", src_text
                )
                if m:
                    mod = m.group(1).split(".")[0]
                    for fp in [
                        f"{src_dir}/{mod}.py",
                        f"{mod}.py",
                        f"{src_dir}/{mod}/__init__.py",
                        f"{mod}/__init__.py",
                    ]:
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

                        base = posixpath.normpath(posixpath.join(src_dir, imp)).lstrip(
                            "/"
                        )
                        for suf in (
                            "",
                            ".js",
                            ".ts",
                            ".jsx",
                            ".tsx",
                            "/index.js",
                            "/index.ts",
                        ):
                            candidate = (base + suf).lstrip("/")
                            if candidate in files:
                                return candidate
            return None

        def _build_symbol_edges() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
            import_edges_local: set[tuple[str, str]] = set()
            for src_fid, src_fp, src_text in imports:
                tgt_fp = resolve(src_fp, src_text)
                if not tgt_fp or tgt_fp == src_fp:
                    continue
                ext = os.path.splitext(src_fp)[1].lstrip(".")
                names = _parse_imported_names(ext, src_text)
                if not names:
                    continue
                target_symbols = symbols_by_file.get(tgt_fp, {})
                for name in names:
                    sid = target_symbols.get(name)
                    if sid:
                        import_edges_local.add((src_fid, sid))

            export_edges_local: set[tuple[str, str]] = set()
            for fp, names in exports_by_file.items():
                fid = files.get(fp)
                if not fid:
                    continue
                symbols = symbols_by_file.get(fp, {})
                for name in names:
                    sid = symbols.get(name)
                    if sid:
                        export_edges_local.add((fid, sid))

            return import_edges_local, export_edges_local

        import_edges, export_edges = await asyncio.to_thread(_build_symbol_edges)

        if not import_edges and not export_edges:
            return "No symbol import/export edges resolved."

        async def _write_edges() -> None:
            BATCH = _NEO4J_GRAPH_BUILD_BATCH
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                if import_edges:
                    await session.run(
                        "MATCH (a:File {project_id:$p})-[r:IMPORTS_SYMBOL]->() DELETE r",
                        p=project_id,
                        timeout=_NEO4J_WRITE_TIMEOUT_S,
                    )
                    edges = list(import_edges)
                    for i in range(0, len(edges), BATCH):
                        batch = [{"src": s, "tgt": t} for s, t in edges[i : i + BATCH]]
                        t0 = time.perf_counter()
                        async with _WRITE_SEM:
                            await session.run(
                                """
                                UNWIND $batch AS edge
                                MATCH (a:File {id: edge.src})
                                MATCH (b {id: edge.tgt})
                                MERGE (a)-[:IMPORTS_SYMBOL]->(b)
                                """,
                                batch=batch,
                                timeout=_NEO4J_WRITE_TIMEOUT_S,
                            )
                        _debug_log(
                            "symbol_import_batch",
                            project_id=project_id,
                            batch=i // BATCH,
                            batch_size=len(batch),
                            elapsed_ms=int((time.perf_counter() - t0) * 1000),
                        )
                        _record_metric(
                            "symbol_import_batch",
                            project_id=project_id,
                            batch=i // BATCH,
                            batch_size=len(batch),
                            elapsed_ms=int((time.perf_counter() - t0) * 1000),
                        )
                        await asyncio.sleep(0)
                if export_edges:
                    await session.run(
                        "MATCH (a:File {project_id:$p})-[r:EXPORTS_SYMBOL]->() DELETE r",
                        p=project_id,
                        timeout=_NEO4J_WRITE_TIMEOUT_S,
                    )
                    edges = list(export_edges)
                    for i in range(0, len(edges), BATCH):
                        batch = [{"src": s, "tgt": t} for s, t in edges[i : i + BATCH]]
                        t0 = time.perf_counter()
                        async with _WRITE_SEM:
                            await session.run(
                                """
                                UNWIND $batch AS edge
                                MATCH (a:File {id: edge.src})
                                MATCH (b {id: edge.tgt})
                                MERGE (a)-[:EXPORTS_SYMBOL]->(b)
                                """,
                                batch=batch,
                                timeout=_NEO4J_WRITE_TIMEOUT_S,
                            )
                        _debug_log(
                            "symbol_export_batch",
                            project_id=project_id,
                            batch=i // BATCH,
                            batch_size=len(batch),
                            elapsed_ms=int((time.perf_counter() - t0) * 1000),
                        )
                        _record_metric(
                            "symbol_export_batch",
                            project_id=project_id,
                            batch=i // BATCH,
                            batch_size=len(batch),
                            elapsed_ms=int((time.perf_counter() - t0) * 1000),
                        )
                        await asyncio.sleep(0)

        # Serialize graph writes to avoid deadlocks with concurrent jobs.
        with _SYMBOL_GRAPH_LOCK:
            for attempt in range(3):
                try:
                    await _write_edges()
                    break
                except Exception as e:
                    if "DeadlockDetected" in str(e) and attempt < 2:
                        import asyncio

                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    raise

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        _debug_log(
            "symbol_graph_done",
            project_path=project_path,
            import_edges=len(import_edges),
            export_edges=len(export_edges),
            elapsed_ms=elapsed_ms,
        )
        _record_metric(
            "symbol_graph_done",
            project_path=project_path,
            import_edges=len(import_edges),
            export_edges=len(export_edges),
            elapsed_ms=elapsed_ms,
        )
        return (
            "Symbol edges built:\n"
            f"  IMPORTS_SYMBOL: {len(import_edges)}\n"
            f"  EXPORTS_SYMBOL: {len(export_edges)}"
        )
    except Exception as e:
        return f"Error building symbol import/export graph: {str(e)}"
