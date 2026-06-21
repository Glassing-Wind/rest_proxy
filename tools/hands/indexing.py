"""tools/indexing.py — workspace indexing, job status, and file watcher tools."""

import os
import sys
import json
import asyncio
import threading
import subprocess
import time
from typing import Dict, List, Optional
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp import Context, FastMCP
from _jobs import (
    _JOBS,
    _JOBS_LOCK,
    claim_index_capacity_lock,
    claim_project_job_lock,
    _finalize_job,
    _job_control_paths,
    _persist_job_state,
    _release_index_capacity_lock,
    _release_project_job_lock,
    _reconcile_job_process_state,
    _render_job_logs,
    load_job_record,
    register_main_loop,
    client_session_id,
)
from _helpers import get_memory_modules, get_project_id, get_workspace_path
from _runtime import resolve_python_runtime
from _semantic_contract import SEMANTIC_CONTRACT_VERSION

from graphrag_core.config import load_env
from graphrag_core.indexing import watcher as index_watcher
from graphrag_core.indexing.manifest import (
    build_manifest,
    load_indexignore_patterns,
    suggest_indexignore_entries,
)
from graphrag_core.indexing.registry import record_indexed_project
from graphrag_core import neo4j as neo4j_utils

load_env()

_TX_TIMEOUT = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
_TX_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
_TX_METADATA_BASE = {"source": "lm_proxy", "tool": "indexing"}
_TS_PACK_INIT_LOCK = threading.Lock()
_TS_PACK_INIT_DONE = False


def _ensure_ts_pack_initialized(ts_pack) -> None:
    """Register ts-pack cache configuration before probing language availability."""
    global _TS_PACK_INIT_DONE
    if _TS_PACK_INIT_DONE:
        return
    with _TS_PACK_INIT_LOCK:
        if _TS_PACK_INIT_DONE:
            return
        init = getattr(ts_pack, "init", None)
        if callable(init):
            config = {}
            cache_dir = os.getenv("LM_PROXY_TS_PACK_CACHE_DIR")
            if cache_dir:
                config["cache_dir"] = cache_dir
            try:
                init(config)
            except Exception:
                pass
        _TS_PACK_INIT_DONE = True


def _is_strict_job_session() -> bool:
    return os.getenv("LM_PROXY_STRICT_JOB_SESSION", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _resolve_active_job_locked(job_id: str) -> tuple[str, dict | None]:
    job_key = str(job_id or "").strip()
    job = _JOBS.get(job_key)
    if job is not None:
        return job_key, job
    for jid, candidate in _JOBS.items():
        project_id = str(candidate.get("project_id") or "")
        if jid.startswith(job_key) or project_id.startswith(job_key):
            return jid, candidate
    return job_key, None


def _active_job_summary_lines() -> list[str]:
    with _JOBS_LOCK:
        active_jobs = [
            (jid, dict(job))
            for jid, job in sorted(_JOBS.items())
            if str(job.get("status") or "").lower() in {"running", "cancelling"}
        ]
    if not active_jobs:
        return ["Active jobs: none"]

    now = time.time()
    lines = ["Active jobs:"]
    for jid, job in active_jobs:
        started_at = float(job.get("started_at") or now)
        elapsed = max(0.0, now - started_at)
        status = str(job.get("status") or "unknown").upper()
        project = str(job.get("project_path") or job.get("project_id") or "unknown")
        lines.append(f"  - {jid}: {status} {elapsed:.0f}s {project}")
    return lines


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


async def _execute_write_scalar(
    session,
    cypher: str,
    *,
    op: str,
    result_key: str,
    **params,
) -> int:
    from neo4j import unit_of_work

    metadata = dict(_TX_METADATA_BASE)
    op_value = op or "write"
    if _TX_OP_PREFIX:
        op_value = f"{_TX_OP_PREFIX}.{op_value}"
    metadata["op"] = op_value

    @unit_of_work(timeout=_TX_TIMEOUT, metadata=metadata)
    async def _tx(tx):
        result = await tx.run(cypher, **params)
        record = await result.single()
        if not record:
            return 0
        return int(record.get(result_key) or 0)

    if hasattr(session, "execute_write"):
        return await session.execute_write(_tx)
    return await _tx(session)


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


def _is_source_eligible_structural_path(file_path: str | None) -> bool:
    """Return True for source files that should count toward structural parse quality."""
    if not file_path:
        return False
    norm = str(file_path).replace("\\", "/").lower()
    basename = os.path.basename(norm)

    if any(
        token in norm
        for token in (
            "/.github/",
            "/docs/",
            "/docs-site/",
            "/agent_docs/",
            "/.claude/",
            "/.cursor/",
        )
    ):
        return False

    if basename in {
        "makefile",
        ".gitignore",
        "readme.md",
        "contributing.md",
        "agents.md",
        "claude.md",
        "pyproject.toml",
        "package.json",
        "tsconfig.json",
        "mkdocs.yml",
        "wrangler.toml",
    }:
        return False

    ext = os.path.splitext(norm)[1].lower()
    return ext in {
        ".py",
        ".swift",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".rb",
        ".php",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".m",
        ".mm",
    }


def _is_structural_expected_manifest_path(rel_path: str | None) -> bool:
    """Return True when structural indexing is expected to materialize a File node."""
    if not rel_path:
        return False
    basename = os.path.basename(str(rel_path).replace("\\", "/"))
    if basename in {".gitignore", ".indexignore", ".env", ".env.example"}:
        return False
    return True


def _is_semantic_expected_path(
    rel_path: str | None,
    abs_path: str | None,
    ext: str | None = None,
) -> bool:
    """Return True when the semantic worker would reasonably be expected to emit chunks."""
    rel = str(rel_path or "")
    abs_file = str(abs_path or "")
    ext_norm = str(ext or os.path.splitext(rel)[1]).lower().lstrip(".")
    rel_norm = rel.replace("\\", "/").lower()
    if ".xcassets/" in rel_norm:
        return False
    if rel_norm.endswith("info.plist"):
        return False
    if abs_file:
        try:
            size = os.path.getsize(abs_file)
            if size <= 0:
                return False
            if size <= 4096:
                try:
                    with open(abs_file, "r", encoding="utf-8", errors="ignore") as fh:
                        if not fh.read().strip():
                            return False
                except OSError:
                    pass
        except OSError:
            return False
    try:
        import tree_sitter_language_pack as ts_pack
    except Exception:
        return _is_source_eligible_structural_path(rel_path)

    _ensure_ts_pack_initialized(ts_pack)

    try:
        fallback_allowed = bool(
            getattr(ts_pack, "should_use_line_window_fallback", lambda _p: False)(rel)
        )
    except Exception:
        fallback_allowed = False
    if fallback_allowed:
        return True

    lang = None
    try:
        if ext_norm:
            lang = ts_pack.detect_language_from_extension(ext_norm)
    except Exception:
        lang = None
    if not lang and abs_file:
        try:
            lang = ts_pack.detect_language(abs_file)
        except Exception:
            lang = None
    if not lang:
        return False
    try:
        has_language = getattr(ts_pack, "has_language", None)
        if callable(has_language):
            return bool(has_language(lang)) or _is_source_eligible_structural_path(
                rel_path
            )
    except Exception:
        return _is_source_eligible_structural_path(rel_path)
    return True


def _is_deadlock_error(exc: Exception) -> bool:
    code = getattr(exc, "code", "") or getattr(exc, "gql_status", "")
    if isinstance(code, str) and "DeadlockDetected" in code:
        return True
    msg = str(exc)
    return "DeadlockDetected" in msg or "deadlock" in msg.lower()


async def _get_apple_graph_health(session, project_id: str) -> dict[str, int]:
    rows = await _execute_read(
        session,
        """
        CALL () {
          MATCH (f:File {project_id: $pid})
          RETURN
            count(CASE WHEN coalesce(f.filepath, f.file_path) ENDS WITH '.xcodeproj/project.pbxproj' THEN 1 END) AS project_files,
            count(CASE WHEN coalesce(f.filepath, f.file_path) ENDS WITH '.xcworkspace/contents.xcworkspacedata' THEN 1 END) AS workspace_files,
            count(CASE WHEN coalesce(f.filepath, f.file_path) ENDS WITH '.xcscheme' THEN 1 END) AS scheme_files,
            count(CASE WHEN coalesce(f.filepath, f.file_path) ENDS WITH '.storyboard'
                            OR coalesce(f.filepath, f.file_path) ENDS WITH '.xib'
                            OR coalesce(f.filepath, f.file_path) CONTAINS '.xcassets/'
                       THEN 1 END) AS resource_like_files
        }
        CALL () {
          MATCH (n:XcodeTarget {project_id: $pid})
          RETURN count(n) AS targets
        }
        CALL () {
          MATCH (n:XcodeScheme {project_id: $pid})
          RETURN count(n) AS schemes
        }
        CALL () {
          MATCH (n:XcodeWorkspace {project_id: $pid})
          RETURN count(n) AS workspaces
        }
        CALL () {
          MATCH (n:Resource {project_id: $pid})
          RETURN count(n) AS resources
        }
        CALL () {
          MATCH (:XcodeTarget {project_id: $pid})-[rel:BUNDLES_FILE]->(:File {project_id: $pid})
          RETURN count(rel) AS bundles_file_edges
        }
        CALL () {
          MATCH (:XcodeScheme {project_id: $pid})-[rel:BUILDS_TARGET]->(:XcodeTarget {project_id: $pid})
          RETURN count(rel) AS builds_target_edges
        }
        CALL () {
          MATCH (:XcodeWorkspace {project_id: $pid})-[rel:REFERENCES_PROJECT]->(:File {project_id: $pid})
          RETURN count(rel) AS references_project_edges
        }
        RETURN project_files, workspace_files, scheme_files, resource_like_files,
               targets, schemes, workspaces, resources,
               bundles_file_edges, builds_target_edges, references_project_edges
        """,
        pid=project_id,
        op="get_indexing_health_apple_graph",
    )
    return dict(rows[0]) if rows else {}


async def _get_shadow_graph_health(session) -> dict[str, int]:
    node_rows = await _execute_read(
        session,
        """
        MATCH (n)
        WHERE n.project_id CONTAINS '::shadow::'
        RETURN count(n) AS nodes, count(DISTINCT n.project_id) AS projects
        """,
        op="get_shadow_graph_node_health",
    )
    rel_rows = await _execute_read(
        session,
        """
        MATCH ()-[r]->()
        WHERE r.project_id CONTAINS '::shadow::'
        RETURN count(r) AS rels, count(DISTINCT r.project_id) AS rel_projects
        """,
        op="get_shadow_graph_rel_health",
    )
    node_record = node_rows[0] if node_rows else {}
    rel_record = rel_rows[0] if rel_rows else {}
    return {
        "nodes": int(node_record.get("nodes") or 0),
        "projects": int(node_record.get("projects") or 0),
        "rels": int(rel_record.get("rels") or 0),
        "rel_projects": int(rel_record.get("rel_projects") or 0),
    }


def _describe_apple_graph_health(coverage: dict[str, int]) -> list[str]:
    notes: List[str] = []
    if coverage.get("project_files", 0) > 0 and coverage.get("targets", 0) == 0:
        notes.append("project files exist, but no XcodeTarget nodes were materialized")
    if coverage.get("scheme_files", 0) > 0 and coverage.get("schemes", 0) == 0:
        notes.append("scheme files exist, but no XcodeScheme nodes were materialized")
    if coverage.get("workspace_files", 0) > 0 and coverage.get("workspaces", 0) == 0:
        notes.append(
            "workspace metadata exists, but no XcodeWorkspace nodes were materialized"
        )
    if coverage.get("resource_like_files", 0) > 0 and coverage.get("resources", 0) == 0:
        notes.append(
            "resource-like files exist, but no Resource nodes were materialized"
        )
    if (
        coverage.get("scheme_files", 0) > 0
        and coverage.get("builds_target_edges", 0) == 0
    ):
        notes.append("scheme files exist, but no BUILDS_TARGET edges were created")
    if (
        coverage.get("workspace_files", 0) > 0
        and coverage.get("references_project_edges", 0) == 0
    ):
        notes.append(
            "workspace metadata exists, but no REFERENCES_PROJECT edges were created"
        )
    if (
        coverage.get("resource_like_files", 0) > 0
        and coverage.get("bundles_file_edges", 0) == 0
    ):
        notes.append(
            "resource-like files exist, but no BUNDLES_FILE edges were created"
        )
    return notes


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
    claimed_lock = False
    project_id = ""
    job_id = ""
    try:
        import time
        import uuid

        # Standalone async callers do not go through mcp_server.py startup, so
        # capture the active loop here as the canonical loop for post-index
        # maintenance and other driver-bound follow-up work.
        register_main_loop(asyncio.get_running_loop())

        project_path = get_workspace_path(workspace_id)
        project_id = get_project_id(workspace_id)
        current_session = client_session_id.get()
        job_id = str(uuid.uuid4())[:8]
        with _JOBS_LOCK:
            # Atomic in-process guard for this server instance.
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
        claimed, blocking_job = claim_project_job_lock(
            project_id,
            job_id,
            project_path=project_path,
        )
        if not claimed:
            blocking_id = (
                str((blocking_job or {}).get("job_id") or "").strip() or "unknown"
            )
            elapsed = None
            if isinstance(blocking_job, dict) and blocking_job.get("started_at"):
                elapsed = max(0.0, time.time() - float(blocking_job["started_at"]))
            lines = [
                "⚠️  Indexing already running for this project.",
                f"  job_id: {blocking_id}",
            ]
            if elapsed is not None:
                lines.append(f"  elapsed: {elapsed:.0f}s")
            if blocking_id != "unknown":
                lines.append("")
                lines.append(
                    f"Use get_index_status('{blocking_id}') to monitor progress."
                )
            return "\n".join(lines)
        claimed_lock = True
        claimed_capacity, blocking_capacity_job = claim_index_capacity_lock(
            job_id,
            project_id=project_id,
            project_path=project_path,
        )
        if not claimed_capacity:
            _release_project_job_lock(project_id, job_id)
            claimed_lock = False
            blocking_id = (
                str((blocking_capacity_job or {}).get("job_id") or "").strip()
                or "unknown"
            )
            blocking_project = str(
                (blocking_capacity_job or {}).get("project_path") or ""
            ).strip()
            lines = [
                "⚠️  Another indexing job is already running.",
                f"  job_id: {blocking_id}",
            ]
            if blocking_project:
                lines.append(f"  project: {blocking_project}")
            lines.append("")
            lines.append(
                "This daemon serializes indexing by default to avoid saturating Neo4j Desktop."
            )
            if blocking_id != "unknown":
                lines.append(
                    f"Use get_index_status('{blocking_id}') to monitor progress."
                )
            lines.append(
                "Set LM_PROXY_MAX_CONCURRENT_INDEX_JOBS above 1 only for a tuned Neo4j server."
            )
            return "\n".join(lines)
        # __file__ is tools/hands/indexing.py — step up two levels to rest_proxy/
        base_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        runtime_dir = os.path.join(base_dir, ".runtime")
        os.makedirs(runtime_dir, exist_ok=True)

        manifest = build_manifest(project_path)
        await record_indexed_project(project_path, project_id, file_count=len(manifest))

        manifest_path = os.path.join(runtime_dir, f"{project_id}_manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        try:
            from memory import bootstrap as memory_bootstrap

            await memory_bootstrap.bootstrap_schema()
        except Exception as exc:
            _debug_log("schema_bootstrap_failed", project_id=project_id, error=str(exc))

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
                    pid=project_id,
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
                    _debug_log(
                        "semantic_prune_failed", project_id=project_id, error=str(exc)
                    )

        if mode == "cleanup":
            _release_project_job_lock(project_id, job_id)
            claimed_lock = False
            return f"Cleanup complete for project '{project_path}' (ID: {project_id}). Orphaned nodes and chunks removed."

        # ── Launch Indexing Subprocesses ──────────────────────────────────────
        control_paths = _job_control_paths(job_id)
        with _JOBS_LOCK:
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
                "last_log_at": time.time(),
                "finished_at": None,
                "cancel_requested": False,
                "struct_proc": None,
                "sem_proc": None,
                "runtime_python": None,
                "runtime_source": None,
                "runtime_conda_env": None,
                "struct_pid": None,
                "sem_pid": None,
                "struct_log_path": control_paths["struct_log_path"],
                "semantic_log_path": control_paths["semantic_log_path"],
                "manifest_path": manifest_path,
            }
        _persist_job_state(job_id)

        neo4j_uri = os.getenv("LM_PROXY_NEO4J_URI", "bolt://127.0.0.1:7687")
        neo4j_user = os.getenv("LM_PROXY_NEO4J_USER", "neo4j")
        neo4j_pass = os.getenv("LM_PROXY_NEO4J_PASSWORD", "password")
        neo4j_db = os.getenv("LM_PROXY_NEO4J_DB", "proxy")
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
            "--neo4j-db",
            neo4j_db,
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
        struct_env.setdefault(
            "LM_PROXY_RUNTIME_PYTHON", str(runtime.get("python") or "")
        )
        struct_env.setdefault(
            "LM_PROXY_RUNTIME_SOURCE", str(runtime.get("source") or "")
        )
        struct_log_fh = open(control_paths["struct_log_path"], "a", encoding="utf-8")
        sem_log_fh = open(control_paths["semantic_log_path"], "a", encoding="utf-8")
        struct_proc = subprocess.Popen(
            struct_cmd,
            stdout=struct_log_fh,
            stderr=subprocess.STDOUT,
            text=True,
            env=struct_env,
            start_new_session=True,
        )
        sem_proc = subprocess.Popen(
            sem_cmd,
            stdout=sem_log_fh,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        struct_log_fh.close()
        sem_log_fh.close()

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
                _JOBS[job_id]["struct_pid"] = struct_proc.pid
                _JOBS[job_id]["sem_pid"] = sem_proc.pid
        _persist_job_state(job_id)
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
        if claimed_lock and project_id and job_id:
            _release_project_job_lock(project_id, job_id)
            _release_index_capacity_lock(job_id)
        return f"Error starting indexing: {e}"


async def get_index_status(job_id: str) -> str:
    """
    Check the status of a background indexing job started by index_workspace.

    Args:
        job_id: The job ID returned by index_workspace.
    """
    current_session = client_session_id.get()
    with _JOBS_LOCK:
        job_id, job = _resolve_active_job_locked(job_id)

        # Security: Only allow sessions to see their own jobs when strict mode is enabled
        if (
            job
            and current_session
            and job.get("session_id") != current_session
            and _is_strict_job_session()
        ):
            return f"Access Denied: Job {job_id} belongs to another session."

    if job is None:
        job = load_job_record(job_id)
        if job is None:
            return "\n".join(
                [f"No job found for id '{job_id}'.", *_active_job_summary_lines()]
            )

    job = _reconcile_job_process_state(job_id) or job
    elapsed = time.time() - job["started_at"]
    finished = job.get("finished_at")
    struct_rc = job.get("struct_rc")
    sem_rc = job.get("sem_rc")
    logs = _render_job_logs(job)
    last_log_at = job.get("last_log_at")
    run_summary = job.get("run_summary") or {}
    metrics = job.get("metrics") or {}

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
    if (
        job.get("status") in {"running", "cancelling"}
        and last_log_at
        and (time.time() - last_log_at) >= 300
    ):
        lines.append(
            f"  heartbeat:  stale ({time.time() - last_log_at:.0f}s since last log)"
        )
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
    if metrics.get("struct"):
        struct_metrics = metrics["struct"]
        lines.append(
            "  struct_metrics: "
            f"parse={struct_metrics.get('parse_s')}s "
            f"nodes={struct_metrics.get('nodes_s')}s "
            f"imports={struct_metrics.get('imports_s')}s "
            f"rels={struct_metrics.get('rels_s')}s "
            f"calls={struct_metrics.get('calls_s')}s "
            f"total={struct_metrics.get('total_s')}s"
        )
    if metrics.get("semantic"):
        sem_metrics = metrics["semantic"]
        lines.append(
            "  semantic_metrics: "
            f"new={sem_metrics.get('new_chunks')} "
            f"skipped={sem_metrics.get('skipped_chunks')} "
            f"parsed_files={sem_metrics.get('parsed_files')} "
            f"skipped_files={sem_metrics.get('skipped_files')} "
            f"total={sem_metrics.get('total_s')}s"
        )
    if metrics.get("gds"):
        gds = metrics["gds"]
        lines.append(
            "  gds: "
            + " | ".join(
                f"{label}={detail}"
                for label, detail in (
                    ("leiden", gds.get("leiden")),
                    ("betweenness", gds.get("betweenness")),
                    ("wcc", gds.get("wcc")),
                )
                if detail
            )
        )
    if last_build:
        lines.append(
            "  graph_build: "
            f"{last_build.get('elapsed_ms')}ms "
            f"(project={last_build.get('project_path')})"
        )
    if job.get("post_index_maintenance_error"):
        lines.append(
            "  maintenance: FAILED ("
            f"{job['post_index_maintenance_error']})"
        )
    elif job.get("post_index_maintenance_done"):
        lines.append("  maintenance: complete")
    elif job.get("status") == "done" and struct_rc == 0:
        lines.append("  maintenance: finalizing")
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


async def cancel_index_job(job_id: str, force: bool = False) -> str:
    """
    Cancel a running indexing job by job_id (or prefix).

    Args:
        job_id: Job ID, job ID prefix, or project ID prefix.
        force: Allow an explicit admin override when strict session ownership
            would otherwise deny cancellation.
    """
    current_session = client_session_id.get()
    with _JOBS_LOCK:
        job_id, job = _resolve_active_job_locked(job_id)

        # Security check: Match session ID when strict mode is enabled
        if (
            job
            and current_session
            and job.get("session_id") != current_session
            and _is_strict_job_session()
            and not force
        ):
            return (
                "Access Denied: Cannot cancel a job belonging to another session.\n"
                f"Use cancel_index_job('{job_id}', force=True) only for an explicit "
                "admin override."
            )

    if job is None:
        job = load_job_record(job_id)
        if job is None:
            return "\n".join(
                [f"No job found for id '{job_id}'.", *_active_job_summary_lines()]
            )

    if (
        current_session
        and job.get("session_id") != current_session
        and _is_strict_job_session()
        and not force
    ):
        return (
            "Access Denied: Cannot cancel a job belonging to another session.\n"
            f"Use cancel_index_job('{job_id}', force=True) only for an explicit "
            "admin override."
        )

    if job.get("status") != "running":
        return f"Job {job_id} is not running (status={job.get('status')})."

    with _JOBS_LOCK:
        if job_id not in _JOBS:
            _JOBS[job_id] = dict(job)
        if job_id in _JOBS:
            _JOBS[job_id]["cancel_requested"] = True
            _JOBS[job_id]["status"] = "cancelling"
        struct_proc = _JOBS.get(job_id, {}).get("struct_proc")
        sem_proc = _JOBS.get(job_id, {}).get("sem_proc")
    struct_pid = job.get("struct_pid")
    sem_pid = job.get("sem_pid")
    _persist_job_state(job_id)

    for proc in [struct_proc, sem_proc]:
        try:
            if proc and proc.poll() is None:
                proc.terminate()
        except Exception:
            continue
    for pid in [struct_pid, sem_pid]:
        try:
            if pid:
                os.kill(int(pid), 15)
        except OSError:
            continue

    suffix = " Admin override used." if force else ""
    return (
        f"Cancel requested for job {job_id}. Processes will terminate shortly.{suffix}"
    )


def _local_root_path(root_uri: object) -> tuple[str | None, str | None]:
    raw_uri = str(root_uri or "").strip()
    if not raw_uri:
        return None, "missing URI"
    parsed = urlparse(raw_uri)
    if parsed.scheme.lower() != "file":
        return None, f"unsupported scheme `{parsed.scheme or '(none)'}`"
    if parsed.netloc not in {"", "localhost"}:
        return None, f"non-local file authority `{parsed.netloc}`"
    path = os.path.realpath(os.path.abspath(unquote(parsed.path)))
    if not os.path.isdir(path):
        return None, "path is not an existing directory"
    return path, None


async def watch_project(
    workspace_id: str | None = None,
    ctx: Context | None = None,
) -> str:
    """
    Pin a project for manual background watching across sessions.

    This is the primary activation path for the watcher in the default shipped
    configuration. When workspace_id is omitted, request standards-based roots
    from the active MCP client and pin each valid local file root.
    """
    if not workspace_id:
        if ctx is None:
            return "Client roots are unavailable outside an MCP request. Pass workspace_id explicitly."
        try:
            roots_result = await ctx.session.list_roots()
        except Exception as exc:
            return (
                "Client roots are unavailable or unsupported. "
                f"Pass workspace_id explicitly. ({type(exc).__name__}: {exc})"
            )

        added: list[str] = []
        existing: list[str] = []
        ignored: list[str] = []
        seen: set[str] = set()
        for root in roots_result.roots:
            root_uri = getattr(root, "uri", None)
            path, reason = _local_root_path(root_uri)
            if not path:
                ignored.append(f"{root_uri or '<missing>'} ({reason})")
                continue
            if path in seen:
                continue
            seen.add(path)
            if index_watcher.add_watch(path):
                added.append(path)
            else:
                existing.append(path)

        lines = ["## MCP Client Roots Watch Sync"]
        lines.append(f"- Roots advertised: {len(roots_result.roots)}")
        lines.append(f"- Newly pinned: {len(added)}")
        lines.append(f"- Already pinned: {len(existing)}")
        lines.append(f"- Ignored: {len(ignored)}")
        if added:
            lines.append("")
            lines.append("Pinned roots:")
            lines.extend(f"- {path}" for path in added)
        if existing:
            lines.append("")
            lines.append("Already pinned roots:")
            lines.extend(f"- {path}" for path in existing)
        if ignored:
            lines.append("")
            lines.append("Ignored roots:")
            lines.extend(f"- {item}" for item in ignored)
        if not added and not existing:
            lines.append("")
            lines.append("No valid local file roots were available; pass workspace_id explicitly.")
        return "\n".join(lines)

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
    structural_paths: set[str] = set()
    semantic_present_paths: set[str] = set()
    semantic_current_contract_paths: set[str] = set()
    semantic_expected_paths: set[str] = set()
    file_nodes = 0
    parsed_true = 0
    parsed_false = 0
    parsed_unknown = 0
    source_eligible_nodes = 0
    source_eligible_parsed_true = 0
    struct_active_run_id: str | None = None
    struct_last_successful_run_id: str | None = None
    struct_index_status: str | None = None
    semantic_active_run_id: str | None = None
    semantic_last_successful_run_id: str | None = None
    semantic_target_struct_run_id: str | None = None
    semantic_active_struct_run_id: str | None = None
    semantic_index_status: str | None = None
    apple_graph_coverage: Dict[str, int] = {}
    shadow_graph_health: Dict[str, int] = {}

    # --- Structural Integrity Metrics (Level 2) ---
    import_fact_total = 0
    import_file_edges = 0
    import_symbol_edges = 0
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
            fp = rec.get("fp")
            if isinstance(fp, str) and fp:
                structural_paths.add(fp)
            if rec["ts"]:
                indexed_files[rec["fp"]] = rec["ts"] / 1000.0
            if rec["vts"]:
                semantic_files[rec["fp"]] = rec["vts"] / 1000.0
                semantic_present_paths.add(rec["fp"])

            p = rec["parsed"]
            if p is True:
                parsed_true += 1
            elif p is False:
                parsed_false += 1
            else:
                parsed_unknown += 1
            if _is_source_eligible_structural_path(fp):
                source_eligible_nodes += 1
                if p is True:
                    source_eligible_parsed_true += 1

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

        apple_graph_coverage = await _get_apple_graph_health(session, project_id)
        shadow_graph_health = await _get_shadow_graph_health(session)

        if audit:
            # 2. Current structural import graph materialization (Level 2).
            # Import nodes preserve extracted facts; resolved internal links are
            # represented directly as File-[:IMPORTS]->File and
            # File-[:IMPORTS_SYMBOL]->Node edges.
            import_records = await _execute_read(
                session,
                """
                CALL () {
                  MATCH (:File {project_id: $pid})-[:CONTAINS]->(i:Import)
                  RETURN count(DISTINCT i) AS facts
                }
                CALL () {
                  MATCH (:File {project_id: $pid})-[r:IMPORTS]->(:File {project_id: $pid})
                  RETURN count(DISTINCT r) AS file_edges
                }
                CALL () {
                  MATCH (:File {project_id: $pid})-[r:IMPORTS_SYMBOL]->(:Node {project_id: $pid})
                  RETURN count(DISTINCT r) AS symbol_edges
                }
                RETURN facts, file_edges, symbol_edges
                """,
                pid=project_id,
                op="audit_import_materialization",
            )
            if import_records:
                import_fact_total = import_records[0]["facts"] or 0
                import_file_edges = import_records[0]["file_edges"] or 0
                import_symbol_edges = import_records[0]["symbol_edges"] or 0

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
            symbol_bearing_exts = {
                ".py",
                ".swift",
                ".ts",
                ".js",
                ".go",
                ".rs",
                ".c",
                ".cpp",
                ".h",
                ".hpp",
                ".rb",
                ".php",
            }
            excluded_basenames = {
                "config.py",
                "setup.py",
                "__init__.py",
                "conftest.py",
                "package.json",
                "tsconfig.json",
                "pnpm-lock.yaml",
                "yarn.lock",
                "package-lock.json",
                "vitest.config.ts",
                "playwright.config.ts",
                "jest.config.ts",
                "svelte.config.js",
                "sst.config.ts",
                "bunfig.toml",
                "flake.nix",
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

    if memory_store._pg_pool_available():
        try:
            async with memory_store._pg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT
                            file_path,
                            bool_or(coalesce((metadata->>'semantic_contract_version')::int, 0) = %s) AS current_contract
                        FROM codebase_embeddings
                        WHERE project_id = %s
                        GROUP BY file_path
                        """,
                        (SEMANTIC_CONTRACT_VERSION, project_id),
                    )
                    for row in await cur.fetchall():
                        if not row or not row[0]:
                            continue
                        semantic_present_paths.add(row[0])
                        if len(row) > 1 and row[1]:
                            semantic_current_contract_paths.add(row[0])
        except Exception:
            pass

    if not structural_paths and not semantic_present_paths:
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
    stale_semantic_contract: List[str] = []
    missing: List[str] = []
    manifest = build_manifest(project_path)
    total_checked_all = len(manifest)
    manifest_paths = {
        entry.get("rel_path") for entry in manifest if entry.get("rel_path")
    }

    for entry in manifest:
        rel = entry.get("rel_path")
        abs_path = entry.get("abs_path")
        if not isinstance(rel, str) or not isinstance(abs_path, str):
            continue
        try:
            mtime = os.path.getmtime(abs_path)
            if rel not in structural_paths and _is_structural_expected_manifest_path(
                rel
            ):
                missing.append(rel)
                continue
            ts = indexed_files.get(rel)
            if ts and mtime > ts:
                stale_graph.append(rel)
            semantic_expected = _is_semantic_expected_path(
                rel, abs_path, entry.get("ext")
            )
            if semantic_expected:
                semantic_expected_paths.add(rel)
            if semantic_expected and rel not in semantic_present_paths:
                stale_vector.append(rel)
                continue
            if semantic_expected and rel not in semantic_current_contract_paths:
                stale_semantic_contract.append(rel)
                continue
            v_ts = semantic_files.get(rel, 0)
            if semantic_expected and v_ts and mtime > v_ts:
                stale_vector.append(rel)
        except (OSError, FileNotFoundError):
            continue

    orphans_graph = [fp for fp in structural_paths if fp not in manifest_paths]

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
    lines.append(f"  - Files in structural index: {len(structural_paths)}")
    lines.append(f"  - Files in semantic index:   {len(semantic_present_paths)}")

    sync_status = "✅ Healthy"
    if (
        stale_graph
        or stale_vector
        or stale_semantic_contract
        or missing
        or orphans_graph
        or ghost_files
    ):
        sync_status = "❌ Out of Sync"
    lines.append(f"  - **Sync Status**: {sync_status}")

    if stale_graph:
        lines.append(f"    - ❌ {len(stale_graph)} Stale Structural Files")
    if stale_vector:
        lines.append(f"    - ⚠️ {len(stale_vector)} Stale Semantic Files")
    if stale_semantic_contract:
        lines.append(
            f"    - ⛔ {len(stale_semantic_contract)} Files on stale semantic contract"
        )
    if missing:
        lines.append(f"    - ❓ {len(missing)} Files missing from index entirely")
    if orphans_graph:
        lines.append(
            f"    - 🧹 {len(orphans_graph)} Orphaned nodes (files deleted from disk)"
        )
    if ghost_files:
        lines.append(
            f"    - 👻 {len(ghost_files)} Ghost files with {ghost_chunks_count} dangling chunks"
        )

    lines.append("\n## 1.5 Run Alignment")
    lines.append(f"  - Structural status:        `{struct_index_status or 'unknown'}`")
    lines.append(f"  - Structural active run:    `{struct_active_run_id or 'none'}`")
    lines.append(
        f"  - Structural last success:  `{struct_last_successful_run_id or 'none'}`"
    )
    lines.append(
        f"  - Semantic status:          `{semantic_index_status or 'unknown'}`"
    )
    lines.append(f"  - Semantic active run:      `{semantic_active_run_id or 'none'}`")
    lines.append(
        f"  - Semantic last success:    `{semantic_last_successful_run_id or 'none'}`"
    )
    lines.append(
        f"  - Semantic target struct:   `{semantic_target_struct_run_id or 'none'}`"
    )
    lines.append(
        f"  - Semantic active struct:   `{semantic_active_struct_run_id or 'none'}`"
    )
    struct_reference_run = struct_active_run_id or struct_last_successful_run_id
    semantic_reference_struct_run = (
        semantic_active_struct_run_id or semantic_target_struct_run_id
    )
    aligned = bool(
        struct_reference_run
        and semantic_reference_struct_run
        and struct_reference_run == semantic_reference_struct_run
        and semantic_index_status == "done"
    )
    if not aligned:
        coverage_verified = (
            struct_index_status == "done"
            and semantic_index_status == "done"
            and bool(manifest_paths)
            and bool(semantic_expected_paths)
            and not missing
            and not stale_graph
            and not stale_vector
            and len(structural_paths) >= len(manifest_paths)
            and semantic_expected_paths.issubset(semantic_present_paths)
            and semantic_expected_paths.issubset(semantic_current_contract_paths)
        )
        if coverage_verified:
            aligned = True
    lines.append(
        f"  - **Run Alignment**:        {'✅ Aligned' if aligned else '⚠️ Not aligned'}"
    )

    shadow_nodes = int(shadow_graph_health.get("nodes") or 0)
    shadow_rels = int(shadow_graph_health.get("rels") or 0)
    if shadow_nodes or shadow_rels:
        lines.append("\n## 1.6 Global Shadow Graph Residue")
        lines.append(
            f"  - Shadow project IDs with nodes: {shadow_graph_health.get('projects', 0)}"
        )
        lines.append(
            "  - Shadow project IDs with relationships: "
            f"{shadow_graph_health.get('rel_projects', 0)}"
        )
        lines.append(f"  - Shadow nodes: {shadow_nodes}")
        lines.append(f"  - Shadow relationships: {shadow_rels}")
        lines.append("  - **Shadow Cleanup Status**: ⚠️ Cleanup recommended")

    apple_files_present = any(
        apple_graph_coverage.get(key, 0) > 0
        for key in (
            "project_files",
            "scheme_files",
            "workspace_files",
            "resource_like_files",
        )
    )
    apple_notes = _describe_apple_graph_health(apple_graph_coverage)
    if apple_files_present:
        lines.append("\n## 1.75 Apple Build Coverage")
        lines.append(
            "  - Apple build files: "
            f"project={apple_graph_coverage.get('project_files', 0)} "
            f"scheme={apple_graph_coverage.get('scheme_files', 0)} "
            f"workspace={apple_graph_coverage.get('workspace_files', 0)} "
            f"resource-like={apple_graph_coverage.get('resource_like_files', 0)}"
        )
        lines.append(
            "  - Graph nodes: "
            f"targets={apple_graph_coverage.get('targets', 0)} "
            f"schemes={apple_graph_coverage.get('schemes', 0)} "
            f"workspaces={apple_graph_coverage.get('workspaces', 0)} "
            f"resources={apple_graph_coverage.get('resources', 0)}"
        )
        lines.append(
            "  - Graph edges: "
            f"bundles_file={apple_graph_coverage.get('bundles_file_edges', 0)} "
            f"builds_target={apple_graph_coverage.get('builds_target_edges', 0)} "
            f"references_project={apple_graph_coverage.get('references_project_edges', 0)}"
        )
        lines.append(
            "  - **Apple Graph Status**: "
            + ("✅ Covered" if not apple_notes else "⚠️ Partial")
        )
        for note in apple_notes:
            lines.append(f"    - {note}")

    # Bucket 2: Structural Integrity (Level 2)
    lines.append("\n## 2. Structural Integrity (Level 2)")
    parse_rate = (parsed_true / file_nodes * 100) if file_nodes > 0 else 0
    source_parse_rate = (
        source_eligible_parsed_true / source_eligible_nodes * 100
        if source_eligible_nodes > 0
        else 0
    )
    lines.append(
        f"  - **Parse Success Rate**: {source_parse_rate:.1f}% "
        f"({source_eligible_parsed_true}/{source_eligible_nodes} source-eligible files)"
    )
    if source_eligible_nodes != file_nodes:
        lines.append(
            f"  - Support-file coverage: {parse_rate:.1f}% "
            f"({parsed_true}/{file_nodes} across all manifest-kept files)"
        )

    if audit:
        lines.append(
            "  - **Import Graph Materialization**: "
            f"facts={import_fact_total}, file_edges={import_file_edges}, "
            f"symbol_edges={import_symbol_edges}"
        )
        lines.append(
            f"  - **Isolated Source Files**: {len(isolated_files)} detected (supporting heuristic)"
        )
        if isolated_files:
            lines.append(
                "    - Impact: isolated files have no structural links, so related-file, call-chain, and blast-radius tools may miss paths through them."
            )
        lines.append(
            f"  - **Symbol-Poor Files**: {len(suspicious_files)} detected (parsed but 0 symbols)"
        )
    else:
        lines.append("  - *Hint: Add `audit=True` to run a deep fidelity check.*")

    # Bucket 3: Suspicious Files
    if audit and (suspicious_files or isolated_files or parsed_false > 0):
        lines.append("\n## 3. Suspicious Files / Parsing Issues")
        if parsed_false > 0:
            lines.append(f"  ### Explicit Parsing Failures ({parsed_false}):")

        if suspicious_files:
            lines.append(
                f"  ### Symbol-Poor Source Files (Top {min(5, len(suspicious_files))}):"
            )
            for f in suspicious_files[:5]:
                lines.append(f"    - `{f['path']}`")

        if isolated_files:
            lines.append("  ### Isolated Files (No structural links):")
            for f in isolated_files[:5]:
                lines.append(f"    - `{f}`")
            if len(isolated_files) > 5:
                lines.append(f"    - ... and {len(isolated_files) - 5} more")

    # Bucket 4: Recommended Actions
    lines.append("\n## 4. Recommended Actions")
    recommendations = []
    if missing or stale_graph or stale_vector or stale_semantic_contract:
        recommendations.append(
            f"- Run `index_workspace(workspace_id='{workspace_id}')` to synchronize stale/missing files."
        )
    if orphans_graph or ghost_files:
        recommendations.append(
            f"- Run `index_workspace(workspace_id='{workspace_id}', mode='cleanup')` to prune orphaned data."
        )
    if not aligned:
        recommendations.append(
            f"- Structural and semantic runs are not aligned. Re-run `index_workspace(workspace_id='{workspace_id}')` and confirm both phases complete successfully."
        )
    if apple_notes:
        recommendations.append(
            "- Apple build metadata is only partially materialized. Re-run structural indexing after verifying the checkout contains the expected `.xcodeproj`, shared schemes, workspace metadata, and bundled resources."
        )
    if shadow_nodes or shadow_rels:
        recommendations.append(
            "- Stale shadow graph data exists. Run `cleanup_stale_shadow_graph(dry_run=False)` during a quiet indexing window."
        )

    if audit:
        if source_parse_rate < 80:
            recommendations.append(
                f"- ⚠️ **Strongly Recommended**: Run `index_workspace(workspace_id='{workspace_id}', mode='rebuild')` or investigate parser/grammar compatibility."
            )
        if import_fact_total > 0 and import_file_edges == 0 and import_symbol_edges == 0:
            recommendations.append(
                "- Import facts were extracted but no internal import links were materialized. "
                "Investigate language-specific import resolution before relying on related-file, "
                "dependency, or flow tools."
            )
        elif suspicious_files:
            recommendations.append(
                "- Investigate suspicious files for language-specific parsing gaps or grammar mismatches."
            )
        if isolated_files:
            recommendations.append(
                "- Inspect isolated files for missing import/call extraction before relying on call-chain or blast-radius completeness."
            )

    if not recommendations:
        recommendations.append("- No actions required. Everything looks healthy!")

    lines.extend(recommendations)
    return "\n".join(lines)


async def cleanup_stale_shadow_graph(
    dry_run: bool = True,
    node_batch: int = 5000,
    rel_batch: int = 5000,
    max_project_ids: int = 1000,
) -> str:
    """
    Inspect or remove stale Neo4j shadow project namespaces.

    Shadow project IDs are used while structural indexing stages a replacement
    graph. Successful promotions remove them. Residue usually means an index
    run was interrupted before promotion or cleanup completed.

    Args:
        dry_run: When True, report residue without deleting anything.
        node_batch: Max nodes to delete per transaction when dry_run is False.
        rel_batch: Max relationships to delete per transaction when dry_run is False.
        max_project_ids: Safety cap for the number of shadow project IDs to process.
    """
    node_batch = max(1, int(node_batch or 5000))
    rel_batch = max(1, int(rel_batch or 5000))
    max_project_ids = max(1, int(max_project_ids or 1000))

    import graph_bootstrap

    driver = await graph_bootstrap.require_driver()
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        before = await _get_shadow_graph_health(session)
        project_rows = await _execute_read(
            session,
            """
            CALL () {
              MATCH (n)
              WHERE n.project_id CONTAINS '::shadow::'
              RETURN n.project_id AS pid
              UNION
              MATCH ()-[r]->()
              WHERE r.project_id CONTAINS '::shadow::'
              RETURN r.project_id AS pid
            }
            RETURN DISTINCT pid
            ORDER BY pid
            LIMIT $limit
            """,
            limit=max_project_ids,
            op="list_stale_shadow_project_ids",
        )
        project_ids = [
            row.get("pid")
            for row in project_rows
            if isinstance(row.get("pid"), str) and row.get("pid")
        ]

        if dry_run:
            lines = ["## Stale Shadow Graph Cleanup Dry Run"]
            lines.append(f"- Shadow project IDs found: {len(project_ids)}")
            lines.append(f"- Shadow nodes: {int(before.get('nodes') or 0)}")
            lines.append(f"- Shadow relationships: {int(before.get('rels') or 0)}")
            if project_ids:
                lines.append(
                    "- To clean: run `cleanup_stale_shadow_graph(dry_run=False)` "
                    "during a quiet indexing window."
                )
            else:
                lines.append("- No stale shadow graph data found.")
            return "\n".join(lines)

        deleted_nodes = 0
        deleted_rels = 0
        processed = 0
        for pid in project_ids:
            processed += 1
            while True:
                count = await _execute_write_scalar(
                    session,
                    """
                    MATCH ()-[r]->()
                    WHERE r.project_id = $pid
                    WITH r LIMIT $limit
                    DELETE r
                    RETURN count(r) AS deleted
                    """,
                    op="cleanup_shadow_rels",
                    result_key="deleted",
                    pid=pid,
                    limit=rel_batch,
                )
                deleted_rels += count
                if count < rel_batch:
                    break
            while True:
                count = await _execute_write_scalar(
                    session,
                    """
                    MATCH (n {project_id: $pid})
                    WITH n LIMIT $limit
                    DETACH DELETE n
                    RETURN count(n) AS deleted
                    """,
                    op="cleanup_shadow_nodes",
                    result_key="deleted",
                    pid=pid,
                    limit=node_batch,
                )
                deleted_nodes += count
                if count < node_batch:
                    break

        after = await _get_shadow_graph_health(session)

    lines = ["## Stale Shadow Graph Cleanup"]
    lines.append(f"- Shadow project IDs processed: {processed}")
    lines.append(f"- Relationships deleted: {deleted_rels}")
    lines.append(f"- Nodes deleted: {deleted_nodes}")
    lines.append(
        f"- Before: nodes={int(before.get('nodes') or 0)} "
        f"relationships={int(before.get('rels') or 0)}"
    )
    lines.append(
        f"- After: nodes={int(after.get('nodes') or 0)} "
        f"relationships={int(after.get('rels') or 0)}"
    )
    if int(after.get("nodes") or 0) or int(after.get("rels") or 0):
        lines.append(
            "- Residue remains; rerun with a higher `max_project_ids` or inspect Neo4j for active shadow writes."
        )
    else:
        lines.append("- No stale shadow graph data remains.")
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
        lines.append(
            "No additional repo-specific `.indexignore` entries are suggested right now."
        )
        if not write:
            lines.append(
                "A new `.indexignore` file is not needed based on the current repo layout."
            )
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
        lines.append(
            "Run `suggest_indexignore(..., write=True)` to write these entries."
        )

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
    mcp.tool()(cleanup_stale_shadow_graph)
    mcp.tool()(get_indexed_projects)
