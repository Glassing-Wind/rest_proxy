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
GRAPH_GOLDENS_PATH = os.path.join(REPO_ROOT, "benchmarks", "live_graph_goldens.json")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from _runtime import resolve_python_runtime  # noqa: E402


def _ensure_runtime_dependencies() -> None:
    try:
        import neo4j  # noqa: F401
    except ModuleNotFoundError:
        if os.environ.get("LM_PROXY_RUNTIME_REEXECED") == "1":
            raise
        runtime = resolve_python_runtime()
        preferred = str(runtime.get("python") or "")
        if not preferred or os.path.realpath(preferred) == os.path.realpath(sys.executable):
            raise
        os.environ["LM_PROXY_RUNTIME_REEXECED"] = "1"
        os.execv(preferred, [preferred, __file__, *sys.argv[1:]])


_ensure_runtime_dependencies()


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
    fastmcp_mod.Context = type("Context", (), {})
    sys.modules["mcp"] = mcp_pkg
    sys.modules["mcp.server"] = server_pkg
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod


@dataclass
class ToolRun:
    name: str
    output: str


@dataclass
class LiveGraphOptions:
    case_ids: set[str]
    regressions_only: bool
    fail_fast: bool
    verbose_progress: bool


def _workspace_basename(workspace_id: str) -> str:
    return os.path.basename(os.path.abspath(workspace_id.rstrip("/")))


def _resolve_golden_params(value, workspace_id: str):
    if value == "$workspace_id":
        return workspace_id
    if isinstance(value, list):
        return [_resolve_golden_params(item, workspace_id) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_golden_params(item, workspace_id) for key, item in value.items()}
    return value


async def _invoke_tool(mcp: FakeMCP, tool_name: str, workspace_id: str, params: dict) -> str:
    if tool_name in {"get_symbol_context", "get_call_chain"}:
        return await mcp.tools[tool_name](workspace_id, **params)
    return await mcp.tools[tool_name](**params)


def _validate_output(case_id: str, label: str, output: str, case: dict) -> None:
    _require_non_error(label, output)
    for expected in case.get("required_substrings") or []:
        if expected not in output:
            raise RuntimeError(
                f"Graph golden regression '{case_id}': expected '{expected}' in {label} output."
            )
    for forbidden in case.get("forbidden_substrings") or []:
        if forbidden in output:
            raise RuntimeError(
                f"Graph golden regression '{case_id}': unexpected '{forbidden}' in {label} output."
            )


def _build_tool_registry() -> FakeMCP:
    _install_mcp_stub()
    from tools.brain.code_intel import core as code_intel_core
    from tools.brain.graph import tools as graph_tools
    from tools.brain.search import graph_query as graph_query_tools
    from tools.brain.search import semantic as semantic_tools
    from tools.brain.search import tools as search_tools
    from tools.hands import dev as dev_tools

    mcp = FakeMCP()
    code_intel_core.register(mcp)
    graph_tools.register(mcp)
    graph_query_tools.register(mcp, include_admin=True)
    semantic_tools.register(mcp)
    search_tools.register(mcp)
    dev_tools.register(mcp)
    return mcp


def _assert_health_healthy(workspace_id: str, output: str) -> None:
    if not output or output.startswith("Error "):
        raise RuntimeError(f"get_indexing_health failed:\n{output}")
    if "**Sync Status**: ❌ Out of Sync" in output:
        raise RuntimeError(
            f"Index health is out of sync for {workspace_id}.\n{output}"
        )
    if "**Run Alignment**:        ⚠️ Not aligned" in output:
        raise RuntimeError(
            f"Index health is not aligned for {workspace_id}.\n{output}"
        )


async def _run_health_check(workspace_id: str) -> ToolRun:
    _install_mcp_stub()
    from tools.hands import indexing as hands_indexing

    output = await hands_indexing.get_indexing_health(workspace_id)
    _assert_health_healthy(workspace_id, output)
    return ToolRun("get_indexing_health", output)


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


async def _pick_live_type_symbol(mcp: FakeMCP, workspace_id: str) -> tuple[str, str] | None:
    raw = await mcp.tools["query_graph"](
        """
        MATCH (n)
        WHERE n.project_id = $pid
          AND (
                n:TypeAlias OR n:AssociatedType OR n:Interface OR n:Protocol
                OR n:Struct OR n:Class OR n:Enum OR n:Extension
              )
          AND n.name IS NOT NULL
          AND trim(n.name) <> ''
          AND n.name <> 'unnamed'
        WITH n.name AS name, count(*) AS occurrences
        WHERE occurrences = 1
        MATCH (picked)
        WHERE picked.project_id = $pid
          AND picked.name = name
          AND (
                picked:TypeAlias OR picked:AssociatedType OR picked:Interface OR picked:Protocol
                OR picked:Struct OR picked:Class OR picked:Enum OR picked:Extension
              )
        RETURN picked.name AS name, picked.filepath AS filepath,
               CASE
                 WHEN picked:TypeAlias THEN 0
                 WHEN picked:AssociatedType THEN 1
                 WHEN picked:Interface THEN 2
                 WHEN picked:Protocol THEN 3
                 WHEN picked:Struct THEN 4
                 WHEN picked:Class THEN 5
                 WHEN picked:Enum THEN 6
                 WHEN picked:Extension THEN 7
                 ELSE 99
               END AS rank
        ORDER BY rank ASC, picked.name ASC
        LIMIT 1
        """,
        workspace_id=workspace_id,
    )
    if raw == "No results found.":
        return None
    rows = json.loads(raw)
    if not rows:
        return None
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


async def _run_known_regressions(mcp: FakeMCP, workspace_id: str) -> list[ToolRun]:
    return await _run_known_regressions_with_options(
        mcp,
        workspace_id,
        LiveGraphOptions(case_ids=set(), regressions_only=False, fail_fast=False, verbose_progress=False),
    )


async def _run_known_regressions_with_options(
    mcp: FakeMCP,
    workspace_id: str,
    options: LiveGraphOptions,
) -> list[ToolRun]:
    runs: list[ToolRun] = []
    workspace_name = _workspace_basename(workspace_id)
    if not os.path.exists(GRAPH_GOLDENS_PATH):
        return runs
    with open(GRAPH_GOLDENS_PATH, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    for case in payload.get("cases") or []:
        if case.get("workspace_name") != workspace_name:
            continue
        case_id = str(case.get("id") or "").strip()
        if options.case_ids and case_id not in options.case_ids:
            continue
        steps = case.get("steps")
        if isinstance(steps, list):
            if options.verbose_progress:
                print(f"[live-graph] regression workflow={case_id} steps={len(steps)}")
            rendered_steps: list[str] = []
            for index, step in enumerate(steps, start=1):
                tool_name = step.get("tool")
                if not tool_name or tool_name not in mcp.tools:
                    raise RuntimeError(
                        f"Graph golden workflow '{case_id}' references unknown tool '{tool_name}'."
                    )
                params = _resolve_golden_params(dict(step.get("params") or {}), workspace_id)
                output = await _invoke_tool(mcp, tool_name, workspace_id, params)
                step_label = f"{tool_name}({case_id} step {index})"
                _validate_output(case_id, step_label, output, step)
                rendered_steps.append(
                    f"### Step {index}: {step.get('name') or tool_name}\n{output.strip()}"
                )
            combined_output = "\n\n".join(rendered_steps)
            for expected in case.get("required_substrings") or []:
                if expected not in combined_output:
                    raise RuntimeError(
                        f"Graph golden workflow '{case_id}': expected '{expected}' in combined output."
                    )
            for forbidden in case.get("forbidden_substrings") or []:
                if forbidden in combined_output:
                    raise RuntimeError(
                        f"Graph golden workflow '{case_id}': unexpected '{forbidden}' in combined output."
                    )
            runs.append(ToolRun(f"workflow:{case_id}", combined_output))
            continue

        tool_name = case.get("tool")
        if not tool_name or tool_name not in mcp.tools:
            raise RuntimeError(f"Graph golden '{case.get('id')}' references unknown tool '{tool_name}'.")
        if options.verbose_progress:
            print(f"[live-graph] regression case={case_id} tool={tool_name}")
        params = _resolve_golden_params(dict(case.get("params") or {}), workspace_id)
        output = await _invoke_tool(mcp, tool_name, workspace_id, params)
        _validate_output(case_id, f"{tool_name}({case.get('id')})", output, case)
        runs.append(ToolRun(f"regression:{case.get('id')}", output))

    return runs


async def _run_live_checks(workspace_id: str, options: LiveGraphOptions) -> list[ToolRun]:
    mcp = _build_tool_registry()
    health_run = await _run_health_check(workspace_id)

    if options.regressions_only:
        return [health_run, *await _run_known_regressions_with_options(mcp, workspace_id, options)]

    resolve_output = await mcp.tools["resolve_graph_project"](workspace_id)
    _require_non_error("resolve_graph_project", resolve_output)

    overview_output = await mcp.tools["get_project_overview"](workspace_id)
    _require_non_error("get_project_overview", overview_output)

    symbol_name, symbol_file = await _pick_live_symbol(mcp, workspace_id)

    symbol_output = await mcp.tools["get_symbol_context"](
        workspace_id, symbol_name, include_source_preview=False, file_path=symbol_file or None
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

    type_runs: list[ToolRun] = []
    type_symbol = await _pick_live_type_symbol(mcp, workspace_id)
    if type_symbol:
        type_name, type_file = type_symbol
        type_output = await mcp.tools["get_symbol_context"](
            workspace_id, type_name, include_source_preview=False, file_path=type_file or None
        )
        _require_non_error("get_symbol_context(type)", type_output)
        if type_name not in type_output:
            raise RuntimeError(
                f"get_symbol_context did not mention selected type symbol '{type_name}'."
            )
        if type_file and type_file not in type_output:
            raise RuntimeError(
                f"get_symbol_context resolved type '{type_name}' to an unexpected file.\n"
                f"Expected file fragment: {type_file}\nOutput:\n{type_output}"
            )
        type_runs.extend(
            [
                ToolRun(
                    "selected_type_symbol",
                    json.dumps({"name": type_name, "filepath": type_file}, indent=2),
                ),
                ToolRun("get_symbol_context(type)", type_output),
            ]
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

    regression_runs = await _run_known_regressions_with_options(mcp, workspace_id, options)

    return [
        health_run,
        ToolRun("resolve_graph_project", resolve_output),
        ToolRun("get_project_overview", overview_output),
        ToolRun(
            "selected_symbol",
            json.dumps({"name": symbol_name, "filepath": symbol_file}, indent=2),
        ),
        ToolRun("get_symbol_context", symbol_output),
        *type_runs,
        ToolRun("get_call_chain", call_chain_output),
        ToolRun("query_graph", raw_query_output),
        *cargo_runs,
        *apple_runs,
        *regression_runs,
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
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only the specified graph golden case id(s). Can be passed multiple times.",
    )
    parser.add_argument(
        "--regressions-only",
        action="store_true",
        help="Skip generic live smoke checks and run only graph golden regressions.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first workspace failure.",
    )
    parser.add_argument(
        "--verbose-progress",
        action="store_true",
        help="Print progress before each graph golden case runs.",
    )
    args = parser.parse_args()

    if not args.workspace_ids:
        raise RuntimeError("workspace_id is required")

    options = LiveGraphOptions(
        case_ids={case_id.strip() for case_id in args.case_id if case_id.strip()},
        regressions_only=bool(args.regressions_only),
        fail_fast=bool(args.fail_fast),
        verbose_progress=bool(args.verbose_progress),
    )

    failures: list[tuple[str, str]] = []
    for workspace_id in args.workspace_ids:
        print(f"[live-graph] workspace={workspace_id}")
        try:
            if args.reindex:
                print(f"[live-graph] reindex mode={args.mode}")
                result = await _ensure_indexed(workspace_id, args.mode)
                print(result)

            runs = await _run_live_checks(workspace_id, options)
            for run in runs:
                print(f"\n=== {run.name} ===")
                print(run.output.strip())
        except Exception as exc:
            failures.append((workspace_id, str(exc)))
            print(f"\n[live-graph] FAILED: {workspace_id}")
            print(str(exc).strip())
            if options.fail_fast:
                break
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
