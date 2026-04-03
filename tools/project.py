"""tools/project.py — project health, overview, import graph, and usage guide tools."""

import os
import hashlib
import threading
import asyncio
import time
import re
import uuid
from neo4j import unit_of_work
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules


_SYMBOL_GRAPH_LOCK = threading.Lock()

_GRAPH_WRITE_CONCURRENCY = max(
    1, int(os.getenv("LM_PROXY_GRAPH_WRITE_CONCURRENCY", "2"))
)
_NEO4J_READ_TIMEOUT_S = float(os.getenv("LM_PROXY_NEO4J_READ_TIMEOUT", "3.0"))
_NEO4J_WRITE_TIMEOUT_S = float(os.getenv("LM_PROXY_NEO4J_WRITE_TIMEOUT", "15.0"))
_TX_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
_TX_METADATA_BASE = {"source": "lm_proxy", "tool": "project"}
_NEO4J_GRAPH_BUILD_BATCH = max(50, int(os.getenv("LM_PROXY_GRAPH_BUILD_BATCH", "500")))

_GRAPH_LOCK_ENABLED = os.getenv(
    "LM_PROXY_GRAPH_BUILD_LOCK_REDIS", "1"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_GRAPH_LOCK_KEY = os.getenv("LM_PROXY_GRAPH_BUILD_LOCK_KEY", "lmproxy:graph_build_lock")
_GRAPH_LOCK_TTL_S = float(os.getenv("LM_PROXY_GRAPH_BUILD_LOCK_TTL", "300"))
_GRAPH_LOCK_WAIT_S = float(os.getenv("LM_PROXY_GRAPH_BUILD_LOCK_WAIT", "30"))
_GRAPH_LOCK_POLL_S = float(os.getenv("LM_PROXY_GRAPH_BUILD_LOCK_POLL", "0.2"))
_GRAPH_LOCK_RENEW_S = float(os.getenv("LM_PROXY_GRAPH_BUILD_LOCK_RENEW", "20"))
_REDIS_URL = os.getenv("LM_PROXY_REDIS_URL", "redis://localhost:6379/0")

_WRITE_SEM = asyncio.Semaphore(_GRAPH_WRITE_CONCURRENCY)
_GRAPH_BUILD_QUEUE: asyncio.Queue | None = None
_GRAPH_BUILD_WORKER: asyncio.Task | None = None
_GRAPH_BUILD_LOCK = asyncio.Lock()
_REDIS_CLIENT: object | None = None

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


def _is_deadlock_error(exc: Exception) -> bool:
    code = getattr(exc, "code", "") or getattr(exc, "gql_status", "")
    if isinstance(code, str) and "DeadlockDetected" in code:
        return True
    msg = str(exc)
    return "DeadlockDetected" in msg or "deadlock" in msg.lower()


async def _execute_write(session, cypher: str, op: str | None = None, **params) -> None:
    metadata = dict(_TX_METADATA_BASE)
    op_value = op or "write"
    if _TX_OP_PREFIX:
        op_value = f"{_TX_OP_PREFIX}.{op_value}"
    metadata["op"] = op_value

    @unit_of_work(timeout=_NEO4J_WRITE_TIMEOUT_S, metadata=metadata)
    async def _tx(tx):
        result = await tx.run(cypher, **params)
        await result.consume()

    if hasattr(session, "execute_write"):
        await session.execute_write(_tx)
    else:
        await _tx(session)


async def _execute_read(session, cypher: str, op: str | None = None, **params):
    metadata = dict(_TX_METADATA_BASE)
    op_value = op or "read"
    if _TX_OP_PREFIX:
        op_value = f"{_TX_OP_PREFIX}.{op_value}"
    metadata["op"] = op_value

    @unit_of_work(timeout=_NEO4J_READ_TIMEOUT_S, metadata=metadata)
    async def _tx(tx):
        result = await tx.run(cypher, **params)
        return await result.data()

    if hasattr(session, "execute_read"):
        return await session.execute_read(_tx)
    return await _tx(session)


async def _run_graph_build_with_retry(fn, label: str, project_path: str) -> str:
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            result = await fn(project_path)
        except Exception as exc:
            if _is_deadlock_error(exc) and attempt < attempts:
                _debug_log(
                    "graph_build_retry",
                    project_path=project_path,
                    phase=label,
                    attempt=attempt,
                )
                await asyncio.sleep(0.2 * attempt)
                continue
            raise
        if isinstance(result, str) and "deadlock" in result.lower():
            if attempt < attempts:
                _debug_log(
                    "graph_build_retry",
                    project_path=project_path,
                    phase=label,
                    attempt=attempt,
                )
                await asyncio.sleep(0.2 * attempt)
                continue
        if isinstance(result, str):
            return result
        return f"{label} graph build completed"
    return f"{label} graph build completed"


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


async def _get_redis() -> object | None:
    global _REDIS_CLIENT
    if not _GRAPH_LOCK_ENABLED:
        return None
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    try:
        import redis.asyncio as aioredis  # type: ignore

        client = aioredis.from_url(_REDIS_URL, decode_responses=True)
        await client.ping()
        _REDIS_CLIENT = client
        return _REDIS_CLIENT
    except Exception as exc:
        _debug_log("graph_lock_redis_unavailable", error=str(exc))
        return None


async def _acquire_graph_lock() -> str | None:
    if not _GRAPH_LOCK_ENABLED:
        return None
    client = await _get_redis()
    if client is None:
        return None
    token = uuid.uuid4().hex
    deadline = time.monotonic() + _GRAPH_LOCK_WAIT_S
    while time.monotonic() < deadline:
        try:
            ok = await client.set(
                _GRAPH_LOCK_KEY,
                token,
                nx=True,
                ex=int(_GRAPH_LOCK_TTL_S),
            )
        except Exception as exc:
            _debug_log("graph_lock_acquire_error", error=str(exc))
            return None
        if ok:
            return token
        await asyncio.sleep(_GRAPH_LOCK_POLL_S)
    return None


async def _release_graph_lock(token: str | None) -> None:
    if not token or not _GRAPH_LOCK_ENABLED:
        return
    client = await _get_redis()
    if client is None:
        return
    try:
        await client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            1,
            _GRAPH_LOCK_KEY,
            token,
        )
    except Exception as exc:
        _debug_log("graph_lock_release_error", error=str(exc))


async def _renew_graph_lock(token: str) -> bool:
    if not _GRAPH_LOCK_ENABLED:
        return False
    client = await _get_redis()
    if client is None:
        return False
    try:
        ok = await client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end",
            1,
            _GRAPH_LOCK_KEY,
            token,
            str(int(_GRAPH_LOCK_TTL_S)),
        )
        return bool(ok)
    except Exception as exc:
        _debug_log("graph_lock_renew_error", error=str(exc))
        return False


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
        lock_token = await _acquire_graph_lock()
        if _GRAPH_LOCK_ENABLED and lock_token is None:
            _debug_log(
                "graph_build_lock_busy",
                project_path=project_path,
                wait_s=_GRAPH_LOCK_WAIT_S,
            )
            _record_metric(
                "graph_build_lock_busy",
                project_path=project_path,
                wait_s=_GRAPH_LOCK_WAIT_S,
            )
            await asyncio.sleep(0.5)
            await _GRAPH_BUILD_QUEUE.put(item)
            _GRAPH_BUILD_QUEUE.task_done()
            continue
        renew_task: asyncio.Task | None = None
        if _GRAPH_LOCK_ENABLED and lock_token:

            async def _renew_loop() -> None:
                while True:
                    await asyncio.sleep(_GRAPH_LOCK_RENEW_S)
                    ok = await _renew_graph_lock(lock_token)
                    if not ok:
                        _record_metric(
                            "graph_build_lock_renew_failed",
                            project_path=project_path,
                        )
                        return

            renew_task = asyncio.create_task(_renew_loop())
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
                await _run_graph_build_with_retry(
                    _build_import_graph_impl, "imports", project_path
                )
                await asyncio.sleep(0)
            if run_symbols:
                await _run_graph_build_with_retry(
                    _build_symbol_import_export_graph_impl, "symbols", project_path
                )
            await _run_graph_build_with_retry(
                _build_asset_graph_impl, "assets", project_path
            )
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
            if renew_task:
                renew_task.cancel()
                try:
                    await renew_task
                except Exception:
                    pass
            await _release_graph_lock(lock_token)
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
- `find_code_duplication(project_path, min_similarity=0.92, max_pairs=50, min_tokens=80, per_chunk=5, sample_size=500, include_paths?, exclude_paths?)` → duplicate detection with exact/normalized hashes, winnowing + small-input fallback, and semantic similarity
  - Winnowing guarantee: matches shorter than `t = w + k − 1` are not guaranteed; small blocks use k-gram/token fallback
  - Tune with `winnow_*` parameters to balance recall vs noise
  - Clone group relationships are `MEMBER_OF_CLONE_GROUP` and `MEMBER_OF_FILE_CLONE_GROUP` (not `IN_*`), and symbol file path is stored on `filepath`
- `get_related_files(project_path, file_path)` → structural neighbors
- `visualize_subgraph(project_path, symbol_name)` → Mermaid subgraph
- `query_graph(cypher)` → raw Neo4j Cypher
- `trace_symbol_cross_project(symbol, source_project, target_project)` → cross-project trace
- `get_test_coverage_for(project_path, file_path)` → tests that cover a file
- `get_symbol_imports_summary(project_path, limit=20)` → summarize IMPORTS_SYMBOL edges (deprecated; use get_symbol_imports_overview)
- `get_symbol_imports_overview(project_path, limit=20, include_implicit=true)` → summarize explicit + implicit symbol import edges
- `get_symbol_exports_summary(project_path, limit=20, include_paths?, exclude_paths?, symbol_prefix?)` → summarize EXPORTS_SYMBOL edges
- `rebuild_symbol_graph(project_path)` → rebuild symbol-level IMPORTS/EXPORTS graph
- `cancel_index_job(job_id)` → cancel a running indexing job
- `get_app_flow_summary(project_path, ui_contains?, model_contains?, service_contains?, include_tests=false, limit=20, as_table=false)` → UI → API → Service → DB paths (includes external API calls)
- `get_backend_flow_summary(project_path, api_contains?, model_contains?, service_contains?, include_tests=false, limit=20, as_table=false)` → API → Service → DB paths (includes external API calls)
- `get_flow_summary(project_path, mode='auto', ui_contains?, api_contains?, model_contains?, service_contains?, include_tests=false, limit=20, as_table=false)` → UI or backend flow (auto tries UI then backend)
  Example:
  `get_app_flow_summary("/Users/michaelmarler/Projects/rental", ui_contains="lease-detail", model_contains="Lease", service_contains="Lease", limit=50, as_table=true)`
 - `get_language_pack_status()` → available vs manifest languages (auto-download status)
 - `get_indexed_projects(query?)` → list indexed repo paths (filters by id prefix or path substring)

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

            driver = await graph_bootstrap.require_driver()

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                r = await _execute_read(
                    session,
                    "MATCH (f:File {project_id:$p}) RETURN count(f) AS files",
                    p=project_id,
                    op="get_project_overview_file_count",
                )
                rec = r[0] if r else None
                n_files = rec["files"] if rec else 0

                r2 = await _execute_read(
                    session,
                    "MATCH (s {project_id:$p}) WHERE s:Function OR s:Class OR s:Struct "
                    "RETURN count(s) AS syms",
                    p=project_id,
                    op="get_project_overview_symbol_count",
                )
                rec2 = r2[0] if r2 else None
                n_syms = rec2["syms"] if rec2 else 0

                r3 = await _execute_read(
                    session,
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
                    op="get_project_overview_dirs",
                )
                dirs = []
                for rec in r3:
                    dirs.append(
                        f"  📂 {rec['top_dir']}/  ({rec['files']} files, {rec['syms']} symbols)"
                    )

                r4 = await _execute_read(
                    session,
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
                    op="get_project_overview_key_files",
                )
                key_files = []
                for rec in r4:
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

            driver = await graph_bootstrap.require_driver()

            limit = max(1, min(int(limit), 100))

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                r1 = await _execute_read(
                    session,
                    """
                    MATCH (f:File {project_id:$p})-[:IMPORTS_SYMBOL]->(s)
                    RETURN s.name AS symbol, count(*) AS n
                    ORDER BY n DESC
                    LIMIT $limit
                    """,
                    p=project_id,
                    limit=limit,
                    op="get_symbol_imports_summary_symbols",
                )
                top_symbols = []
                for rec in r1:
                    top_symbols.append((rec["symbol"], rec["n"]))

                r2 = await _execute_read(
                    session,
                    """
                    MATCH (f:File {project_id:$p})-[:IMPORTS_SYMBOL]->(s)
                    WITH f.filepath AS file, count(*) AS n, collect(DISTINCT s.name) AS symbols
                    ORDER BY n DESC
                    LIMIT $limit
                    RETURN file, n, symbols
                    """,
                    p=project_id,
                    limit=limit,
                    op="get_symbol_imports_summary_files",
                )
                top_files = []
                for rec in r2:
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
    async def get_symbol_imports_overview(
        project_path: str, limit: int = 20, include_implicit: bool = True
    ) -> str:
        """
        Summarize explicit (IMPORTS_SYMBOL) and implicit (IMPLICIT_IMPORTS_SYMBOL)
        symbol import edges for a project.

        Args:
            project_path: Absolute path to the project root.
            limit: Max rows to return per section (default 20).
            include_implicit: Include IMPLICIT_IMPORTS_SYMBOL edges when true.
        """
        try:
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            limit = max(1, min(int(limit), 100))

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                r_exp_count = await _execute_read(
                    session,
                    "MATCH (:File {project_id:$p})-[r:IMPORTS_SYMBOL]->() RETURN count(r) AS n",
                    p=project_id,
                    op="get_symbol_imports_overview_exp_count",
                )
                exp_count = r_exp_count[0]["n"] if r_exp_count else 0

                imp_count = 0
                if include_implicit:
                    r_imp_count = await _execute_read(
                        session,
                        "MATCH (:File {project_id:$p})-[r:IMPLICIT_IMPORTS_SYMBOL]->() RETURN count(r) AS n",
                        p=project_id,
                        op="get_symbol_imports_overview_imp_count",
                    )
                    imp_count = r_imp_count[0]["n"] if r_imp_count else 0

                r_exp_symbols = await _execute_read(
                    session,
                    """
                    MATCH (f:File {project_id:$p})-[:IMPORTS_SYMBOL]->(s)
                    RETURN s.name AS symbol, count(*) AS n
                    ORDER BY n DESC
                    LIMIT $limit
                    """,
                    p=project_id,
                    limit=limit,
                    op="get_symbol_imports_overview_exp_symbols",
                )
                exp_symbols = [(rec["symbol"], rec["n"]) for rec in r_exp_symbols]

                r_exp_files = await _execute_read(
                    session,
                    """
                    MATCH (f:File {project_id:$p})-[:IMPORTS_SYMBOL]->(s)
                    WITH f.filepath AS file, count(*) AS n, collect(DISTINCT s.name) AS symbols
                    ORDER BY n DESC
                    LIMIT $limit
                    RETURN file, n, symbols
                    """,
                    p=project_id,
                    limit=limit,
                    op="get_symbol_imports_overview_exp_files",
                )
                exp_files = [
                    (rec["file"], rec["n"], rec["symbols"]) for rec in r_exp_files
                ]

                imp_symbols = []
                imp_files = []
                if include_implicit:
                    r_imp_symbols = await _execute_read(
                        session,
                        """
                        MATCH (f:File {project_id:$p})-[:IMPLICIT_IMPORTS_SYMBOL]->(s)
                        RETURN s.name AS symbol, count(*) AS n
                        ORDER BY n DESC
                        LIMIT $limit
                        """,
                        p=project_id,
                        limit=limit,
                        op="get_symbol_imports_overview_imp_symbols",
                    )
                    imp_symbols = [(rec["symbol"], rec["n"]) for rec in r_imp_symbols]

                    r_imp_files = await _execute_read(
                        session,
                        """
                        MATCH (f:File {project_id:$p})-[:IMPLICIT_IMPORTS_SYMBOL]->(s)
                        WITH f.filepath AS file, count(*) AS n, collect(DISTINCT s.name) AS symbols
                        ORDER BY n DESC
                        LIMIT $limit
                        RETURN file, n, symbols
                        """,
                        p=project_id,
                        limit=limit,
                        op="get_symbol_imports_overview_imp_files",
                    )
                    imp_files = [
                        (rec["file"], rec["n"], rec["symbols"]) for rec in r_imp_files
                    ]

            if not exp_symbols and not exp_files and not imp_symbols and not imp_files:
                return "No symbol import edges found."

            lines = [f"# Symbol import overview: {project_path.split('/')[-1]}", ""]
            lines.append("## Counts")
            lines.append(f"IMPORTS_SYMBOL: {exp_count}")
            if include_implicit:
                lines.append(f"IMPLICIT_IMPORTS_SYMBOL: {imp_count}")

            if exp_symbols:
                lines.append("")
                lines.append("## Top explicit imported symbols")
                for name, n in exp_symbols:
                    lines.append(f"- {name}  ({n})")
            if exp_files:
                lines.append("")
                lines.append("## Files with most explicit symbol imports")
                for file, n, symbols in exp_files[:limit]:
                    sample = ", ".join(symbols[:6])
                    lines.append(f"- {file}  ({n})  [{sample}]")

            if include_implicit and imp_symbols:
                lines.append("")
                lines.append("## Top implicit imported symbols")
                for name, n in imp_symbols:
                    lines.append(f"- {name}  ({n})")
            if include_implicit and imp_files:
                lines.append("")
                lines.append("## Files with most implicit symbol imports")
                for file, n, symbols in imp_files[:limit]:
                    sample = ", ".join(symbols[:6])
                    lines.append(f"- {file}  ({n})  [{sample}]")

            return "\n".join(lines)
        except Exception as e:
            return f"Error summarizing symbol imports overview: {str(e)}"

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

            driver = await graph_bootstrap.require_driver()

            limit = max(1, min(int(limit), 100))

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                r1 = await _execute_read(
                    session,
                    """
                    MATCH (f:File {project_id:$p})-[:EXPORTS_SYMBOL]->(s)
                    RETURN s.name AS symbol, count(*) AS n
                    ORDER BY n DESC
                    LIMIT $limit
                    """,
                    p=project_id,
                    limit=limit,
                    op="get_symbol_exports_summary_symbols",
                )
                top_symbols = []
                for rec in r1:
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

                r2 = await _execute_read(
                    session,
                    """
                    MATCH (f:File {project_id:$p})-[:EXPORTS_SYMBOL]->(s)
                    WITH f.filepath AS file, count(*) AS n, collect(DISTINCT s.name) AS symbols
                    ORDER BY n DESC
                    LIMIT $limit
                    RETURN file, n, symbols
                    """,
                    p=project_id,
                    limit=fetch_limit,
                    op="get_symbol_exports_summary_files",
                )
                top_files = []
                for rec in r2:
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

    @mcp.tool()
    async def rebuild_symbol_graph(project_path: str) -> str:
        """
        Rebuild symbol-level IMPORTS/EXPORTS graph for a project.
        """
        result = await _run_graph_build_with_retry(
            _build_symbol_import_export_graph_impl, "symbols", project_path
        )
        return result

    @mcp.tool()
    async def get_app_flow_summary(
        project_path: str,
        ui_contains: str | None = None,
        model_contains: str | None = None,
        service_contains: str | None = None,
        include_tests: bool = False,
        limit: int = 20,
        as_table: bool = False,
    ) -> str:
        """
        Summarize UI → API → Service → DB paths for a project.
        """
        try:
            import graph_bootstrap

            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            query_limit = limit
            if model_contains:
                query_limit = max(limit * 10, 200)
            driver = await graph_bootstrap.require_driver()
            rows = []
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                result = await _execute_read(
                    session,
                    """
                    CALL {
                        MATCH (ui:File {project_id:$p})-[:ASSET_LINKS]->(js:File {project_id:$p})
                        MATCH (js)-[:CALLS_API]->(api:File {project_id:$p})
                        OPTIONAL MATCH (api)-[:CALLS_SERVICE]->(svc:File {project_id:$p})
                        OPTIONAL MATCH (svc)-[:CALLS_DB_MODEL]->(model:Model {project_id:$p})
                        OPTIONAL MATCH (svc)-[:CALLS_DB]->(schema:File {project_id:$p, filepath:'prisma/schema.prisma'})
                        OPTIONAL MATCH (js)-[:CALLS_API_EXTERNAL]->(ext:ExternalAPI {project_id:$p})
                        WHERE ($ui_filter IS NULL OR ui.filepath CONTAINS $ui_filter)
                          AND ($include_tests OR (
                            NOT ui.filepath STARTS WITH 'tests/'
                            AND NOT ui.filepath CONTAINS '/tests/'
                            AND NOT ui.filepath CONTAINS '__tests__'
                            AND NOT ui.filepath CONTAINS '.test.'
                            AND NOT js.filepath STARTS WITH 'tests/'
                            AND NOT js.filepath CONTAINS '/tests/'
                            AND NOT js.filepath CONTAINS '__tests__'
                            AND NOT js.filepath CONTAINS '.test.'
                          ))
                        RETURN ui.filepath AS ui,
                           js.filepath AS js,
                           api.filepath AS api,
                           svc.filepath AS svc,
                           model.name AS model,
                           schema.filepath AS schema,
                           ext.url AS external
                        UNION
                        MATCH (ui:File {project_id:$p})-[:CALLS_API]->(api:File {project_id:$p})
                        OPTIONAL MATCH (api)-[:CALLS_SERVICE]->(svc:File {project_id:$p})
                        OPTIONAL MATCH (svc)-[:CALLS_DB_MODEL]->(model:Model {project_id:$p})
                        OPTIONAL MATCH (svc)-[:CALLS_DB]->(schema:File {project_id:$p, filepath:'prisma/schema.prisma'})
                        OPTIONAL MATCH (ui)-[:CALLS_API_EXTERNAL]->(ext:ExternalAPI {project_id:$p})
                        WHERE ($ui_filter IS NULL OR ui.filepath CONTAINS $ui_filter)
                          AND ($include_tests OR (
                            NOT ui.filepath STARTS WITH 'tests/'
                            AND NOT ui.filepath CONTAINS '/tests/'
                            AND NOT ui.filepath CONTAINS '__tests__'
                            AND NOT ui.filepath CONTAINS '.test.'
                          ))
                        RETURN ui.filepath AS ui,
                           ui.filepath AS js,
                           api.filepath AS api,
                           svc.filepath AS svc,
                           model.name AS model,
                           schema.filepath AS schema,
                           ext.url AS external
                    }
                    RETURN ui, js, api, svc, model, schema, external
                    LIMIT $limit
                    """,
                    p=project_id,
                    ui_filter=ui_contains,
                    include_tests=include_tests,
                    limit=query_limit,
                    op="get_app_flow_summary",
                )
                raw_rows = []
                for row in result:
                    raw_rows.append(
                        (
                            row.get("ui"),
                            row.get("js"),
                            row.get("api"),
                            row.get("svc"),
                            row.get("model"),
                            row.get("schema"),
                            row.get("external"),
                        )
                    )

            if ui_contains:
                raw_rows = [r for r in raw_rows if r[0] and ui_contains in r[0]]
            if model_contains:
                raw_rows = [r for r in raw_rows if r[4] and model_contains in r[4]]
            if service_contains:
                raw_rows = [r for r in raw_rows if r[3] and service_contains in r[3]]

            if as_table:
                rows = [
                    "| UI | JS | API | Service | Model | Schema | External |",
                    "| --- | --- | --- | --- | --- | --- | --- |",
                ]
                for ui, js, api, svc, model, schema, external in raw_rows:
                    rows.append(
                        f"| {ui or ''} | {js or ''} | {api or ''} | {svc or ''} | {model or ''} | {schema or ''} | {external or ''} |"
                    )
            else:
                rows = [
                    " -> ".join(
                        [v for v in [ui, js, api, svc, model, schema, external] if v]
                    )
                    for ui, js, api, svc, model, schema, external in raw_rows
                ]
            if limit and len(rows) > limit:
                rows = rows[:limit]
            if not rows:
                return "No UI → API → Service → DB paths found."
            rows = list(dict.fromkeys(rows))
            return "\n".join(rows)
        except Exception as exc:
            return f"Error building flow summary: {str(exc)}"

    @mcp.tool()
    async def get_backend_flow_summary(
        project_path: str,
        api_contains: str | None = None,
        model_contains: str | None = None,
        service_contains: str | None = None,
        include_tests: bool = False,
        limit: int = 20,
        as_table: bool = False,
    ) -> str:
        """
        Summarize API → Service → DB paths for a project.
        """
        try:
            import graph_bootstrap

            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            query_limit = limit
            if model_contains:
                query_limit = max(limit * 10, 200)
            driver = await graph_bootstrap.require_driver()
            rows = []
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                result = await _execute_read(
                    session,
                    """
                    MATCH (api:File {project_id:$p})
                    OPTIONAL MATCH (api)-[:CALLS_SERVICE]->(svc:File {project_id:$p})
                    OPTIONAL MATCH (svc)-[:CALLS_DB_MODEL]->(model:Model {project_id:$p})
                    OPTIONAL MATCH (svc)-[:CALLS_DB]->(schema:File {project_id:$p, filepath:'prisma/schema.prisma'})
                    OPTIONAL MATCH (api)-[:CALLS_API_EXTERNAL]->(ext:ExternalAPI {project_id:$p})
                    WHERE ($api_filter IS NULL OR api.filepath CONTAINS $api_filter)
                      AND ($service_filter IS NULL OR svc.filepath CONTAINS $service_filter)
                      AND ($model_filter IS NULL OR model.name CONTAINS $model_filter)
                      AND (svc IS NOT NULL OR model IS NOT NULL OR schema IS NOT NULL OR ext IS NOT NULL)
                      AND ($include_tests OR (
                        NOT api.filepath STARTS WITH 'tests/'
                        AND NOT api.filepath CONTAINS '/tests/'
                        AND NOT api.filepath CONTAINS '__tests__'
                        AND NOT api.filepath CONTAINS '.test.'
                        AND (svc IS NULL OR (
                          NOT svc.filepath STARTS WITH 'tests/'
                          AND NOT svc.filepath CONTAINS '/tests/'
                          AND NOT svc.filepath CONTAINS '__tests__'
                          AND NOT svc.filepath CONTAINS '.test.'
                        ))
                      ))
                    RETURN api.filepath AS api,
                       svc.filepath AS svc,
                       model.name AS model,
                       schema.filepath AS schema,
                       ext.url AS external
                    LIMIT $limit
                    """,
                    p=project_id,
                    api_filter=api_contains,
                    service_filter=service_contains,
                    model_filter=model_contains,
                    include_tests=include_tests,
                    limit=query_limit,
                    op="get_backend_flow_summary",
                )
                for row in result:
                    rows.append(
                        (
                            row.get("api"),
                            row.get("svc"),
                            row.get("model"),
                            row.get("schema"),
                            row.get("external"),
                        )
                    )

            rows = [r for r in rows if r[1] or r[2] or r[3] or r[4]]
            if not rows:
                return "No API → Service → DB paths found."

            if as_table:
                output = [
                    "| API | Service | Model | Schema | External |",
                    "| --- | --- | --- | --- | --- |",
                ]
                for api, svc, model, schema, external in rows[:limit]:
                    output.append(
                        f"| {api or ''} | {svc or ''} | {model or ''} | {schema or ''} | {external or ''} |"
                    )
            else:
                output = [
                    " -> ".join([v for v in [api, svc, model, schema, external] if v])
                    for api, svc, model, schema, external in rows
                ]

            output = list(dict.fromkeys(output))
            if limit and len(output) > limit:
                output = output[:limit]
            return "\n".join(output)
        except Exception as exc:
            return f"Error building backend flow summary: {str(exc)}"

    @mcp.tool()
    async def get_flow_summary(
        project_path: str,
        mode: str = "auto",
        ui_contains: str | None = None,
        api_contains: str | None = None,
        model_contains: str | None = None,
        service_contains: str | None = None,
        include_tests: bool = False,
        limit: int = 20,
        as_table: bool = False,
    ) -> str:
        """
        Summarize UI → API → Service → DB paths or API → Service → DB paths.

        Args:
            mode: 'auto', 'ui', or 'backend'.
            ui_contains: Filter UI files (ui mode only).
            api_contains: Filter API files (backend mode only).
            model_contains: Filter model names.
            service_contains: Filter service files.
            include_tests: Include test files.
            limit: Max rows.
            as_table: Render as table when supported.
        """
        mode_norm = (mode or "auto").strip().lower()
        if mode_norm not in {"auto", "ui", "backend"}:
            return "Invalid mode. Use 'auto', 'ui', or 'backend'."

        if mode_norm in {"auto", "ui"}:
            ui_result = await get_app_flow_summary(
                project_path,
                ui_contains=ui_contains,
                model_contains=model_contains,
                service_contains=service_contains,
                include_tests=include_tests,
                limit=limit,
                as_table=as_table,
            )
            if mode_norm == "ui":
                return ui_result
            if not ui_result.startswith("No UI → API → Service → DB paths found"):
                return ui_result

        backend_result = await get_backend_flow_summary(
            project_path,
            api_contains=api_contains,
            model_contains=model_contains,
            service_contains=service_contains,
            include_tests=include_tests,
            limit=limit,
            as_table=as_table,
        )
        return backend_result


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

        driver = await graph_bootstrap.require_driver()

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            r = await _execute_read(
                session,
                session,
                "MATCH (f:File {project_id:$p}) RETURN f.filepath AS fp, f.id AS fid",
                p=project_id,
                op="build_import_graph_files",
            )
            files: dict[str, str] = {}
            for rec in r:
                files[rec["fp"]] = rec["fid"]

            stems: dict[str, list[str]] = {}
            for fp in files:
                stem = PurePosixPath(fp).stem
                stems.setdefault(stem, []).append(fp)

            r2 = await _execute_read(
                session,
                session,
                "MATCH (f:File {project_id:$p})-[:CONTAINS]->(imp:Import) "
                "RETURN f.id AS src_fid, f.filepath AS src_fp, imp.source AS src_text",
                p=project_id,
                op="build_import_graph_imports",
            )
            imports = []
            for rec in r2:
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
            await _execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:IMPORTS]->() DELETE r",
                p=project_id,
                timeout=_NEO4J_WRITE_TIMEOUT_S,
            )
            for i in range(0, len(edges), BATCH):
                batch = [{"src": s, "tgt": t} for s, t in edges[i : i + BATCH]]
                t0 = time.perf_counter()
                async with _WRITE_SEM:
                    await _execute_write(
                        session,
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


async def _build_asset_graph_impl(project_path: str) -> str:
    """Build asset linkage edges (HTML → assets, JS/TS → API spec/routes)."""
    try:
        _debug_log("asset_graph_start", project_path=project_path)
        start = time.perf_counter()
        import posixpath
        from pathlib import PurePosixPath

        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        import graph_bootstrap

        driver = await graph_bootstrap.require_driver()

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            r = await _execute_read(
                session,
                session,
                "MATCH (f:File {project_id:$p}) RETURN f.filepath AS fp, f.id AS fid",
                p=project_id,
                op="build_asset_graph_files",
            )
            files: dict[str, str] = {}
            for rec in r:
                files[rec["fp"]] = rec["fid"]

        if not files:
            return "No files found for asset graph."

        html_files = [
            (fp, fid) for fp, fid in files.items() if fp.endswith((".html", ".astro"))
        ]
        script_files = [
            (fp, fid)
            for fp, fid in files.items()
            if fp.endswith((".js", ".ts", ".tsx", ".astro"))
        ]

        api_targets: list[str] = []
        api_target_paths: list[str] = []
        if "src/api/openapi.yaml" in files:
            api_target_paths.append("src/api/openapi.yaml")
        if "src/api/routes.ts" in files:
            api_target_paths.append("src/api/routes.ts")

        for fp in files:
            if fp.startswith("src/pages/api/") or fp.startswith("pages/api/"):
                if fp.endswith((".ts", ".js", ".tsx", ".jsx")):
                    api_target_paths.append(fp)
            if fp.startswith("src/app/api/") or fp.startswith("app/api/"):
                if fp.endswith(("route.ts", "route.js", "route.tsx", "route.jsx")):
                    api_target_paths.append(fp)
            if fp.startswith("src/app/") or fp.startswith("app/"):
                if fp.endswith(("route.ts", "route.js", "route.tsx", "route.jsx")):
                    api_target_paths.append(fp)

        for fp in files:
            if fp.startswith("src/api/") and fp.endswith((".ts", ".js", ".tsx")):
                api_target_paths.append(fp)

        api_targets = [files[p] for p in api_target_paths if p in files]
        api_targets = list(dict.fromkeys(api_targets))

        def _route_path_from_file(fp: str) -> str | None:
            path = PurePosixPath(fp)
            parts = path.parts
            if len(parts) < 2:
                return None
            if parts[0] == "src":
                parts = parts[1:]
            if not parts:
                return None
            if parts[0] == "app" and path.name.startswith("route."):
                route_parts = parts[1:-1]
                if not route_parts:
                    return "/"
                return "/" + "/".join(route_parts)
            if parts[0] == "pages" and len(parts) > 1 and parts[1] == "api":
                rel = parts[2:]
                if not rel:
                    return "/api"
                file_stem = PurePosixPath(*rel).stem
                if file_stem == "index":
                    rel = rel[:-1]
                else:
                    rel = rel[:-1] + (file_stem,)
                if not rel:
                    return "/api"
                return "/api/" + "/".join(rel)
            if parts[0] == "api":
                rel = parts[1:]
                if not rel:
                    return "/api"
                file_stem = PurePosixPath(*rel).stem
                if file_stem == "index":
                    rel = rel[:-1]
                else:
                    rel = rel[:-1] + (file_stem,)
                if not rel:
                    return "/api"
                return "/api/" + "/".join(rel)
            return None

        route_targets: dict[str, str] = {}
        for fp, fid in files.items():
            route_path = _route_path_from_file(fp)
            if route_path:
                route_targets.setdefault(route_path, fid)

        def _read_text(abs_path: str) -> str:
            try:
                if os.path.getsize(abs_path) > 1_000_000:
                    return ""
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                    return fh.read()
            except OSError:
                return ""

        def _resolve_href(src_fp: str, raw: str) -> str | None:
            raw = raw.split("#", 1)[0].split("?", 1)[0].strip()
            if not raw:
                return None
            if raw.startswith(("http://", "https://", "//", "data:", "mailto:")):
                return None
            if raw.startswith("/"):
                candidate = raw.lstrip("/")
                if candidate.startswith("assets/"):
                    candidate = posixpath.join("src/public", candidate)
                else:
                    public_candidate = posixpath.join("src/public", candidate)
                    if public_candidate in files:
                        candidate = public_candidate
            else:
                src_dir = str(PurePosixPath(src_fp).parent)
                candidate = posixpath.normpath(posixpath.join(src_dir, raw)).lstrip("/")
                if candidate.startswith("../"):
                    return None
            return candidate if candidate in files else None

        html_edges: list[tuple[str, str]] = []
        for fp, fid in html_files:
            abs_path = os.path.join(project_path, fp)
            content = _read_text(abs_path)
            if not content:
                continue
            for m in re.finditer(r"<script[^>]+src=[\"']([^\"']+)[\"']", content, re.I):
                target = _resolve_href(fp, m.group(1))
                if target and files[target] != fid:
                    html_edges.append((fid, files[target]))
            for m in re.finditer(r"<link[^>]+href=[\"']([^\"']+)[\"']", content, re.I):
                href = m.group(1)
                if not href.lower().endswith(".css"):
                    continue
                target = _resolve_href(fp, href)
                if target and files[target] != fid:
                    html_edges.append((fid, files[target]))

        api_edges: list[tuple[str, str]] = []
        if api_targets or route_targets:
            api_re = re.compile(r"[\"'](/api/[^\"']+)[\"']")
            route_re = re.compile(r"[\"'](/[^\"']+)[\"']")
            client_re = re.compile(r"\b(fetch|axios|ky|ofetch)\b")
            for fp, fid in script_files:
                abs_path = os.path.join(project_path, fp)
                content = _read_text(abs_path)
                if not content:
                    continue
                if not api_re.search(content) and not client_re.search(content):
                    continue
                matched_targets: set[str] = set()
                for literal in route_re.findall(content):
                    cleaned = literal.split("?", 1)[0].split("#", 1)[0]
                    if cleaned in route_targets:
                        matched_targets.add(route_targets[cleaned])
                    elif cleaned.startswith("/api/") and api_targets:
                        matched_targets.update(api_targets)
                if not matched_targets and api_re.search(content):
                    matched_targets.update(api_targets)
                for tgt in matched_targets:
                    if tgt != fid:
                        api_edges.append((fid, tgt))

        service_edges: list[tuple[str, str]] = []
        service_files = {
            os.path.splitext(os.path.basename(fp))[0]: fid
            for fp, fid in files.items()
            if fp.startswith("src/services/") and fp.endswith((".ts", ".js"))
        }
        if service_files:
            backend_files = [
                (fp, fid)
                for fp, fid in files.items()
                if (
                    fp.startswith("src/api/")
                    or fp.startswith("src/webhooks/")
                    or fp.startswith("src/jobs/")
                    or fp.startswith("src/pages/api/")
                    or fp.startswith("pages/api/")
                    or fp.startswith("src/app/api/")
                    or fp.startswith("app/api/")
                )
                and fp.endswith((".ts", ".js"))
            ]
            for fp, fid in backend_files:
                abs_path = os.path.join(project_path, fp)
                content = _read_text(abs_path)
                if not content:
                    continue
                for name, svc_fid in service_files.items():
                    if name in {"index", "types"}:
                        continue
                    if re.search(rf"\b{name}\b", content):
                        service_edges.append((fid, svc_fid))

        db_edges: list[tuple[str, str]] = []
        schema_fp = "prisma/schema.prisma"
        schema_fid = files.get(schema_fp)
        if schema_fid:
            schema_path = os.path.join(project_path, schema_fp)
            schema_text = _read_text(schema_path)
            models = set(re.findall(r"\bmodel\s+(\w+)\s+\{", schema_text))
            if models:
                delegates = set(models)
                for name in list(models):
                    if name:
                        delegates.add(name[0].lower() + name[1:])
                prisma_re = re.compile(
                    r"\b(?:this\.)?prisma\.([A-Za-z_][A-Za-z0-9_]*)\b"
                )
                scan_files = [
                    (fp, fid)
                    for fp, fid in files.items()
                    if fp.startswith("src/") and fp.endswith((".ts", ".js"))
                ]
                for fp, fid in scan_files:
                    abs_path = os.path.join(project_path, fp)
                    content = _read_text(abs_path)
                    if not content:
                        continue
                    for match in prisma_re.findall(content):
                        if match in delegates:
                            db_edges.append((fid, schema_fid))
                            break

        html_edges = list(set(html_edges))
        api_edges = list(set(api_edges))
        service_edges = list(set(service_edges))
        db_edges = list(set(db_edges))
        if not html_edges and not api_edges and not service_edges and not db_edges:
            return "No asset edges resolved."

        BATCH = _NEO4J_GRAPH_BUILD_BATCH
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await _execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:ASSET_LINKS]->() DELETE r",
                p=project_id,
                timeout=_NEO4J_WRITE_TIMEOUT_S,
            )
            await _execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:CALLS_API]->() DELETE r",
                p=project_id,
                timeout=_NEO4J_WRITE_TIMEOUT_S,
            )
            await _execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:CALLS_SERVICE]->() DELETE r",
                p=project_id,
                timeout=_NEO4J_WRITE_TIMEOUT_S,
            )
            await _execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:CALLS_DB]->() DELETE r",
                p=project_id,
                timeout=_NEO4J_WRITE_TIMEOUT_S,
            )
            if html_edges:
                for i in range(0, len(html_edges), BATCH):
                    batch = [{"src": s, "tgt": t} for s, t in html_edges[i : i + BATCH]]
                    async with _WRITE_SEM:
                        await _execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (a:File {id: edge.src})
                            MATCH (b:File {id: edge.tgt})
                            MERGE (a)-[:ASSET_LINKS]->(b)
                            """,
                            batch=batch,
                            timeout=_NEO4J_WRITE_TIMEOUT_S,
                        )
            if api_edges:
                for i in range(0, len(api_edges), BATCH):
                    batch = [{"src": s, "tgt": t} for s, t in api_edges[i : i + BATCH]]
                    async with _WRITE_SEM:
                        await _execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (a:File {id: edge.src})
                            MATCH (b:File {id: edge.tgt})
                            MERGE (a)-[:CALLS_API]->(b)
                            """,
                            batch=batch,
                            timeout=_NEO4J_WRITE_TIMEOUT_S,
                        )

            if service_edges:
                for i in range(0, len(service_edges), BATCH):
                    batch = [
                        {"src": s, "tgt": t} for s, t in service_edges[i : i + BATCH]
                    ]
                    async with _WRITE_SEM:
                        await _execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (a:File {id: edge.src})
                            MATCH (b:File {id: edge.tgt})
                            MERGE (a)-[:CALLS_SERVICE]->(b)
                            """,
                            batch=batch,
                            timeout=_NEO4J_WRITE_TIMEOUT_S,
                        )

            if db_edges:
                for i in range(0, len(db_edges), BATCH):
                    batch = [{"src": s, "tgt": t} for s, t in db_edges[i : i + BATCH]]
                    async with _WRITE_SEM:
                        await _execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (a:File {id: edge.src})
                            MATCH (b:File {id: edge.tgt})
                            MERGE (a)-[:CALLS_DB]->(b)
                            """,
                            batch=batch,
                            timeout=_NEO4J_WRITE_TIMEOUT_S,
                        )

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        _debug_log(
            "asset_graph_done",
            project_path=project_path,
            elapsed_ms=elapsed_ms,
            asset_links=len(html_edges),
            api_links=len(api_edges),
            service_links=len(service_edges),
            db_links=len(db_edges),
        )
        return (
            f"ASSET graph built for {project_path.split('/')[-1]}:\n"
            f"  {len(html_edges)} ASSET_LINKS edges\n"
            f"  {len(api_edges)} CALLS_API edges\n"
            f"  {len(service_edges)} CALLS_SERVICE edges\n"
            f"  {len(db_edges)} CALLS_DB edges\n"
            f"  elapsed={elapsed_ms}ms"
        )
    except Exception as exc:
        _debug_log("asset_graph_error", project_path=project_path, error=str(exc))
        return f"Error building asset graph: {str(exc)}"


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

        driver = await graph_bootstrap.require_driver()
        await memory_store.open_pool()
        pg_available = memory_store._pg_pool_available()

        # Collect symbols by file
        symbols_by_file: dict[str, dict[str, str]] = {}
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            r = await _execute_read(
                session,
                """
                MATCH (f:File {project_id:$p})-[:CONTAINS]->(s)
                WHERE s:Function OR s:Class OR s:Struct OR s:Enum OR s:Trait OR s:Method OR s:Protocol
                RETURN f.filepath AS fp, s.name AS name, s.id AS sid
                """,
                p=project_id,
                op="build_symbol_graph_symbols",
            )
            for rec in r:
                fp = rec["fp"]
                name = rec["name"]
                sid = rec["sid"]
                if not fp or not name or not sid:
                    continue
                symbols_by_file.setdefault(fp, {})[name] = sid

        # Load file exports from Postgres metadata
        exports_by_file: dict[str, list[str]] = {}
        if pg_available:
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

        # Fallback: if no exports metadata, treat defined symbols as exports.
        if not exports_by_file:
            for fp, symbols in symbols_by_file.items():
                if symbols:
                    exports_by_file[fp] = list(symbols.keys())

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

        swift_module_map: dict[str, list[str]] = {}
        for fp in symbols_by_file:
            parts = fp.replace("\\", "/").split("/")
            if "Sources" in parts:
                src_idx = parts.index("Sources")
                if src_idx > 0:
                    mod_name = parts[src_idx - 1]
                    swift_module_map.setdefault(mod_name, []).append(fp)

        def _parse_imported_names(ext: str, src_text: str) -> list[str]:
            src_text = src_text.strip()
            names: list[str] = []

            def _clean(name: str) -> str:
                name = name.strip()
                if name.startswith("type "):
                    name = name[5:].strip()
                if name.startswith("typeof "):
                    name = name[7:].strip()
                return name

            if ext == "py":
                m = re.match(r"from\s+[\w.]+\s+import\s+(.+)", src_text)
                if m:
                    block = m.group(1).strip()
                    block = block.strip("()")
                    for part in block.split(","):
                        name = _clean(part)
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
                # import * as Name from 'x'
                m_star = re.match(
                    r"import\s+\*\s+as\s+([\w$]+)\s+from\s+['\"]", src_text
                )
                if m_star:
                    names.append(m_star.group(1))
                # import Default, {a as b, c} from 'x'
                m_both = re.match(r"import\s+([\w$]+)\s*,\s*\{([^}]+)\}", src_text)
                if m_both:
                    names.append(m_both.group(1))
                    block = m_both.group(2)
                    for part in block.split(","):
                        name = _clean(part)
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
                        name = _clean(part)
                        if not name:
                            continue
                        if " as " in name:
                            name = name.split(" as ", 1)[0].strip()
                        names.append(name)
            elif ext == "swift":
                m = re.match(r"^(?:@testable\s+)?import\s+(\w+)", src_text)
                if m:
                    names.append(m.group(1))
            elif ext == "rs":
                m = re.match(r"^(?:pub\s+)?use\s+(.+)$", src_text)
                if m:
                    body = m.group(1).strip().rstrip(";")

                    def _add_name(path: str) -> None:
                        path = path.strip()
                        if not path:
                            return
                        for prefix in ("crate::", "self::", "super::"):
                            if path.startswith(prefix):
                                path = path[len(prefix) :]
                        if path.endswith("::*"):
                            return
                        name = path.split("::")[-1].strip()
                        if " as " in name:
                            name = name.split(" as ", 1)[0].strip()
                        if not name or name in {"self", "super", "crate"}:
                            return
                        names.append(name)

                    if "{" in body and "}" in body:
                        prefix, rest = body.split("{", 1)
                        prefix = prefix.strip()
                        rest = rest.split("}", 1)[0]
                        for part in rest.split(","):
                            part = part.strip()
                            if not part or part in {"self", "super", "crate"}:
                                continue
                            full = part
                            if prefix:
                                if prefix.endswith("::"):
                                    full = f"{prefix}{part}"
                                else:
                                    full = f"{prefix}::{part}"
                            _add_name(full)
                    else:
                        _add_name(body)
            return list(dict.fromkeys(names))

        # Reuse Import nodes to resolve file edges and symbol edges
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            r2 = await _execute_read(
                session,
                """
                MATCH (f:File {project_id:$p})-[:CONTAINS]->(imp:Import)
                RETURN f.id AS src_fid, f.filepath AS src_fp, imp.source AS src_text
                """,
                p=project_id,
                op="build_symbol_graph_imports",
            )
            imports = []
            for rec in r2:
                imports.append((rec["src_fid"], rec["src_fp"], rec["src_text"] or ""))

        # Resolve file imports using same logic as build_import_graph
        files: dict[str, str] = {}
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            rfiles = await _execute_read(
                session,
                session,
                "MATCH (f:File {project_id:$p}) RETURN f.filepath AS fp, f.id AS fid",
                p=project_id,
                op="build_symbol_graph_files",
            )
            for rec in rfiles:
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
                    if imp.startswith("./") or imp.startswith("../"):
                        import posixpath

                        base = posixpath.normpath(posixpath.join(src_dir, imp)).lstrip(
                            "/"
                        )
                    elif imp.startswith("@/") or imp.startswith("~/"):
                        base = imp[2:]
                    elif imp.startswith("src/"):
                        base = imp
                    else:
                        base = None
                    if base:
                        for suf in (
                            "",
                            ".js",
                            ".ts",
                            ".jsx",
                            ".tsx",
                            "/index.js",
                            "/index.ts",
                            "/index.jsx",
                            "/index.tsx",
                        ):
                            candidate = (base + suf).lstrip("/")
                            if candidate in files:
                                return candidate
            elif ext == "rs":
                m = re.match(r"(?:pub\s+)?(?:use|mod)\s+(?:crate::)?([\w:]+)", src_text)
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
            return None

        SWIFT_IMPORT_RE = re.compile(r"^(?:@testable\s+)?import\s+(\w+)", re.MULTILINE)
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

        def _build_symbol_edges() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
            import_edges_local: set[tuple[str, str]] = set()
            for src_fid, src_fp, src_text in imports:
                tgt_fp = resolve(src_fp, src_text)
                ext = os.path.splitext(src_fp)[1].lstrip(".")
                names = _parse_imported_names(ext, src_text)
                if not names:
                    continue
                if ext == "swift":
                    for mod in names:
                        for fp in swift_module_map.get(mod, []):
                            if fp == src_fp:
                                continue
                            target_symbols = symbols_by_file.get(fp, {})
                            for sid in target_symbols.values():
                                import_edges_local.add((src_fid, sid))
                    continue
                if not tgt_fp or tgt_fp == src_fp:
                    if not tgt_fp and names:
                        mod_stem = os.path.basename(src_text)
                        m_path = re.search(
                            r"from\s+[\x27\x22]([^\x27\x22]+)[\x27\x22]",
                            src_text,
                        )
                        if m_path:
                            mod_stem = os.path.basename(m_path.group(1))
                        if mod_stem in stems and len(stems[mod_stem]) == 1:
                            tgt_fp = stems[mod_stem][0]
                        elif mod_stem:
                            matches = stems.get(mod_stem, [])
                            if matches:
                                tgt_fp = matches[0]
                    if not tgt_fp or tgt_fp == src_fp:
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
                    await _execute_write(
                        session,
                        "MATCH (a:File {project_id:$p})-[r:IMPORTS_SYMBOL]->() DELETE r",
                        p=project_id,
                        timeout=_NEO4J_WRITE_TIMEOUT_S,
                    )
                    edges = list(import_edges)
                    for i in range(0, len(edges), BATCH):
                        batch = [{"src": s, "tgt": t} for s, t in edges[i : i + BATCH]]
                        t0 = time.perf_counter()
                        async with _WRITE_SEM:
                            await _execute_write(
                                session,
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
                    await _execute_write(
                        session,
                        "MATCH (a:File {project_id:$p})-[r:EXPORTS_SYMBOL]->() DELETE r",
                        p=project_id,
                        timeout=_NEO4J_WRITE_TIMEOUT_S,
                    )
                    edges = list(export_edges)
                    for i in range(0, len(edges), BATCH):
                        batch = [{"src": s, "tgt": t} for s, t in edges[i : i + BATCH]]
                        t0 = time.perf_counter()
                        async with _WRITE_SEM:
                            await _execute_write(
                                session,
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
