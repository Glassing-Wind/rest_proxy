"""Utility implementations for graph tools."""

from __future__ import annotations

import os
import time

from _helpers import get_project_id, get_workspace_path
from tools.brain.graph import core as graph_core
from tools.brain.graph import runtime as graph_runtime


async def get_graph_build_metrics_impl(limit: int = 50) -> str:
    limit = max(1, min(int(limit), 200))
    ib_count, ib_avg, ib_max = graph_core._summarize_batches("import_graph_batch", limit)
    si_count, si_avg, si_max = graph_core._summarize_batches("symbol_import_batch", limit)
    se_count, se_avg, se_max = graph_core._summarize_batches("symbol_export_batch", limit)
    last_build = graph_core.get_last_graph_build_metric()
    recent = graph_runtime.get_recent_metrics(10)

    lines = ["# Graph build metrics"]
    if last_build:
        lines.append(
            "Last build: "
            f"project={last_build.get('project_path')} "
            f"elapsed_ms={last_build.get('elapsed_ms')}"
        )
    else:
        lines.append("Last build: none")

    lines.append(f"import_graph_batch: count={ib_count} avg_ms={ib_avg} max_ms={ib_max}")
    lines.append(f"symbol_import_batch: count={si_count} avg_ms={si_avg} max_ms={si_max}")
    lines.append(f"symbol_export_batch: count={se_count} avg_ms={se_avg} max_ms={se_max}")

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


async def get_language_pack_status_impl() -> str:
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


async def rebuild_subgraph_impl(fn, label: str, workspace_id: str) -> str:
    project_path = get_workspace_path(workspace_id)
    start_time = time.time()

    graph_runtime.record_metric(f"rebuild_{label}_start", project_path=project_path)
    try:
        result = await fn(project_path)
        elapsed = (time.time() - start_time) * 1000
        graph_runtime.record_metric(
            f"rebuild_{label}_done", project_path=project_path, elapsed_ms=elapsed
        )
        return result
    except Exception as exc:
        return f"Error rebuilding {label} graph: {str(exc)}"


async def get_heuristic_flow_summary_impl(
    *,
    driver,
    neo4j_db: str,
    workspace_id: str,
    limit: int = 20,
    as_table: bool = False,
) -> str:
    project_id = get_project_id(workspace_id)
    async with driver.session(database=neo4j_db) as session:
        result = await graph_core._execute_read(
            session,
            """
            MATCH (f1:File {project_id: $p})
            WHERE (f1.filepath CONTAINS 'ui' OR f1.filepath CONTAINS 'view' OR f1.filepath CONTAINS 'component' OR f1.filepath CONTAINS 'pages')
              AND NOT (f1.filepath CONTAINS 'test' OR f1.filepath CONTAINS 'spec')
            MATCH (f1)-[:IMPORTS]->(f2:File {project_id: $p})
            WHERE (f2.filepath CONTAINS 'api' OR f2.filepath CONTAINS 'client' OR f2.filepath CONTAINS 'controller' OR f2.filepath CONTAINS 'routes')
            OPTIONAL MATCH (f2)-[:IMPORTS]->(f3:File {project_id: $p})
            WHERE (f3.filepath CONTAINS 'service' OR f3.filepath CONTAINS 'domain' OR f3.filepath CONTAINS 'provider' OR f3.filepath CONTAINS 'usecase')
            OPTIONAL MATCH (f3)-[:IMPORTS]->(f4:File {project_id: $p})
            WHERE (f4.filepath CONTAINS 'model' OR f4.filepath CONTAINS 'db' OR f4.filepath CONTAINS 'entity' OR f4.filepath CONTAINS 'schema')
            RETURN f1.filepath AS ui, f2.filepath AS api, f3.filepath AS svc, f4.filepath AS model
            ORDER BY ui, api
            LIMIT $limit
            """,
            p=project_id,
            limit=limit * 2,
            op="get_heuristic_flow_summary",
        )
    if not result:
        return "No heuristic paths found."

    rows = []
    for row in result:
        path = [value for value in [row.get("ui"), row.get("api"), row.get("svc"), row.get("model")] if value]
        if len(path) >= 2:
            rows.append(path)

    if not rows:
        return "No heuristic paths found."

    if as_table:
        output = ["| Origin | Endpoint | Secondary | Data |", "| --- | --- | --- | --- |"]
        for row in rows[:limit]:
            padded = row + [""] * (4 - len(row))
            output.append(f"| {' | '.join(padded)} |")
        return "\n".join(output)
    return "\n".join([" -> ".join(row) for row in rows[:limit]])


async def get_topology_summary_impl(
    *,
    driver,
    neo4j_db: str,
    workspace_id: str,
    limit: int = 10,
) -> str:
    project_id = get_project_id(workspace_id)
    async with driver.session(database=neo4j_db) as session:
        result = await graph_core._execute_read(
            session,
            """
            MATCH (f:File {project_id: $p})
            OPTIONAL MATCH (f)-[:IMPORTS]->(out:File {project_id: $p})
            OPTIONAL MATCH (in:File {project_id: $p})-[:IMPORTS]->(f)
            WITH f, count(DISTINCT out) AS outbound, count(DISTINCT in) AS inbound
            WHERE inbound + outbound > 0
            RETURN f.filepath AS fp, inbound, outbound
            ORDER BY inbound + outbound DESC
            LIMIT $limit
            """,
            p=project_id,
            limit=limit,
            op="get_topology_summary",
        )
    if not result:
        return "No architectural topology found (index might be empty)."

    output = ["### Architectural Topology (Most Connected Files)\n"]
    for rec in result:
        output.append(f"- `{rec['fp']}`: {rec['inbound']} incoming, {rec['outbound']} outgoing imports")
    return "\n".join(output)
