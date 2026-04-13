"""tools/indexing.py — workspace indexing, job status, and file watcher tools."""

import os
import sys
import json
import asyncio
import hashlib
import threading
import subprocess
import time
from typing import Dict, List, Optional
from mcp.server.fastmcp import FastMCP
from _jobs import _JOBS, _JOBS_LOCK, _drain_proc_output, _finalize_job, client_session_id
from _helpers import get_memory_modules, get_project_id, get_workspace_path
from _runtime import resolve_python_runtime

from graphrag_core.config import load_env
from graphrag_core.indexing import watcher as index_watcher
from graphrag_core.indexing.manifest import build_manifest, load_indexignore_patterns, suggest_indexignore_entries
from graphrag_core.indexing.registry import record_indexed_project
from graphrag_core import neo4j as neo4j_utils

load_env()

_TX_TIMEOUT = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
_TX_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
_TX_METADATA_BASE = {"source": "lm_proxy", "tool": "indexing"}


def _is_strict_job_session() -> bool:
    return os.getenv("LM_PROXY_STRICT_JOB_SESSION", "").strip().lower() in {"1", "true", "yes"}


async def _execute_read(session, cypher: str, op: str | None = None, **params):
    return await neo4j_utils.execute_read(
        session,
        cypher,
        op=op or "read",
        op_prefix=_TX_OP_PREFIX,
        timeout_s=_TX_TIMEOUT,
        base_metadata=_TX_METADATA_BASE,
        **params,
    )


def _debug_log(message: str, **fields: object) -> None:
    payload: Dict[str, object] = {"message": message}
    payload.update(fields)
    try:
        print(
            f"[lm-proxy:indexing] {json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}",
            file=sys.stderr,
            flush=True,
        )
    except Exception:
        print(
            f"[lm-proxy:indexing] {message} {fields}",
            file=sys.stderr,
            flush=True,
        )


def _is_deadlock_error(exc: Exception) -> bool:
    code = getattr(exc, "code", "") or getattr(exc, "gql_status", "")
    if isinstance(code, str) and "DeadlockDetected" in code:
        return True
    msg = str(exc)
    return "DeadlockDetected" in msg or "deadlock" in msg.lower()


async def _retry_deadlock(label: str, fn, attempts: int = 3) -> None:
    for attempt in range(1, attempts + 1):
        try:
            await fn()
            return
        except Exception as exc:
            if not _is_deadlock_error(exc) or attempt >= attempts:
                raise
            delay = 0.2 * attempt
            print(
                f"[lm-proxy:indexing] Deadlock retry {label} ({attempt}/{attempts})",
                file=sys.stderr,
                flush=True,
            )
            await asyncio.sleep(delay)


async def load_watched_config() -> None:
    """Load the list of watched project paths from the config on startup."""
    await index_watcher.load_watched_config()


async def start_watcher(index_fn) -> asyncio.Task | None:
    """Start the polling watcher loop and return the task."""
    return await index_watcher.start_watcher(index_fn)


async def stop_watcher() -> None:
    """Cancel the watcher task if running."""
    await index_watcher.stop_watcher()


# ─────────────────────────────────────────────────────────────────────────────
# Module-level tool functions (importable by mcp_server.py for the watcher)
# ─────────────────────────────────────────────────────────────────────────────


async def index_workspace(workspace_id: str, mode: str = "incremental") -> str:
    """
    Trigger a re-index (semantic and structural) of a directory into the graph.
    Returns immediately with a job_id. Use get_index_status(job_id) to monitor progress.

    Args:
        workspace_id: The logical workspace ID or absolute path to the project root.
        mode: Status of the index:
              - "incremental" (default): Updates modified files only.
              - "rebuild": Wipes all existing project data and starts fresh.
              - "cleanup": Only removes orphaned/deleted files from the index.
    """
    try:
        import time, uuid

        project_path = get_workspace_path(workspace_id)
        project_id = get_project_id(workspace_id)
        # __file__ is tools/hands/indexing.py — step up two levels to rest_proxy/
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        runtime_dir = os.path.join(base_dir, ".runtime")
        os.makedirs(runtime_dir, exist_ok=True)

        manifest = build_manifest(project_path)
        await record_indexed_project(
            project_path, project_id, file_count=len(manifest)
        )

        manifest_path = os.path.join(runtime_dir, f"{project_id}_manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        memory_store, _, _, _, _ = get_memory_modules()
        await memory_store.open_pool()

        import graph_bootstrap
        driver = await graph_bootstrap.require_driver()
        valid_relpaths = [e["rel_path"] for e in manifest]

        # ── Handle REBUILD mode (Nuclear Wipe) ────────────────────────────────
        if mode == "rebuild":
            _debug_log("rebuild_wipe_start", project_id=project_id)
            # 1. Neo4j Wipe
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as _s:
                await _s.run(
                    "MATCH (n) WHERE n.project_id = $pid DETACH DELETE n",
                    pid=project_id
                )
            # 2. Postgres Wipe
            if memory_store._pg_pool_available():
                async with memory_store._pg_pool.connection() as conn:
                    await conn.execute(
                        "DELETE FROM codebase_embeddings WHERE project_id = %s",
                        (project_id,),
                    )

        # ── Handle CLEANUP mode (Prune Orphans) ──────────────────────────────
        elif mode == "cleanup" or mode == "incremental":
            async def _cleanup() -> None:
                async with driver.session(database=graph_bootstrap._NEO4J_DB) as _s:
                    # 1. Delete files no longer in manifest
                    await _s.run(
                        "MATCH (f:File {project_id: $pid}) "
                        "WHERE NOT f.filepath IN $paths "
                        "DETACH DELETE f",
                        pid=project_id,
                        paths=valid_relpaths,
                    )
                    # 2. Update timestamp for files STILL in manifest (detected as healthy)
                    await _s.run(
                        "MATCH (f:File {project_id: $pid}) "
                        "WHERE f.filepath IN $paths "
                        "SET f.indexed_at = timestamp()",
                        pid=project_id,
                        paths=valid_relpaths,
                    )
                    # 3. Clean up symbols that are no longer referenced
                    await _s.run(
                        "MATCH (s {project_id: $pid}) "
                        "WHERE (s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum OR s:Import) "
                        "  AND NOT ()-[:CONTAINS]->(s) "
                        "DETACH DELETE s",
                        pid=project_id,
                    )

            await _retry_deadlock("index_cleanup", _cleanup)

            if memory_store._pg_pool_available() and valid_relpaths:
                try:
                    async with memory_store._pg_pool.connection() as conn:
                        await conn.execute(
                            """
                            DELETE FROM codebase_embeddings
                            WHERE project_id = %s
                              AND NOT (file_path = ANY(%s))
                            """,
                            (project_id, valid_relpaths),
                        )
                except Exception as exc:
                    _debug_log("semantic_prune_failed", project_id=project_id, error=str(exc))

        if mode == "cleanup":
            return f"Cleanup complete for project '{project_path}' (ID: {project_id}). Orphaned nodes and chunks removed."

        # ── Launch Indexing Subprocesses ──────────────────────────────────────
        current_session = client_session_id.get()
        with _JOBS_LOCK:
            # Atomic check: Is this project already being indexed?
            for existing_id, existing_job in _JOBS.items():
                if (
                    existing_job.get("project_id") == project_id
                    and existing_job.get("status") == "running"
                ):
                    return (
                        f"⚠️  Indexing already running for this project.\n"
                        f"  job_id: {existing_id}\n"
                        f"  elapsed: {time.time() - existing_job['started_at']:.0f}s\n"
                        f"\nUse get_index_status('{existing_id}') to monitor progress."
                    )

            job_id = str(uuid.uuid4())[:8]
            _JOBS[job_id] = {
                "status": "running",
                "session_id": current_session,
                "project_id": project_id,
                "project_path": project_path,
                "file_count": len(manifest),
                "struct_rc": None,
                "sem_rc": None,
                "logs": [],
                "started_at": time.time(),
                "finished_at": None,
                "cancel_requested": False,
                "struct_proc": None,
                "sem_proc": None,
                "runtime_python": None,
                "runtime_source": None,
                "runtime_conda_env": None,
            }

        neo4j_uri = os.getenv("LM_PROXY_NEO4J_URI", "bolt://127.0.0.1:7687")
        neo4j_user = os.getenv("LM_PROXY_NEO4J_USER", "neo4j")
        neo4j_pass = os.getenv("LM_PROXY_NEO4J_PASSWORD", "password")
        runtime = resolve_python_runtime()
        python_cmd = list(runtime["cmd"])

        struct_cmd = python_cmd + [
            os.path.join(base_dir, "scripts", "run_struct_index.py"),
            project_path,
            project_id,
            "--manifest-file",
            manifest_path,
            "--neo4j-uri",
            neo4j_uri,
            "--neo4j-user",
            neo4j_user,
            "--neo4j-pass",
            neo4j_pass,
        ]
        sem_cmd = python_cmd + [
            os.path.join(base_dir, "scripts", "index_workspace.py"),
            project_path,
            project_id,
            "--manifest-file",
            manifest_path,
        ]
        if mode == "rebuild":
            sem_cmd.append("--rebuild")

        struct_env = dict(os.environ)
        struct_env.setdefault("TS_PACK_SERIAL_PARSE", "1")
        struct_env.setdefault("TS_PACK_INCLUDE_INTRA_FILE_CALLS", "1")
        cache_dir = os.getenv("LM_PROXY_TS_PACK_CACHE_DIR")
        if cache_dir:
            struct_env.setdefault("TS_PACK_CACHE_DIR", cache_dir)
        if os.getenv("LM_PROXY_TS_PACK_AUTO_DOWNLOAD", "1").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            struct_env.setdefault("TS_PACK_AUTO_DOWNLOAD", "1")
        struct_env.setdefault("LM_PROXY_RUNTIME_PYTHON", str(runtime.get("python") or ""))
        struct_env.setdefault("LM_PROXY_RUNTIME_SOURCE", str(runtime.get("source") or ""))
        struct_proc = subprocess.Popen(
            struct_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=struct_env,
        )
        sem_proc = subprocess.Popen(
            sem_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        with _JOBS_LOCK:
            if job_id in _JOBS:
                _JOBS[job_id]["runtime_python"] = runtime.get("python")
                _JOBS[job_id]["runtime_source"] = runtime.get("source")
                _JOBS[job_id]["runtime_conda_env"] = runtime.get("conda_env")
                _JOBS[job_id]["logs"].append(
                    "[runtime] "
                    f"python={runtime.get('python')} "
                    f"source={runtime.get('source')} "
                    f"conda_env={runtime.get('conda_env') or '-'}"
                )
                _JOBS[job_id]["struct_proc"] = struct_proc
                _JOBS[job_id]["sem_proc"] = sem_proc

        threading.Thread(
            target=_drain_proc_output,
            args=(struct_proc, job_id, "[struct]", "struct_rc"),
            daemon=True,
        ).start()
        threading.Thread(
            target=_drain_proc_output,
            args=(sem_proc, job_id, "[semantic]", "sem_rc"),
            daemon=True,
        ).start()
        threading.Thread(
            target=_finalize_job, args=(job_id, manifest_path), daemon=True
        ).start()

        rebuild_info = " (Full Rebuild)" if mode == "rebuild" else ""
        return (
            f"Indexing{rebuild_info} started in background.\n"
            f"  job_id:      {job_id}\n"
            f"  project_id:  {project_id}\n"
            f"  files found: {len(manifest)}\n"
            f"\nUse get_index_status('{job_id}') to monitor progress. "
            f"A file parse summary will appear when the job completes."
        )
    except Exception as e:
        return f"Error starting indexing: {e}"


async def get_index_status(job_id: str) -> str:
    """
    Check the status of a background indexing job started by index_workspace.

    Args:
        job_id: The job ID returned by index_workspace.
    """
    current_session = client_session_id.get()
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            for jid, j in _JOBS.items():
                if jid.startswith(job_id) or j.get("project_id", "").startswith(job_id):
                    job = j
                    job_id = jid
                    break
        
        # Security: Only allow sessions to see their own jobs when strict mode is enabled
        if job and current_session and job.get("session_id") != current_session and _is_strict_job_session():
            return f"Access Denied: Job {job_id} belongs to another session."

    if job is None:
        return (
            f"No job found for id '{job_id}'.\n"
            f"Active jobs: {list(_JOBS.keys()) or 'none'}"
        )

    elapsed = time.time() - job["started_at"]
    finished = job.get("finished_at")
    struct_rc = job.get("struct_rc")
    sem_rc = job.get("sem_rc")
    logs = job.get("logs", [])
    run_summary = job.get("run_summary") or {}

    try:
        from tools.hands.project import get_last_graph_build_metric
    except Exception:
        get_last_graph_build_metric = None

    last_build = get_last_graph_build_metric() if get_last_graph_build_metric else None

    lines = [
        f"Job {job_id}: {job['status'].upper()}",
        f"  project:    {job['project_path']}",
        f"  project_id: {job['project_id']}",
        f"  files:      {job['file_count']}",
        f"  runtime:    {job.get('runtime_python') or 'unknown'}"
        + (
            f" [{job.get('runtime_source')}"
            + (
                f", env={job.get('runtime_conda_env')}"
                if job.get("runtime_conda_env")
                else ""
            )
            + "]"
            if job.get("runtime_source")
            else ""
        ),
        f"  elapsed:    {elapsed:.1f}s",
        f"  struct:     exit {struct_rc} ({'ok' if struct_rc == 0 else 'FAILED'})"
        if struct_rc is not None
        else "  struct:     running…",
        f"  semantic:   exit {sem_rc} ({'ok' if sem_rc == 0 else 'FAILED'})"
        if sem_rc is not None
        else "  semantic:   running…",
    ]
    if run_summary:
        struct_run = run_summary.get("struct_active_run_id") or "unknown"
        semantic_run = run_summary.get("semantic_active_run_id") or "unknown"
        semantic_struct = run_summary.get("semantic_active_struct_run_id") or "unknown"
        aligned = (
            run_summary.get("semantic_index_status") == "done"
            and struct_run != "unknown"
            and struct_run == semantic_struct
        )
        lines.extend(
            [
                f"  struct_run: {struct_run}",
                f"  semantic_run: {semantic_run}",
                f"  semantic_struct_run: {semantic_struct}",
                f"  aligned:    {'yes' if aligned else 'no'}",
            ]
        )
    if last_build:
        lines.append(
            "  graph_build: "
            f"{last_build.get('elapsed_ms')}ms "
            f"(project={last_build.get('project_path')})"
        )
    if finished:
        lines.append(f"  finished:   {(finished - job['started_at']):.1f}s total")
        parse_summary = None
        for line in reversed(logs):
            if "[lm-proxy:indexer] File parse summary" in line:
                parse_summary = line
                break
        if parse_summary:
            lines.append(f"  parse_summary: {parse_summary}")
    if logs:
        lines.append("\nRecent log lines (last 20):")
        lines.extend(logs[-20:])
    return "\n".join(lines)


async def cancel_index_job(job_id: str) -> str:
    """
    Cancel a running indexing job by job_id (or prefix).
    """
    current_session = client_session_id.get()
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            for jid, j in _JOBS.items():
                if jid.startswith(job_id) or j.get("project_id", "").startswith(job_id):
                    job = j
                    job_id = jid
                    break

        if job is None:
            return (
                f"No job found for id '{job_id}'.\n"
                f"Active jobs: {list(_JOBS.keys()) or 'none'}"
            )

        # Security check: Match session ID when strict mode is enabled
        if current_session and job.get("session_id") != current_session and _is_strict_job_session():
            return f"Access Denied: Cannot cancel a job belonging to another session."

        if job.get("status") != "running":
            return f"Job {job_id} is not running (status={job.get('status')})."

        job["cancel_requested"] = True
        job["status"] = "cancelling"
        struct_proc = job.get("struct_proc")
        sem_proc = job.get("sem_proc")

    for proc in [struct_proc, sem_proc]:
        try:
            if proc and proc.poll() is None:
                proc.terminate()
        except Exception:
            continue

    return f"Cancel requested for job {job_id}. Processes will terminate shortly."


async def watch_project(workspace_id: str) -> str:
    """
    Pin a project for manual background watching across sessions.

    This is the primary activation path for the watcher in the default shipped
    configuration.
    """
    project_path = get_workspace_path(workspace_id)
    if not os.path.exists(project_path):
        return f"Error: Path does not exist: {project_path}"
    abs_path = os.path.abspath(project_path)
    if not index_watcher.add_watch(abs_path):
        return f"Project already has a pinned watch: {abs_path}"
    return f"Pinned project for background watching: {abs_path}"


async def get_indexing_health(workspace_id: str, audit: bool = False) -> str:
    """
    Check the status of the structural index (Neo4j) and semantic index (Postgres).
    Compares disk mtime with recorded 'indexed_at' and 'vector_indexed_at' timestamps.

    Args:
        workspace_id: Logical workspace ID or absolute path.
        audit: If True, perform a Level 2 Deep Audit of parsing fidelity and structural integrity.
    """
    project_path = get_workspace_path(workspace_id)
    if not os.path.exists(project_path):
        return f"Error: Path does not exist: {project_path}"

    project_id = get_project_id(workspace_id)
    memory_store, _, _, _, _ = get_memory_modules()
    await memory_store.open_pool()
    import graph_bootstrap

    driver = await graph_bootstrap.require_driver()

    indexed_files: Dict[str, float] = {}
    semantic_files: Dict[str, float] = {}
    file_nodes = 0
    parsed_true = 0
    parsed_false = 0
    parsed_unknown = 0
    struct_active_run_id: str | None = None
    struct_last_successful_run_id: str | None = None
    struct_index_status: str | None = None
    semantic_active_run_id: str | None = None
    semantic_last_successful_run_id: str | None = None
    semantic_target_struct_run_id: str | None = None
    semantic_active_struct_run_id: str | None = None
    semantic_index_status: str | None = None
    
    # --- Structural Integrity Metrics (Level 2) ---
    import_total = 0
    import_resolved_internal = 0
    isolated_files: List[str] = []
    suspicious_files: List[Dict[str, object]] = []

    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        # 1. Basic Metadata (Level 1)
        records = await _execute_read(
            session,
            """
            MATCH (f:File {project_id: $pid}) 
            RETURN f.filepath AS fp, f.indexed_at AS ts, f.vector_indexed_at AS vts, f.parsed AS parsed
            """,
            pid=project_id,
            op="get_indexing_health_files",
        )
        for rec in records:
            file_nodes += 1
            if rec["ts"]:
                indexed_files[rec["fp"]] = rec["ts"] / 1000.0
            if rec["vts"]:
                semantic_files[rec["fp"]] = rec["vts"] / 1000.0
            
            p = rec["parsed"]
            if p is True: parsed_true += 1
            elif p is False: parsed_false += 1
            else: parsed_unknown += 1

        run_records = await _execute_read(
            session,
            """
            MATCH (p:Project {id:$pid})
            RETURN
              p.struct_active_run_id AS struct_active_run_id,
              p.struct_last_successful_run_id AS struct_last_successful_run_id,
              p.struct_index_status AS struct_index_status,
              p.semantic_active_run_id AS semantic_active_run_id,
              p.semantic_last_successful_run_id AS semantic_last_successful_run_id,
              p.semantic_target_struct_run_id AS semantic_target_struct_run_id,
              p.semantic_active_struct_run_id AS semantic_active_struct_run_id,
              p.semantic_index_status AS semantic_index_status
            """,
            pid=project_id,
            op="get_indexing_health_runs",
        )
        if run_records:
            rec = run_records[0]
            struct_active_run_id = rec.get("struct_active_run_id")
            struct_last_successful_run_id = rec.get("struct_last_successful_run_id")
            struct_index_status = rec.get("struct_index_status")
            semantic_active_run_id = rec.get("semantic_active_run_id")
            semantic_last_successful_run_id = rec.get("semantic_last_successful_run_id")
            semantic_target_struct_run_id = rec.get("semantic_target_struct_run_id")
            semantic_active_struct_run_id = rec.get("semantic_active_struct_run_id")
            semantic_index_status = rec.get("semantic_index_status")

        if audit:
            # 2. Internal Import Resolution Rate (Level 2)
            import_records = await _execute_read(
                session,
                """
                MATCH (f:File {project_id: $pid})-[:CONTAINS]->(i:Import)
                OPTIONAL MATCH (i)-[:RESOLVES_TO]->(target:File {project_id: $pid})
                RETURN count(i) AS total, count(target) AS resolved
                """,
                pid=project_id,
                op="audit_import_resolution",
            )
            if import_records:
                import_total = import_records[0]["total"] or 0
                import_resolved_internal = import_records[0]["resolved"] or 0

            # 3. Symbol Density Audit (Level 2)
            density_records = await _execute_read(
                session,
                """
                MATCH (f:File {project_id: $pid})
                WHERE (f.parsed = true OR f.parsed IS NULL) AND NOT (f.filepath CONTAINS '/vendor/' OR f.filepath CONTAINS '/node_modules/')
                OPTIONAL MATCH (f)-[:CONTAINS]->(s)
                WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum OR s:Method OR s:Interface
                WITH f, count(s) AS sym_count
                WHERE sym_count = 0
                RETURN f.filepath AS fp
                ORDER BY f.filepath ASC
                """,
                pid=project_id,
                op="audit_symbol_density",
            )
            symbol_bearing_exts = {".py", ".swift", ".ts", ".js", ".go", ".rs", ".c", ".cpp", ".h", ".hpp", ".rb", ".php"}
            excluded_basenames = {
                "config.py", "setup.py", "__init__.py", "conftest.py",
                "package.json", "tsconfig.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json",
                "vitest.config.ts", "playwright.config.ts", "jest.config.ts", "svelte.config.js",
                "sst.config.ts", "bunfig.toml", "flake.nix"
            }
            
            for d_rec in density_records:
                fp = d_rec["fp"]
                ext = os.path.splitext(fp)[1].lower()
                base = os.path.basename(fp)
                # Skip ambient types and explicitly excluded basenames
                if fp.lower().endswith(".d.ts") or base in excluded_basenames:
                    continue
                if ext in symbol_bearing_exts:
                    suspicious_files.append({"path": fp})

            # 4. Isolated File Detection (Heuristic)
            isolated_records = await _execute_read(
                session,
                """
                MATCH (f:File {project_id: $pid})
                WHERE NOT (
                    f.filepath ENDS WITH '.md' OR 
                    f.filepath ENDS WITH '.txt' OR 
                    f.filepath ENDS WITH '.json' OR 
                    f.filepath ENDS WITH '.yml' OR 
                    f.filepath ENDS WITH '.yaml' OR
                    f.filepath ENDS WITH '.toml' OR
                    f.filepath ENDS WITH '.nix' OR
                    f.filepath ENDS WITH '.sh' OR
                    f.filepath ENDS WITH '.d.ts' OR
                    f.filepath CONTAINS '/.github/' OR
                    f.filepath CONTAINS '/.husky/'
                )
                AND NOT EXISTS {
                    MATCH (f)-[:IMPORTS|CALLS|ASSET_LINKS|CALLS_API|CALLS_SERVICE|CALLS_DB]-(other:File {project_id: $pid})
                    WHERE f <> other
                }
                RETURN f.filepath AS fp
                LIMIT 10
                """,
                pid=project_id,
                op="audit_isolation",
            )
            isolated_files = [r["fp"] for r in isolated_records]

    if not indexed_files and not semantic_files:
        if file_nodes > 0:
            return (
                f"Project '{project_path}' ({project_id}) has a structural index "
                f"with {file_nodes} file nodes, but no health metadata yet.\n"
                "Run index_workspace to initialize health timestamps."
            )
        return f"Project '{project_path}' ({project_id}) is not indexed. Run index_workspace first."

    # --- Phase 1: Synchronization Logic ---
    stale_graph: List[str] = []
    stale_vector: List[str] = []
    missing: List[str] = []
    manifest = build_manifest(project_path)
    total_checked_all = len(manifest)
    manifest_paths = {entry.get("rel_path") for entry in manifest if entry.get("rel_path")}

    for entry in manifest:
        rel = entry.get("rel_path")
        abs_path = entry.get("abs_path")
        if not isinstance(rel, str) or not isinstance(abs_path, str):
            continue
        try:
            mtime = os.path.getmtime(abs_path)
            if rel not in indexed_files:
                missing.append(rel)
                continue
            if mtime > indexed_files[rel]:
                stale_graph.append(rel)
            v_ts = semantic_files.get(rel, 0)
            if v_ts == 0 or mtime > v_ts:
                stale_vector.append(rel)
        except (OSError, FileNotFoundError):
            continue

    orphans_graph = [fp for fp in indexed_files if fp not in manifest_paths]
    
    ghost_chunks_count = 0
    ghost_files: List[str] = []
    if memory_store._pg_pool_available() and manifest_paths:
        try:
            async with memory_store._pg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT file_path, count(*) 
                        FROM codebase_embeddings 
                        WHERE project_id = %s AND NOT (file_path = ANY(%s))
                        GROUP BY file_path
                        """,
                        (project_id, list(manifest_paths)),
                    )
                    rows = await cur.fetchall()
                    ghost_files = [r[0] for r in rows]
                    ghost_chunks_count = sum(r[1] for r in rows)
        except Exception:
            pass

    # --- Result Formatting ---
    lines = [f"# Indexing Health Audit: `{project_path}`"]
    lines.append(f"Project ID: `{project_id}`\n")
    
    # Bucket 1: Synchronization
    lines.append("## 1. Synchronization (Level 1)")
    lines.append(f"  - Files on disk:             {total_checked_all}")
    lines.append(f"  - Files in structural index: {len(indexed_files)}")
    lines.append(f"  - Files in semantic index:   {len(semantic_files)}")
    
    sync_status = "✅ Healthy"
    if stale_graph or stale_vector or missing or orphans_graph or ghost_files:
        sync_status = "❌ Out of Sync"
    lines.append(f"  - **Sync Status**: {sync_status}")
    
    if stale_graph:
        lines.append(f"    - ❌ {len(stale_graph)} Stale Structural Files")
    if stale_vector:
        lines.append(f"    - ⚠️ {len(stale_vector)} Stale Semantic Files")
    if missing:
        lines.append(f"    - ❓ {len(missing)} Files missing from index entirely")
    if orphans_graph:
        lines.append(f"    - 🧹 {len(orphans_graph)} Orphaned nodes (files deleted from disk)")
    if ghost_files:
        lines.append(f"    - 👻 {len(ghost_files)} Ghost files with {ghost_chunks_count} dangling chunks")

    lines.append("\n## 1.5 Run Alignment")
    lines.append(f"  - Structural status:        `{struct_index_status or 'unknown'}`")
    lines.append(f"  - Structural active run:    `{struct_active_run_id or 'none'}`")
    lines.append(f"  - Structural last success:  `{struct_last_successful_run_id or 'none'}`")
    lines.append(f"  - Semantic status:          `{semantic_index_status or 'unknown'}`")
    lines.append(f"  - Semantic active run:      `{semantic_active_run_id or 'none'}`")
    lines.append(f"  - Semantic last success:    `{semantic_last_successful_run_id or 'none'}`")
    lines.append(f"  - Semantic target struct:   `{semantic_target_struct_run_id or 'none'}`")
    lines.append(f"  - Semantic active struct:   `{semantic_active_struct_run_id or 'none'}`")
    aligned = bool(
        struct_active_run_id
        and semantic_active_struct_run_id
        and struct_active_run_id == semantic_active_struct_run_id
        and semantic_index_status == "done"
    )
    lines.append(f"  - **Run Alignment**:        {'✅ Aligned' if aligned else '⚠️ Not aligned'}")

    # Bucket 2: Structural Integrity (Level 2)
    lines.append("\n## 2. Structural Integrity (Level 2)")
    parse_rate = (parsed_true / file_nodes * 100) if file_nodes > 0 else 0
    lines.append(f"  - **Parse Success Rate**: {parse_rate:.1f}% ({parsed_true}/{file_nodes})")
    
    if audit:
        import_rate = (import_resolved_internal / import_total * 100) if import_total > 0 else 0
        lines.append(f"  - **Internal Import Resolution**: {import_rate:.1f}% ({import_resolved_internal}/{import_total} resolved)")
        lines.append(f"  - **Isolated Source Files**: {len(isolated_files)} detected (supporting heuristic)")
        lines.append(f"  - **Symbol-Poor Files**: {len(suspicious_files)} detected (parsed but 0 symbols)")
    else:
        lines.append("  - *Hint: Add `audit=True` to run a deep fidelity check.*")

    # Bucket 3: Suspicious Files
    if audit and (suspicious_files or isolated_files or parsed_false > 0):
        lines.append("\n## 3. Suspicious Files / Parsing Issues")
        if parsed_false > 0:
            lines.append(f"  ### Explicit Parsing Failures ({parsed_false}):")
        
        if suspicious_files:
            lines.append(f"  ### Symbol-Poor Source Files (Top {min(5, len(suspicious_files))}):")
            for f in suspicious_files[:5]:
                lines.append(f"    - `{f['path']}`")
        
        if isolated_files:
            lines.append(f"  ### Isolated Files (No structural links):")
            for f in isolated_files[:5]:
                lines.append(f"    - `{f}`")
            if len(isolated_files) > 5:
                lines.append(f"    - ... and {len(isolated_files)-5} more")

    # Bucket 4: Recommended Actions
    lines.append("\n## 4. Recommended Actions")
    recommendations = []
    if missing or stale_graph or stale_vector:
        recommendations.append(f"- Run `index_workspace(workspace_id='{workspace_id}')` to synchronize stale/missing files.")
    if orphans_graph or ghost_files:
        recommendations.append(f"- Run `index_workspace(workspace_id='{workspace_id}', mode='cleanup')` to prune orphaned data.")
    if not aligned:
        recommendations.append(
            f"- Structural and semantic runs are not aligned. Re-run `index_workspace(workspace_id='{workspace_id}')` and confirm both phases complete successfully."
        )
    
    if audit:
        if parse_rate < 80 or (import_total > 0 and (import_resolved_internal/import_total) < 0.5):
            recommendations.append(f"- ⚠️ **Strongly Recommended**: Run `index_workspace(workspace_id='{workspace_id}', mode='rebuild')` or investigate parser/grammar compatibility.")
        elif suspicious_files:
             recommendations.append("- Investigate suspicious files for language-specific parsing gaps or grammar mismatches.")
    
    if not recommendations:
        recommendations.append("- No actions required. Everything looks healthy!")
    
    lines.extend(recommendations)
    return "\n".join(lines)


async def get_indexed_projects(query: Optional[str] = None) -> str:
    """
    Return indexed project paths from the shared graph registry (Neo4j).
    Falls back to the local registry if Neo4j is unavailable.

    Args:
        query: Optional project_id prefix or path substring to filter by.
    """
    items: list[tuple[str, str, float | None, str | None]] = []

    try:
        import graph_bootstrap

        if graph_bootstrap._NEO4J_ENABLED:
            driver = await graph_bootstrap.require_driver()
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await _execute_read(
                    session,
                    """
                    MATCH (p:Project)
                    WHERE p.project_path IS NOT NULL
                    RETURN p.id AS id,
                           p.project_path AS path,
                           p.last_indexed AS last_indexed,
                           p.source_url AS source_url
                    """,
                    op="list_indexed_projects",
                )
                for row in records:
                    pid = row.get("id")
                    path = row.get("path")
                    if not pid or not path:
                        continue
                    items.append(
                        (
                            pid,
                            path,
                            row.get("last_indexed"),
                            row.get("source_url"),
                        )
                    )
    except Exception:
        items = []

    if not items:
        data = index_watcher.load_indexed_projects()
        for pid, entry in data.items():
            path = entry.get("project_path")
            if not isinstance(path, str) or not path:
                continue
            last_indexed = entry.get("last_indexed")
            if not isinstance(last_indexed, (int, float)):
                last_indexed = None
            source_url = entry.get("source_url")
            if not isinstance(source_url, str):
                source_url = None
            items.append((pid, path, last_indexed, source_url))

    if not items:
        return "No indexed projects found."

    if query:
        q = query.strip()
        items = [
            it
            for it in items
            if it[0].startswith(q) or (q in (it[1] or "")) or (q in (it[3] or ""))
        ]
        if not items:
            return f"No indexed projects matched '{query}'."

    items.sort(key=lambda it: it[2] or 0, reverse=True)
    lines = ["## Indexed projects"]
    for pid, path, ts, source_url in items:
        suffix = f"  (last_indexed={int(ts)})" if ts else ""
        extra = f"  (source_url={source_url})" if source_url else ""
        lines.append(f"- {pid}  {path}{suffix}{extra}")
    return "\n".join(lines)


async def unwatch_project(workspace_id: str) -> str:
    """
    Remove a project's manual pinned background watch.
    """
    project_path = get_workspace_path(workspace_id)
    abs_path = os.path.abspath(project_path)
    if index_watcher.remove_watch(abs_path):
        return f"Removed pinned watch for project: {abs_path}"
    return f"Project does not have a pinned watch: {abs_path}"


async def suggest_indexignore(workspace_id: str, write: bool = False) -> str:
    """
    Suggest repo-specific .indexignore entries on top of the built-in manifest skip rules.

    Args:
        workspace_id: The logical workspace ID or absolute path to the project root.
        write: When True, create or append the suggested entries to .indexignore.
    """
    project_path = get_workspace_path(workspace_id)
    suggestions = suggest_indexignore_entries(project_path)
    existing = load_indexignore_patterns(project_path)
    indexignore_path = os.path.join(project_path, ".indexignore")

    lines = [f"## .indexignore Suggestions: `{project_path}`"]
    lines.append(
        "- Built-in manifest skips already cover common junk such as `node_modules/`, `dist/`, `tmp/`, lockfiles, caches, and large binary assets."
    )
    if existing:
        lines.append(f"- Existing custom patterns: {len(existing)}")
    else:
        lines.append("- Existing custom patterns: none")

    if not suggestions:
        lines.append("")
        lines.append("No additional repo-specific `.indexignore` entries are suggested right now.")
        if not write:
            lines.append("A new `.indexignore` file is not needed based on the current repo layout.")
        return "\n".join(lines)

    lines.append("")
    lines.append("Suggested additions:")
    for item in suggestions:
        lines.append(f"- `{item['pattern']}`")
        lines.append(f"  reason: {item['reason']}")

    if write:
        os.makedirs(project_path, exist_ok=True)
        existing_text = ""
        if os.path.exists(indexignore_path):
            with open(indexignore_path, "r", encoding="utf-8") as fh:
                existing_text = fh.read().rstrip()
        additions = "\n".join(item["pattern"] for item in suggestions)
        new_parts = []
        if existing_text:
            new_parts.append(existing_text)
        else:
            new_parts.append("# Repo-specific index exclusions")
        new_parts.append("# Added by suggest_indexignore")
        new_parts.append(additions)
        with open(indexignore_path, "w", encoding="utf-8") as fh:
            fh.write("\n\n".join(part for part in new_parts if part).rstrip() + "\n")
        lines.append("")
        lines.append(f"Wrote suggestions to `{indexignore_path}`.")
    else:
        lines.append("")
        lines.append("Run `suggest_indexignore(..., write=True)` to write these entries.")

    return "\n".join(lines)


def register(mcp: FastMCP) -> None:
    """Register all indexing tools with the FastMCP instance."""
    mcp.tool()(index_workspace)
    mcp.tool()(get_index_status)
    mcp.tool()(cancel_index_job)
    mcp.tool()(watch_project)
    mcp.tool()(unwatch_project)
    mcp.tool()(suggest_indexignore)
    mcp.tool()(get_indexing_health)
    mcp.tool()(get_indexed_projects)
