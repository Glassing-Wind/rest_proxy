"""tools/graph/cli.py — CLI/entrypoint flow summary helpers."""

from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import Awaitable, Callable
import re

import graph_bootstrap
from _helpers import get_project_id


ExecuteRead = Callable[..., Awaitable[list[dict[str, object]]]]


async def get_cli_flow_summary(
    project_path: str,
    execute_read: ExecuteRead,
    include_tests: bool = False,
    limit: int = 20,
    as_table: bool = False,
) -> str:
    """Build a CLI/entrypoint-oriented flow summary for non-web repos."""
    try:
        project_id = get_project_id(project_path)
        driver = await graph_bootstrap.require_driver()

        def _path_filters() -> list[str]:
            return [
                "Apps/%",
                "Tools/%",
                "Scripts/%",
                "src/%",
                "Sources/%",
                "",
            ]

        entry_points: list[tuple[str, str]] = []
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            for pattern in _path_filters():
                rows = await execute_read(
                    session,
                    """
                    MATCH (f:File {project_id:$pid})
                    WHERE f.filepath STARTS WITH $prefix
                      AND (f.filepath ENDS WITH 'main.swift'
                        OR f.filepath ENDS WITH 'main.rs'
                        OR f.filepath ENDS WITH 'main.py'
                        OR f.filepath ENDS WITH 'main.go'
                        OR f.filepath ENDS WITH 'main.js'
                        OR f.filepath ENDS WITH 'main.ts'
                        OR f.filepath ENDS WITH 'server.ts'
                        OR f.filepath ENDS WITH 'server.js'
                        OR f.filepath ENDS WITH 'app.ts'
                        OR f.filepath ENDS WITH 'app.js'
                        OR f.filepath ENDS WITH 'App.swift'
                        OR f.filepath ENDS WITH 'AppDelegate.swift'
                        OR f.filepath ENDS WITH 'SceneDelegate.swift'
                        OR f.filepath ENDS WITH 'mcp_server.py'
                        OR f.filepath ENDS WITH 'proxy.py'
                        OR f.filepath CONTAINS 'CLI')
                    RETURN f.filepath AS fp
                    """,
                    pid=project_id,
                    prefix=pattern.rstrip("%"),
                    op="flow_cli_entry_files",
                )
                for rec in rows:
                    entry_points.append((rec["fp"], "file"))

            # SwiftUI @main/App entrypoints (repo-wide)
            swift_rows = await execute_read(
                session,
                """
                MATCH (f:File {project_id:$pid})-[:CONTAINS]->(s)
                WHERE f.filepath ENDS WITH '.swift'
                  AND (s:Struct OR s:Class)
                  AND s.name CONTAINS 'App'
                RETURN DISTINCT f.filepath AS fp
                """,
                pid=project_id,
                op="flow_cli_swiftui_main",
            )
            for rec in swift_rows:
                entry_points.append((rec["fp"], "swiftui"))

            # Filename heuristic for SwiftUI apps
            swift_file_rows = await execute_read(
                session,
                """
                MATCH (f:File {project_id:$pid})
                WHERE f.filepath ENDS WITH 'App.swift'
                RETURN f.filepath AS fp
                """,
                pid=project_id,
                op="flow_cli_swiftui_files",
            )
            for rec in swift_file_rows:
                entry_points.append((rec["fp"], "swiftui_file"))

            # Rust bin targets via Cargo.toml
            cargo_rows = await execute_read(
                session,
                """
                MATCH (f:File {project_id:$pid})
                WHERE f.filepath ENDS WITH 'Cargo.toml'
                RETURN f.filepath AS fp
                """,
                pid=project_id,
                op="flow_cli_cargo",
            )
            cargo_files = [rec["fp"] for rec in cargo_rows]

        # Expand Cargo.toml bins with a light parse
        for cargo_fp in cargo_files:
            abs_path = os.path.join(project_path, cargo_fp)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
                bins = re.findall(
                    r"\[\[bin\]\][^\[]*?name\s*=\s*\"([^\"]+)\"", text, re.S
                )
                for name in bins:
                    entry_points.append((f"src/bin/{name}.rs", "cargo"))
            except Exception:
                continue

        code_exts = {".swift", ".rs", ".py", ".go", ".js", ".ts", ".sh"}
        entry_points = [
            (fp, kind)
            for fp, kind in entry_points
            if PurePosixPath(fp).suffix in code_exts or not PurePosixPath(fp).suffix
        ]

        if not include_tests:
            entry_points = [
                (fp, kind) for fp, kind in entry_points if "test" not in fp.lower()
            ]

        # Deduplicate
        entry_points = list(dict.fromkeys(entry_points))
        if not entry_points:
            return "No CLI/entrypoint paths found."

        rows: list[tuple[str, str | None]] = []
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            for fp, _kind in entry_points:
                # Find neighboring files via imports (cap per entry to keep variety)
                import_rows = await execute_read(
                    session,
                    """
                    MATCH (f:File {project_id:$pid, filepath:$fp})-[:IMPORTS]->(t:File)
                    RETURN t.filepath AS target
                    """,
                    pid=project_id,
                    fp=fp,
                    op="flow_cli_imports",
                )
                targets = [r["target"] for r in import_rows][:5]
                if not targets:
                    rows.append((fp, None))
                else:
                    for target in targets:
                        rows.append((fp, target))

        if as_table:
            output = ["| Entry | Imports |", "| --- | --- |"]
            for entry, target in rows[:limit]:
                output.append(f"| {entry} | {target or ''} |")
        else:
            output = [
                " -> ".join([v for v in [entry, target] if v]) for entry, target in rows
            ]

        output = list(dict.fromkeys(output))
        if limit and len(output) > limit:
            output = output[:limit]
        return "\n".join(output)
    except Exception as exc:
        return f"Error building CLI flow summary: {str(exc)}"
