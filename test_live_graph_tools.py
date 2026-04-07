#!/usr/bin/env python3
"""Live graph smoke test against a real indexed workspace.

Usage:
    python3 test_live_graph_tools.py /abs/path/to/project [/abs/path/to/project ...]
    python3 test_live_graph_tools.py /abs/path/to/project --reindex --mode incremental
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
import types


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _install_mcp_stub() -> None:
    if "mcp.server.fastmcp" in sys.modules:
        return
    mcp_pkg = types.ModuleType("mcp")
    server_pkg = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")
    fastmcp_mod.FastMCP = FakeMCP
    sys.modules["mcp"] = mcp_pkg
    sys.modules["mcp.server"] = server_pkg
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod


@dataclass
class ToolRun:
    name: str
    output: str


def _build_tool_registry() -> FakeMCP:
    _install_mcp_stub()
    from tools.brain.code_intel import core as code_intel_core
    from tools.brain.graph import tools as graph_tools
    from tools.brain.search import graph_query as graph_query_tools

    mcp = FakeMCP()
    code_intel_core.register(mcp)
    graph_tools.register(mcp)
    graph_query_tools.register(mcp)
    return mcp


def _extract_job_id(index_output: str) -> str:
    match = re.search(r"job_id:\s*([a-z0-9]+)", index_output)
    if not match:
        raise RuntimeError(f"Could not parse job_id from index output:\n{index_output}")
    return match.group(1)


async def _wait_for_index(job_id: str, timeout_s: float = 1800.0) -> None:
    _install_mcp_stub()
    from tools.hands import indexing as hands_indexing

    started = time.time()
    while True:
        status = await hands_indexing.get_index_status(job_id)
        if "FAILED" in status:
            raise RuntimeError(f"Index job failed:\n{status}")
        if "Job " in status and ("DONE" in status or "CANCELLED" in status):
            return
        if time.time() - started > timeout_s:
            raise TimeoutError(f"Timed out waiting for index job {job_id}:\n{status}")
        await asyncio.sleep(2.0)


async def _ensure_indexed(workspace_id: str, mode: str) -> str:
    _install_mcp_stub()
    from tools.hands import indexing as hands_indexing

    result = await hands_indexing.index_workspace(workspace_id, mode=mode)
    if result.startswith("Error starting indexing:"):
        raise RuntimeError(result)
    if result.startswith("Cleanup complete"):
        return result
    job_id = _extract_job_id(result)
    await _wait_for_index(job_id)
    return result


async def _pick_live_symbol(mcp: FakeMCP, workspace_id: str) -> tuple[str, str]:
    raw = await mcp.tools["query_graph"](
        """
        MATCH (n)
        WHERE n.project_id = $pid
          AND (n:Function OR n:Method OR n:Class OR n:Struct OR n:Trait OR n:Enum)
          AND n.name IS NOT NULL
          AND trim(n.name) <> ''
          AND n.name <> 'unnamed'
        WITH n.name AS name, count(*) AS occurrences
        WHERE occurrences = 1
        MATCH (picked)
        WHERE picked.project_id = $pid
          AND (picked:Function OR picked:Method OR picked:Class OR picked:Struct OR picked:Trait OR picked:Enum)
          AND picked.name = name
        OPTIONAL MATCH (picked)-[:CALLS]->(callee)
        WITH picked, count(callee) AS outbound
        ORDER BY outbound DESC, coalesce(picked.pagerank, 0.0) DESC, picked.name ASC
        RETURN picked.name AS name, picked.filepath AS filepath, outbound
        LIMIT 1
        """,
        workspace_id=workspace_id,
    )
    rows = json.loads(raw)
    if not rows:
        raise RuntimeError(f"No live symbols found for workspace: {workspace_id}")
    row = rows[0]
    return row["name"], row.get("filepath") or ""


async def _count_apple_files(mcp: FakeMCP, workspace_id: str) -> int:
    raw = await mcp.tools["query_graph"](
        """
        MATCH (f:File {project_id: $pid})
        WHERE f.filepath ENDS WITH '.xcodeproj/project.pbxproj'
           OR f.filepath ENDS WITH '.xcworkspace/contents.xcworkspacedata'
           OR f.filepath ENDS WITH '.xcscheme'
           OR f.filepath ENDS WITH '.storyboard'
           OR f.filepath ENDS WITH '.xib'
           OR f.filepath CONTAINS '.xcassets/'
        RETURN count(f) AS apple_file_count
        """,
        workspace_id=workspace_id,
    )
    rows = json.loads(raw)
    if not rows:
        return 0
    return int(rows[0].get("apple_file_count") or 0)


async def _count_cargo_files(mcp: FakeMCP, workspace_id: str) -> int:
    raw = await mcp.tools["query_graph"](
        """
        MATCH (f:File {project_id: $pid})
        WHERE f.filepath ENDS WITH 'Cargo.toml'
        RETURN count(f) AS cargo_file_count
        """,
        workspace_id=workspace_id,
    )
    rows = json.loads(raw)
    if not rows:
        return 0
    return int(rows[0].get("cargo_file_count") or 0)


def _require_non_error(name: str, output: str) -> None:
    if not output or output.startswith("Error "):
        raise RuntimeError(f"{name} failed:\n{output}")


async def _run_live_checks(workspace_id: str) -> list[ToolRun]:
    mcp = _build_tool_registry()

    resolve_output = await mcp.tools["resolve_graph_project"](workspace_id)
    _require_non_error("resolve_graph_project", resolve_output)

    overview_output = await mcp.tools["get_project_overview"](workspace_id)
    _require_non_error("get_project_overview", overview_output)

    symbol_name, symbol_file = await _pick_live_symbol(mcp, workspace_id)

    symbol_output = await mcp.tools["get_symbol_context"](
        workspace_id, symbol_name, include_source_preview=False
    )
    _require_non_error("get_symbol_context", symbol_output)
    if symbol_name not in symbol_output:
        raise RuntimeError(
            f"get_symbol_context did not mention selected symbol '{symbol_name}'."
        )
    if symbol_file and symbol_file not in symbol_output:
        raise RuntimeError(
            f"get_symbol_context resolved '{symbol_name}' to an unexpected file.\n"
            f"Expected file fragment: {symbol_file}\nOutput:\n{symbol_output}"
        )

    call_chain_output = await mcp.tools["get_call_chain"](
        workspace_id, symbol_name, depth=2, direction="down", file_path=symbol_file or None
    )
    _require_non_error("get_call_chain", call_chain_output)

    raw_query_output = await mcp.tools["query_graph"](
        """
        MATCH (f:File {project_id: $pid})
        RETURN count(f) AS file_count
        """,
        workspace_id=workspace_id,
    )
    _require_non_error("query_graph", raw_query_output)

    cargo_file_count = await _count_cargo_files(mcp, workspace_id)
    cargo_runs: list[ToolRun] = [
        ToolRun(
            "cargo_presence",
            json.dumps({"cargo_file_count": cargo_file_count}, indent=2),
        )
    ]
    if cargo_file_count > 0:
        if "Cargo Workspace Context" not in overview_output:
            raise RuntimeError(
                "Cargo repo appears indexed but project overview did not include Cargo Workspace Context."
            )

        code_importance_output = await mcp.tools["get_code_importance"](workspace_id)
        _require_non_error("get_code_importance", code_importance_output)
        if "Crate:" not in code_importance_output:
            raise RuntimeError(
                f"Cargo repo appears indexed but code importance did not include crate grouping:\n{code_importance_output}"
            )

        related_output = await mcp.tools["get_related_files"](workspace_id, symbol_file or "")
        _require_non_error("get_related_files", related_output)
        cargo_runs.extend(
            [
                ToolRun("get_code_importance", code_importance_output),
                ToolRun("get_related_files", related_output),
            ]
        )

    apple_file_count = await _count_apple_files(mcp, workspace_id)
    apple_runs: list[ToolRun] = [
        ToolRun(
            "apple_presence",
            json.dumps({"apple_file_count": apple_file_count}, indent=2),
        )
    ]
    if apple_file_count > 0:
        apple_summary_output = await mcp.tools["get_flow_summary"](
            workspace_id,
            mode="apple",
            limit=10,
        )
        _require_non_error("get_flow_summary(mode=apple)", apple_summary_output)
        if apple_summary_output.startswith("No Apple build graph paths found."):
            raise RuntimeError(
                f"Apple repo appears indexed but Apple flow summary returned no paths:\n{apple_summary_output}"
            )
        apple_runs.append(ToolRun("get_flow_summary(mode=apple)", apple_summary_output))

    return [
        ToolRun("resolve_graph_project", resolve_output),
        ToolRun("get_project_overview", overview_output),
        ToolRun(
            "selected_symbol",
            json.dumps({"name": symbol_name, "filepath": symbol_file}, indent=2),
        ),
        ToolRun("get_symbol_context", symbol_output),
        ToolRun("get_call_chain", call_chain_output),
        ToolRun("query_graph", raw_query_output),
        *cargo_runs,
        *apple_runs,
    ]


async def _main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "workspace_ids",
        nargs="+",
        help="Logical workspace IDs or absolute project paths",
    )
    parser.add_argument("--reindex", action="store_true", help="Run index_workspace before checks")
    parser.add_argument(
        "--mode",
        default="incremental",
        choices=["incremental", "rebuild", "cleanup"],
        help="Index mode to use when --reindex is set",
    )
    args = parser.parse_args()

    if not args.workspace_ids:
        raise RuntimeError("workspace_id is required")

    failures: list[tuple[str, str]] = []
    for workspace_id in args.workspace_ids:
        print(f"[live-graph] workspace={workspace_id}")
        try:
            if args.reindex:
                print(f"[live-graph] reindex mode={args.mode}")
                result = await _ensure_indexed(workspace_id, args.mode)
                print(result)

            runs = await _run_live_checks(workspace_id)
            for run in runs:
                print(f"\n=== {run.name} ===")
                print(run.output.strip())
        except Exception as exc:
            failures.append((workspace_id, str(exc)))
            print(f"\n[live-graph] FAILED: {workspace_id}")
            print(str(exc).strip())
        print()

    if failures:
        print("[live-graph] failures:")
        for workspace_id, error in failures:
            print(f"- {workspace_id}: {error.splitlines()[0]}")
        return 1

    print("\n[live-graph] smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
