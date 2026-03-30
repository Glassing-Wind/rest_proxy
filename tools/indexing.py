"""tools/indexing.py — workspace indexing, job status, and file watcher tools."""

import os
import sys
import json
import asyncio
import hashlib
import threading
import subprocess
from typing import Dict
from mcp.server.fastmcp import FastMCP
from _jobs import _JOBS, _JOBS_LOCK, _drain_proc_output, _finalize_job

# --- Background Watcher State ---
CONFIG_DIR = os.path.expanduser("~/.gemini/antigravity/rest_proxy_config")
WATCHED_CONFIG_PATH = os.path.join(CONFIG_DIR, "watched_projects.json")
WATCHED_PATHS: Dict[str, Dict[str, float]] = {}  # project_path → {file_path: mtime}
WATCH_INTERVAL = 30  # seconds between polls

_WATCHER_TASK: asyncio.Task | None = None


def _save_watched_config() -> None:
    """Save the list of watched project paths to a local JSON config."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(WATCHED_CONFIG_PATH, "w") as f:
            json.dump(list(WATCHED_PATHS.keys()), f)
    except Exception as e:
        print(f"[lm-proxy:watcher] Failed to save config: {e}", file=sys.stderr)


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
        from pathlib import Path

        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        # __file__ is tools/indexing.py — step up one level to rest_proxy/
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        manifest = []
        root = Path(project_path)
        skip_dirs = {
            ".git",
            "__pycache__",
            "node_modules",
            ".cache",
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
        MAX_FILE_SIZE = 1 * 1024 * 1024

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
                if stats.st_size > MAX_FILE_SIZE:
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

        manifest_path = os.path.join(base_dir, f"{project_id}_manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        import graph_bootstrap

        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()
        valid_relpaths = [e["rel_path"] for e in manifest]
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
            f"\nUse get_index_status('{job_id}') to monitor progress."
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
    if logs:
        lines.append("\nRecent log lines (last 20):")
        lines.extend(logs[-20:])
    return "\n".join(lines)


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

    await graph_bootstrap.init_graph_db()
    driver = graph_bootstrap.get_driver()

    indexed_files: Dict[str, float] = {}
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        r = await session.run(
            "MATCH (f:File {project_id: $pid}) RETURN f.filepath AS fp, f.indexed_at AS ts",
            pid=project_id,
        )
        async for rec in r:
            # Neo4j timestamp() is in milliseconds; convert to seconds
            if rec["ts"]:
                indexed_files[rec["fp"]] = rec["ts"] / 1000.0

    if not indexed_files:
        return f"Project '{project_path}' ({project_id}) is not indexed. Run index_workspace first."

    stale: List[str] = []
    missing: List[str] = []
    total_checked = 0

    for root, _, files in os.walk(project_path):
        if any(
            x in root
            for x in [".git", "node_modules", ".build", "build", "dist", "DerivedData"]
        ):
            continue
        for f in files:
            if not f.endswith(
                (".py", ".swift", ".js", ".ts", ".rs", ".go", ".c", ".h", ".cpp")
            ):
                continue
            fpath = os.path.join(root, f)
            rel = os.path.relpath(fpath, project_path)
            total_checked += 1
            try:
                mtime = os.path.getmtime(fpath)
                if rel not in indexed_files:
                    missing.append(rel)
                elif mtime > indexed_files[rel]:
                    stale.append(rel)
            except (OSError, FileNotFoundError):
                continue

    lines = [f"## Indexing Health for `{project_path}`"]
    lines.append(f"  Project ID: {project_id}")
    lines.append(f"  Files in index: {len(indexed_files)}")
    lines.append(f"  Files on disk:  {total_checked}")

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
    mcp.tool()(watch_project)
    mcp.tool()(unwatch_project)
    mcp.tool()(get_indexing_health)
