"""Helpers for cross-store reference lookups."""

from __future__ import annotations

import os

from neo4j import unit_of_work

from _helpers import get_memory_modules, get_project_id


async def find_references_impl(workspace_id: str | list[str], symbol_name: str) -> str:
    try:
        works = [workspace_id] if isinstance(workspace_id, str) else workspace_id
        pids = [get_project_id(workspace) for workspace in works]

        import graph_bootstrap

        tx_timeout = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
        tx_op_prefix = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
        tx_metadata_base = {"source": "lm_proxy", "tool": "code_intel"}

        async def _execute_read(session, cypher: str, timeout: float | None = None, op: str | None = None, **params):
            metadata = dict(tx_metadata_base)
            op_value = op or "read"
            if tx_op_prefix:
                op_value = f"{tx_op_prefix}.{op_value}"
            metadata["op"] = op_value

            @unit_of_work(timeout=timeout or tx_timeout, metadata=metadata)
            async def _tx(tx):
                result = await tx.run(cypher, **params)
                return await result.data()

            if hasattr(session, "execute_read"):
                return await session.execute_read(_tx)
            return await _tx(session)

        driver = await graph_bootstrap.require_driver()

        graph_refs = []
        graph_ref_keys: set[tuple[str, str | None]] = set()
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            records = await _execute_read(
                session,
                """
                MATCH (target {name: $name})
                WHERE target.project_id IN $pids
                MATCH (caller:Node)-[:CALLS|CALLS_INFERRED]->(target)
                MATCH (f:File)-[:CONTAINS]->(caller)
                RETURN f.filepath AS fp, caller.start_line AS sl, caller.name AS cn, target.project_id AS tpid
                UNION
                MATCH (target {qualified_name: $name})
                WHERE target.project_id IN $pids
                MATCH (caller:Node)-[:CALLS|CALLS_INFERRED]->(target)
                MATCH (f:File)-[:CONTAINS]->(caller)
                RETURN f.filepath AS fp, caller.start_line AS sl, caller.name AS cn, target.project_id AS tpid
                UNION
                MATCH (target {name: $name})
                WHERE target.project_id IN $pids
                MATCH (f:File)-[:IMPORTS_SYMBOL]->(target)
                RETURN f.filepath AS fp, null AS sl, f.name AS cn, target.project_id AS tpid
                UNION
                MATCH (f:File {project_id: $pid})-[:CONTAINS]->(imp:Import)
                WHERE imp.source CONTAINS $name OR imp.source =~ $re
                RETURN f.filepath AS fp, null AS sl, f.name AS cn, $pid AS tpid
                """,
                name=symbol_name,
                pids=pids,
                pid=pids[0] if pids else "",
                re=f".*\\b{symbol_name}\\b.*",
                op="find_references",
            )
            for rec in records:
                line = rec.get("sl")
                line_part = f":{line}" if line else ""
                graph_refs.append(f"- {rec['fp']}{line_part} ({rec['cn']}) [Project: {rec['tpid']}]")
                graph_ref_keys.add((rec["fp"], str(line) if line else None))

        semantic_refs = []
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
                    if (fp, str(sl) if sl else None) in graph_ref_keys:
                        continue
                    preview = text.strip().splitlines()[0][:80]
                    semantic_refs.append(f"- {fp}:{sl} (semantic) [Project: {pid}]  >> {preview}...")

        sections = []
        if graph_refs:
            sections.append("### Functional References (Graph)\n" + "\n".join(sorted(list(set(graph_refs)))))
        if semantic_refs:
            sections.append("### Mentions & Type Usages (Semantic)\n" + "\n".join(sorted(list(set(semantic_refs)))))
        if not sections:
            return f"No references found for '{symbol_name}' in the specified projects."
        return "\n\n".join(sections)
    except Exception as exc:
        return f"Error finding references: {str(exc)}"
