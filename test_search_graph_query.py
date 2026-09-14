import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "search" / "graph_query.py"


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


def load_module():
    spec = importlib.util.spec_from_file_location("tools.brain.search.graph_query", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda workspace_id: "proj123"
    helpers_mod.get_workspace_path = lambda workspace_id: workspace_id

    search_core = types.ModuleType("tools.brain.search.core")
    search_core._execute_read = mock.AsyncMock(return_value=[])

    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    search_pkg = types.ModuleType("tools.brain.search")
    search_pkg.__path__ = []
    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = FakeMCP

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "tools": tools_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.search": search_pkg,
            "tools.brain.search.core": search_core,
            "mcp.server.fastmcp": mcp_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module, search_core


class GraphQueryTests(unittest.TestCase):
    def setUp(self):
        self.module, self.search_core = load_module()
        self.mcp = FakeMCP()
        self.module.register(self.mcp)
        self.graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

        async def _require_driver():
            return FakeDriver()

        self.graph_bootstrap_mod.require_driver = _require_driver
        self.graph_bootstrap_mod._NEO4J_DB = "neo4j"

    def test_find_definitions_prefers_repo_owned_over_generated_and_test_matches(self):
        rows = [
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "packages/sdk/js/src/v2/gen/types.gen.ts",
                "line": 1,
                "type": "TypeAlias",
                "file_roles": None,
            },
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "packages/opencode/src/config/config.ts",
                "line": 8,
                "type": "Class",
                "file_roles": None,
            },
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "packages/opencode/test/config/config.test.ts",
                "line": 5,
                "type": "Class",
                "file_roles": None,
            },
            {
                "project_id": "profilequery123",
                "project_path": "",
                "file": "scratch/query.ts",
                "line": 4,
                "type": "Function",
                "file_roles": None,
            },
        ]

        async def fake_execute_read(session, cypher, **kwargs):
            return rows

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["find_definitions"]("Config"))

        self.assertIn("fallback exact-name lookup", output)
        self.assertIn(
            "Prefer `get_symbol_context`, `list_symbol_matches`, or `search_codebase`",
            output,
        )
        self.assertIn("Best candidate definitions:", output)
        lines = [line for line in output.splitlines() if line.startswith("- [")]
        self.assertGreaterEqual(len(lines), 3)
        self.assertIn("packages/opencode/src/config/config.ts:8", lines[0])
        self.assertIn("packages/opencode/test/config/config.test.ts:5", lines[1])
        self.assertIn("packages/sdk/js/src/v2/gen/types.gen.ts:1", lines[2])
        self.assertNotIn("profilequery123", output)

    def test_find_definitions_excludes_shadow_projects_even_with_project_paths(self):
        rows = [
            {"project_id": pid, "project_path": path, "file": file,
             "line": 10, "type": "Function", "file_roles": ["implementation_surface"]}
            for pid, path, file in [
                ("repo", "/workspace/repo", "src/main.py"),
                ("nested", "/workspace/repo/nested", "main.py"),
                ("repo::shadow::run1", None, "shadow_without_path.py"),
                ("repo::shadow::run2", "/workspace/repo", "shadow_with_path.py"),
            ]
        ]
        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.search_core, "_execute_read", return_value=rows):
                output = asyncio.run(self.mcp.tools["find_definitions"]("main"))
        self.assertIn("src/main.py:10", output)
        self.assertIn("Project: /workspace/repo/nested", output)
        self.assertNotIn("shadow", output)
        self.assertEqual(sum(line.startswith("- [") for line in output.splitlines()), 2)

    def test_find_definitions_with_only_shadow_matches_returns_not_found(self):
        rows = [{"project_id": "repo::shadow::run", "project_path": "/workspace/repo"}]
        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.search_core, "_execute_read", return_value=rows):
                output = asyncio.run(self.mcp.tools["find_definitions"]("main"))
        self.assertEqual(output, "Symbol 'main' not found in any indexed project.")

    def test_find_definitions_skips_test_path_penalty_when_file_roles_are_present(self):
        rows = [
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "packages/opencode/test/config/config.test.ts",
                "line": 5,
                "type": "Class",
                "file_roles": [],
            },
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "packages/sdk/js/src/v2/gen/types.gen.ts",
                "line": 1,
                "type": "TypeAlias",
                "file_roles": None,
            },
        ]

        async def fake_execute_read(session, cypher, **kwargs):
            return rows

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["find_definitions"]("Config"))

        lines = [line for line in output.splitlines() if line.startswith("- [")]
        self.assertIn("packages/opencode/test/config/config.test.ts:5", lines[0])
        self.assertIn("packages/sdk/js/src/v2/gen/types.gen.ts:1", lines[1])

    def test_find_definitions_legacy_test_path_penalty_still_applies_when_roles_missing(self):
        rows = [
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "packages/opencode/test/config/config.test.ts",
                "line": 5,
                "type": "Class",
                "file_roles": None,
            },
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "packages/opencode/src/config/config.ts",
                "line": 8,
                "type": "Class",
                "file_roles": [],
            },
        ]

        async def fake_execute_read(session, cypher, **kwargs):
            return rows

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["find_definitions"]("Config"))

        lines = [line for line in output.splitlines() if line.startswith("- [")]
        self.assertIn("packages/opencode/src/config/config.ts:8", lines[0])

    def test_find_definitions_demotes_docs_surface_when_roles_are_present(self):
        rows = [
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "docs/reference/config.md",
                "line": 12,
                "type": "Class",
                "file_roles": ["docs_surface"],
            },
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "packages/opencode/src/config/config.ts",
                "line": 8,
                "type": "Class",
                "file_roles": [],
            },
        ]

        async def fake_execute_read(session, cypher, **kwargs):
            return rows

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["find_definitions"]("Config"))

        lines = [line for line in output.splitlines() if line.startswith("- [")]
        self.assertIn("packages/opencode/src/config/config.ts:8", lines[0])
        self.assertIn("docs/reference/config.md:12", lines[1])

    def test_find_definitions_legacy_docs_path_penalty_still_applies_when_roles_missing(self):
        rows = [
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "docs/reference/config.md",
                "line": 12,
                "type": "Class",
                "file_roles": None,
            },
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "packages/opencode/src/config/config.ts",
                "line": 8,
                "type": "Class",
                "file_roles": None,
            },
        ]

        async def fake_execute_read(session, cypher, **kwargs):
            return rows

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["find_definitions"]("Config"))

        lines = [line for line in output.splitlines() if line.startswith("- [")]
        self.assertIn("packages/opencode/src/config/config.ts:8", lines[0])
        self.assertIn("docs/reference/config.md:12", lines[1])

    def test_find_definitions_legacy_benchmark_path_penalty_still_applies_when_roles_missing(self):
        rows = [
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "benchmarks/config_benchmark.ts",
                "line": 12,
                "type": "Class",
                "file_roles": None,
            },
            {
                "project_id": "opencode",
                "project_path": "/Users/michaelmarler/Projects/opencode",
                "file": "packages/opencode/src/config/config.ts",
                "line": 8,
                "type": "Class",
                "file_roles": None,
            },
        ]

        async def fake_execute_read(session, cypher, **kwargs):
            return rows

        with mock.patch.dict(sys.modules, {"graph_bootstrap": self.graph_bootstrap_mod}):
            with mock.patch.object(self.search_core, "_execute_read", side_effect=fake_execute_read):
                output = asyncio.run(self.mcp.tools["find_definitions"]("Config"))

        lines = [line for line in output.splitlines() if line.startswith("- [")]
        self.assertIn("packages/opencode/src/config/config.ts:8", lines[0])
        self.assertIn("benchmarks/config_benchmark.ts:12", lines[1])


if __name__ == "__main__":
    unittest.main()
