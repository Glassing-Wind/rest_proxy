"""tools/indexing.py — workspace indexing, job status, and file watcher tools."""

import os
import sys
import json
import asyncio
import hashlib
import threading
import subprocess
from neo4j import unit_of_work
from typing import Dict, List, Optional
from mcp.server.fastmcp import FastMCP
from _jobs import _JOBS, _JOBS_LOCK, _drain_proc_output, _finalize_job
from _helpers import get_memory_modules

# --- Background Watcher State ---
CONFIG_DIR = os.path.expanduser("~/.gemini/antigravity/rest_proxy_config")
WATCHED_CONFIG_PATH = os.path.join(CONFIG_DIR, "watched_projects.json")
INDEXED_CONFIG_PATH = os.path.join(CONFIG_DIR, "indexed_projects.json")
WATCHED_PATHS: Dict[str, Dict[str, float]] = {}  # project_path → {file_path: mtime}
WATCH_INTERVAL = 30  # seconds between polls

_WATCHER_TASK: asyncio.Task | None = None

_TX_TIMEOUT = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
_TX_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
_TX_METADATA_BASE = {"source": "lm_proxy", "tool": "indexing"}


async def _execute_read(session, cypher: str, op: str | None = None, **params):
    metadata = dict(_TX_METADATA_BASE)
    op_value = op or "read"
    if _TX_OP_PREFIX:
        op_value = f"{_TX_OP_PREFIX}.{op_value}"
    metadata["op"] = op_value

    @unit_of_work(timeout=_TX_TIMEOUT, metadata=metadata)
    async def _tx(tx):
        result = await tx.run(cypher, **params)
        return await result.data()

    if hasattr(session, "execute_read"):
        return await session.execute_read(_tx)
    return await _tx(session)


def _save_watched_config() -> None:
    """Save the list of watched project paths to a local JSON config."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(WATCHED_CONFIG_PATH, "w") as f:
            json.dump(list(WATCHED_PATHS.keys()), f)
    except Exception as e:
        print(f"[lm-proxy:watcher] Failed to save config: {e}", file=sys.stderr)


def _load_indexed_projects() -> dict[str, dict[str, object]]:
    try:
        if os.path.exists(INDEXED_CONFIG_PATH):
            with open(INDEXED_CONFIG_PATH) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        return {}
    return {}


def _save_indexed_projects(data: dict[str, dict[str, object]]) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(INDEXED_CONFIG_PATH, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[lm-proxy:indexing] Failed to save index map: {e}", file=sys.stderr)


async def _record_indexed_project(
    project_path: str,
    project_id: str,
    file_count: int | None = None,
    source_url: str | None = None,
    source_type: str | None = None,
) -> None:
    try:
        import time

        abs_path = os.path.abspath(project_path)
        data = _load_indexed_projects()
        data[project_id] = {
            "project_path": abs_path,
            "last_indexed": time.time(),
        }
        _save_indexed_projects(data)
    except Exception:
        pass

    try:
        import graph_bootstrap

        if not graph_bootstrap._NEO4J_ENABLED:
            return
        driver = await graph_bootstrap.require_driver()
        now = time.time()
        effective_type = source_type or ("git" if source_url else "local")
        name = os.path.basename(abs_path)

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            tx_metadata = dict(_TX_METADATA_BASE)
            tx_metadata["op"] = "update_project_indexed"

            @unit_of_work(timeout=_TX_TIMEOUT, metadata=tx_metadata)
            async def _tx(tx):
                result = await tx.run(
                    """
                    MERGE (p:Project {id: $id})
                    SET p.project_path = $path,
                        p.name = $name,
                        p.last_indexed = $ts,
                        p.source_type = $source_type,
                        p.source_url = $source_url,
                        p.file_count = $file_count
                    """,
                    id=project_id,
                    path=abs_path,
                    name=name,
                    ts=now,
                    source_type=effective_type,
                    source_url=source_url,
                    file_count=file_count,
                )
                await result.consume()

            if hasattr(session, "execute_write"):
                await session.execute_write(_tx)
            else:
                await _tx(session)
    except Exception:
        return


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
    try:
        if os.path.exists(WATCHED_CONFIG_PATH):
            with open(WATCHED_CONFIG_PATH) as f:
                paths = json.load(f)
            for p in paths:
                if os.path.exists(p):
                    WATCHED_PATHS[os.path.abspath(p)] = {}
            print(
                f"[lm-proxy:watcher] Restored {len(WATCHED_PATHS)} watched projects.",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"[lm-proxy:watcher] Failed to load config: {e}", file=sys.stderr)


async def start_watcher(index_fn) -> asyncio.Task:
    """Start the polling watcher loop and return the task."""
    global _WATCHER_TASK
    _WATCHER_TASK = asyncio.create_task(_poll_watcher(index_fn))
    return _WATCHER_TASK


async def stop_watcher() -> None:
    """Cancel the watcher task if running."""
    global _WATCHER_TASK
    if _WATCHER_TASK:
        _WATCHER_TASK.cancel()
        try:
            await _WATCHER_TASK
        except asyncio.CancelledError:
            pass


async def _poll_watcher(index_fn) -> None:
    """Background loop to check for file changes in watched projects."""
    while True:
        try:
            for project_path, last_mtimes in list(WATCHED_PATHS.items()):
                changed = False
                current_mtimes = {}
                for root, _, files in os.walk(project_path):
                    if any(
                        x in root
                        for x in [
                            ".git",
                            "node_modules",
                            "__pycache__",
                            "build",
                            "dist",
                        ]
                    ):
                        continue
                    for f in files:
                        if not f.endswith(
                            (
                                ".py",
                                ".swift",
                                ".js",
                                ".ts",
                                ".jsx",
                                ".tsx",
                                ".md",
                                ".rs",
                                ".go",
                                ".cpp",
                                ".c",
                                ".h",
                                ".java",
                                ".rb",
                                ".php",
                                ".cs",
                                ".json",
                                ".toml",
                                ".yaml",
                                ".yml",
                            )
                        ):
                            continue
                        fpath = os.path.join(root, f)
                        try:
                            mtime = os.path.getmtime(fpath)
                            current_mtimes[fpath] = mtime
                            if fpath not in last_mtimes or mtime > last_mtimes[fpath]:
                                changed = True
                        except (OSError, FileNotFoundError):
                            continue
                if not changed and len(current_mtimes) != len(last_mtimes):
                    changed = True
                WATCHED_PATHS[project_path] = current_mtimes
                if changed:
                    print(
                        f"[lm-proxy:watcher] Change detected in {project_path}. Triggering index...",
                        file=sys.stderr,
                    )
                    try:
                        await index_fn(project_path)
                    except Exception as e:
                        print(
                            f"[lm-proxy:watcher] Indexing failed: {e}", file=sys.stderr
                        )
        except Exception as e:
            print(f"[lm-proxy:watcher] Loop error: {e}", file=sys.stderr)
        await asyncio.sleep(WATCH_INTERVAL)


def _build_manifest(project_path: str) -> list[dict[str, object]]:
    from pathlib import Path

    manifest: list[dict[str, object]] = []
    root = Path(project_path)
    skip_dirs = {
        ".git",
        "__pycache__",
        "node_modules",
        ".cache",
        ".ruff_cache",
        ".next",
        ".turbo",
        ".gemini",
        ".agents",
        ".agent",
        ".build",
        "target",
        "build",
        "dist",
        "Pods",
        "DerivedData",
        "venv",
        ".venv",
        "env",
        "vendor",
        "third_party",
        "vendored",
        "external",
        "testdata",
        "fixtures",
        "__fixtures__",
        "__mocks__",
        "mocks",
        "snapshots",
        "__snapshots__",
        "parsers",
    }
    skip_exts = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".mp4",
        ".mp3",
        ".bin",
        ".exe",
        ".dll",
        ".so",
        ".pyc",
        ".lock",
        ".dylib",
        ".a",
        ".o",
        ".dSYM",
        ".wasm",
        ".swiftmodule",
        ".swiftdeps",
        ".d",
    }
    skip_filenames = {"parser.c", "grammar.json", "node-types.json", "parser.h"}
    max_file_size = 1 * 1024 * 1024

    indexignore_patterns: list[str] = []
    indexignore_path = root / ".indexignore"
    if indexignore_path.exists():
        import fnmatch

        for line in indexignore_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                indexignore_patterns.append(line)

    def _is_ignored(rel: str) -> bool:
        if not indexignore_patterns:
            return False
        import fnmatch

        parts = rel.replace("\\", "/")
        for pat in indexignore_patterns:
            if fnmatch.fnmatch(parts, pat):
                return True
            if fnmatch.fnmatch(parts.split("/")[-1], pat):
                return True
        return False

    for path in root.rglob("*"):
        if any(part in skip_dirs for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() in skip_exts:
            continue
        if path.name in skip_filenames:
            continue
        try:
            rel = str(path.relative_to(root))
            if _is_ignored(rel):
                continue
            stats = path.stat()
            if stats.st_size > max_file_size:
                continue
            manifest.append(
                {
                    "abs_path": str(path.absolute()),
                    "rel_path": rel,
                    "ext": path.suffix.lower().lstrip("."),
                    "size": stats.st_size,
                }
            )
        except Exception:
            continue
    return manifest


# ─────────────────────────────────────────────────────────────────────────────
# Module-level tool functions (importable by mcp_server.py for the watcher)
# ─────────────────────────────────────────────────────────────────────────────


async def index_workspace(project_path: str) -> str:
    """
    Trigger a full re-index (semantic and structural) of a directory into the graph.
    Returns immediately with a job_id. Use get_index_status(job_id) to monitor progress.

    Args:
        project_path: Absolute path to the project root.
    """
    try:
        import time, uuid

        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        # __file__ is tools/indexing.py — step up one level to rest_proxy/
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        manifest = _build_manifest(project_path)
        await _record_indexed_project(
            project_path, project_id, file_count=len(manifest)
        )

        manifest_path = os.path.join(base_dir, f"{project_id}_manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        memory_store, _, _, _, _ = get_memory_modules()
        await memory_store.open_pool()

        import graph_bootstrap

        driver = await graph_bootstrap.require_driver()
        valid_relpaths = [e["rel_path"] for e in manifest]

        async def _cleanup() -> None:
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as _s:
                # 1. Delete files no longer in manifest
                del_r = await _s.run(
                    "MATCH (f:File {project_id: $pid}) "
                    "WHERE NOT f.filepath IN $paths "
                    "DETACH DELETE f RETURN count(*) AS deleted",
                    pid=project_id,
                    paths=valid_relpaths,
                )
                del_rec = await del_r.single()
                stale_files = del_rec["deleted"] if del_rec else 0

                # 2. Update timestamp for files STILL in manifest (or new ones)
                # This allows get_indexing_health to detect stale files vs disk.
                await _s.run(
                    "MATCH (f:File {project_id: $pid}) "
                    "WHERE f.filepath IN $paths "
                    "SET f.indexed_at = timestamp()",
                    pid=project_id,
                    paths=valid_relpaths,
                )

                # 3. Clean up stale symbols for files that were modified/re-indexed
                del_sym = await _s.run(
                    "MATCH (f:File {project_id: $pid}) WHERE f.filepath IN $paths "
                    "MATCH (f)-[:CONTAINS]->(s) "
                    "WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum OR s:Import "
                    "DETACH DELETE s RETURN count(*) AS deleted",
                    pid=project_id,
                    paths=valid_relpaths,
                )
                del_sym_rec = await del_sym.single()
                stale_syms = del_sym_rec["deleted"] if del_sym_rec else 0

                # ... (rest of cleanup)
                del_s = await _s.run(
                    "MATCH (s {project_id: $pid}) "
                    "WHERE (s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum OR s:Import) "
                    "  AND NOT ()-[:CONTAINS]->(s) "
                    "DETACH DELETE s RETURN count(*) AS deleted",
                    pid=project_id,
                )
                del_srec = await del_s.single()
                stale_syms += del_srec["deleted"] if del_srec else 0
                _ = stale_files
                _ = stale_syms

        await _retry_deadlock("index_cleanup", _cleanup)

        if memory_store._pg_pool_available() and valid_relpaths:
            try:
                async with memory_store._pg_pool.connection() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
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

        with _JOBS_LOCK:
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
        with _JOBS_LOCK:
            _JOBS[job_id] = {
                "status": "running",
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
            }

        neo4j_uri = os.getenv("LM_PROXY_NEO4J_URI", "bolt://localhost:7687")
        neo4j_user = os.getenv("LM_PROXY_NEO4J_USER", "neo4j")
        neo4j_pass = os.getenv("LM_PROXY_NEO4J_PASSWORD", "password")

        struct_cmd = [
            sys.executable,
            os.path.join(base_dir, "run_struct_index.py"),
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
        sem_cmd = [
            sys.executable,
            os.path.join(base_dir, "index_workspace.py"),
            project_path,
            project_id,
            "--manifest-file",
            manifest_path,
        ]

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

        return (
            f"Indexing started in background.\n"
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
    import time

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

    elapsed = time.time() - job["started_at"]
    finished = job.get("finished_at")
    struct_rc = job.get("struct_rc")
    sem_rc = job.get("sem_rc")
    logs = job.get("logs", [])

    try:
        from tools.project import get_last_graph_build_metric
    except Exception:
        get_last_graph_build_metric = None

    last_build = get_last_graph_build_metric() if get_last_graph_build_metric else None

    lines = [
        f"Job {job_id}: {job['status'].upper()}",
        f"  project:    {job['project_path']}",
        f"  project_id: {job['project_id']}",
        f"  files:      {job['file_count']}",
        f"  elapsed:    {elapsed:.1f}s",
        f"  struct:     exit {struct_rc} ({'ok' if struct_rc == 0 else 'FAILED'})"
        if struct_rc is not None
        else "  struct:     running…",
        f"  semantic:   exit {sem_rc} ({'ok' if sem_rc == 0 else 'FAILED'})"
        if sem_rc is not None
        else "  semantic:   running…",
    ]
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


async def watch_project(project_path: str) -> str:
    """
    Start a background watcher for a project.
    It will automatically trigger `index_workspace` when files change.
    """
    if not os.path.exists(project_path):
        return f"Error: Path does not exist: {project_path}"
    abs_path = os.path.abspath(project_path)
    if abs_path in WATCHED_PATHS:
        return f"Project is already being watched: {abs_path}"
    WATCHED_PATHS[abs_path] = {}
    _save_watched_config()
    return f"Started watching project: {abs_path}. Indexing will occur automatically on changes."


async def get_indexing_health(project_path: str) -> str:
    """
    Check if the index for a project is stale compared to the files on disk.
    Compares the 'indexed_at' timestamp in Neo4j with file modification times.
    """
    if not os.path.exists(project_path):
        return f"Error: Path does not exist: {project_path}"

    project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
    import graph_bootstrap

    driver = await graph_bootstrap.require_driver()

    indexed_files: Dict[str, float] = {}
    file_nodes = 0
    parsed_true = 0
    parsed_false = 0
    parsed_unknown = 0
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        records = await _execute_read(
            session,
            "MATCH (f:File {project_id: $pid}) RETURN f.filepath AS fp, f.indexed_at AS ts",
            pid=project_id,
            op="get_indexing_health_files",
        )
        for rec in records:
            file_nodes += 1
            # Neo4j timestamp() is in milliseconds; convert to seconds
            if rec["ts"]:
                indexed_files[rec["fp"]] = rec["ts"] / 1000.0
        records2 = await _execute_read(
            session,
            """
            MATCH (f:File {project_id:$pid})
            RETURN
              sum(CASE WHEN coalesce(f.parsed, false) = true THEN 1 ELSE 0 END) AS parsed_true,
              sum(CASE WHEN f.parsed = false THEN 1 ELSE 0 END) AS parsed_false,
              sum(CASE WHEN f.parsed IS NULL THEN 1 ELSE 0 END) AS parsed_unknown
            """,
            pid=project_id,
            op="get_indexing_health_counts",
        )
        rec2 = records2[0] if records2 else None
        if rec2:
            parsed_true = rec2["parsed_true"] or 0
            parsed_false = rec2["parsed_false"] or 0
            parsed_unknown = rec2["parsed_unknown"] or 0

    if not indexed_files:
        if file_nodes > 0:
            return (
                f"Project '{project_path}' ({project_id}) has a structural index "
                f"with {file_nodes} file nodes, but no indexed_at timestamps yet.\n"
                "Run index_workspace once more or let post-index maintenance complete "
                "to refresh index health metadata."
            )
        return f"Project '{project_path}' ({project_id}) is not indexed. Run index_workspace first."

    stale: List[str] = []
    missing: List[str] = []
    manifest = _build_manifest(project_path)
    total_checked_all = len(manifest)

    for entry in manifest:
        rel = entry.get("rel_path")
        abs_path = entry.get("abs_path")
        if not rel or not abs_path:
            continue
        try:
            mtime = os.path.getmtime(abs_path)
            if rel not in indexed_files:
                missing.append(rel)
            elif mtime > indexed_files[rel]:
                stale.append(rel)
        except (OSError, FileNotFoundError):
            continue

    lines = [f"## Indexing Health for `{project_path}`"]
    lines.append(f"  Project ID: {project_id}")
    lines.append(f"  Files in index:        {len(indexed_files)}")
    lines.append(f"  Files on disk:         {total_checked_all}")
    lines.append("  Filter: matches indexer manifest filters")
    if file_nodes:
        lines.append(
            f"  Parsed files:          {parsed_true}"
            f" (unparsed={parsed_false}, unknown={parsed_unknown})"
        )

    if not stale and not missing:
        lines.append("\n✅ Index is up to date.")
    else:
        if stale:
            lines.append(f"\n❌ {len(stale)} stale files (modified since last index):")
            for s in stale[:10]:
                lines.append(f"  - {s}")
            if len(stale) > 10:
                lines.append(f"  - ... and {len(stale) - 10} more")
        if missing:
            lines.append(f"\n⚠️ {len(missing)} files missing from index:")
            for m in missing[:10]:
                lines.append(f"  - {m}")
            if len(missing) > 10:
                lines.append(f"  - ... and {len(missing) - 10} more")
        lines.append("\nRun `index_workspace()` to refresh the index.")

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
        data = _load_indexed_projects()
        for pid, entry in data.items():
            path = entry.get("project_path")
            if not path:
                continue
            items.append((pid, path, entry.get("last_indexed"), None))

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


async def unwatch_project(project_path: str) -> str:
    """
    Stop watching a project.
    """
    abs_path = os.path.abspath(project_path)
    if abs_path in WATCHED_PATHS:
        del WATCHED_PATHS[abs_path]
        _save_watched_config()
        return f"Stopped watching project: {abs_path}"
    return f"Project is not currently being watched: {abs_path}"


def register(mcp: FastMCP) -> None:
    """Register all indexing tools with the FastMCP instance."""
    mcp.tool()(index_workspace)
    mcp.tool()(get_index_status)
    mcp.tool()(cancel_index_job)
    mcp.tool()(watch_project)
    mcp.tool()(unwatch_project)
    mcp.tool()(get_indexing_health)
    mcp.tool()(get_indexed_projects)
