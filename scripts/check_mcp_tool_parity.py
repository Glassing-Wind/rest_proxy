#!/usr/bin/env python3
"""Compare selected live tool outputs between direct invocation and MCP transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

_LM_PROXY_FALLBACK_PYTHON = (
    "/opt/homebrew/Caskroom/miniforge/base/envs/lmproxy/bin/python"
)


def _preferred_python() -> str | None:
    for candidate in (
        os.environ.get("LM_PROXY_PYTHON"),
        os.environ.get("LM_PROXY_INDEX_PYTHON"),
        _LM_PROXY_FALLBACK_PYTHON,
        shutil.which("python3"),
        shutil.which("python"),
    ):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _reexec_with_preferred_python_if_needed(exc: ModuleNotFoundError) -> None:
    if os.environ.get("LM_PROXY_RUNTIME_REEXECED") == "1":
        return
    preferred = _preferred_python()
    if not preferred or os.path.realpath(preferred) == os.path.realpath(sys.executable):
        return
    os.environ["LM_PROXY_RUNTIME_REEXECED"] = "1"
    os.execv(preferred, [preferred, __file__, *sys.argv[1:]])


def _ensure_runtime_dependencies() -> None:
    try:
        import neo4j  # noqa: F401
    except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
        _reexec_with_preferred_python_if_needed(exc)
        raise


try:
    from test_live_graph_tools import (  # noqa: E402
        GRAPH_GOLDENS_PATH,
        _build_tool_registry,
        _install_mcp_stub,
        _resolve_golden_params,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - runtime guard
    _reexec_with_preferred_python_if_needed(exc)
    raise

_ensure_runtime_dependencies()


BASE_URL = "http://127.0.0.1:8001"
MCP_URL = f"{BASE_URL}/mcp"
HEALTH_URL = f"{BASE_URL}/health"
PROTOCOL_VERSION = "2025-06-18"

DEFAULT_WORKSPACES = {
    "FrameCreator": "/Users/michaelmarler/Projects/FrameCreator",
    "sample-food-truck": "/Users/michaelmarler/Projects/sample-food-truck",
    "Fruta-upstream": "/Users/michaelmarler/Projects/Fruta-upstream",
    "LoomBackgroundMusic": "/Users/michaelmarler/Projects/LoomBackgroundMusic",
    "rest_proxy": "/Users/michaelmarler/Projects/rest_proxy",
    "pydantic-ai": "/Users/michaelmarler/Projects/pydantic-ai",
    "spring-petclinic-upstream": "/Users/michaelmarler/Projects/spring-petclinic-upstream",
    "okhttp-upstream": "/Users/michaelmarler/Projects/okhttp-upstream",
    "swift-nio": "/Users/michaelmarler/Projects/swift-nio",
    "tree-sitter-language-pack": "/Users/michaelmarler/Projects/tree-sitter-language-pack",
}

DEFAULT_CASE_IDS = [
    "framecreator_sidebar_symbol_context",
    "framecreator_views_directory_snapshot",
    "framecreator_flow_summary",
    "framecreator_apple_build_summary",
    "sample_food_truck_apple_build_summary",
    "sample_food_truck_project_related_files",
    "fruta_apple_build_summary",
    "loombackgroundmusic_apple_build_summary",
    "loombackgroundmusic_workspace_related_files",
    "loombackgroundmusic_workspace_directory_snapshot",
    "rest_proxy_repo_dependency_summary",
    "rest_proxy_backend_flow_summary",
    "pydantic_ai_symbol_imports_overview",
    "pydantic_ai_provider_wiring_search",
    "pydantic_ai_provider_related_files",
    "pydantic_ai_provider_subgraph",
    "spring_petclinic_owner_controller_symbol_context",
    "okhttp_proceed_symbol_context",
    "swift_nio_code_importance",
    "swift_nio_bytebuffer_exports_summary",
    "ts_pack_detect_language_cross_project",
]

DEFAULT_DIRECT_PARITY_CHECKS = [
    {
        "id": "mcp_tool_catalog_symbol_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "symbol", "limit": 8},
        "required_substrings": [
            "MCP tool catalog for `symbol`:",
            "`get_symbol_context`",
            "`find_references`",
        ],
    },
    {
        "id": "mcp_tool_catalog_natural_docs_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "learn library docs", "limit": 10},
        "required_substrings": [
            "MCP tool catalog for `learn library docs`:",
            "`search_documentation`",
            "`research_documentation`",
        ],
    },
    {
        "id": "mcp_tool_catalog_natural_memory_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "remember repo fact", "limit": 10},
        "required_substrings": [
            "MCP tool catalog for `remember repo fact`:",
            "`add_memory`",
        ],
    },
    {
        "id": "mcp_tool_catalog_natural_memory_review_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "review memories", "limit": 10},
        "required_substrings": [
            "MCP tool catalog for `review memories`:",
            "`list_memories`",
        ],
    },
    {
        "id": "mcp_tool_catalog_natural_change_review_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "precommit test coverage", "limit": 10},
        "required_substrings": [
            "MCP tool catalog for `precommit test coverage`:",
            "`get_test_coverage_for`",
        ],
    },
    {
        "id": "mcp_tool_catalog_architecture_onboarding_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "repo architecture onboarding", "limit": 5},
        "required_substrings": [
            "MCP tool catalog for `repo architecture onboarding`:",
            "`get_project_overview`",
            "summarize architecture",
        ],
        "forbidden_substrings": [
            "`get_code_importance`",
            "`get_code_communities`",
        ],
    },
    {
        "id": "mcp_tool_catalog_blast_radius_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "blast radius", "limit": 5},
        "required_substrings": [
            "MCP tool catalog for `blast radius`:",
            "`get_code_importance`",
            "Prefer after: get_project_overview",
        ],
    },
    {
        "id": "mcp_tool_catalog_clustered_architecture_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "clustered repo architecture", "limit": 5},
        "required_substrings": [
            "MCP tool catalog for `clustered repo architecture`:",
            "`get_code_communities`",
            "Prefer after: get_project_overview",
        ],
    },
    {
        "id": "mcp_tool_catalog_symbol_definition_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "where is this symbol defined", "limit": 5},
        "required_substrings": [
            "MCP tool catalog for `where is this symbol defined`:",
            "`find_definitions`",
        ],
    },
    {
        "id": "mcp_tool_catalog_jump_definition_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "jump to definition", "limit": 5},
        "required_substrings": [
            "MCP tool catalog for `jump to definition`:",
            "`find_definitions`",
        ],
    },
    {
        "id": "mcp_tool_catalog_ranking_debug_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "why did search rank this result", "limit": 5},
        "required_substrings": [
            "MCP tool catalog for `why did search rank this result`:",
            "`trace_code_ranking`",
            "`analyze_duplicate_results`",
        ],
    },
    {
        "id": "mcp_tool_catalog_duplicate_results_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "duplicate search results", "limit": 5},
        "required_substrings": [
            "MCP tool catalog for `duplicate search results`:",
            "`analyze_duplicate_results`",
            "`rerank_retrieval_results`",
        ],
    },
    {
        "id": "mcp_tool_catalog_change_review_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "review changed code before commit", "limit": 5},
        "required_substrings": [
            "MCP tool catalog for `review changed code before commit`:",
            "`get_changed_symbols`",
        ],
    },
    {
        "id": "mcp_tool_catalog_fullstack_flow_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "ui api service db flow", "limit": 5},
        "required_substrings": [
            "MCP tool catalog for `ui api service db flow`:",
            "`get_app_flow_summary`",
        ],
    },
    {
        "id": "mcp_tool_catalog_backend_request_flow_intent",
        "tool": "get_mcp_tool_catalog",
        "params": {"intent": "how does a request reach the database", "limit": 5},
        "required_substrings": [
            "MCP tool catalog for `how does a request reach the database`:",
            "`get_backend_flow_summary`",
        ],
        "forbidden_substrings": [
            "`get_app_flow_summary`",
        ],
    },
    {
        "id": "stale_shadow_cleanup_dry_run",
        "tool": "cleanup_stale_shadow_graph",
        "params": {"dry_run": True, "max_project_ids": 25},
        "compare_output": False,
        "required_substrings": [
            "## Stale Shadow Graph Cleanup Dry Run",
            "Shadow project IDs found:",
            "Shadow nodes:",
            "Shadow relationships:",
        ],
    },
    {
        "id": "rest_proxy_git_summary",
        "tool": "git_summary",
        "workspace_name": "rest_proxy",
        "params": {"workspace_id": "$workspace_id"},
        "required_substrings": [
            "### Git Summary: `/Users/michaelmarler/Projects/rest_proxy`",
            "**Branch:** `codex/enterprise-hardening`",
        ],
    },
    {
        "id": "neo4j_documentation_sources",
        "tool": "list_documentation_sources",
        "params": {"topic": "neo4j", "limit": 8},
        "required_substrings": [
            "Documentation domains for topic='neo4j'",
            "Topic members:",
            "neo4j-gds",
            "neo4j.com",
        ],
    },
    {
        "id": "neo4j_gds_documentation_search",
        "tool": "search_documentation",
        "params": {"query": "maxIterations parameter", "topic": "neo4j-gds", "k": 3},
        "required_substrings": [
            "Documentation search: 'maxIterations parameter'  [topic=neo4j-gds]",
            "neo4j.com",
        ],
    },
    {
        "id": "rest_proxy_list_memories",
        "tool": "list_memories",
        "workspace_name": "rest_proxy",
        "params": {
            "workspace_id": "$workspace_id",
            "include_global": True,
            "min_importance": 1,
        },
        "required_substrings": [
            "Memories for '/Users/michaelmarler/Projects/rest_proxy'",
        ],
    },
    {
        "id": "rest_proxy_search_memory",
        "tool": "search_memory",
        "workspace_name": "rest_proxy",
        "params": {
            "workspace_id": "$workspace_id",
            "query": "architecture retrieval semantic roles",
            "global_search": True,
        },
        "required_substrings": [
            "## Working Memory",
        ],
    },
    {
        "id": "rest_proxy_describe_dev_file",
        "tool": "describe_file",
        "workspace_name": "rest_proxy",
        "params": {
            "project_path": "$workspace_id",
            "file_path": "tools/hands/dev.py",
        },
        "required_substrings": [
            "=== tools/hands/dev.py ===",
            "Purpose:",
            "register",
        ],
    },
    {
        "id": "rest_proxy_extract_changed_symbols_body",
        "tool": "extract_function_body",
        "workspace_name": "rest_proxy",
        "params": {
            "workspace_id": "$workspace_id",
            "file_path": "tools/hands/dev.py",
            "symbol_name": "get_changed_symbols",
        },
        "required_substrings": [
            "## `get_changed_symbols`",
            "git diff",
        ],
    },
    {
        "id": "rest_proxy_extract_fake_mcp_interface",
        "tool": "extract_class_interface",
        "workspace_name": "rest_proxy",
        "params": {
            "workspace_id": "$workspace_id",
            "file_path": "test_dev_tools.py",
            "class_name": "FakeMCP",
        },
        "required_substrings": [
            "## `FakeMCP`",
            "__init__",
            "tool",
        ],
    },
    {
        "id": "rest_proxy_find_symbol_usages_local",
        "tool": "find_symbol_usages",
        "workspace_name": "rest_proxy",
        "params": {
            "workspace_id": "$workspace_id",
            "file_path": "tools/hands/dev.py",
            "symbol_name": "get_changed_symbols",
        },
        "required_substrings": [
            "## `get_changed_symbols` in `dev.py`",
        ],
    },
    {
        "id": "rest_proxy_changed_symbols_parity",
        "tool": "get_changed_symbols",
        "workspace_name": "rest_proxy",
        "params": {"workspace_id": "$workspace_id", "since": "HEAD"},
    },
    {
        "id": "rest_proxy_test_coverage_for_dev_tools",
        "tool": "get_test_coverage_for",
        "workspace_name": "rest_proxy",
        "params": {
            "workspace_id": "$workspace_id",
            "file_path": "tools/hands/dev.py",
        },
        "compare_output": False,
        "required_substrings": [
            "## Tests covering `tools/hands/dev.py`",
            "test_dev_tools.py",
        ],
    },
    {
        "id": "retrieval_qa_rerank_compact_contract",
        "tool": "rerank_retrieval_results",
        "params": {
            "query": "parse_config implementation",
            "mode": "code",
            "results": [
                {
                    "file_path": "src/parser.py",
                    "content": "def parse_config(text):\n    return json.loads(text)\n",
                    "rank_score": 0.91,
                    "metadata": {
                        "chunk_role": "implementation",
                        "node_types": ["function_definition"],
                        "file_roles": ["runtime"],
                        "file_symbols": ["parse_config"],
                    },
                },
                {
                    "file_path": "src/parser_copy.py",
                    "content": "def parse_config(text):\n    return json.loads(text)\n",
                    "rank_score": 0.88,
                    "metadata": {
                        "chunk_role": "implementation",
                        "node_types": ["function_definition"],
                        "file_roles": ["runtime"],
                        "file_symbols": ["parse_config"],
                    },
                },
                {
                    "file_path": "tests/test_parser.py",
                    "content": "def test_parse_config():\n    assert parse_config('{}') == {}\n",
                    "rank_score": 0.72,
                    "metadata": {
                        "chunk_role": "test",
                        "node_types": ["function_definition"],
                        "file_roles": ["test"],
                        "file_symbols": ["test_parse_config"],
                    },
                },
            ],
        },
        "required_substrings": [
            '"input_count": 3',
            '"suppressed_count": 1',
            '"path": "src/parser.py"',
            '"path": "src/parser_copy.py"',
            '"exact_duplicate": 1',
        ],
    },
    {
        "id": "retrieval_qa_duplicate_analysis_compact_contract",
        "tool": "analyze_duplicate_results",
        "params": {
            "query": "parse_config implementation",
            "mode": "code",
            "results": [
                {
                    "file_path": "src/parser.py",
                    "content": "def parse_config(text):\n    return json.loads(text)\n",
                    "rank_score": 0.91,
                },
                {
                    "file_path": "src/parser_copy.py",
                    "content": "def parse_config(text):\n    return json.loads(text)\n",
                    "rank_score": 0.88,
                },
                {
                    "file_path": "tests/test_parser.py",
                    "content": "def test_parse_config():\n    assert parse_config('{}') == {}\n",
                    "rank_score": 0.72,
                },
            ],
        },
        "required_substrings": [
            '"mode": "code_retrieval"',
            '"duplicate_pair_count": 1',
            '"right_path": "src/parser_copy.py"',
            '"exact_duplicate"',
        ],
    },
    {
        "id": "retrieval_qa_code_ranking_trace_compact_contract",
        "tool": "trace_code_ranking",
        "params": {
            "query": "parse_config implementation",
            "results": [
                {
                    "file_path": "src/parser.py",
                    "content": "def parse_config(text):\n    return json.loads(text)\n",
                    "rank_score": 0.91,
                    "metadata": {
                        "chunk_role": "implementation",
                        "node_types": ["function_definition"],
                        "file_roles": ["runtime"],
                        "file_symbols": ["parse_config"],
                    },
                },
                {
                    "file_path": "tests/test_parser.py",
                    "content": "def test_parse_config():\n    assert parse_config('{}') == {}\n",
                    "rank_score": 0.72,
                    "metadata": {
                        "chunk_role": "test",
                        "node_types": ["function_definition"],
                        "file_roles": ["test"],
                        "file_symbols": ["test_parse_config"],
                    },
                },
            ],
        },
        "required_substrings": [
            '"query_class": "implementation_search"',
            '"path": "src/parser.py"',
            '"contributions"',
            '"exact_identifier_bonus"',
            '"function_definition"',
        ],
    },
    {
        "id": "framecreator_indexing_health",
        "tool": "get_indexing_health",
        "workspace_name": "FrameCreator",
        "params": {"workspace_id": "$workspace_id"},
        "required_substrings": [
            "# Indexing Health Audit: `/Users/michaelmarler/Projects/FrameCreator`",
            "**Sync Status**: ✅ Healthy",
            "**Run Alignment**:        ✅ Aligned",
        ],
    },
    {
        "id": "framecreator_resolve_graph_project",
        "tool": "resolve_graph_project",
        "workspace_name": "FrameCreator",
        "params": {"workspace_id": "$workspace_id"},
        "required_substrings": [
            '"workspace_path": "/Users/michaelmarler/Projects/FrameCreator"',
            '"project_id": "19b79d7c3feb"',
        ],
    },
    {
        "id": "framecreator_project_overview",
        "tool": "get_project_overview",
        "workspace_name": "FrameCreator",
        "params": {"workspace_id": "$workspace_id"},
        "required_substrings": [
            "# Project Overview: FrameCreator",
            "## Key Files (most symbol-dense, non-test)",
        ],
    },
    {
        "id": "spring_owner_find_definitions",
        "tool": "find_definitions",
        "workspace_name": "spring-petclinic-upstream",
        "params": {"symbol_name": "OwnerController"},
        "required_substrings": [
            "Definition matches for `OwnerController`",
            "src/main/java/org/springframework/samples/petclinic/owner/OwnerController.java:48",
        ],
    },
    {
        "id": "pydantic_infer_provider_list_symbol_matches",
        "tool": "list_symbol_matches",
        "workspace_name": "pydantic-ai",
        "params": {
            "project_path": "$workspace_id",
            "query": "infer_provider",
            "limit": 10,
            "kinds": None,
        },
        "required_substrings": [
            "Symbol matches for 'infer_provider':",
            "infer_provider_class (Function)",
        ],
    },
    {
        "id": "spring_process_find_form_grep",
        "tool": "grep_codebase",
        "workspace_name": "spring-petclinic-upstream",
        "params": {
            "workspace_id": "$workspace_id",
            "pattern": "processFindForm",
            "file_glob": "*.java",
        },
        "required_substrings": [
            "## `processFindForm` — 2 file(s)",
            "OwnerController.java",
            "OwnerControllerTests.java",
        ],
    },
    {
        "id": "framecreator_sidebar_call_chain_up",
        "tool": "get_call_chain",
        "workspace_name": "FrameCreator",
        "params": {
            "symbol_name": "SidebarView",
            "file_path": "FrameCreator/Views/SidebarView.swift",
            "direction": "up",
            "depth": 2,
        },
        "required_substrings": [
            "`SidebarView` resolved but no graph callers within 2 hops.",
            "`FrameCreator/Views/ContentView.swift:49`  >> SidebarView(viewModel: viewModel)",
        ],
    },
    {
        "id": "okhttp_real_interceptor_chain_references",
        "tool": "find_references",
        "workspace_name": "okhttp-upstream",
        "params": {
            "workspace_id": "$workspace_id",
            "symbol_name": "RealInterceptorChain",
        },
        "required_substrings": [
            "### Mentions & Type Usages (Semantic)",
            "okhttp/src/commonJvmAndroid/kotlin/okhttp3/internal/http/RealInterceptorChain.kt:53",
            "okhttp/src/commonJvmAndroid/kotlin/okhttp3/internal/connection/ConnectInterceptor.kt:1",
        ],
    },
    {
        "id": "pydantic_ai_provider_provenance",
        "tool": "trace_graph_provenance",
        "workspace_name": "pydantic-ai",
        "params": {
            "workspace_id": "$workspace_id",
            "symbol_filter": "infer_provider",
            "file_filter": "providers/__init__.py",
        },
        "required_substrings": [
            "# Graph Provenance: pydantic-ai",
            "Symbol filter: `infer_provider`",
            "File filter: `providers/__init__.py`",
            "## Resolved Internal Samples",
            "Resolve note: Unresolved and filtered decisions remain available through index-time provenance logging.",
            "## File Graph Link Samples",
        ],
    },
    {
        "id": "pydantic_ai_code_importance",
        "tool": "get_code_importance",
        "workspace_name": "pydantic-ai",
        "params": {"workspace_id": "$workspace_id"},
        "required_substrings": [
            "Most important files [",
            "Recommended starting points:",
            "pydantic_ai_slim/pydantic_ai/agent/__init__.py",
            "pydantic_ai_slim/pydantic_ai/models/__init__.py",
        ],
    },
    {
        "id": "pydantic_ai_code_communities",
        "tool": "get_code_communities",
        "workspace_name": "pydantic-ai",
        "params": {"workspace_id": "$workspace_id"},
        "required_substrings": [
            "Architectural clusters [",
            "Community Summary:",
            "Dominant concerns:",
            "Priority exploration order:",
        ],
    },
    {
        "id": "pydantic_ai_provider_exports_summary",
        "tool": "get_symbol_exports_summary",
        "workspace_name": "pydantic-ai",
        "params": {
            "project_path": "$workspace_id",
            "limit": 10,
            "symbol_prefix": "infer_provider",
        },
        "required_substrings": [
            "# Symbol export summary: pydantic-ai",
            "## Inspect First",
            "infer_provider_class",
            "pydantic_ai_slim/pydantic_ai/providers/__init__.py",
        ],
    },
]


def _request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict | None = None,
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


def _extract_sse_json(raw: str) -> dict:
    for line in raw.splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError("No SSE data frame found")


def _parse_payload(headers: dict[str, str], raw: str) -> dict:
    content_type = headers.get("content-type", "")
    if "text/event-stream" in content_type:
        return _extract_sse_json(raw)
    return json.loads(raw)


def _extract_text_from_result(result: dict) -> str:
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


_SCHEME_BUILDS_RE = re.compile(
    r"^(?P<prefix>\s*(?:-\s*)?scheme `[^`]+` builds )(?P<body>.+)$"
)
_WORKSPACE_REFS_RE = re.compile(
    r"^(?P<prefix>\s*(?:-\s*)?workspace `[^`]+` references )(?P<body>.+)$"
)
_VOLATILE_SHADOW_HEALTH_LABELS = (
    "Shadow project IDs found:",
    "Shadow project IDs with nodes:",
    "Shadow project IDs with relationships:",
    "Shadow nodes:",
    "Shadow relationships:",
)


def _normalize_order_insensitive_line(line: str) -> str:
    stripped = line.rstrip()
    stripped_text = stripped.strip()
    bullet_prefix = "- " if stripped_text.startswith("- ") else ""
    comparable = stripped_text[2:].strip() if bullet_prefix else stripped_text
    for label in _VOLATILE_SHADOW_HEALTH_LABELS:
        if comparable.startswith(label) and comparable != label:
            indent = line[: len(line) - len(line.lstrip())]
            return f"{indent}{bullet_prefix}{label} <volatile>"
    for pattern in (_SCHEME_BUILDS_RE, _WORKSPACE_REFS_RE):
        match = pattern.match(stripped)
        if not match:
            continue
        items = [
            item.strip()
            for item in str(match.group("body") or "").split(",")
            if item.strip()
        ]
        return f"{match.group('prefix')}{', '.join(sorted(dict.fromkeys(items)))}"
    return stripped


def _normalize(text: str) -> str:
    lines = [
        _normalize_order_insensitive_line(line)
        for line in str(text).strip().splitlines()
    ]
    section_headers = {
        "## Files with most symbol exports",
    }
    normalized: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        normalized.append(line)
        if line.strip() in section_headers:
            idx += 1
            section_lines: list[str] = []
            while idx < len(lines):
                candidate = lines[idx]
                stripped = candidate.strip()
                if stripped.startswith("#") and stripped != line.strip():
                    break
                if stripped.startswith("- "):
                    section_lines.append(candidate)
                    idx += 1
                    continue
                if not stripped:
                    break
                section_lines.append(candidate)
                idx += 1
            bullet_lines = [
                item for item in section_lines if item.strip().startswith("- ")
            ]
            other_lines = [
                item for item in section_lines if not item.strip().startswith("- ")
            ]
            normalized.extend(sorted(bullet_lines))
            normalized.extend(other_lines)
            continue
        idx += 1
    return "\n".join(normalized).strip()


def _normalized_expected_variants(text: str) -> list[str]:
    normalized = _normalize_order_insensitive_line(str(text))
    variants = [str(text), normalized]
    stripped = normalized.lstrip()
    if stripped.startswith("workspace `") or stripped.startswith("scheme `"):
        variants.append(f"- {stripped}")
    return variants


def _first_ranked_result_label(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if (stripped.startswith("--- ") and " (Score:" in stripped) or (
            stripped.startswith("--- ") and stripped.endswith(" ---")
        ):
            return stripped
    return ""


def _find_references_lines(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip().startswith("- ")}


def _require_non_error(name: str, output: str) -> None:
    if not output or output.startswith("Error "):
        raise AssertionError(f"{name} failed:\n{output}")


def _is_optional_search_dependency_unavailable(output: str) -> bool:
    return "LM Studio server is unavailable" in str(
        output
    ) or "Start the server or check LMSTUDIO_BASE_URL" in str(output)


def _load_cases(case_ids: list[str]) -> list[dict]:
    with open(GRAPH_GOLDENS_PATH, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    selected: list[dict] = []
    wanted = set(case_ids)
    for case in payload.get("cases") or []:
        case_id = str(case.get("id") or "")
        if case_id in wanted:
            if case.get("steps"):
                raise AssertionError(
                    f"Workflow case '{case_id}' is not supported in MCP parity smoke"
                )
            selected.append(case)
    missing = wanted - {str(case.get("id") or "") for case in selected}
    if missing:
        raise AssertionError(f"Unknown case ids: {sorted(missing)}")
    return selected


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
                "clientInfo": {"name": "graphrag-mcp-parity", "version": "1.0"},
            },
        },
    )
    assert status == 200, f"initialize failed with status={status}"
    session_id = headers.get("mcp-session-id")
    assert session_id, "initialize response missing Mcp-Session-Id"
    payload = _parse_payload(headers, raw)
    result = payload.get("result") or {}
    assert result.get("protocolVersion") == PROTOCOL_VERSION, result
    notify_status, _, _ = _request(
        MCP_URL,
        method="POST",
        headers={
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            "Mcp-Session-Id": session_id,
        },
        body={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    )
    assert notify_status == 202 or notify_status == 200, (
        f"initialized notification failed with status={notify_status}"
    )
    return session_id


def _mcp_call(
    session_id: str, tool_name: str, arguments: dict, workspace_id: str
) -> str:
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
    assert status == 200, f"tools/call failed for {tool_name} with status={status}"
    payload = _parse_payload(headers, raw)
    if payload.get("error"):
        raise AssertionError(
            f"MCP tool call failed for {tool_name}: {payload['error']}"
        )
    result = payload.get("result") or {}
    return _extract_text_from_result(result)


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
    assert status == 200, f"tools/list failed with status={status}"
    payload = _parse_payload(headers, raw)
    result = payload.get("result") or {}
    tools = result.get("tools") or []
    return sorted(
        str(tool.get("name") or "")
        for tool in tools
        if isinstance(tool, dict) and str(tool.get("name") or "").strip()
    )


async def _direct_call(mcp, tool_name: str, workspace_id: str, params: dict) -> str:
    if tool_name in {"get_symbol_context", "get_call_chain"}:
        return await mcp.tools[tool_name](workspace_id, **params)
    return await mcp.tools[tool_name](**params)


def _build_parity_tool_registry():
    mcp = _build_tool_registry()
    _install_mcp_stub()
    from tools.brain import tool_catalog
    from tools.hands import indexing as indexing_tools

    tool_catalog.register(mcp)
    indexing_tools.register(mcp)
    return mcp


async def _run_direct_parity_cases(cases: list[dict], session_id: str) -> list[dict]:
    mcp = _build_parity_tool_registry()
    results: list[dict] = []
    for case in cases:
        case_id = str(case.get("id") or "")
        workspace_name = str(case.get("workspace_name") or "")
        workspace_id = DEFAULT_WORKSPACES.get(workspace_name)
        assert workspace_id and os.path.isdir(workspace_id), (
            f"Missing workspace for {workspace_name}"
        )
        tool_name = str(case.get("tool") or "")
        params = _resolve_golden_params(dict(case.get("params") or {}), workspace_id)
        direct_output = await _direct_call(mcp, tool_name, workspace_id, params)
        if (
            tool_name == "search_codebase"
            and _is_optional_search_dependency_unavailable(direct_output)
        ):
            results.append(
                {
                    "case_id": case_id,
                    "tool": tool_name,
                    "workspace": workspace_name,
                    "status": "skipped",
                    "reason": "LM Studio unavailable",
                }
            )
            continue
        mcp_output = _mcp_call(session_id, tool_name, params, workspace_id)
        if (
            tool_name == "search_codebase"
            and _is_optional_search_dependency_unavailable(mcp_output)
        ):
            results.append(
                {
                    "case_id": case_id,
                    "tool": tool_name,
                    "workspace": workspace_name,
                    "status": "skipped",
                    "reason": "LM Studio unavailable",
                }
            )
            continue
        _require_non_error(f"{case_id} direct {tool_name}", direct_output)
        _require_non_error(f"{case_id} MCP {tool_name}", mcp_output)
        direct_norm = _normalize(direct_output)
        mcp_norm = _normalize(mcp_output)
        if tool_name == "search_codebase":
            direct_top = _first_ranked_result_label(direct_norm)
            mcp_top = _first_ranked_result_label(mcp_norm)
            if direct_top or mcp_top:
                assert direct_top and mcp_top and direct_top == mcp_top, (
                    f"MCP top-hit parity mismatch for {case_id}\n"
                    f"--- direct top ---\n{direct_top}\n\n--- mcp top ---\n{mcp_top}"
                )
            else:
                assert direct_norm == mcp_norm, (
                    f"MCP search parity mismatch for {case_id}\n"
                    f"--- direct ---\n{direct_norm}\n\n--- mcp ---\n{mcp_norm}"
                )
        elif tool_name == "find_references":
            direct_lines = _find_references_lines(direct_norm)
            mcp_lines = _find_references_lines(mcp_norm)
            assert mcp_lines.issubset(direct_lines), (
                f"MCP find_references parity mismatch for {case_id}\n"
                f"--- direct ---\n{direct_norm}\n\n--- mcp ---\n{mcp_norm}"
            )
        else:
            assert direct_norm == mcp_norm, (
                f"MCP parity mismatch for {case_id}\n"
                f"--- direct ---\n{direct_norm}\n\n--- mcp ---\n{mcp_norm}"
            )
        for expected in case.get("required_substrings") or []:
            variants = _normalized_expected_variants(str(expected))
            assert any(variant in mcp_norm for variant in variants), (
                f"Missing expected substring for {case_id}: {expected}"
            )
        for forbidden in case.get("forbidden_substrings") or []:
            variants = _normalized_expected_variants(str(forbidden))
            assert not any(variant in mcp_norm for variant in variants), (
                f"Unexpected forbidden substring for {case_id}: {forbidden}"
            )
        results.append(
            {
                "case_id": case_id,
                "tool": tool_name,
                "workspace": workspace_name,
                "status": "checked",
            }
        )
    return results


async def _run_direct_tier1_checks(checks: list[dict], session_id: str) -> list[dict]:
    mcp = _build_parity_tool_registry()
    results: list[dict] = []
    for check in checks:
        check_id = str(check["id"])
        workspace_name = str(check.get("workspace_name") or "")
        workspace_id = ""
        if workspace_name:
            workspace_id = DEFAULT_WORKSPACES.get(workspace_name) or ""
            assert workspace_id and os.path.isdir(workspace_id), (
                f"Missing workspace for {workspace_name}"
            )
        tool_name = str(check["tool"])
        params = _resolve_golden_params(dict(check.get("params") or {}), workspace_id)
        direct_output = await _direct_call(mcp, tool_name, workspace_id, params)
        mcp_output = _mcp_call(session_id, tool_name, params, workspace_id)
        _require_non_error(f"{check_id} direct {tool_name}", direct_output)
        _require_non_error(f"{check_id} MCP {tool_name}", mcp_output)
        direct_norm = _normalize(direct_output)
        mcp_norm = _normalize(mcp_output)
        compare_output = bool(check.get("compare_output", True))
        if not compare_output:
            pass
        elif tool_name == "find_references":
            direct_lines = _find_references_lines(direct_norm)
            mcp_lines = _find_references_lines(mcp_norm)
            assert mcp_lines.issubset(direct_lines), (
                f"MCP parity mismatch for {check_id}\n"
                f"--- direct ---\n{direct_norm}\n\n--- mcp ---\n{mcp_norm}"
            )
        else:
            assert direct_norm == mcp_norm, (
                f"MCP parity mismatch for {check_id}\n"
                f"--- direct ---\n{direct_norm}\n\n--- mcp ---\n{mcp_norm}"
            )
        for expected in check.get("required_substrings") or []:
            variants = _normalized_expected_variants(str(expected))
            assert any(variant in direct_norm for variant in variants), (
                f"Missing expected substring for {check_id} direct output: {expected}"
            )
            assert any(variant in mcp_norm for variant in variants), (
                f"Missing expected substring for {check_id}: {expected}"
            )
        for forbidden in check.get("forbidden_substrings") or []:
            variants = _normalized_expected_variants(str(forbidden))
            assert not any(variant in direct_norm for variant in variants), (
                f"Unexpected forbidden substring for {check_id} direct output: {forbidden}"
            )
            assert not any(variant in mcp_norm for variant in variants), (
                f"Unexpected forbidden substring for {check_id}: {forbidden}"
            )
        results.append(
            {
                "case_id": check_id,
                "tool": tool_name,
                "workspace": workspace_name or "global",
                "status": "checked",
            }
        )
    return results


async def _run_all_parity_checks(
    checks: list[dict], cases: list[dict], session_id: str
) -> list[dict]:
    tier_results = await _run_direct_tier1_checks(checks, session_id)
    case_results = await _run_direct_parity_cases(cases, session_id)
    return [*tier_results, *case_results]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case-id", action="append", default=[], help="Parity case id(s) to run"
    )
    args = parser.parse_args()

    status, _, _ = _request(HEALTH_URL)
    assert status == 200, "brain server health check failed"

    case_ids = args.case_id or DEFAULT_CASE_IDS
    cases = _load_cases(case_ids)
    session_id = _initialize_session()
    tool_names = _mcp_list_tools(session_id)
    for expected_tool in (
        "get_indexing_health",
        "get_symbol_context",
        "search_codebase",
        "get_directory_snapshot",
        "get_flow_summary",
        "get_apple_build_summary",
        "get_backend_flow_summary",
        "get_repo_dependency_summary",
        "get_call_chain",
        "find_references",
        "trace_graph_provenance",
        "trace_symbol_cross_project",
        "get_related_files",
        "get_code_importance",
        "get_code_communities",
        "get_symbol_imports_overview",
        "get_symbol_exports_summary",
        "visualize_subgraph",
        "get_mcp_tool_catalog",
        "cleanup_stale_shadow_graph",
        "git_summary",
        "list_documentation_sources",
        "search_documentation",
        "list_memories",
        "search_memory",
        "rerank_retrieval_results",
        "analyze_duplicate_results",
        "trace_code_ranking",
        "describe_file",
        "extract_function_body",
        "extract_class_interface",
        "find_symbol_usages",
        "get_changed_symbols",
        "get_test_coverage_for",
    ):
        assert expected_tool in tool_names, (
            f"Expected MCP tool '{expected_tool}' was not listed"
        )

    try:
        results = asyncio.run(
            _run_all_parity_checks(DEFAULT_DIRECT_PARITY_CHECKS, cases, session_id)
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

    print("MCP tool parity smoke check passed")
    print(f"- session id returned: {session_id}")
    checked = [item for item in results if item.get("status") != "skipped"]
    skipped = [item for item in results if item.get("status") == "skipped"]
    print(f"- cases checked: {', '.join(item['case_id'] for item in checked)}")
    if skipped:
        skipped_summary = ", ".join(
            f"{item['case_id']} ({item.get('reason') or 'skipped'})" for item in skipped
        )
        print(f"- cases skipped: {skipped_summary}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"MCP tool parity smoke check failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
