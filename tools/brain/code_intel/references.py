"""Helpers for cross-store reference lookups."""

from __future__ import annotations

import os
import re

from neo4j import unit_of_work

from _helpers import get_memory_modules, get_project_id


_LOW_SIGNAL_REFERENCE_RE = re.compile(
    r"(^|/)(session-ses_[^/]+\.md|agents\.md|readme(?:\.[^/]+)?|changelog(?:\.[^/]+)?)$",
    re.IGNORECASE,
)
_LOW_SIGNAL_REFERENCE_DIR_RE = re.compile(r"(^|/)(docs?|documentation|notes?)/", re.IGNORECASE)


def _reference_path_penalty(file_path: str | None) -> tuple[int, str]:
    norm = (file_path or "").replace("\\", "/").lower()
    if not norm:
        return (9, norm)
    if _LOW_SIGNAL_REFERENCE_RE.search(norm) or _LOW_SIGNAL_REFERENCE_DIR_RE.search(norm):
        return (8, norm)
    if norm.endswith((".md", ".rst", ".txt")):
        return (7, norm)
    if any(token in norm for token in ("/tests/", "/test/", "/fixtures/", "/e2e/", ".spec.", ".stories.")):
        return (5, norm)
    if any(token in norm for token in ("/generated/", "/gen/", ".gen.", "_generated.", "pregeneratedspm/")):
        return (4, norm)
    if norm.endswith(".swift"):
        return (0, norm)
    if norm.endswith((".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go")):
        return (1, norm)
    return (3, norm)


def _semantic_snippet(content: str | None, symbol_name: str) -> str:
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("// File:"):
            continue
        if symbol_name in line:
            return line[:120]
    return ""


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
        external_refs = []
        definition_paths: set[str] = set()
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            definition_records = await _execute_read(
                session,
                """
                MATCH (target:Node)-[:DEFINED_IN_FILE]->(f:File)
                WHERE target.project_id IN $pids
                  AND (target.name = $name OR target.qualified_name = $name)
                RETURN DISTINCT f.filepath AS fp
                """,
                name=symbol_name,
                pids=pids,
                op="find_references_definitions",
            )
            definition_paths = {str(rec.get("fp") or "") for rec in definition_records if rec.get("fp")}
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

            external_records = await _execute_read(
                session,
                """
                MATCH (target:ExternalSymbol)
                WHERE target.project_id IN $pids
                  AND (target.name = $name OR target.qualified_name = $name)
                MATCH (caller:Node)-[:CALLS_EXTERNAL_SYMBOL]->(target)
                MATCH (f:File)-[:CONTAINS]->(caller)
                RETURN f.filepath AS fp,
                       caller.start_line AS sl,
                       caller.name AS cn,
                       target.qualified_name AS qn,
                       target.language AS language,
                       target.project_id AS tpid
                """,
                name=symbol_name,
                pids=pids,
                op="find_references_external",
            )
            for rec in external_records:
                line = rec.get("sl")
                line_part = f":{line}" if line else ""
                external_refs.append(
                    f"- {rec['fp']}{line_part} ({rec['cn']}) -> {rec.get('qn') or symbol_name} "
                    f"[{rec.get('language') or 'external'} | Project: {rec['tpid']}]"
                )

        semantic_refs: list[tuple[int, int, str, str]] = []
        low_signal_semantic_refs: list[tuple[int, int, str, str]] = []
        memory_store, _, _, _, _ = get_memory_modules()
        await memory_store.open_pool()
        async with memory_store._pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT file_path,
                           metadata->>'start_line' as start_line,
                           project_id,
                           content
                    FROM codebase_embeddings
                    WHERE project_id = ANY(%s)
                      AND (content ILIKE %s OR content ~ %s)
                    LIMIT 240
                    """,
                    (pids, f"%{symbol_name}%", f"\\b{symbol_name}\\b"),
                )
                semantic_candidates: dict[str, tuple[int, int, str, str]] = {}
                async for row in cur:
                    fp, sl, pid, text = row
                    if (fp, str(sl) if sl else None) in graph_ref_keys:
                        continue
                    preview = _semantic_snippet(text, symbol_name)
                    if not preview:
                        continue
                    line_part = f":{sl}" if sl else ""
                    rendered = f"- {fp}{line_part} (semantic) [Project: {pid}]  >> {preview}..."
                    bucket, _ = _reference_path_penalty(fp)
                    same_definition_file = 1 if str(fp or "") in definition_paths else 0
                    entry = (bucket, same_definition_file, fp or "", rendered)
                    current = semantic_candidates.get(fp or "")
                    if current is None or entry < current:
                        semantic_candidates[fp or ""] = entry

                for entry in semantic_candidates.values():
                    bucket, same_definition_file, fp, rendered = entry
                    if bucket >= 7 or same_definition_file:
                        low_signal_semantic_refs.append(entry)
                    else:
                        semantic_refs.append(entry)

        sections = []
        if graph_refs:
            sections.append("### Functional References (Graph)\n" + "\n".join(sorted(list(set(graph_refs)))))
        if external_refs:
            sections.append("### External Symbol Callers (Graph)\n" + "\n".join(sorted(list(set(external_refs)))))
        if semantic_refs:
            deduped = []
            seen = set()
            for _, _, _, rendered in sorted(semantic_refs, key=lambda item: (item[0], item[1], item[2], item[3])):
                if rendered in seen:
                    continue
                seen.add(rendered)
                deduped.append(rendered)
            sections.append("### Mentions & Type Usages (Semantic)\n" + "\n".join(deduped))
        if low_signal_semantic_refs and not (graph_refs or external_refs or semantic_refs):
            deduped = []
            seen = set()
            for _, _, _, rendered in sorted(low_signal_semantic_refs, key=lambda item: (item[0], item[1], item[2], item[3])):
                if rendered in seen:
                    continue
                seen.add(rendered)
                deduped.append(rendered)
            sections.append("### Supporting Mentions (Low-signal Semantic)\n" + "\n".join(deduped[:10]))
        if not sections:
            return f"No references found for '{symbol_name}' in the specified projects."
        return "\n\n".join(sections)
    except Exception as exc:
        return f"Error finding references: {str(exc)}"
