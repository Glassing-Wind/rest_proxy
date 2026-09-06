import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "hands" / "dev.py"


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params):
        self.executed.append((query, params))

    async def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor(self.rows)


class FakePool:
    def __init__(self, rows):
        self.rows = rows

    def connection(self):
        return FakeConnection(self.rows)


class FakeMemoryStore:
    def __init__(self, rows):
        self._pg_pool = FakePool(rows)

    async def open_pool(self):
        return None


class FakeGraphResult:
    async def data(self):
        return []


class FakeGraphTx:
    async def run(self, *args, **kwargs):
        return FakeGraphResult()


class FakeGraphSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute_read(self, fn):
        return await fn(FakeGraphTx())


class FakeGraphDriver:
    def session(self, database=None):
        return FakeGraphSession()


class FakeRowsGraphResult:
    def __init__(self, rows):
        self.rows = rows

    async def data(self):
        return list(self.rows)


class FakeRowsGraphTx:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return FakeRowsGraphResult(self.rows)


class FakeRowsGraphSession:
    def __init__(self, rows):
        self.tx = FakeRowsGraphTx(rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute_read(self, fn):
        return await fn(self.tx)


class FakeRowsGraphDriver:
    def __init__(self, rows):
        self.rows = rows
        self.session_obj = FakeRowsGraphSession(rows)

    def session(self, database=None):
        return self.session_obj


def load_module(memory_store):
    spec = importlib.util.spec_from_file_location("dev_tools_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_memory_modules = lambda: (memory_store, None, None, None, None)
    helpers_mod.get_project_id = lambda workspace_id: "proj123"
    helpers_mod.normalize_neo4j_path = lambda path: path.replace("\\", "/")
    helpers_mod.get_workspace_path = lambda workspace_id: "/tmp/repo"

    mcp_mod = types.ModuleType("mcp.server.fastmcp")
    mcp_mod.FastMCP = FakeMCP

    neo4j_mod = types.ModuleType("neo4j")
    neo4j_mod.unit_of_work = lambda *args, **kwargs: (lambda fn: fn)
    graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

    async def _require_driver():
        return FakeGraphDriver()

    graph_bootstrap_mod.require_driver = _require_driver
    graph_bootstrap_mod._NEO4J_DB = "neo4j"

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "mcp.server.fastmcp": mcp_mod,
            "neo4j": neo4j_mod,
            "graph_bootstrap": graph_bootstrap_mod,
        },
    ):
        spec.loader.exec_module(module)
    module._graph_bootstrap_mod = graph_bootstrap_mod
    return module


class DevToolsTests(unittest.TestCase):
    def test_lint_project_subset_applies_ruff_fixes_when_requested(self):
        memory_store = FakeMemoryStore([])
        module = load_module(memory_store)
        mcp = FakeMCP()
        module.register(mcp)

        def path_exists(path):
            return path == "/tmp/repo/src/app.py"

        with mock.patch.object(module.os.path, "exists", side_effect=path_exists), mock.patch.object(
            module.os.path, "isfile", return_value=False
        ), mock.patch(
            "shutil.which",
            side_effect=lambda name: "/usr/bin/ruff" if name == "ruff" else None,
        ), mock.patch(
            "subprocess.run",
            return_value=types.SimpleNamespace(stdout="Fixed 1 error.\n"),
        ) as run:
            output = asyncio.run(
                mcp.tools["lint_project_subset"](
                    "/tmp/repo", ["src/app.py"], fix=True
                )
            )

        self.assertEqual(
            run.call_args.args[0],
            ["/usr/bin/ruff", "check", "--fix", "/tmp/repo/src/app.py"],
        )
        self.assertIn("fixes applied where available", output)

    def test_extract_class_interface_includes_decorators_and_property_markers(self):
        memory_store = FakeMemoryStore([])
        module = load_module(memory_store)
        mcp = FakeMCP()
        module.register(mcp)
        source = """class Service:
    @classmethod
    def build(cls):
        return cls()

    @property
    def name(self):
        return \"service\"
"""
        ts_pack = types.ModuleType("tree_sitter_language_pack")
        ts_pack.detect_language = lambda path: "python"
        ts_pack.ProcessConfig = lambda lang: types.SimpleNamespace(language=lang)
        ts_pack.process = lambda code, config: {
            "structure": [
                {
                    "name": "Service",
                    "kind": "Class",
                    "span": {"start_line": 0, "end_line": 7},
                    "children": [
                        {
                            "name": "build",
                            "kind": "Method",
                            "signature": "build(cls)",
                            "span": {"start_line": 2, "end_line": 3},
                        },
                        {
                            "name": "name",
                            "kind": "Property",
                            "span": {"start_line": 6, "end_line": 7},
                        },
                    ],
                }
            ]
        }

        with mock.patch.object(module.os.path, "exists", return_value=True), mock.patch(
            "builtins.open", mock.mock_open(read_data=source)
        ), mock.patch.dict(sys.modules, {"tree_sitter_language_pack": ts_pack}):
            output = asyncio.run(
                mcp.tools["extract_class_interface"](
                    "/tmp/repo", "service.py", "Service"
                )
            )

        self.assertIn("@classmethod", output)
        self.assertIn("build(cls)  [method, L3]", output)
        self.assertIn("@property", output)
        self.assertIn("name  [property, L7]", output)

    def test_grep_codebase_groups_hits_by_file_and_applies_glob(self):
        memory_store = FakeMemoryStore([])
        module = load_module(memory_store)
        mcp = FakeMCP()
        module.register(mcp)

        def fake_subprocess_run(cmd, **kwargs):
            self.assertTrue(cmd[0].endswith("/rg") or cmd[0] == "rg")
            self.assertIn("--glob", cmd)
            self.assertIn("*.py", cmd)
            self.assertIn("Router", cmd)
            return types.SimpleNamespace(
                stdout="\n".join(
                    [
                        "src/api/router.py:10:class Router:",
                        "src/api/router.py:22:def buildRouter():",
                        "tests/test_router.py:5:from src.api.router import Router",
                    ]
                )
            )

        with mock.patch("subprocess.run", side_effect=fake_subprocess_run):
            output = asyncio.run(
                mcp.tools["grep_codebase"]("/tmp/repo", "Router", "*.py")
            )

        self.assertIn("## `Router` — 2 file(s)", output)
        self.assertIn("**src/api/router.py**", output)
        self.assertIn("L10: class Router:", output)
        self.assertIn("L22: def buildRouter():", output)
        self.assertIn("**tests/test_router.py**", output)

    def test_get_test_coverage_for_uses_semantic_test_chunk_fallback(self):
        memory_store = FakeMemoryStore(
            [
                ("tests/test_workspace_registry.py", {"file_roles": ["test_surface"]}),
                ("tests/test_workspace_registry.py", {"file_roles": ["test_surface"]}),
                ("tests/test_workspace_registry.py", {"file_roles": ["test_surface"]}),
                ("spec/workspace_registry_spec.py", None),
            ]
        )
        module = load_module(memory_store)
        mcp = FakeMCP()
        module.register(mcp)

        def fake_subprocess_run(cmd, **kwargs):
            return types.SimpleNamespace(stdout="")

        with mock.patch("subprocess.run", side_effect=fake_subprocess_run), mock.patch.dict(
            sys.modules, {"graph_bootstrap": module._graph_bootstrap_mod}
        ):
            output = asyncio.run(
                mcp.tools["get_test_coverage_for"]("/tmp/repo", "src/workspace_registry.py")
            )

        self.assertIn("tests/test_workspace_registry.py", output)
        self.assertIn("semantic test-chunk match (3)", output)

    def test_get_test_coverage_for_executes_graph_lookup_with_open_session(self):
        memory_store = FakeMemoryStore([])
        module = load_module(memory_store)
        mcp = FakeMCP()
        module.register(mcp)
        driver = FakeRowsGraphDriver(
            [{"tf": "tests/test_workspace_registry.py", "tf2": None}]
        )

        async def fake_require_driver():
            return driver

        module._graph_bootstrap_mod.require_driver = fake_require_driver
        with mock.patch(
            "subprocess.run", return_value=types.SimpleNamespace(stdout="")
        ), mock.patch.dict(
            sys.modules, {"graph_bootstrap": module._graph_bootstrap_mod}
        ):
            output = asyncio.run(
                mcp.tools["get_test_coverage_for"](
                    "/tmp/repo", "src/workspace_registry.py"
                )
            )

        self.assertIn("tests/test_workspace_registry.py", output)
        self.assertIn("imports this file", output)
        self.assertTrue(driver.session_obj.tx.calls)

    def test_get_test_coverage_for_skips_test_path_semantic_hit_when_roles_present_empty(self):
        memory_store = FakeMemoryStore(
            [("tests/test_workspace_registry.py", {"file_roles": []})]
        )
        module = load_module(memory_store)
        mcp = FakeMCP()
        module.register(mcp)

        def fake_subprocess_run(cmd, **kwargs):
            return types.SimpleNamespace(stdout="")

        with mock.patch("subprocess.run", side_effect=fake_subprocess_run), mock.patch.dict(
            sys.modules, {"graph_bootstrap": module._graph_bootstrap_mod}
        ):
            output = asyncio.run(
                mcp.tools["get_test_coverage_for"]("/tmp/repo", "src/workspace_registry.py")
            )

        self.assertIn("No test files found", output)

    def test_get_test_coverage_for_legacy_test_path_semantic_hit_still_applies_when_roles_missing(self):
        memory_store = FakeMemoryStore(
            [("tests/test_workspace_registry.py", None)]
        )
        module = load_module(memory_store)
        mcp = FakeMCP()
        module.register(mcp)

        def fake_subprocess_run(cmd, **kwargs):
            return types.SimpleNamespace(stdout="")

        with mock.patch("subprocess.run", side_effect=fake_subprocess_run), mock.patch.dict(
            sys.modules, {"graph_bootstrap": module._graph_bootstrap_mod}
        ):
            output = asyncio.run(
                mcp.tools["get_test_coverage_for"]("/tmp/repo", "src/workspace_registry.py")
            )

        self.assertIn("tests/test_workspace_registry.py", output)
        self.assertIn("semantic test-chunk match (1)", output)

    def test_get_test_coverage_for_routes_falls_back_to_route_test_match(self):
        memory_store = FakeMemoryStore([])
        module = load_module(memory_store)
        mcp = FakeMCP()
        module.register(mcp)

        def fake_subprocess_run(cmd, **kwargs):
            if cmd[:2] == ["find", "/tmp/repo"]:
                return types.SimpleNamespace(stdout="")
            if cmd and cmd[0] == "rg":
                if "financeAdminRoutes" in cmd:
                    return types.SimpleNamespace(stdout="")
                return types.SimpleNamespace(stdout="tests/routes.test.ts\n")
            return types.SimpleNamespace(stdout="")

        with mock.patch("subprocess.run", side_effect=fake_subprocess_run), mock.patch.dict(
            sys.modules, {"graph_bootstrap": module._graph_bootstrap_mod}
        ):
            output = asyncio.run(
                mcp.tools["get_test_coverage_for"]("/tmp/repo", "src/api/routes/financeAdminRoutes.ts")
            )

        self.assertIn("tests/routes.test.ts", output)
        self.assertIn("route test match", output)

    def test_get_test_coverage_for_prioritizes_same_package_and_caps_basename_noise(self):
        memory_store = FakeMemoryStore([])
        module = load_module(memory_store)
        mcp = FakeMCP()
        module.register(mcp)

        def fake_subprocess_run(cmd, **kwargs):
            if cmd[:2] == ["find", "/tmp/repo"]:
                return types.SimpleNamespace(stdout="/tmp/repo/packages/opencode/test/config/config.test.ts\n")
            if cmd and cmd[0] == "rg":
                return types.SimpleNamespace(
                    stdout="\n".join(
                        [
                            "packages/opencode/test/config/config.test.ts",
                            "packages/opencode/test/config/markdown.test.ts",
                            "packages/app/src/utils/persist.test.ts",
                            "packages/app/src/utils/server-health.test.ts",
                            "packages/ui/src/components/x.test.ts",
                            "packages/opencode/test/provider/provider.test.ts",
                            "packages/opencode/test/session/llm.test.ts",
                            "packages/desktop-electron/src/renderer/html.test.ts",
                            "packages/app/src/context/model-variant.test.ts",
                            "packages/opencode/test/project/vcs.test.ts",
                            "packages/opencode/test/snapshot/snapshot.test.ts",
                            "packages/app/src/pages/layout/helpers.test.ts",
                        ]
                    )
                )
            return types.SimpleNamespace(stdout="")

        with mock.patch("subprocess.run", side_effect=fake_subprocess_run), mock.patch.dict(
            sys.modules, {"graph_bootstrap": module._graph_bootstrap_mod}
        ):
            output = asyncio.run(
                mcp.tools["get_test_coverage_for"](
                    "/tmp/repo",
                    "packages/opencode/src/config/config.ts",
                )
            )

        lines = [line for line in output.splitlines() if line.startswith("- `")]
        self.assertIn("packages/opencode/test/config/config.test.ts", lines[0])
        self.assertLessEqual(len(lines), 11)

    def test_get_changed_symbols_detects_exported_const_arrow_and_typealias(self):
        memory_store = FakeMemoryStore([])
        module = load_module(memory_store)
        mcp = FakeMCP()
        module.register(mcp)

        diff = """diff --git a/src/api/routes.ts b/src/api/routes.ts
++/src/api/routes.ts
@@
+export const buildRouter = async () => {
+export type RouteContext = {
+export interface RouteHelpers {
diff --git a/src/misc.ts b/src/misc.ts
++/src/misc.ts
@@
+const unrelated = value
diff --git a/README.md b/README.md
++ b/README.md
@@
+Document the new routing behavior.
diff --git a/.env.example b/.env.example
++ b/.env.example
@@
+ROUTING_MODE=strict
"""

        def fake_subprocess_run(cmd, **kwargs):
            return types.SimpleNamespace(stdout=diff)

        with mock.patch("subprocess.run", side_effect=fake_subprocess_run):
            output = asyncio.run(
                mcp.tools["get_changed_symbols"]("/tmp/repo", "HEAD~1")
            )

        self.assertIn("`buildRouter`", output)
        self.assertIn("`RouteContext`", output)
        self.assertIn("`RouteHelpers`", output)
        self.assertIn(
            "**Changed source files without detected symbol definitions:**", output
        )
        self.assertIn("src/misc.ts", output)
        self.assertIn("**Changed non-code/support files:**", output)
        self.assertIn("README.md", output)
        self.assertIn(".env.example", output)
        self.assertLess(output.index("src/misc.ts"), output.index("README.md"))

    def test_extract_function_body_uses_graph_fallback_with_execute_read(self):
        memory_store = FakeMemoryStore([])
        module = load_module(memory_store)
        mcp = FakeMCP()
        module.register(mcp)

        source = """def unrelated():
    return None

def graph_only():
    return 42
"""
        ts_pack = types.ModuleType("tree_sitter_language_pack")
        ts_pack.detect_language = lambda path: "python"

        class FakeProcessConfig:
            @classmethod
            def all(cls, lang):
                return types.SimpleNamespace(language=lang)

        ts_pack.ProcessConfig = FakeProcessConfig
        ts_pack.process = lambda code, config: {"structure": []}
        driver = FakeRowsGraphDriver(
            [{"sl": 4, "el": 5, "labels": ["Function"]}]
        )

        async def fake_require_driver():
            return driver

        module._graph_bootstrap_mod.require_driver = fake_require_driver

        with mock.patch.object(module.os.path, "exists", return_value=True), mock.patch(
            "builtins.open", mock.mock_open(read_data=source)
        ), mock.patch.dict(
            sys.modules,
            {
                "tree_sitter_language_pack": ts_pack,
                "graph_bootstrap": module._graph_bootstrap_mod,
            },
        ):
            output = asyncio.run(
                mcp.tools["extract_function_body"](
                    "/tmp/repo", "src/service.py", "graph_only"
                )
            )

        self.assertIn("## `graph_only` (Function)  —  L4–5", output)
        self.assertIn("def graph_only():", output)
        self.assertTrue(driver.session_obj.tx.calls)


if __name__ == "__main__":
    unittest.main()
