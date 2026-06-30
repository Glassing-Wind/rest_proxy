"""Cross-project symbol tracing core.

This module owns cross-project definition selection, usage retrieval, and
role-aware ranking. MCP wrappers should delegate here and only handle tool
registration/presentation boundaries.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from _helpers import WorkspaceRegistry, get_memory_modules, get_project_id, get_workspace_path


ExecuteRead = Callable[..., Awaitable[list[dict]]]

_STUB_SUFFIXES = (".pyi", ".d.ts", ".d.cts", ".d.mts")
_CODE_SUFFIXES = (".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".go", ".swift")


def _is_test_like_path(file_path: str) -> bool:
    normalized = (file_path or "").lower()
    return (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or "__tests__" in normalized
        or normalized.startswith("test_")
        or "/test_" in normalized
        or normalized.endswith("_test.py")
        or normalized.endswith("_test.rs")
        or normalized.endswith("_spec.rb")
        or ".test." in normalized
        or ".spec." in normalized
    )


def _coerce_file_roles(raw_roles) -> set[str] | None:
    if raw_roles is None:
        return None
    if isinstance(raw_roles, dict):
        raw_roles = raw_roles.get("file_roles")
    if isinstance(raw_roles, (list, tuple, set)):
        return {str(role).strip() for role in raw_roles if str(role).strip()}
    return None


def _definition_rank(record: dict) -> tuple[int, int, str]:
    filepath = str(record.get("filepath") or "")
    kind = str(record.get("kind") or "")
    roles = _coerce_file_roles(record.get("file_roles"))
    score = 0
    if filepath.endswith(_STUB_SUFFIXES):
        score -= 50
    elif filepath.endswith(_CODE_SUFFIXES):
        score += 20
    if kind == "ExportAlias":
        score -= 10
    if roles is not None:
        if roles & {"test_surface", "example_surface", "benchmark_surface"}:
            score -= 15
        if "implementation_surface" not in roles and roles & {
            "docs_surface",
            "support_surface",
        }:
            score -= 10
    else:
        normalized = filepath.lower()
        if _is_test_like_path(normalized):
            score -= 15
        elif (
            normalized.startswith("docs/")
            or "/docs/" in normalized
            or normalized.startswith("examples/")
            or "/examples/" in normalized
            or normalized.startswith("fixtures/")
            or "/fixtures/" in normalized
            or normalized.startswith("benchmark/")
            or "/benchmark/" in normalized
            or normalized.startswith("benchmarks/")
            or "/benchmarks/" in normalized
        ):
            score -= 10
    if "__init__.pyi" in filepath:
        score -= 15
    if "__init__.py" in filepath:
        score -= 5
    start_line = int(record.get("start_line") or 0)
    return score, -start_line, filepath


def _is_test_like_cross_project_hit(file_path: str, raw_roles) -> bool:
    roles = _coerce_file_roles(raw_roles)
    if roles is not None:
        if {"test_surface", "example_surface", "benchmark_surface"} & roles:
            return True
        if "implementation_surface" in roles:
            return False
        return bool(roles & {"docs_surface", "support_surface"})
    normalized = (file_path or "").lower()
    return _is_test_like_path(normalized) or (
        normalized.startswith("docs/")
        or "/docs/" in normalized
        or normalized.startswith("examples/")
        or "/examples/" in normalized
        or normalized.startswith("fixtures/")
        or "/fixtures/" in normalized
        or normalized.startswith("benchmark/")
        or "/benchmark/" in normalized
        or normalized.startswith("benchmarks/")
        or "/benchmarks/" in normalized
    )


def _semantic_usage_rank(record: dict) -> tuple[int, float, str, int]:
    filepath = str(record.get("file_path") or "")
    content = str(record.get("content") or "")
    score = 0
    if filepath.endswith(_STUB_SUFFIXES):
        score -= 40
    elif filepath.endswith(_CODE_SUFFIXES):
        score += 20
    if _is_test_like_cross_project_hit(filepath, record.get("file_roles")):
        score -= 35
    if "/scripts/" in filepath or filepath.startswith("scripts/"):
        score += 8
    if "/src/" in filepath or filepath.startswith("src/"):
        score += 10
    if "/tools/" in filepath or filepath.startswith("tools/"):
        score += 4
    if "def " in content or "fn " in content or "function " in content or "class " in content:
        score += 3
    rrf = float(record.get("rrf") or 0.0)
    chunk_index = int(record.get("chunk_index") or 0)
    return score, rrf, filepath, -chunk_index


def _render_semantic_hit(record: dict) -> str:
    fp = str(record.get("file_path") or "?")
    idx = record.get("chunk_index")
    content = str(record.get("content") or "")
    rrf = float(record.get("rrf") or 0.0)
    return (
        f"  [chunk {idx}]  {fp}  (score: {rrf:.4f})\n"
        f"    {content[:200].strip().replace(chr(10), ' ')}..."
    )


def _symbol_centered_preview(content: str, symbol_name: str, limit: int = 600) -> str:
    text = str(content or "").strip()
    if len(text) <= limit:
        return text
    hit = text.lower().find(str(symbol_name or "").lower())
    if hit < 0:
        return text[:limit].rstrip()
    start = max(0, hit - limit // 3)
    line_start = text.rfind("\n", 0, start)
    if line_start >= 0:
        start = line_start + 1
    end = min(len(text), start + limit)
    return text[start:end].rstrip()


async def trace_symbol_cross_project_core(
    *,
    symbol_name: str,
    source_workspace: str,
    target_workspace: str,
    execute_read: ExecuteRead,
) -> str:
    """Trace where a source-project symbol is consumed in a target project."""
    from embedding_service import get_embedding_service
    import graph_bootstrap

    memory_store, _, _, _, _ = get_memory_modules()

    src_id = WorkspaceRegistry.resolve_id(source_workspace) or get_project_id(source_workspace)
    tgt_id = WorkspaceRegistry.resolve_id(target_workspace) or get_project_id(target_workspace)

    src_path = get_workspace_path(source_workspace)
    tgt_path = get_workspace_path(target_workspace)
    src_name = (src_path or source_workspace).rstrip("/").split("/")[-1]
    tgt_name = (tgt_path or target_workspace).rstrip("/").split("/")[-1]

    driver = await graph_bootstrap.require_driver()

    definition: dict = {}
    resolved_names: list[str] = [symbol_name]
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        records = await execute_read(
            session,
            """
            MATCH (s {name: $name, project_id: $pid})
            WHERE s:Function OR s:Class OR s:Struct OR s:Trait
               OR s:Enum OR s:Method OR s:Protocol
               OR s:Interface OR s:Extension OR s:TypeAlias OR s:AssociatedType
            OPTIONAL MATCH (s)<-[:CONTAINS]-(f:File)
            RETURN head([label IN labels(s) WHERE label <> 'Node']) AS kind,
                   s.filepath    AS filepath,
                   s.start_line  AS start_line,
                   s.end_line    AS end_line,
                   s.signature   AS signature,
                   f.semantic_file_roles AS file_roles
            LIMIT 12
            """,
            name=symbol_name,
            pid=src_id,
            op="trace_symbol_definition",
        )
        alias_records = await execute_read(
            session,
            """
            MATCH (f:File {project_id: $pid})-[alias:EXPORTS_SYMBOL_AS]->(target)
            WHERE alias.name = $name
            OPTIONAL MATCH (f)-[:EXPORTS_SYMBOL]->(target)
            RETURN 'ExportAlias' AS kind,
                   f.filepath AS filepath,
                   alias.line AS start_line,
                   alias.line AS end_line,
                   coalesce(target.signature, target.name) AS signature,
                   target.name AS target_name,
                   f.semantic_file_roles AS file_roles
            ORDER BY f.filepath ASC
            LIMIT 12
            """,
            name=symbol_name,
            pid=src_id,
            op="trace_symbol_alias_definition",
        )
        candidates = [dict(rec) for rec in records] + [dict(rec) for rec in alias_records]
        if candidates:
            definition = max(candidates, key=_definition_rank)
            target_name = definition.get("target_name")
            if isinstance(target_name, str) and target_name and target_name != symbol_name:
                resolved_names.append(target_name)

    graph_usages: list[str] = []
    async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
        records = await execute_read(
            session,
            """
            MATCH (target)
            WHERE target.name IN $names
              AND (target:Function OR target:Class OR target:Struct
               OR target:Method   OR target:Trait OR target:Protocol
               OR target:Interface OR target:Extension OR target:TypeAlias OR target:AssociatedType)
            MATCH (caller {project_id: $tpid})-[:CALLS|CALLS_INFERRED]->(target)
            RETURN DISTINCT
                   caller.name      AS caller_name,
                   caller.filepath  AS caller_file,
                   caller.start_line AS caller_line,
                   head([label IN labels(caller) WHERE label <> 'Node']) AS caller_kind
            ORDER BY caller.filepath, caller.start_line
            LIMIT 20
            """,
            names=resolved_names,
            tpid=tgt_id,
            op="trace_symbol_graph_usages",
        )
        for rec in records:
            name = rec["caller_name"] or "(file scope)"
            fp = rec["caller_file"] or "?"
            line = f":{rec['caller_line']}" if rec["caller_line"] else ""
            graph_usages.append(
                f"  {name}{line}  [{rec['caller_kind'] or 'Node'}]  in {fp}"
            )

    sem_usage_records: list[dict] = []
    await memory_store.open_pool()

    svc = get_embedding_service()
    semantic_query = " ".join(dict.fromkeys(resolved_names))
    vecs = await svc.embed_batch_async([semantic_query])
    query_vector = vecs[0]

    async def _fetch_semantic():
        vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"
        async with memory_store._pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    WITH exact AS (
                        SELECT file_path, chunk_index, content, metadata, 1000.0 AS rrf
                        FROM codebase_embeddings
                        WHERE project_id = %(pid)s
                          AND (
                            content ILIKE %(ilike)s
                            OR (%(ilike_alt)s <> '' AND content ILIKE %(ilike_alt)s)
                          )
                        LIMIT 200
                    ),
                    sem AS (
                        SELECT file_path, chunk_index, content, metadata,
                               ROW_NUMBER() OVER (
                                   ORDER BY embedding <=> %(vec)s::vector
                               ) AS sem_rank
                        FROM codebase_embeddings
                        WHERE project_id = %(pid)s
                        LIMIT 40
                    ),
                    kw AS (
                        SELECT file_path, chunk_index,
                               ROW_NUMBER() OVER (
                                   ORDER BY ts_rank(search_vec,
                                       plainto_tsquery('english', %(qt)s)) DESC
                               ) AS kw_rank
                        FROM codebase_embeddings
                        WHERE project_id = %(pid)s
                          AND (
                            content ILIKE %(ilike)s
                            OR (%(ilike_alt)s <> '' AND content ILIKE %(ilike_alt)s)
                          )
                        LIMIT 40
                    )
                    SELECT file_path, chunk_index, content, metadata, rrf FROM exact
                    UNION ALL
                    SELECT s.file_path, s.chunk_index, s.content, s.metadata,
                           (1.0/(60+s.sem_rank) + COALESCE(1.0/(60+k.kw_rank), 0.0)) AS rrf
                    FROM sem s LEFT JOIN kw k
                      ON s.file_path = k.file_path AND s.chunk_index = k.chunk_index
                    WHERE (
                        s.content ILIKE %(ilike)s
                        OR (%(ilike_alt)s <> '' AND s.content ILIKE %(ilike_alt)s)
                    )
                    ORDER BY rrf DESC LIMIT 200
                    """,
                    {
                        "vec": vec_str,
                        "pid": tgt_id,
                        "qt": semantic_query,
                        "ilike": f"%{symbol_name}%",
                        "ilike_alt": (
                            f"%{resolved_names[1]}%" if len(resolved_names) > 1 else ""
                        ),
                    },
                )
                rows = await cur.fetchall()
                deduped = []
                seen = set()
                for row in rows:
                    key = (row[0], row[1])
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(row)
                return deduped

    async def _fetch_src_preview():
        if not definition.get("filepath"):
            return []
        async with memory_store._pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT content FROM codebase_embeddings
                    WHERE project_id = %s AND file_path = %s
                    ORDER BY
                      CASE WHEN content ILIKE %s THEN 0 ELSE 1 END,
                      ABS(
                        COALESCE((metadata->>'start_line')::int, 0) - %s
                      ),
                      chunk_index
                    LIMIT 2
                    """,
                    (
                        src_id,
                        definition["filepath"],
                        f"%{symbol_name}%",
                        int(definition.get("start_line") or 0),
                    ),
                )
                return await cur.fetchall()

    sem_rows, src_rows = await asyncio.gather(_fetch_semantic(), _fetch_src_preview())

    seen_files: set[str] = set()
    sorted_semantic_rows = sorted(
        (
            {
                "file_path": fp,
                "chunk_index": idx,
                "content": content,
                "file_roles": (metadata or {}).get("file_roles") if isinstance(metadata, dict) else None,
                "rrf": rrf,
            }
            for fp, idx, content, metadata, rrf in sem_rows
        ),
        key=_semantic_usage_rank,
        reverse=True,
    )
    for row in sorted_semantic_rows:
        fp = str(row.get("file_path") or "")
        if fp in seen_files:
            continue
        seen_files.add(fp)
        sem_usage_records.append(dict(row))
        if len(sem_usage_records) >= 5:
            break
    implementation_hits = [
        record
        for record in sem_usage_records
        if not _is_test_like_cross_project_hit(
            str(record.get("file_path") or ""),
            record.get("file_roles"),
        )
    ]
    supporting_hits = [
        record
        for record in sem_usage_records
        if _is_test_like_cross_project_hit(
            str(record.get("file_path") or ""),
            record.get("file_roles"),
        )
    ]

    lines = [
        f"## Cross-project trace: `{symbol_name}`",
        f"   {src_name}  →  {tgt_name}",
        "",
    ]

    if definition:
        lines += [
            f"### Definition  [{src_name}]",
            f"  Kind:      {definition.get('kind', '?')}",
            f"  File:      {definition.get('filepath', '?')}  "
            f"L{definition.get('start_line', '?')}-{definition.get('end_line', '?')}",
        ]
        if definition.get("signature"):
            lines.append(f"  Signature: {definition['signature']}")
        if src_rows:
            preview = "\n".join(
                _symbol_centered_preview(r[0], symbol_name)
                for r in src_rows
            )
            lines += ["", f"```\n{preview.strip()}\n```"]
    else:
        lines.append(f"`{symbol_name}` not found in Neo4j for [{src_name}].")
        lines.append("   (Symbol may be in an un-indexed file or a different casing.)")

    lines += ["", f"### Usages  [{tgt_name}]"]

    if graph_usages or sem_usage_records:
        lines.append("Use this to decide where the target project most concretely depends on the source symbol.")
        if graph_usages:
            lines.append("")
            lines.append("Inspect First")
            lines.append(f"- start with `{graph_usages[0].strip()}` because it is the strongest structural consumer")
        elif implementation_hits:
            best_hit = implementation_hits[0]
            lines.append("")
            lines.append("Inspect First")
            lines.append(
                f"- start with `{best_hit['file_path']}` because it is the strongest implementation-side consumer evidence"
            )
        elif supporting_hits:
            best_hit = supporting_hits[0]
            lines.append("")
            lines.append("Inspect First")
            lines.append(
                f"- start with `{best_hit['file_path']}` because only test/support references were found in the target project"
            )

    if graph_usages:
        lines.append(f"**Call-graph hits** ({len(graph_usages)}):")
        lines.extend(graph_usages)
    else:
        lines.append("  No direct call-graph edges found.")

    if implementation_hits:
        lines += ["", f"**Implementation / consumer text hits** ({len(implementation_hits)}):"]
        lines.extend(_render_semantic_hit(record) for record in implementation_hits)
    elif supporting_hits:
        lines += ["", "No implementation-side consumer text hits found."]

    if supporting_hits:
        lines += ["", f"**Test / supporting text hits** ({len(supporting_hits)}):"]
        lines.extend(_render_semantic_hit(record) for record in supporting_hits)
    else:
        if not implementation_hits:
            lines.append("  No semantic text hits found.")

    if not graph_usages and not sem_usage_records:
        lines += [
            "",
            f"`{symbol_name}` appears to be defined in [{src_name}] but not yet referenced in [{tgt_name}].",
            f"   Try `search_codebase` in [{tgt_name}] with a broader semantic query.",
        ]

    return "\n".join(lines)
