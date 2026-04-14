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
    def test_get_test_coverage_for_uses_semantic_test_chunk_fallback(self):
        memory_store = FakeMemoryStore(
            [("tests/test_workspace_registry.py", 3), ("spec/workspace_registry_spec.py", 1)]
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
        self.assertIn("**Files changed (file-level only):**", output)
        self.assertIn("src/misc.ts", output)


if __name__ == "__main__":
    unittest.main()
