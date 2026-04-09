"""Summary implementations for graph-backed search tools."""

from __future__ import annotations

import fnmatch
import os

from _helpers import get_project_id
from tools.brain.graph import core as graph_tools


def _is_test_like_path(file_path: str) -> bool:
    normalized = (file_path or "").lower()
    return (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or "__tests__" in normalized
        or "/spec/" in normalized
        or normalized.startswith("spec/")
        or ".test." in normalized
        or normalized.endswith("_test.py")
        or normalized.endswith("_test.rs")
        or normalized.endswith("_test.go")
        or normalized.endswith("_spec.rb")
    )


async def get_symbol_imports_overview_impl(
    *,
    driver,
    neo4j_db: str,
    project_path: str,
    limit: int = 20,
    include_implicit: bool = False,
) -> str:
    project_id = get_project_id(project_path)
    limit = max(1, min(int(limit), 100))

    async with driver.session(database=neo4j_db) as session:
        r_exp_count = await graph_tools._execute_read(
            session,
            "MATCH (:File {project_id:$p})-[r:IMPORTS_SYMBOL]->() RETURN count(r) AS n",
            p=project_id,
            op="get_symbol_imports_overview_exp_count",
        )
        exp_count = r_exp_count[0]["n"] if r_exp_count else 0

        imp_count = 0
        if include_implicit:
            r_imp_count = await graph_tools._execute_read(
                session,
                "MATCH (:File {project_id:$p})-[r:IMPLICIT_IMPORTS_SYMBOL]->() RETURN count(r) AS n",
                p=project_id,
                op="get_symbol_imports_overview_imp_count",
            )
            imp_count = r_imp_count[0]["n"] if r_imp_count else 0

        r_exp_symbols = await graph_tools._execute_read(
            session,
            """
            MATCH (f:File {project_id:$p})-[:IMPORTS_SYMBOL]->(s)
            RETURN s.name AS symbol, count(*) AS n
            ORDER BY n DESC
            LIMIT $limit
            """,
            p=project_id,
            limit=limit,
            op="get_symbol_imports_overview_exp_symbols",
        )
        exp_symbols = [(rec["symbol"], rec["n"]) for rec in r_exp_symbols]

        r_exp_files = await graph_tools._execute_read(
            session,
            """
            MATCH (f:File {project_id:$p})-[:IMPORTS_SYMBOL]->(s)
            WITH f.filepath AS file, count(*) AS n, collect(DISTINCT s.name) AS symbols
            ORDER BY n DESC
            LIMIT $limit
            RETURN file, n, symbols
            """,
            p=project_id,
            limit=limit,
            op="get_symbol_imports_overview_exp_files",
        )
        exp_files = [(rec["file"], rec["n"], rec["symbols"]) for rec in r_exp_files]

        imp_symbols = []
        imp_files = []
        if include_implicit:
            r_imp_symbols = await graph_tools._execute_read(
                session,
                """
                MATCH (f:File {project_id:$p})-[:IMPLICIT_IMPORTS_SYMBOL]->(s)
                WHERE f.filepath ENDS WITH '.swift'
                RETURN s.name AS symbol, count(*) AS n
                ORDER BY n DESC
                LIMIT $limit
                """,
                p=project_id,
                limit=limit,
                op="get_symbol_imports_overview_imp_symbols",
            )
            imp_symbols = [(rec["symbol"], rec["n"]) for rec in r_imp_symbols]

            r_imp_files = await graph_tools._execute_read(
                session,
                """
                MATCH (f:File {project_id:$p})-[:IMPLICIT_IMPORTS_SYMBOL]->(s)
                WHERE f.filepath ENDS WITH '.swift'
                WITH f.filepath AS file, count(*) AS n, collect(DISTINCT s.name) AS symbols
                ORDER BY n DESC
                LIMIT $limit
                RETURN file, n, symbols
                """,
                p=project_id,
                limit=limit,
                op="get_symbol_imports_overview_imp_files",
            )
            imp_files = [(rec["file"], rec["n"], rec["symbols"]) for rec in r_imp_files]

    if not exp_symbols and not exp_files and not imp_symbols and not imp_files:
        return "No symbol import edges found."

    lines = [f"# Symbol import overview: {project_path.split('/')[-1]}", ""]
    lines.append("## Counts")
    lines.append(f"IMPORTS_SYMBOL: {exp_count}")
    if include_implicit:
        lines.append(f"IMPLICIT_IMPORTS_SYMBOL: {imp_count}")

    if exp_symbols:
        lines.append("")
        lines.append("## Top explicit imported symbols")
        for name, count in exp_symbols:
            lines.append(f"- {name}  ({count})")
    if exp_files:
        lines.append("")
        lines.append("## Files with most explicit symbol imports")
        for file, count, symbols in exp_files[:limit]:
            sample = ", ".join(symbols[:6])
            lines.append(f"- {file}  ({count})  [{sample}]")

    if include_implicit and imp_symbols:
        lines.append("")
        lines.append("## Top implicit imported symbols")
        for name, count in imp_symbols:
            lines.append(f"- {name}  ({count})")
    if include_implicit and imp_files:
        lines.append("")
        lines.append("## Files with most implicit symbol imports")
        for file, count, symbols in imp_files[:limit]:
            sample = ", ".join(symbols[:6])
            lines.append(f"- {file}  ({count})  [{sample}]")

    return "\n".join(lines)


async def get_symbol_exports_summary_impl(
    *,
    driver,
    neo4j_db: str,
    project_path: str,
    limit: int = 20,
    include_paths: list | None = None,
    exclude_paths: list | None = None,
    symbol_prefix: str = "",
) -> str:
    project_id = get_project_id(project_path)
    limit = max(1, min(int(limit), 100))

    def _path_allowed(file_path: str) -> bool:
        if include_paths and not any(fnmatch.fnmatch(file_path, pat) for pat in include_paths):
            return False
        if exclude_paths and any(fnmatch.fnmatch(file_path, pat) for pat in exclude_paths):
            return False
        if not include_paths and _is_test_like_path(file_path):
            return False
        return True

    def _symbol_allowed(name: str) -> bool:
        if not isinstance(name, str) or not name:
            return False
        if symbol_prefix and not name.startswith(symbol_prefix):
            return False
        return True

    def _heuristic_visibility_ok(file_path: str, visibility: str | None, name: str) -> bool:
        visibility_norm = (visibility or "").strip().lower()
        if visibility_norm in {"public", "open", "pub"} or visibility_norm.startswith("pub("):
            return True

        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".py":
            return not name.startswith("_")
        return False

    export_mode = "graph"
    export_edges = 0

    async with driver.session(database=neo4j_db) as session:
        count_rows = await graph_tools._execute_read(
            session,
            "MATCH (:File {project_id:$p})-[r:EXPORTS_SYMBOL]->() RETURN count(r) AS n",
            p=project_id,
            op="get_symbol_exports_summary_count",
        )
        export_edges = count_rows[0]["n"] if count_rows else 0

        r1 = await graph_tools._execute_read(
            session,
            """
            MATCH (f:File {project_id:$p})-[:EXPORTS_SYMBOL]->(s)
            RETURN s.name AS symbol, count(*) AS n
            ORDER BY n DESC
            LIMIT $limit
            """,
            p=project_id,
            limit=limit,
            op="get_symbol_exports_summary_symbols",
        )
        top_symbols = []
        for rec in r1:
            name = rec["symbol"]
            if not _symbol_allowed(name):
                continue
            top_symbols.append((name, rec["n"]))

        fetch_limit = min(limit * 10, 200) if (include_paths or exclude_paths or symbol_prefix) else limit
        r2 = await graph_tools._execute_read(
            session,
            """
            MATCH (f:File {project_id:$p})-[:EXPORTS_SYMBOL]->(s)
            WITH f.filepath AS file, count(*) AS n, collect(DISTINCT s.name) AS symbols
            ORDER BY n DESC
            LIMIT $limit
            RETURN file, n, symbols
            """,
            p=project_id,
            limit=fetch_limit,
            op="get_symbol_exports_summary_files",
        )
        top_files = []
        for rec in r2:
            file = rec["file"]
            if not _path_allowed(file):
                continue
            symbols = rec["symbols"]
            if symbol_prefix:
                symbols = [s for s in symbols if isinstance(s, str) and s.startswith(symbol_prefix)]
                if not symbols:
                    continue
            top_files.append((file, rec["n"], symbols))

        if not top_symbols and not top_files:
            export_mode = "heuristic"
            heuristic_limit = min(limit * 10, 400)
            r3 = await graph_tools._execute_read(
                session,
                """
                MATCH (f:File {project_id:$p})-[:CONTAINS]->(s)
                WHERE s.name IS NOT NULL
                  AND (
                    s:Function OR s:Method OR s:Class OR s:Struct OR s:Trait
                    OR s:Enum OR s:Protocol OR s:Extension OR s:TypeAlias OR s:AssociatedType
                  )
                RETURN f.filepath AS file, s.name AS symbol, coalesce(s.visibility, '') AS visibility
                ORDER BY f.filepath, s.name
                LIMIT $limit
                """,
                p=project_id,
                limit=heuristic_limit,
                op="get_symbol_exports_summary_heuristic",
            )

            file_symbols: dict[str, list[str]] = {}
            symbol_counts: dict[str, int] = {}
            for rec in r3:
                file = rec["file"]
                name = rec["symbol"]
                visibility = rec.get("visibility")
                if not file or not _path_allowed(file) or not _symbol_allowed(name):
                    continue
                if not _heuristic_visibility_ok(file, visibility, name):
                    continue
                file_symbols.setdefault(file, [])
                if name not in file_symbols[file]:
                    file_symbols[file].append(name)
                symbol_counts[name] = symbol_counts.get(name, 0) + 1

            top_symbols = sorted(
                symbol_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:limit]
            top_files = sorted(
                (
                    (file, len(symbols), symbols)
                    for file, symbols in file_symbols.items()
                    if symbols
                ),
                key=lambda item: (-item[1], item[0]),
            )[:limit]

    if not top_symbols and not top_files:
        return (
            "No symbol exports found.\n"
            "Checked EXPORTS_SYMBOL edges, then visibility/name-based public-surface heuristics."
        )

    lines = [f"# Symbol export summary: {project_path.split('/')[-1]}", ""]
    if export_mode == "graph":
        lines.append(f"Source: EXPORTS_SYMBOL edges ({export_edges})")
    else:
        lines.append(
            "Source: heuristic public-surface inference "
            "(visibility metadata, or Python non-underscore naming when visibility is absent)"
        )
    lines.append("")
    if top_symbols:
        lines.append("## Top exported symbols")
        for name, count in top_symbols:
            lines.append(f"- {name}  ({count})")
        lines.append("")
    if top_files:
        lines.append("## Files with most symbol exports")
        for file, count, symbols in top_files:
            sample = ", ".join(symbols[:6])
            lines.append(f"- {file}  ({count})  [{sample}]")
    return "\n".join(lines)
