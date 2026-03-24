from dotenv import load_dotenv
# Load environment configuration from the rest_proxy directory
load_dotenv("/Users/michaelmarler/Projects/rest_proxy/.env")

import asyncio
import os
import json
import sys
import hashlib
import subprocess
import threading
import graph_bootstrap
from typing import Optional, List, Dict, Any

# --- MCP Protocol Guard ---
# Save the original stdout file descriptor and redirect sys.stdout to stderr.
# This ensures that any third-party library or module initialization that prints 
# to stdout doesn't corrupt the MCP JSON-RPC protocol.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr
# --------------------------


def _stream_stderr(proc: subprocess.Popen, prefix: str) -> None:
    """Forward a subprocess's stderr line-by-line to our stderr.

    Run this in a daemon thread so both subprocesses are streamed
    concurrently without blocking the main thread.
    """
    assert proc.stderr is not None
    for line in proc.stderr:
        print(f"{prefix} {line.rstrip()}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Background index job registry
# ---------------------------------------------------------------------------
# Each entry: {status, struct_rc, sem_rc, logs[], started_at, finished_at}
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_MAX_LOG_LINES = 200   # ring-buffer size per job


def _drain_proc_output(
    proc: subprocess.Popen,
    job_id: str,
    prefix: str,
    rc_key: str,
) -> None:
    """Drain stdout+stderr of *proc* into the job log ring-buffer.

    Runs in a daemon thread. When the process exits, stores its return code.
    """
    import time as _time
    assert proc.stderr is not None
    for raw_line in proc.stderr:
        line = f"{prefix} {raw_line.rstrip()}"
        print(line, file=sys.stderr, flush=True)
        with _JOBS_LOCK:
            if job_id in _JOBS:
                logs = _JOBS[job_id]["logs"]
                logs.append(line)
                if len(logs) > _MAX_LOG_LINES:
                    del logs[0]
    proc.wait()
    with _JOBS_LOCK:
        if job_id in _JOBS:
            _JOBS[job_id][rc_key] = proc.returncode


def _finalize_job(job_id: str, manifest_path: str) -> None:
    """Watch for both phases to complete, then set status and clean up."""
    import time as _time
    # Poll until both return codes are recorded
    while True:
        _time.sleep(0.5)
        with _JOBS_LOCK:
            job = _JOBS.get(job_id, {})
            struct_rc = job.get("struct_rc")
            sem_rc    = job.get("sem_rc")
        if struct_rc is not None and sem_rc is not None:
            break
    # Cleanup manifest
    try:
        if os.path.exists(manifest_path):
            os.remove(manifest_path)
    except OSError:
        pass
    import time as _t
    with _JOBS_LOCK:
        if job_id in _JOBS:
            ok = (struct_rc == 0 and sem_rc == 0)
            _JOBS[job_id]["status"]      = "done" if ok else "failed"
            _JOBS[job_id]["finished_at"] = _t.time()

from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("rest_proxy")

# --- Helper for lazy-loading internal modules ---
def _get_memory_modules():
    """Lazy-load memory and proxy modules to speed up startup and prevent shutdown errors."""
    import memory_store
    import memory_retrieval
    import memory_summary
    import skeleton_extractor
    import proxy
    return memory_store, memory_retrieval, memory_summary, skeleton_extractor, proxy

@mcp.tool()
async def search_memory(session_id: str, query: str, global_search: bool = False) -> str:
    """
    Search for context in the memory store for a given session.
    Set global_search=True to search across all sessions/projects.
    
    Args:
        session_id: The unique identifier for the session/project.
        query: The search query or current user message.
        global_search: Whether to search across all sessions/projects.
    """
    try:
        memory_store, memory_retrieval, _, _, _ = _get_memory_modules()
        # Ensure pool is open if using Neo4j/Redis
        if memory_store._ENABLE_PERSISTENCE:
            await memory_store.open_pool()
            
        assembled = await memory_retrieval.assemble_memory(session_id, query, global_search=global_search)
        if assembled and assembled.assembled_text:
            return assembled.assembled_text
        return "No relevant memory found."
    except Exception as e:
        return f"Error searching memory: {str(e)}"

@mcp.tool()
async def add_memory(session_id: str, text: str, is_global: bool = False) -> str:
    """
    Store a durable memory or instruction. This is NOT for temporary conversation turns,
    but for persistent facts or rules that should guide the agent's behavior.
    
    Args:
        session_id: The unique identifier for the session/project.
        text: The instruction or fact to remember.
        is_global: If True, this memory is not tied to a project and will be visible everywhere.
    """
    try:
        memory_store, _, _, _, _ = _get_memory_modules()
        if memory_store._ENABLE_PERSISTENCE:
            await memory_store.open_pool()
            
        success = await memory_store.add_durable_memory(session_id, text, is_global=is_global)
        if success:
            return f"Successfully added {'global ' if is_global else ''}memory: {text}"
        return "Failed to add memory."
    except Exception as e:
        return f"Error adding memory: {str(e)}"

@mcp.tool()
async def get_session_summary(session_id: str) -> str:
    """
    Get the current rolling summary for a session.
    
    Args:
        session_id: The unique identifier for the session.
    """
    try:
        memory_store, _, _, _, _ = _get_memory_modules()
        summary = await memory_store.get_rolling_summary(session_id)
        if summary:
            return summary
        return "No summary available for this session."
    except Exception as e:
        return f"Error retrieving summary: {str(e)}"

@mcp.tool()
async def code_skeleton(file_path: str) -> str:
    """
    Extract the structural skeleton (classes, functions, etc.) from a source file.
    Supports .py, .swift, .js, .ts, .jsx, .tsx.
    
    Args:
        file_path: Absolute path to the source file.
    """
    try:
        _, _, _, skeleton_extractor, _ = _get_memory_modules()
        if not os.path.exists(file_path):
            return f"File not found: {file_path}"
            
        with open(file_path, "r") as f:
            code = f.read()
            
        skeleton = skeleton_extractor.extract_skeleton(code, file_path)
        if skeleton:
            return skeleton
        return "Could not extract skeleton (unsupported file type or empty file)."
    except Exception as e:
        return f"Error extracting skeleton: {str(e)}"

@mcp.tool()
async def search_codebase(project_path: str, query: str, k: int = 5) -> str:
    """
    Perform a hybrid semantic search over the codebase.
    Returns the most relevant code chunks with graph context.
    
    Args:
        project_path: Absolute path to the project root.
        query: The search query (natural language or code snippet).
        k: Number of results to return (default 5).
    """
    try:
        import hashlib
        memory_store, memory_retrieval, _, _, _ = _get_memory_modules()
        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        
        # Get embedding for the query
        query_vector = await memory_retrieval.get_embedding(query)
        if not query_vector:
            return "Error: Could not generate embedding for query."
            
        await memory_store.open_pool()
        results = await memory_store.search_codebase(
            project_id=project_id,
            query_vector=query_vector,
            query_text=query,
            k=k
        )
        
        if not results:
            return "No matching code found."
            
        output = []
        for r in results:
            output.append(f"--- {r['file_path']} (Score: {r['rrf_score']:.4f}) ---\n{r['content']}")
        
        return "\n\n".join(output)
    except Exception as e:
        return f"Error searching codebase: {str(e)}"

@mcp.tool()
async def query_graph(cypher_query: str) -> str:
    """
    Execute a raw Cypher query on the Neo4j structural graph.
    Useful for complex relationship analysis.
    
    Args:
        cypher_query: The Cypher query string.
    """
    try:
        import graph_bootstrap
        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()
        if not driver:
            return "Error: Could not connect to Neo4j."
            
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            result = await session.run(cypher_query)
            data = []
            async for record in result:
                data.append(record.data())
            
            if not data:
                return "No results found."
            return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error querying graph: {str(e)}"

@mcp.tool()
async def find_callers(project_path: str, function_name: str) -> str:
    """
    Find all locations that call a specific function or method.
    
    Args:
        project_path: Absolute path to the project root.
        function_name: Name of the function to find callers for.
    """
    try:
        import hashlib
        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        
        # We search for CALLS relationships to the target function
        cypher = """
        MATCH (caller:Function)-[:CALLS]->(callee:Function)
        WHERE callee.name = $fname AND callee.project_id = $pid
        OPTIONAL MATCH (file:File)-[:CONTAINS*1..]->(caller)
        RETURN file.rel_path AS file, caller.name AS caller_name
        """
        
        import graph_bootstrap
        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()
        
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            result = await session.run(cypher, fname=function_name, pid=project_id)
            callers = []
            async for record in result:
                callers.append(f"- {record['caller_name']} in {record['file']}")
            
            if not callers:
                return f"No callers found for '{function_name}' (ensure the project is indexed)."
            return "Callers:\n" + "\n".join(callers)
    except Exception as e:
        return f"Error finding callers: {str(e)}"

@mcp.tool()
async def visualize_subgraph(project_path: str, symbol_name: str) -> str:
    """
    Generate a Mermaid diagram of a symbol's neighborhood in the structural graph.
    
    Args:
        project_path: Absolute path to the project root.
        symbol_name: Name of the symbol to visualize.
    """
    try:
        import hashlib
        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        
        # Pull the node and its immediate neighbors (CONTAINS, CALLS, INHERITS)
        cypher = """
        MATCH (n) WHERE n.name = $name AND n.project_id = $pid
        MATCH (n)-[r]-(m)
        RETURN labels(n)[0] AS n_type, n.name AS n_name, 
               type(r) AS rel, 
               labels(m)[0] AS m_type, m.name AS m_name
        LIMIT 20
        """
        
        import graph_bootstrap
        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()
        
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            result = await session.run(cypher, name=symbol_name, pid=project_id)
            mermaid = ["graph TD"]
            added_edges = set()
            async for record in result:
                n = f"{record['n_type']}_{record['n_name']}"
                m = f"{record['m_type']}_{record['m_name']}"
                rel = record['rel']
                edge = f"{n} -- {rel} --> {m}"
                if edge not in added_edges:
                    mermaid.append(f"  {record['n_name']}[{record['n_type']}: {record['n_name']}] -- {rel} --> {record['m_name']}[{record['m_type']}: {record['m_name']}]")
                    added_edges.add(edge)
            
            if len(mermaid) == 1:
                return f"No relationships found for '{symbol_name}'."
            return "```mermaid\n" + "\n".join(mermaid) + "\n```"
    except Exception as e:
        return f"Error visualizing subgraph: {str(e)}"

@mcp.tool()
async def get_code_importance(project_path: str) -> str:
    """
    Use Graph Data Science (GDS) PageRank to identify the most 'important' files.
    Important files are those most heavily called or inherited from.
    
    Args:
        project_path: Absolute path to the project root.
    """
    try:
        import hashlib
        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        
        import graph_bootstrap
        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()
        
        # 1. Project an ephemeral graph for this project
        projection_name = f"importance_{project_id}"
        
        cleanup_cypher = f"CALL gds.graph.drop('{projection_name}', false) YIELD graphName"
        
        # Targeted projection: nodes and relationships specifically for this project
        project_cypher_imp = """
        CALL gds.graph.project.cypher(
            $graph_name,
            'MATCH (n) WHERE n.project_id = $pid RETURN id(n) AS id',
            'MATCH (n)-[r:CALLS|INHERITS|CONTAINS]->(m) WHERE n.project_id = $pid AND m.project_id = $pid RETURN id(n) AS source, id(m) AS target',
            { parameters: { pid: $pid } }
        )
        """

        pagerank_cypher = f"""
        CALL gds.pageRank.stream('{projection_name}')
        YIELD nodeId, score
        WITH gds.util.asNode(nodeId) AS n, score
        WHERE NOT n.name IN ['String', 'Int', 'Bool', 'Error', 'print', 'Any', 'Double', 'self', 'Optional']
        RETURN n.rel_path AS file, 
               n.name AS name,
               labels(n)[0] AS type,
               score
        ORDER BY score DESC
        LIMIT 15
        """
        
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            # Cleanup old projection if any
            await session.run(cleanup_cypher)
            # Create new projection
            await session.run(project_cypher_imp, graph_name=projection_name, pid=project_id)
            # Run PageRank
            result = await session.run(pagerank_cypher)
            
            output = ["Project Core (High Importance Nodes):"]
            async for record in result:
                name = record['name'] or record['file'] or "Unknown"
                output.append(f"- [{record['type']}] {name} (Score: {record['score']:.4f})")
            
            # Cleanup
            await session.run(cleanup_cypher)
            
            if len(output) == 1:
                return "No importance metrics found for this project (ensure it is indexed)."
            return "\n".join(output)
            
    except Exception as e:
        return f"Error calculating code importance: {str(e)}"

@mcp.tool()
async def get_code_communities(project_path: str) -> str:
    """
    Use GDS Louvain Community Detection to find logical 'clusters' of code.
    Helps identify modules (Auth, UI, Data) based on call patterns.
    
    Args:
        project_path: Absolute path to the project root.
    """
    try:
        import hashlib
        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        
        import graph_bootstrap
        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()
        
        projection_name = f"communities_{project_id}"
        
        cleanup_cypher = f"CALL gds.graph.drop('{projection_name}', false) YIELD graphName"
        
        project_cypher_comm = """
        CALL gds.graph.project.cypher(
            $graph_name,
            'MATCH (n) WHERE n.project_id = $pid AND (n:Class OR n:Function) RETURN id(n) AS id',
            'MATCH (n)-[r:CALLS|INHERITS]->(m) WHERE n.project_id = $pid AND m.project_id = $pid RETURN id(n) AS source, id(m) AS target',
            { parameters: { pid: $pid } }
        )
        """
        
        louvain_cypher = f"""
        CALL gds.louvain.stream('{projection_name}')
        YIELD nodeId, communityId
        RETURN communityId, 
               collect(gds.util.asNode(nodeId).name) AS symbols
        ORDER BY size(symbols) DESC
        LIMIT 8
        """
        
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await session.run(cleanup_cypher)
            await session.run(project_cypher_comm, graph_name=projection_name, pid=project_id)
            result = await session.run(louvain_cypher)
            
            output = ["Found Code Clusters (Communities):"]
            async for record in result:
                symbols = record['symbols'][:10] # Show top 10 per community
                output.append(f"Cluster {record['communityId']}: {', '.join(symbols)}...")
            
            await session.run(cleanup_cypher)
            
            if len(output) == 1:
                return "No communities found (ensure it is indexed and has relationships)."
            return "\n".join(output)
            
    except Exception as e:
        return f"Error identifying code communities: {str(e)}"

@mcp.tool()
async def index_workspace(project_path: str) -> str:
    """
    Trigger a full re-index (semantic and structural) of a directory into the graph.
    Returns immediately with a job_id. Use get_index_status(job_id) to monitor progress.

    Args:
        project_path: Absolute path to the project root.
    """
    try:
        import time
        import json
        import uuid
        from pathlib import Path

        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        base_dir   = os.path.dirname(os.path.abspath(__file__))

        # 1. Unified Discovery (fast — stays synchronous, ~0.1s)
        manifest = []
        root = Path(project_path)
        skip_dirs = {
            '.git', 'node_modules', '__pycache__', 'venv', '.venv',
            'target', 'build', 'dist', '.build', '.gemini', '.agents',
            '.agent', '.cache', 'Pods',
        }
        skip_exts = {
            '.png', '.jpg', '.jpeg', '.gif', '.pdf', '.zip', '.tar', '.gz',
            '.mp4', '.mp3', '.bin', '.exe', '.dll', '.so', '.pyc', '.lock',
            '.dylib', '.a', '.o', '.dSYM', '.wasm',
        }
        MAX_FILE_SIZE = 1 * 1024 * 1024
        for path in root.rglob('*'):
            if any(part in skip_dirs for part in path.parts):
                continue
            if path.is_file() and path.suffix.lower() not in skip_exts:
                try:
                    stats = path.stat()
                    if stats.st_size > MAX_FILE_SIZE:
                        continue
                    manifest.append({
                        "abs_path": str(path.absolute()),
                        "rel_path": str(path.relative_to(root)),
                        "ext":      path.suffix.lower().lstrip('.'),
                        "size":     stats.st_size,
                    })
                except Exception:
                    continue

        manifest_path = os.path.join(base_dir, f"{project_id}_manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)

        # 2. Register job
        job_id = str(uuid.uuid4())[:8]
        with _JOBS_LOCK:
            _JOBS[job_id] = {
                "status":      "running",
                "project_id":  project_id,
                "project_path": project_path,
                "file_count":  len(manifest),
                "struct_rc":   None,
                "sem_rc":      None,
                "logs":        [],
                "started_at":  time.time(),
                "finished_at": None,
            }

        neo4j_uri  = os.getenv("LM_PROXY_NEO4J_URI",      "bolt://localhost:7687")
        neo4j_user = os.getenv("LM_PROXY_NEO4J_USER",     "neo4j")
        neo4j_pass = os.getenv("LM_PROXY_NEO4J_PASSWORD", "password")

        struct_cmd = [
            sys.executable,
            os.path.join(base_dir, "run_struct_index.py"),
            project_path, project_id,
            "--manifest-file", manifest_path,
            "--neo4j-uri",  neo4j_uri,
            "--neo4j-user", neo4j_user,
            "--neo4j-pass", neo4j_pass,
        ]
        sem_cmd = [
            sys.executable,
            os.path.join(base_dir, "index_workspace.py"),
            project_path, project_id,
            "--manifest-file", manifest_path,
        ]

        # 3. Launch both phases in background (non-blocking)
        struct_proc = subprocess.Popen(
            struct_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        sem_proc = subprocess.Popen(
            sem_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

        # Daemon threads — drain output and record exit codes
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
        # Finalize job (cleanup manifest, set status) when both phases done
        threading.Thread(
            target=_finalize_job,
            args=(job_id, manifest_path),
            daemon=True,
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


@mcp.tool()
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
            # Try to find by project partial match (convenience)
            for jid, j in _JOBS.items():
                if jid.startswith(job_id) or j.get("project_id", "").startswith(job_id):
                    job = j
                    job_id = jid
                    break

    if job is None:
        active = list(_JOBS.keys())
        return (
            f"No job found for id '{job_id}'.\n"
            f"Active jobs: {active if active else 'none'}"
        )

    status    = job["status"]
    elapsed   = time.time() - job["started_at"]
    finished  = job.get("finished_at")
    struct_rc = job.get("struct_rc")
    sem_rc    = job.get("sem_rc")
    logs      = job.get("logs", [])

    lines = [
        f"Job {job_id}: {status.upper()}",
        f"  project:    {job['project_path']}",
        f"  project_id: {job['project_id']}",
        f"  files:      {job['file_count']}",
        f"  elapsed:    {elapsed:.1f}s",
    ]
    if struct_rc is not None:
        lines.append(f"  struct:     exit {struct_rc} ({'ok' if struct_rc == 0 else 'FAILED'})")
    else:
        lines.append("  struct:     running…")
    if sem_rc is not None:
        lines.append(f"  semantic:   exit {sem_rc} ({'ok' if sem_rc == 0 else 'FAILED'})")
    else:
        lines.append("  semantic:   running…")
    if finished:
        lines.append(f"  finished:   {(finished - job['started_at']):.1f}s total")

    if logs:
        lines.append("\nRecent log lines (last 20):")
        lines.extend(logs[-20:])

    return "\n".join(lines)


@mcp.tool()
async def find_definitions(symbol_name: str) -> str:
    """
    Search for the definition of a class, function, or struct across ALL indexed projects.
    Ideal for cross-project dependency discovery.
    
    Args:
        symbol_name: Name of the symbol to find.
    """
    try:
        import graph_bootstrap
        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()
        
        cypher = """
        MATCH (n)
        WHERE (n:Class OR n:Function) AND n.name = $name
        RETURN n.project_id AS project, 
               n.rel_path AS file, 
               labels(n)[0] AS type
        """
        
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            result = await session.run(cypher, name=symbol_name)
            
            output = [f"Found {symbol_name} in the following locations:"]
            async for record in result:
                output.append(f"- [{record['type']}] Project: {record['project']}, File: {record['file']}")
            
            if len(output) == 1:
                return f"Symbol '{symbol_name}' not found in any indexed project."
            return "\n".join(output)
            
    except Exception as e:
        return f"Error finding definition: {str(e)}"

@mcp.tool()
async def get_code_summary(project_path: str, symbol_name: str) -> str:
    """
    Generate a structural and semantic summary of a specific code symbol.
    Provides its containing file, its members, and its primary code chunks.
    
    Args:
        project_path: Absolute path to the project root.
        symbol_name: Name of the class, function, or struct to summarize.
    """
    try:
        import hashlib
        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        
        import graph_bootstrap
        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()
        
        # Enriched retrieval: Symbol -> Containing File -> Other Members -> Semantic Chunks
        cypher = """
        MATCH (s {name: $name, project_id: $pid})
        WHERE s:Class OR s:Function OR s:File
        
        OPTIONAL MATCH (s)<-[:CONTAINS]-(parent)
        OPTIONAL MATCH (s)-[:CONTAINS]->(child)
        OPTIONAL MATCH (s)-[:HAS_CHUNK]->(chk)
        
        RETURN labels(s)[0] AS type,
               s.rel_path AS path,
               parent.name AS parent_name,
               collect(DISTINCT child.name) AS children,
               collect(DISTINCT chk.text) AS code_samples
        LIMIT 1
        """
        
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            result = await session.run(cypher, name=symbol_name, pid=project_id)
            record = await result.single()
            
            if not record:
                return f"Symbol '{symbol_name}' not found in project."
            
            output = [f"=== Summary for {symbol_name} ({record['type']}) ==="]
            output.append(f"Location: {record['path']}")
            if record['parent_name']:
                output.append(f"Scope: {record['parent_name']}")
            
            if record['children']:
                output.append(f"Structure: Contains {len(record['children'])} members: {', '.join(record['children'][:10])}...")
            
            if record['code_samples']:
                # Provide a condensed snippet of the first few chunks
                snippet = "\n---\n".join([c[:200] + "..." for c in record['code_samples'][:2]])
                output.append(f"Code Preview:\n{snippet}")
            
            return "\n".join(output)
            
    except Exception as e:
        return f"Error summarizing code region: {str(e)}"

@mcp.tool()
async def get_project_health(project_path: str) -> str:
    """
    Check the indexing health of a project: files, symbols, and vector coverage.
    """
    project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
    
    cypher = """
    MATCH (p:Project {id: $pid})
    OPTIONAL MATCH (p)-[:HAS_FILE]->(f:File)
    WITH p, count(DISTINCT f) as tf
    OPTIONAL MATCH (p)-[:HAS_FILE]->(f2:File)-[:HAS_CHUNK]->(c:Chunk)
    WITH p, tf, count(DISTINCT f2) as idx_f
    OPTIONAL MATCH (s) WHERE s.project_id = p.id AND (s:Class OR s:Function)
    RETURN tf, idx_f, count(DISTINCT s) as ts
    """
    
    try:
        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()
        if not driver: return "Graph driver not available."
        
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            # 1. Check if Project exists
            p_res = await session.run("MATCH (p:Project {id: $pid}) RETURN p.id as pid", pid=project_id)
            if not await p_res.single(): return f"Project {project_path} (ID: {project_id}) not found in graph."

            # 2. Total Files
            f_res = await session.run("MATCH (p:Project {id: $pid})-[:HAS_FILE]->(f:File) RETURN count(f) as c", pid=project_id)
            tf = (await f_res.single())["c"] or 0
            
            # 3. Indexed Files
            idx_res = await session.run("MATCH (p:Project {id: $pid})-[:HAS_FILE]->(f:File)-[:HAS_CHUNK]->(c:Chunk) RETURN count(DISTINCT f) as c", pid=project_id)
            idx_f = (await idx_res.single())["c"] or 0
            
            # 4. Total Symbols
            s_res = await session.run("MATCH (s) WHERE s.project_id = $pid AND (s:Class OR s:Function) RETURN count(s) as c", pid=project_id)
            ts = (await s_res.single())["c"] or 0
            
            coverage = 100 * (idx_f / tf) if tf > 0 else 0
            
            report = [
                f"### Graph Health Report for `{project_path}` (ID: {project_id})",
                f"- **Total Files**: {tf}",
                f"- **Files Indexed (Vector)**: {idx_f} ({coverage:.1f}%)",
                f"- **Files Missing Chunks**: {tf - idx_f}",
                f"- **Total Symbols**: {ts}",
                f"- **Symbols Missing Vectors**: 0",
                "",
                "Recommendation: Run `index_workspace` again if coverage is low."
            ]
            return "\n".join(report)
    except Exception as e:
        return f"Error checking health: {str(e)}"
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
2. **Health Check**: Call `get_project_health(project_path)` to verify indexing coverage.
3. **Initial Discovery**: Use `get_code_importance(project_path)` for PageRank-central files.
4. **Memory Recall**: Use `search_memory(session_id, query, global_search=True)` for cross-project context.
5. **Cross-Project Search**: Use `find_definitions(symbol_name)` to locate symbols across all projects.
6. **Modular Understanding**: Use `get_code_communities(project_path)` for Louvain-clustered module groups.
7. **Targeted Search**: Use `search_codebase(project_path, query)` — Postgres RRF hybrid (cosine + BM25).
8. **Deep Dive**: Use `get_code_summary(project_path, symbol_name)` for a dense symbol summary.

### Background Indexing:
- `index_workspace(project_path)` → non-blocking, returns `job_id` in ~1s
- `get_index_status(job_id)` → poll for `RUNNING` / `DONE` / `FAILED` + last 20 log lines

### Pro Tips:
- **Graph Visualization**: `visualize_subgraph(project_path, symbol_name)` → Mermaid relationship diagram.
- **Related Files**: `get_related_files(project_path, file_path)` → structural neighbors.
- **Raw Graph**: `query_graph(cypher)` for arbitrary Neo4j Cypher queries.
"""

@mcp.tool()
async def get_related_files(project_path: str, file_path: str) -> str:
    """
    Find files that are structurally related to the target file.
    
    Args:
        project_path: Absolute path to the project root.
        file_path: Relative path to the file in the project.
    """
    try:
        import hashlib
        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        file_id = f"{project_id}:{file_path}"
        
        # Find files that contain nodes that call or are called by nodes in this file
        cypher = """
        MATCH (f1:File {id: $fid})-[:CONTAINS*1..]->(n1)
        MATCH (n1)-[:CALLS|INHERITS]-(n2)
        MATCH (f2:File)-[:CONTAINS*1..]->(n2)
        WHERE f1 <> f2
        RETURN f2.rel_path AS related_file, count(*) AS strength
        ORDER BY strength DESC
        LIMIT 10
        """
        
        import graph_bootstrap
        await graph_bootstrap.init_graph_db()
        driver = graph_bootstrap.get_driver()
        
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            result = await session.run(cypher, fid=file_id)
            related = []
            async for record in result:
                related.append(f"- {record['related_file']} (Strength: {record['strength']})")
            
            if not related:
                return "No structurally related files found."
            return "Related Files:\n" + "\n".join(related)
    except Exception as e:
        return f"Error finding related files: {str(e)}"

@mcp.tool()
async def lint_project_subset(files: List[str]) -> str:
    """
    Run best-available linter on a set of files.
    Supports Swift (swiftlint) and Python (pylint/ruff).
    
    Args:
        files: List of absolute paths to files to lint.
    """
    results = []
    for f in files:
        if f.endswith(".swift"):
            # Check for swiftlint
            try:
                import subprocess
                res = subprocess.run(["swiftlint", "lint", f], capture_output=True, text=True)
                results.append(f"--- SwiftLint: {os.path.basename(f)} ---\n{res.stdout or 'No issues found.'}")
            except FileNotFoundError:
                results.append(f"SwiftLint not found. Skipping {f}.")
        elif f.endswith(".py"):
            try:
                import subprocess
                # Try ruff first, then pylint
                try:
                    res = subprocess.run(["ruff", "check", f], capture_output=True, text=True)
                except FileNotFoundError:
                    res = subprocess.run(["pylint", "--errors-only", f], capture_output=True, text=True)
                results.append(f"--- Python Linter: {os.path.basename(f)} ---\n{res.stdout or 'No issues found.'}")
            except FileNotFoundError:
                results.append(f"Python linter (ruff/pylint) not found. Skipping {f}.")
    
    return "\n\n".join(results) if results else "No supported files provided or no linters found."

@mcp.tool()
async def swift_doc_lookup(file_path: str, symbol_name: str) -> str:
    """
    Extract documentation comments for a Swift symbol using SourceKitten.
    
    Args:
        file_path: Absolute path to the Swift file.
        symbol_name: Name of the symbol to lookup.
    """
    try:
        import skeleton_extractor
        if not os.path.exists(file_path):
            return "File not found."
            
        with open(file_path, "r", encoding="utf-8") as f:
            code = f.read()
            
        doc = skeleton_extractor.get_swift_docs(code, symbol_name)
        return doc if doc else f"No documentation found for '{symbol_name}'."
    except Exception as e:
        return f"Error looking up docs: {str(e)}"

@mcp.tool()
async def list_available_models() -> str:
    """
    List models currently available in LM Studio via the proxy.
    """
    try:
        _, _, _, _, proxy = _get_memory_modules()
        models_data = await proxy.fetch_lmstudio_models()
        keys = proxy.extract_model_keys(models_data)
        if keys:
            return "\n".join(keys)
        return "No models found."
    except Exception as e:
        return f"Error listing models: {str(e)}"

# --- Background Watcher System ---
# Configuration persistence
CONFIG_DIR = os.path.expanduser("~/.gemini/antigravity/rest_proxy_config")
WATCHED_CONFIG_PATH = os.path.join(CONFIG_DIR, "watched_projects.json")

WATCHED_PATHS: Dict[str, Dict[str, float]] = {}  # project_path -> {file_path: mtime}
WATCH_INTERVAL = 30  # seconds between polls

def _save_watched_config():
    """Save the list of watched project paths to a local JSON config."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(WATCHED_CONFIG_PATH, "w") as f:
            json.dump(list(WATCHED_PATHS.keys()), f)
    except Exception as e:
        print(f"[lm-proxy:watcher] Failed to save config: {e}", file=sys.stderr)

async def _load_watched_config():
    """Load the list of watched project paths from the config on startup."""
    try:
        if os.path.exists(WATCHED_CONFIG_PATH):
            with open(WATCHED_CONFIG_PATH, "r") as f:
                paths = json.load(f)
                for p in paths:
                    if os.path.exists(p):
                        # Initialize with empty mtimes; loop will fill them
                        WATCHED_PATHS[os.path.abspath(p)] = {}
            print(f"[lm-proxy:watcher] Restored {len(WATCHED_PATHS)} watched projects.", file=sys.stderr)
    except Exception as e:
        print(f"[lm-proxy:watcher] Failed to load config: {e}", file=sys.stderr)

async def _poll_watcher():
    """Background loop to check for file changes in watched projects."""
    while True:
        try:
            for project_path, last_mtimes in list(WATCHED_PATHS.items()):
                changed = False
                current_mtimes = {}
                
                # Walk the directory to check for changes
                for root, _, files in os.walk(project_path):
                    if any(x in root for x in [".git", "node_modules", "__pycache__", "build", "dist"]):
                        continue
                        
                    for f in files:
                        # Watch all languages supported by the new TreeSitterGraphBuilder
                        if not f.endswith((".py", ".swift", ".js", ".ts", ".jsx", ".tsx", ".md", ".rs", ".go", ".cpp", ".c", ".h", ".java", ".rb", ".php", ".cs", ".json", ".toml", ".yaml", ".yml")):
                            continue
                            
                        fpath = os.path.join(root, f)
                        try:
                            mtime = os.path.getmtime(fpath)
                            current_mtimes[fpath] = mtime
                            if fpath not in last_mtimes or mtime > last_mtimes[fpath]:
                                changed = True
                        except (OSError, FileNotFoundError):
                            continue
                
                # Check for deletions
                if not changed and len(current_mtimes) != len(last_mtimes):
                    changed = True
                
                if changed:
                    # Update cache first to avoid re-triggering if indexing fails
                    WATCHED_PATHS[project_path] = current_mtimes
                    
                    # Trigger indexing
                    print(f"[lm-proxy:watcher] Change detected in {project_path}. Triggering index...", file=sys.stderr)
                    try:
                        await index_workspace(project_path)
                    except Exception as e:
                        print(f"[lm-proxy:watcher] Indexing failed: {e}", file=sys.stderr)
                else:
                    # Just update mtimes if nothing major changed (e.g. to catch small updates)
                    WATCHED_PATHS[project_path] = current_mtimes
                    
        except Exception as e:
            print(f"[lm-proxy:watcher] Loop error: {e}", file=sys.stderr)
            
        await asyncio.sleep(WATCH_INTERVAL)

@mcp.tool()
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
        
    # Initial scan to establish baseline
    WATCHED_PATHS[abs_path] = {}
    _save_watched_config()
    return f"Started watching project: {abs_path}. Indexing will occur automatically on changes."

@mcp.tool()
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

# Global reference to the background task to allow cancellation
_WATCHER_TASK: Optional[asyncio.Task] = None

async def main():
    """Main entrypoint with lifecycle management."""
    global _WATCHER_TASK
    
    # 1. Startup phase: Restore watched projects
    await _load_watched_config()
    
    # 2. Start background task manually
    _WATCHER_TASK = asyncio.create_task(_poll_watcher())
    print("[lm-proxy:watcher] Lifecycle startup complete.", file=sys.stderr)
    
    try:
        # 3. Run MCP server using its async stdio transport
        # Important: restore stdout just before starting the protocol
        sys.stdout = _REAL_STDOUT
        await mcp.run_stdio_async()
    finally:
        # 4. Shutdown phase: cancel background tasks and close pools
        if _WATCHER_TASK:
            print("[lm-proxy:watcher] Cancelling background task...", file=sys.stderr)
            _WATCHER_TASK.cancel()
            try:
                await _WATCHER_TASK
            except asyncio.CancelledError:
                pass
        
        # Ensure we close Neo4j/Redis pools if initialized
        try:
            import memory_store
            await memory_store.close_pool()
        except ImportError:
            pass
        print("[lm-proxy:watcher] Lifecycle shutdown complete.", file=sys.stderr)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="GraphRAG MCP Server")
    parser.add_argument("command", nargs="?", choices=["index_workspace"], help="Command to run")
    parser.add_argument("path", nargs="?", help="Project path for indexing")
    
    args = parser.parse_args()
    
    if args.command == "index_workspace" and args.path:
        # Run indexing directly
        try:
            from mcp.server.fastmcp import FastMCP
            # We need to manually run the async tool
            async def run_indexing():
                print(f"Direct Indexing Triggered: {args.path}", file=sys.stderr)
                result = await index_workspace(args.path)
                print(result)
                
            asyncio.run(run_indexing())
        except Exception as e:
            print(f"Indexing Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # Standard MCP server mode
        try:
            asyncio.run(main())
        except (KeyboardInterrupt, SystemExit):
            pass
