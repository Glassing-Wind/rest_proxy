#!/usr/bin/env python3
"""Run MCP-only investigation workflows and report the first trust hesitation."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tool_choice_eval import evaluate_tool_choice, extract_catalog_tools  # noqa: E402


WORKFLOWS_PATH = ROOT / "benchmarks" / "mcp_investigation_workflows.json"
BASE_URL = os.environ.get("BRAIN_SERVER_BASE_URL") or (
    f"http://127.0.0.1:{os.environ.get('BRAIN_SERVER_PORT', '8001')}"
)
MCP_URL = f"{BASE_URL}/mcp"
HEALTH_URL = f"{BASE_URL}/health"
PROTOCOL_VERSION = "2025-06-18"


class TrustHesitation(AssertionError):
    """First point where the MCP workflow no longer feels safe to trust."""

    def __init__(
        self,
        *,
        workflow_id: str,
        step_name: str,
        tool_name: str,
        reason: str,
        detail: str = "",
    ) -> None:
        super().__init__(reason)
        self.workflow_id = workflow_id
        self.step_name = step_name
        self.tool_name = tool_name
        self.reason = reason
        self.detail = detail

    def format(self) -> str:
        lines = [
            "FIRST TRUST HESITATION",
            f"- workflow: {self.workflow_id}",
            f"- step: {self.step_name}",
            f"- tool: {self.tool_name}",
            f"- reason: {self.reason}",
        ]
        if self.detail:
            lines.append(f"- detail: {self.detail}")
        return "\n".join(lines)


def _load_workflows(path: Path = WORKFLOWS_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    workflows = payload.get("workflows")
    if not isinstance(workflows, list):
        raise ValueError(f"{path} must contain a top-level workflows list")
    return [workflow for workflow in workflows if isinstance(workflow, dict)]


def _request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], str]:
    data = None
    req_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
        req_headers.setdefault("Accept", "application/json, text/event-stream")
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, raw


def _extract_sse_json(raw: str) -> dict[str, Any]:
    for line in raw.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError("No SSE data frame found")


def _parse_payload(headers: dict[str, str], raw: str) -> dict[str, Any]:
    if "text/event-stream" in headers.get("content-type", ""):
        return _extract_sse_json(raw)
    return json.loads(raw)


def _extract_text_from_result(result: dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text") or ""))
        if texts:
            return "\n".join(texts)
    structured = result.get("structuredContent")
    if isinstance(structured, str):
        return structured
    if structured is not None:
        return json.dumps(structured, indent=2, sort_keys=True)
    if "text" in result:
        return str(result.get("text") or "")
    return json.dumps(result, indent=2, sort_keys=True)


def _initialize_session() -> str:
    status, headers, raw = _request(
        MCP_URL,
        method="POST",
        headers={"MCP-Protocol-Version": PROTOCOL_VERSION},
        body={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "graphrag-mcp-investigation-pass",
                    "version": "1.0",
                },
            },
        },
    )
    if status != 200:
        raise AssertionError(f"initialize failed with status={status}: {raw}")
    session_id = headers.get("mcp-session-id")
    if not session_id:
        raise AssertionError("initialize response missing Mcp-Session-Id")
    result = (_parse_payload(headers, raw).get("result") or {})
    if result.get("protocolVersion") != PROTOCOL_VERSION:
        raise AssertionError(f"unexpected protocol negotiation: {result}")
    notify_status, _, _ = _request(
        MCP_URL,
        method="POST",
        headers={
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Session-Id": session_id,
        },
        body={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )
    if notify_status not in {200, 202}:
        raise AssertionError(f"initialized notification failed with status={notify_status}")
    return session_id


def _mcp_call(session_id: str, tool_name: str, arguments: dict[str, Any], workspace_id: str) -> str:
    mcp_arguments = dict(arguments)
    if (
        tool_name in {"get_symbol_context", "get_call_chain"}
        and "workspace_id" not in mcp_arguments
    ):
        mcp_arguments["workspace_id"] = workspace_id
    status, headers, raw = _request(
        MCP_URL,
        method="POST",
        headers={
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Session-Id": session_id,
        },
        body={
            "jsonrpc": "2.0",
            "id": 100,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": mcp_arguments},
        },
    )
    if status != 200:
        raise AssertionError(f"tools/call failed for {tool_name} with status={status}: {raw}")
    payload = _parse_payload(headers, raw)
    if payload.get("error"):
        raise AssertionError(f"MCP tool call failed for {tool_name}: {payload['error']}")
    return _extract_text_from_result(payload.get("result") or {})


def _mcp_list_tools(session_id: str) -> list[str]:
    status, headers, raw = _request(
        MCP_URL,
        method="POST",
        headers={
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Session-Id": session_id,
        },
        body={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    if status != 200:
        raise AssertionError(f"tools/list failed with status={status}: {raw}")
    payload = _parse_payload(headers, raw)
    tools = (payload.get("result") or {}).get("tools") or []
    return sorted(
        str(tool.get("name") or "")
        for tool in tools
        if isinstance(tool, dict) and str(tool.get("name") or "").strip()
    )


def _resolve_params(value: Any, workspace_id: str) -> Any:
    if value == "$workspace_id":
        return workspace_id
    if isinstance(value, list):
        return [_resolve_params(item, workspace_id) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_params(item, workspace_id) for key, item in value.items()}
    return value


def _snippet(text: str, *, limit: int = 900) -> str:
    compact = "\n".join(line.rstrip() for line in str(text).strip().splitlines())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit].rstrip()}\n... <truncated>"


def _require_output(
    *,
    workflow_id: str,
    step: dict[str, Any],
    tool_name: str,
    output: str,
) -> None:
    step_name = str(step.get("name") or tool_name)
    if not str(output).strip():
        raise TrustHesitation(
            workflow_id=workflow_id,
            step_name=step_name,
            tool_name=tool_name,
            reason="tool returned empty output",
        )
    lower = str(output).lstrip().lower()
    if lower.startswith("error ") or lower.startswith("error:"):
        raise TrustHesitation(
            workflow_id=workflow_id,
            step_name=step_name,
            tool_name=tool_name,
            reason="tool returned an error payload",
            detail=_snippet(output),
        )
    for expected in step.get("required_substrings") or []:
        if str(expected) not in output:
            raise TrustHesitation(
                workflow_id=workflow_id,
                step_name=step_name,
                tool_name=tool_name,
                reason=f"missing expected evidence: {expected}",
                detail=_snippet(output),
            )
    for forbidden in step.get("forbidden_substrings") or []:
        if str(forbidden) in output:
            raise TrustHesitation(
                workflow_id=workflow_id,
                step_name=step_name,
                tool_name=tool_name,
                reason=f"hit forbidden evidence: {forbidden}",
                detail=_snippet(output),
            )


def _run_trust_checks(
    *,
    workflow_id: str,
    step: dict[str, Any],
    tool_name: str,
    output: str,
) -> None:
    step_name = str(step.get("name") or tool_name)
    for check in step.get("trust_checks") or []:
        if not isinstance(check, dict):
            continue
        kind = str(check.get("kind") or "")
        label = str(check.get("label") or kind or "trust check")
        values = [str(item) for item in check.get("values") or []]
        if kind == "contains_any" and not any(value in output for value in values):
            raise TrustHesitation(
                workflow_id=workflow_id,
                step_name=step_name,
                tool_name=tool_name,
                reason=f"trust check failed: {label}",
                detail=_snippet(output),
            )
        if kind == "contains_all":
            missing = [value for value in values if value not in output]
            if missing:
                raise TrustHesitation(
                    workflow_id=workflow_id,
                    step_name=step_name,
                    tool_name=tool_name,
                    reason=f"trust check failed: {label}",
                    detail=f"missing={missing}\n{_snippet(output)}",
                )


def _check_tool_choice(
    *,
    workflow_id: str,
    step: dict[str, Any],
    tool_name: str,
    output: str,
) -> None:
    case_id = step.get("tool_choice_case")
    if not case_id:
        return
    step_name = str(step.get("name") or tool_name)
    proposed = extract_catalog_tools(output) if tool_name == "get_mcp_tool_catalog" else [tool_name]
    report = evaluate_tool_choice(str(case_id), proposed)
    if report["status"] != "healthy":
        raise TrustHesitation(
            workflow_id=workflow_id,
            step_name=step_name,
            tool_name=tool_name,
            reason=f"tool-choice regression for {case_id}: {report['status']}",
            detail=json.dumps(
                {
                    "first_tool": report["first_tool"],
                    "preferred_tools": report["preferred_tools"],
                    "guidance": report["guidance"],
                    "proposed_tools": report["proposed_tools"],
                },
                indent=2,
                sort_keys=True,
            ),
        )


def _select_workflows(
    workflows: list[dict[str, Any]],
    wanted_ids: list[str],
) -> list[dict[str, Any]]:
    if not wanted_ids:
        return workflows
    wanted = {item.strip() for item in wanted_ids if item.strip()}
    selected = [workflow for workflow in workflows if str(workflow.get("id") or "") in wanted]
    matched = {str(workflow.get("id") or "") for workflow in selected}
    missing = wanted - matched
    if missing:
        raise ValueError(f"Unknown workflow id(s): {sorted(missing)}")
    return selected


def _workflow_workspace_id(workflow: dict[str, Any], override: str | None) -> str:
    workspace_id = override or str(workflow.get("workspace_id") or "")
    if not workspace_id:
        raise ValueError(f"Workflow {workflow.get('id')} is missing workspace_id")
    return os.path.abspath(os.path.expanduser(workspace_id))


def _run_workflow(
    *,
    workflow: dict[str, Any],
    session_id: str,
    tool_names: set[str],
    workspace_override: str | None = None,
    verbose: bool = False,
) -> list[dict[str, str]]:
    workflow_id = str(workflow.get("id") or "unnamed_workflow")
    workspace_id = _workflow_workspace_id(workflow, workspace_override)
    if not os.path.isdir(workspace_id):
        raise TrustHesitation(
            workflow_id=workflow_id,
            step_name="workspace preflight",
            tool_name="local filesystem",
            reason=f"workspace directory is missing: {workspace_id}",
        )

    results: list[dict[str, str]] = []
    for step in workflow.get("steps") or []:
        if not isinstance(step, dict):
            continue
        tool_name = str(step.get("tool") or "")
        step_name = str(step.get("name") or tool_name)
        if tool_name not in tool_names:
            raise TrustHesitation(
                workflow_id=workflow_id,
                step_name=step_name,
                tool_name=tool_name,
                reason="tool is not listed by the live MCP server",
            )
        params = _resolve_params(dict(step.get("params") or {}), workspace_id)
        if verbose:
            print(f"[{workflow_id}] {step_name}: {tool_name}", flush=True)
        output = _mcp_call(session_id, tool_name, params, workspace_id)
        _require_output(
            workflow_id=workflow_id,
            step=step,
            tool_name=tool_name,
            output=output,
        )
        _check_tool_choice(
            workflow_id=workflow_id,
            step=step,
            tool_name=tool_name,
            output=output,
        )
        _run_trust_checks(
            workflow_id=workflow_id,
            step=step,
            tool_name=tool_name,
            output=output,
        )
        results.append(
            {
                "workflow": workflow_id,
                "step": step_name,
                "tool": tool_name,
                "status": "trusted",
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Exercise preferred investigation workflows through the live MCP "
            "transport and report the first trust hesitation."
        )
    )
    parser.add_argument(
        "--workflow-id",
        action="append",
        default=[],
        help="Workflow id from benchmarks/mcp_investigation_workflows.json.",
    )
    parser.add_argument(
        "--workspace-id",
        help="Override the workflow workspace path. The repo must already be indexed.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print each MCP step.")
    args = parser.parse_args()

    status, _, _ = _request(HEALTH_URL)
    if status != 200:
        raise TrustHesitation(
            workflow_id="preflight",
            step_name="brain server health",
            tool_name="MCP transport",
            reason=f"brain server health check failed with status={status}",
        )

    workflows = _select_workflows(_load_workflows(), args.workflow_id)
    session_id = _initialize_session()
    try:
        tool_names = set(_mcp_list_tools(session_id))
        results: list[dict[str, str]] = []
        for workflow in workflows:
            results.extend(
                _run_workflow(
                    workflow=workflow,
                    session_id=session_id,
                    tool_names=tool_names,
                    workspace_override=args.workspace_id,
                    verbose=args.verbose,
                )
            )
    finally:
        _request(
            MCP_URL,
            method="DELETE",
            headers={
                "MCP-Protocol-Version": PROTOCOL_VERSION,
                "Mcp-Session-Id": session_id,
            },
        )

    print("MCP investigation pass completed without trust hesitations.")
    print(f"- workflows checked: {', '.join(sorted({item['workflow'] for item in results}))}")
    print(f"- MCP tool calls trusted: {len(results)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TrustHesitation as exc:
        print(exc.format(), file=sys.stderr)
        raise SystemExit(1)
