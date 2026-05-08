"""Helpers for cross-store reference lookups."""

from __future__ import annotations

import os
import re

from neo4j import unit_of_work

from _helpers import get_memory_modules, get_project_id, get_workspace_path


_LOW_SIGNAL_REFERENCE_RE = re.compile(
    r"(^|/)(session-ses_[^/]+\.md|agents\.md|readme(?:\.[^/]+)?|changelog(?:\.[^/]+)?)$",
    re.IGNORECASE,
)
_LOW_SIGNAL_REFERENCE_DIR_RE = re.compile(r"(^|/)(docs?|documentation|notes?)/", re.IGNORECASE)


def _file_roles_present(raw_roles) -> bool:
    return isinstance(raw_roles, list)


def _normalize_file_roles(raw_roles) -> set[str]:
    if not _file_roles_present(raw_roles):
        return set()
    return {
        str(role).strip().lower()
        for role in raw_roles
        if isinstance(role, str) and str(role).strip()
    }


def _reference_path_penalty(file_path: str | None, raw_roles=None) -> tuple[int, str]:
    norm = (file_path or "").replace("\\", "/").lower()
    roles = _normalize_file_roles(raw_roles)
    if not norm:
        return (9, norm)
    if _LOW_SIGNAL_REFERENCE_RE.search(norm) or _LOW_SIGNAL_REFERENCE_DIR_RE.search(norm):
        return (8, norm)
    if norm.endswith((".md", ".rst", ".txt")):
        return (7, norm)
    if {"test_surface", "example_surface", "benchmark_surface"} & roles:
        return (5, norm)
    if _file_roles_present(raw_roles):
        test_like = False
    else:
        test_like = (
            norm.startswith("tests/")
            or norm.startswith("test/")
            or norm.startswith("examples/")
            or norm.startswith("fixtures/")
            or norm.startswith("e2e/")
            or any(
                token in norm
                for token in ("/tests/", "/test/", "/examples/", "/fixtures/", "/e2e/", ".spec.", ".stories.")
            )
        )
    if test_like:
        return (5, norm)
    if {"generated_surface", "binding_surface"} & roles:
        return (4, norm)
    if not _file_roles_present(raw_roles) and any(
        token in norm for token in ("/generated/", "/gen/", ".gen.", "_generated.", "pregeneratedspm/")
    ):
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


def _file_level_caller_symbol_name(file_path: str | None, caller_name: str | None) -> str | None:
    fp = str(file_path or "").strip()
    cn = str(caller_name or "").strip()
    if not fp or not cn:
        return None
    basename = os.path.basename(fp)
    if cn != basename:
        return None
    stem, ext = os.path.splitext(basename)
    if not stem or not ext:
        return None
    return stem


def _lookup_local_declaration_line(
    workspace_root: str | None,
    file_path: str | None,
    symbol_name: str | None,
) -> int | None:
    root = str(workspace_root or "").strip()
    rel_path = str(file_path or "").strip()
    target_name = str(symbol_name or "").strip()
    if not root or not rel_path or not target_name:
        return None
    abs_path = os.path.join(root, rel_path)
    if not os.path.exists(abs_path):
        return None
    ext = os.path.splitext(abs_path)[1].lower()
    declaration_patterns = {
        ".swift": rf"^\s*(?:public|internal|private|fileprivate|open|final|indirect|actor|static|\s)*\b"
        rf"(?:struct|class|enum|protocol|extension)\s+{re.escape(target_name)}\b",
        ".go": rf"^\s*func\s*(?:\([^)]*\)\s*)?{re.escape(target_name)}\b",
        ".py": rf"^\s*(?:async\s+def|def|class)\s+{re.escape(target_name)}\b",
        ".rs": rf"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|struct|enum|trait)\s+{re.escape(target_name)}\b",
        ".ts": rf"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type|const)\s+{re.escape(target_name)}\b",
        ".tsx": rf"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|interface|type|const)\s+{re.escape(target_name)}\b",
        ".js": rf"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const)\s+{re.escape(target_name)}\b",
        ".jsx": rf"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const)\s+{re.escape(target_name)}\b",
    }
    declaration_re = re.compile(
        declaration_patterns.get(
            ext,
            rf"^\s*(?:def|class|struct|enum|interface|trait|function|func)\s+{re.escape(target_name)}\b",
        )
    )
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
            for line_no, raw_line in enumerate(fh, start=1):
                if declaration_re.search(raw_line):
                    return line_no
    except Exception:
        return None
    return None


def _is_definition_like_preview(file_path: str | None, preview: str | None, symbol_name: str) -> bool:
    line = str(preview or "").strip()
    if not line:
        return False
    ext = os.path.splitext(str(file_path or "").strip().lower())[1]
    target = re.escape(symbol_name)
    patterns = {
        ".swift": rf"\b(?:struct|class|enum|protocol|extension)\s+{target}\b",
        ".go": rf"^\s*func\s*(?:\([^)]*\)\s*)?{target}\b",
        ".py": rf"^\s*(?:async\s+def|def|class)\s+{target}\b",
        ".rs": rf"\b(?:fn|struct|enum|trait)\s+{target}\b",
        ".ts": rf"\b(?:function|class|interface|type|const)\s+{target}\b",
        ".tsx": rf"\b(?:function|class|interface|type|const)\s+{target}\b",
        ".js": rf"\b(?:function|class|const)\s+{target}\b",
        ".jsx": rf"\b(?:function|class|const)\s+{target}\b",
    }
    return re.search(patterns.get(ext, rf"\b{target}\b"), line) is not None


async def find_references_impl(workspace_id: str | list[str], symbol_name: str) -> str:
    try:
        works = [workspace_id] if isinstance(workspace_id, str) else workspace_id
        pids = [get_project_id(workspace) for workspace in works]
        workspace_roots = {
            get_project_id(workspace): get_workspace_path(workspace)
            for workspace in works
        }

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
        definition_rows: list[tuple[str, int | None, str, str]] = []
        definition_paths: set[str] = set()
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            definition_records = await _execute_read(
                session,
                """
                MATCH (target:Node)-[:DEFINED_IN_FILE]->(f:File)
                WHERE target.project_id IN $pids
                  AND (target.name = $name OR target.qualified_name = $name)
                RETURN DISTINCT f.filepath AS fp,
                                target.start_line AS sl,
                                target.name AS cn,
                                target.project_id AS tpid
                """,
                name=symbol_name,
                pids=pids,
                op="find_references_definitions",
            )
            definition_paths = {str(rec.get("fp") or "") for rec in definition_records if rec.get("fp")}
            definition_rows = [
                (
                    str(rec.get("fp") or ""),
                    rec.get("sl"),
                    str(rec.get("cn") or symbol_name),
                    str(rec.get("tpid") or ""),
                )
                for rec in definition_records
                if rec.get("fp")
            ]
            records = await _execute_read(
                session,
                """
                MATCH (target {name: $name})
                WHERE target.project_id IN $pids
                MATCH (caller:Node)-[:CALLS|CALLS_INFERRED]->(target)
                MATCH (f:File)-[:CONTAINS]->(caller)
                RETURN f.filepath AS fp, caller.start_line AS sl, caller.name AS cn, target.project_id AS tpid
                UNION
                MATCH (target {name: $name})
                WHERE target.project_id IN $pids
                MATCH (caller:File)-[:CALLS|CALLS_INFERRED]->(target)
                RETURN caller.filepath AS fp, null AS sl, caller.name AS cn, target.project_id AS tpid
                UNION
                MATCH (target {qualified_name: $name})
                WHERE target.project_id IN $pids
                MATCH (caller:Node)-[:CALLS|CALLS_INFERRED]->(target)
                MATCH (f:File)-[:CONTAINS]->(caller)
                RETURN f.filepath AS fp, caller.start_line AS sl, caller.name AS cn, target.project_id AS tpid
                UNION
                MATCH (target {qualified_name: $name})
                WHERE target.project_id IN $pids
                MATCH (caller:File)-[:CALLS|CALLS_INFERRED]->(target)
                RETURN caller.filepath AS fp, null AS sl, caller.name AS cn, target.project_id AS tpid
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
            for fp, line, caller_name, target_pid in definition_rows:
                line_part = f":{line}" if line else ""
                key = (fp, str(line) if line else None)
                if key in graph_ref_keys:
                    continue
                graph_refs.append(f"- {fp}{line_part} ({caller_name}) [Project: {target_pid}]")
                graph_ref_keys.add(key)
            file_level_callers: dict[tuple[str, str], str] = {}
            for rec in records:
                inferred_symbol = _file_level_caller_symbol_name(rec.get("fp"), rec.get("cn"))
                if inferred_symbol:
                    file_level_callers[(str(rec.get("tpid") or ""), str(rec.get("fp") or ""))] = inferred_symbol
            file_level_symbol_rows: dict[tuple[str, str], tuple[str, int | None]] = {}
            if file_level_callers:
                promoted = await _execute_read(
                    session,
                    """
                    UNWIND $rows AS row
                    MATCH (f:File {project_id: row.pid, filepath: row.fp})-[:CONTAINS]->(s:Node)
                    WHERE s.name = row.symbol_name
                    RETURN row.pid AS pid, row.fp AS fp, s.name AS symbol_name, s.start_line AS sl
                    ORDER BY sl ASC
                    """,
                    rows=[
                        {"pid": pid, "fp": fp, "symbol_name": inferred_symbol}
                        for (pid, fp), inferred_symbol in file_level_callers.items()
                    ],
                    op="find_references_file_level_callers",
                )
                for row in promoted:
                    key = (str(row.get("pid") or ""), str(row.get("fp") or ""))
                    if key in file_level_symbol_rows:
                        continue
                    file_level_symbol_rows[key] = (
                        str(row.get("symbol_name") or ""),
                        row.get("sl"),
                    )
            for rec in records:
                key = (str(rec.get("tpid") or ""), str(rec.get("fp") or ""))
                promoted = file_level_symbol_rows.get(key)
                line = promoted[1] if promoted and promoted[1] is not None else rec.get("sl")
                caller_name = promoted[0] if promoted and promoted[0] else rec.get("cn")
                if promoted is None:
                    inferred_symbol = _file_level_caller_symbol_name(rec.get("fp"), rec.get("cn"))
                    local_line = _lookup_local_declaration_line(
                        workspace_roots.get(str(rec.get("tpid") or "")),
                        rec.get("fp"),
                        inferred_symbol,
                    )
                    if inferred_symbol:
                        caller_name = inferred_symbol
                    if local_line is not None:
                        line = local_line
                line_part = f":{line}" if line else ""
                graph_refs.append(f"- {rec['fp']}{line_part} ({caller_name}) [Project: {rec['tpid']}]")
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
        semantic_definition_candidates: list[tuple[int, str, str]] = []
        memory_store, _, _, _, _ = get_memory_modules()
        await memory_store.open_pool()
        async with memory_store._pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT file_path,
                           metadata->>'start_line' as start_line,
                           project_id,
                           metadata->'file_roles' as file_roles,
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
                    fp, sl, pid, file_roles, text = row
                    if (fp, str(sl) if sl else None) in graph_ref_keys:
                        continue
                    preview = _semantic_snippet(text, symbol_name)
                    if not preview:
                        continue
                    bucket, _ = _reference_path_penalty(fp, file_roles)
                    if bucket < 4 and _is_definition_like_preview(fp, preview, symbol_name):
                        semantic_definition_candidates.append((bucket, fp or "", str(pid or "")))
                    line_part = f":{sl}" if sl else ""
                    rendered = f"- {fp}{line_part} (semantic) [Project: {pid}]  >> {preview}..."
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

        if not any(f"({symbol_name})" in ref for ref in graph_refs):
            for _, fp, pid in sorted(semantic_definition_candidates, key=lambda item: (item[0], item[1], item[2])):
                line = _lookup_local_declaration_line(
                    workspace_roots.get(pid),
                    fp,
                    symbol_name,
                )
                line_part = f":{line}" if line else ""
                key = (fp, str(line) if line else None)
                if key in graph_ref_keys:
                    continue
                graph_refs.append(f"- {fp}{line_part} ({symbol_name}) [Project: {pid}]")
                graph_ref_keys.add(key)
                break

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
