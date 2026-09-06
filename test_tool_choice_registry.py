import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
GOLDENS_PATH = os.path.join(REPO_ROOT, "benchmarks", "tool_choice_goldens.json")
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
    fastmcp_mod.Context = type("Context", (), {})
    sys.modules["mcp"] = mcp_pkg
    sys.modules["mcp.server"] = server_pkg
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod


def _install_runtime_stubs() -> None:
    if "neo4j" not in sys.modules:
        neo4j_mod = types.ModuleType("neo4j")

        class _AsyncGraphDatabase:
            @staticmethod
            def driver(*args, **kwargs):
                return None

        def unit_of_work(timeout=None, metadata=None):
            def decorator(fn):
                return fn

            return decorator

        neo4j_mod.AsyncGraphDatabase = _AsyncGraphDatabase
        neo4j_mod.unit_of_work = unit_of_work
        sys.modules["neo4j"] = neo4j_mod
    if "proxy" not in sys.modules:
        proxy_pkg = types.ModuleType("proxy")
        proxy_pkg.__path__ = []
        sys.modules["proxy"] = proxy_pkg
    if "proxy.logging" not in sys.modules:
        proxy_logging = types.ModuleType("proxy.logging")
        proxy_logging.debug_log = lambda *args, **kwargs: None
        sys.modules["proxy.logging"] = proxy_logging
    if "dotenv" not in sys.modules:
        dotenv_mod = types.ModuleType("dotenv")
        dotenv_mod.load_dotenv = lambda *args, **kwargs: False
        dotenv_mod.dotenv_values = lambda *args, **kwargs: {}
        sys.modules["dotenv"] = dotenv_mod


def _build_tool_registry() -> FakeMCP:
    _install_mcp_stub()
    _install_runtime_stubs()
    from tools.brain import documentation as documentation_tools
    from tools.brain import memory as memory_tools
    from tools.brain import tool_catalog
    from tools.brain.code_intel import core as code_intel_core
    from tools.brain.graph import tools as graph_tools
    from tools.brain.search import graph_query as graph_query_tools
    from tools.brain.search import cross_project as cross_project_tools
    from tools.brain.search import semantic as semantic_tools
    from tools.brain.search import tools as search_tools
    from tools.hands import dev as dev_tools
    from tools.hands import indexing as indexing_tools

    mcp = FakeMCP()
    with mock.patch.dict(os.environ, {"LM_PROXY_GRAPH_ENABLED": "0"}, clear=False):
        tool_catalog.register(mcp)
        code_intel_core.register(mcp)
        graph_tools.register(mcp)
        graph_query_tools.register(mcp)
        cross_project_tools.register(mcp)
        semantic_tools.register(mcp)
        search_tools.register(mcp)
        documentation_tools.register(mcp)
        memory_tools.register(mcp)
        dev_tools.register(mcp)
        indexing_tools.register(mcp)
    return mcp


class ToolChoiceRegistryTests(unittest.TestCase):
    def test_registered_tools_have_catalog_entries(self):
        from tools.brain.tool_catalog import TOOL_CATALOG

        registry = _build_tool_registry()
        registered = set(registry.tools)
        missing = sorted(registered - set(TOOL_CATALOG))

        self.assertEqual(
            [],
            missing,
            msg="Registered tools without tool-catalog entries: " + ", ".join(missing),
        )

    def test_tool_catalog_entries_are_actionable(self):
        from tools.brain.tool_catalog import TOOL_CATALOG

        valid_tiers = {
            "primary",
            "secondary",
            "docs",
            "support",
            "dev",
            "memory",
            "operational",
            "experimental",
            "admin",
        }
        for name, entry in TOOL_CATALOG.items():
            self.assertIn(entry.tier, valid_tiers, name)
            self.assertTrue(entry.workflow.strip(), name)
            self.assertTrue(entry.reach_for_when.strip(), name)
            self.assertNotIn("TODO", entry.reach_for_when, name)

    def test_tool_catalog_renderer_filters_by_intent(self):
        from tools.brain.tool_catalog import render_tool_catalog

        output = render_tool_catalog(intent="symbol", limit=10)
        self.assertIn("get_symbol_context", output)
        self.assertIn("find_references", output)
        self.assertNotIn("delete_documentation", output)

    def test_tool_catalog_renderer_handles_natural_intents(self):
        from tools.brain.tool_catalog import render_tool_catalog

        docs_output = render_tool_catalog(intent="learn library docs", limit=10)
        self.assertIn("search_documentation", docs_output)
        self.assertIn("research_documentation", docs_output)
        self.assertLess(
            docs_output.find("`search_documentation`"),
            docs_output.find("`research_documentation`"),
        )

        memory_output = render_tool_catalog(intent="remember repo fact", limit=10)
        self.assertIn("add_memory", memory_output)

        memory_review_output = render_tool_catalog(intent="review memories", limit=10)
        self.assertIn("list_memories", memory_review_output)

        precommit_output = render_tool_catalog(intent="precommit", limit=10)
        self.assertIn("git_summary", precommit_output)
        self.assertNotIn("trace_code_ranking", precommit_output)

        coverage_output = render_tool_catalog(intent="test coverage", limit=10)
        self.assertIn("get_test_coverage_for", coverage_output)

        docs_inventory_output = render_tool_catalog(
            intent="which docs are indexed", limit=5
        )
        self.assertIn("list_documentation_sources", docs_inventory_output)
        self.assertLess(
            docs_inventory_output.find("`list_documentation_sources`"),
            docs_inventory_output.find("`search_documentation`"),
        )

    def test_tool_catalog_renderer_handles_product_shape_intents(self):
        from tools.brain.tool_catalog import render_tool_catalog

        architecture_output = render_tool_catalog(
            intent="repo architecture onboarding", limit=5
        )
        self.assertIn("get_project_overview", architecture_output)
        self.assertNotIn("get_code_importance", architecture_output)
        self.assertNotIn("get_code_communities", architecture_output)

        blast_radius_output = render_tool_catalog(intent="blast radius", limit=5)
        self.assertIn("get_code_importance", blast_radius_output)
        self.assertIn("Prefer after: get_project_overview", blast_radius_output)

        cluster_output = render_tool_catalog(
            intent="clustered repo architecture", limit=5
        )
        self.assertIn("get_code_communities", cluster_output)
        self.assertIn("Prefer after: get_project_overview", cluster_output)

        definition_output = render_tool_catalog(
            intent="where is this symbol defined", limit=5
        )
        self.assertIn("find_definitions", definition_output)

    def test_tool_catalog_renderer_handles_debug_and_review_intents(self):
        from tools.brain.tool_catalog import render_tool_catalog

        jump_output = render_tool_catalog(intent="jump to definition", limit=5)
        self.assertIn("find_definitions", jump_output)

        rank_output = render_tool_catalog(
            intent="why did search rank this result", limit=5
        )
        self.assertIn("trace_code_ranking", rank_output)
        self.assertIn("analyze_duplicate_results", rank_output)

        duplicate_output = render_tool_catalog(intent="duplicate search results", limit=5)
        self.assertIn("analyze_duplicate_results", duplicate_output)
        self.assertIn("rerank_retrieval_results", duplicate_output)

        change_review_output = render_tool_catalog(
            intent="review changed code before commit", limit=5
        )
        self.assertIn("get_changed_symbols", change_review_output)

        code_search_output = render_tool_catalog(intent="code search", limit=5)
        self.assertLess(
            code_search_output.find("`search_codebase`"),
            code_search_output.find("`grep_codebase`"),
        )

    def test_tool_catalog_renderer_handles_flow_intents(self):
        from tools.brain.tool_catalog import render_tool_catalog

        full_stack_output = render_tool_catalog(
            intent="ui api service db flow", limit=5
        )
        self.assertIn("get_app_flow_summary", full_stack_output)

        frontend_output = render_tool_catalog(
            intent="frontend backend database path", limit=5
        )
        self.assertIn("get_app_flow_summary", frontend_output)

        application_output = render_tool_catalog(intent="application flow", limit=5)
        self.assertIn("get_flow_summary", application_output)

        backend_output = render_tool_catalog(
            intent="how does a request reach the database", limit=5
        )
        self.assertIn("get_backend_flow_summary", backend_output)
        self.assertNotIn("get_app_flow_summary", backend_output)

    def test_tool_catalog_renderer_handles_route_handler_intents(self):
        from tools.brain.tool_catalog import render_tool_catalog

        for intent in (
            "where is this route handled",
            "request handler route lookup",
            "api route implementation",
            "spring controller endpoint",
            "gin route handler",
            "axum route handler",
        ):
            with self.subTest(intent=intent):
                output = render_tool_catalog(intent=intent, limit=5)
                self.assertIn("search_codebase", output)

    def test_tool_choice_goldens_reference_registered_tools(self):
        payload = json.loads(Path(GOLDENS_PATH).read_text(encoding="utf-8"))
        registry = _build_tool_registry()
        registered = set(registry.tools)

        missing = []
        for case in payload["cases"]:
            for key in ("preferred_tools", "acceptable_fallbacks"):
                for tool_name in case.get(key, []):
                    if tool_name not in registered:
                        missing.append((case["id"], key, tool_name))

        self.assertEqual(
            [],
            missing,
            msg="Missing registered tools in tool-choice goldens: "
            + ", ".join(
                f"{case_id}:{key}:{tool_name}" for case_id, key, tool_name in missing
            ),
        )


if __name__ == "__main__":
    unittest.main()
