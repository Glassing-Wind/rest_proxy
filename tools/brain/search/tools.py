"""tools/search/tools.py — graph-backed search summaries."""

from mcp.server.fastmcp import FastMCP

from tools.brain.graph import core as graph_tools
from _helpers import get_project_id


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_symbol_imports_summary(project_path: str, limit: int = 20) -> str:
        """
        Summarize symbol-level import edges (IMPORTS_SYMBOL) for a project.

        Args:
            project_path: Absolute path to the project root.
            limit: Max rows to return per section (default 20).
        """
        try:
            project_id = get_project_id(project_path)
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            limit = max(1, min(int(limit), 100))

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                r1 = await graph_tools._execute_read(
                    session,
                    """
                    MATCH (f:File {project_id:$p})-[:IMPORTS_SYMBOL]->(s)
                    RETURN s.name AS symbol, count(*) AS n
                    ORDER BY n DESC
                    LIMIT $limit
                    """,
                    p=project_id,
                    limit=limit,
                    op="get_symbol_imports_summary_symbols",
                )
                top_symbols = []
                for rec in r1:
                    top_symbols.append((rec["symbol"], rec["n"]))

                r2 = await graph_tools._execute_read(
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
                    op="get_symbol_imports_summary_files",
                )
                top_files = []
                for rec in r2:
                    top_files.append((rec["file"], rec["n"], rec["symbols"]))

            if not top_symbols and not top_files:
                return "No IMPORTS_SYMBOL edges found."

            lines = [f"# Symbol import summary: {project_path.split('/')[-1]}", ""]
            if top_symbols:
                lines.append("## Top imported symbols")
                for name, n in top_symbols:
                    lines.append(f"- {name}  ({n})")
                lines.append("")
            if top_files:
                lines.append("## Files with most symbol imports")
                for file, n, symbols in top_files[:limit]:
                    sample = ", ".join(symbols[:6])
                    lines.append(f"- {file}  ({n})  [{sample}]")
            return "\n".join(lines)
        except Exception as e:
            return f"Error summarizing symbol imports: {str(e)}"

    @mcp.tool()
    async def get_symbol_imports_overview(
        project_path: str, limit: int = 20, include_implicit: bool = True
    ) -> str:
        """
        Summarize explicit (IMPORTS_SYMBOL) and implicit (IMPLICIT_IMPORTS_SYMBOL)
        symbol import edges for a project.

        Args:
            project_path: Absolute path to the project root.
            limit: Max rows to return per section (default 20).
            include_implicit: Include IMPLICIT_IMPORTS_SYMBOL edges when true.
        """
        try:
            project_id = get_project_id(project_path)
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            limit = max(1, min(int(limit), 100))

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
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
                exp_files = [
                    (rec["file"], rec["n"], rec["symbols"]) for rec in r_exp_files
                ]

                imp_symbols = []
                imp_files = []
                if include_implicit:
                    r_imp_symbols = await graph_tools._execute_read(
                        session,
                        """
                        MATCH (f:File {project_id:$p})-[:IMPLICIT_IMPORTS_SYMBOL]->(s)
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
                        WITH f.filepath AS file, count(*) AS n, collect(DISTINCT s.name) AS symbols
                        ORDER BY n DESC
                        LIMIT $limit
                        RETURN file, n, symbols
                        """,
                        p=project_id,
                        limit=limit,
                        op="get_symbol_imports_overview_imp_files",
                    )
                    imp_files = [
                        (rec["file"], rec["n"], rec["symbols"]) for rec in r_imp_files
                    ]

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
                for name, n in exp_symbols:
                    lines.append(f"- {name}  ({n})")
            if exp_files:
                lines.append("")
                lines.append("## Files with most explicit symbol imports")
                for file, n, symbols in exp_files[:limit]:
                    sample = ", ".join(symbols[:6])
                    lines.append(f"- {file}  ({n})  [{sample}]")

            if include_implicit and imp_symbols:
                lines.append("")
                lines.append("## Top implicit imported symbols")
                for name, n in imp_symbols:
                    lines.append(f"- {name}  ({n})")
            if include_implicit and imp_files:
                lines.append("")
                lines.append("## Files with most implicit symbol imports")
                for file, n, symbols in imp_files[:limit]:
                    sample = ", ".join(symbols[:6])
                    lines.append(f"- {file}  ({n})  [{sample}]")

            return "\n".join(lines)
        except Exception as e:
            return f"Error summarizing symbol imports overview: {str(e)}"

    @mcp.tool()
    async def get_symbol_exports_summary(
        project_path: str,
        limit: int = 20,
        include_paths: list | None = None,
        exclude_paths: list | None = None,
        symbol_prefix: str = "",
    ) -> str:
        """
        Summarize symbol-level export edges (EXPORTS_SYMBOL) for a project.

        Args:
            project_path: Absolute path to the project root.
            limit: Max rows to return per section (default 20).
            include_paths: Optional list of glob patterns to include (file_path).
            exclude_paths: Optional list of glob patterns to exclude (file_path).
            symbol_prefix: Optional prefix to filter exported symbols.
        """
        try:
            project_id = get_project_id(project_path)
            import graph_bootstrap
            import fnmatch

            driver = await graph_bootstrap.require_driver()

            limit = max(1, min(int(limit), 100))

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
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
                    if (
                        symbol_prefix
                        and isinstance(name, str)
                        and not name.startswith(symbol_prefix)
                    ):
                        continue
                    top_symbols.append((name, rec["n"]))

                fetch_limit = limit
                if include_paths or exclude_paths or symbol_prefix:
                    fetch_limit = min(limit * 10, 200)

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
                    if include_paths:
                        if not any(fnmatch.fnmatch(file, pat) for pat in include_paths):
                            continue
                    if exclude_paths:
                        if any(fnmatch.fnmatch(file, pat) for pat in exclude_paths):
                            continue
                    symbols = rec["symbols"]
                    if symbol_prefix:
                        symbols = [
                            s
                            for s in symbols
                            if isinstance(s, str) and s.startswith(symbol_prefix)
                        ]
                        if not symbols:
                            continue
                    top_files.append((file, rec["n"], symbols))

            if not top_symbols and not top_files:
                return "No EXPORTS_SYMBOL edges found."

            lines = [f"# Symbol export summary: {project_path.split('/')[-1]}", ""]
            if top_symbols:
                lines.append("## Top exported symbols")
                for name, n in top_symbols:
                    lines.append(f"- {name}  ({n})")
                lines.append("")
            if top_files:
                lines.append("## Files with most symbol exports")
                for file, n, symbols in top_files:
                    sample = ", ".join(symbols[:6])
                    lines.append(f"- {file}  ({n})  [{sample}]")
            return "\n".join(lines)
        except Exception as e:
            return f"Error summarizing symbol exports: {str(e)}"
