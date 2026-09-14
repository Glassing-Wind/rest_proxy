import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "brain" / "graph" / "utility.py"


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeDriver:
    def session(self, database=None):
        return FakeSession()


def load_module():
    spec = importlib.util.spec_from_file_location("graph_utility_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    helpers_mod = types.ModuleType("_helpers")
    helpers_mod.get_project_id = lambda workspace_id: "proj123"
    helpers_mod.get_workspace_path = lambda workspace_id: "/tmp/repo"

    graph_core_mod = types.ModuleType("tools.brain.graph.core")
    graph_core_mod._summarize_batches = lambda event, limit: (1, 2, 3)
    graph_core_mod.get_last_graph_build_metric = lambda: {"project_path": "/tmp/repo", "elapsed_ms": 99}

    async def _execute_read(*args, **kwargs):
        return []

    graph_core_mod._execute_read = _execute_read

    tools_pkg = types.ModuleType("tools")
    tools_pkg.__path__ = []
    brain_pkg = types.ModuleType("tools.brain")
    brain_pkg.__path__ = []
    graph_pkg = types.ModuleType("tools.brain.graph")
    graph_pkg.__path__ = []

    with mock.patch.dict(
        sys.modules,
        {
            "_helpers": helpers_mod,
            "tools": tools_pkg,
            "tools.brain": brain_pkg,
            "tools.brain.graph": graph_pkg,
            "tools.brain.graph.core": graph_core_mod,
        },
    ):
        spec.loader.exec_module(module)
    return module


class GraphUtilityTests(unittest.TestCase):
    def setUp(self):
        self.module = load_module()

    def test_topology_summary_formats_rows(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_topology_summary":
                return [{"fp": "src/a.py", "inbound": 2, "outbound": 3}]
            if op == "utility_cargo_schema_labels":
                return [{"labels": []}]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_topology_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/repo",
                    limit=10,
                )
            )
        self.assertIn("src/a.py", output)
        self.assertIn("2 incoming, 3 outgoing", output)

    def test_topology_summary_includes_cargo_crate_context(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_topology_summary":
                return [{"fp": "crates/api/src/lib.rs", "inbound": 4, "outbound": 5}]
            if op == "utility_cargo_schema_labels":
                return [{"labels": ["CargoCrate"]}]
            if op == "utility_cargo_crates":
                return [{"crate": "api", "crate_name": "api", "manifest_path": "crates/api/Cargo.toml"}]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_topology_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/rustws",
                    limit=10,
                )
            )
        self.assertIn("Crate: api", output)
        self.assertIn("[crate:api]", output)

    def test_topology_summary_prefers_non_test_files(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_topology_summary":
                return [
                    {"fp": "tests/services.test.ts", "inbound": 0, "outbound": 11},
                    {"fp": "src/api/routes.ts", "inbound": 2, "outbound": 34},
                ]
            if op == "utility_cargo_schema_labels":
                return [{"labels": []}]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_topology_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/repo",
                    limit=10,
                )
            )
        self.assertLess(output.find("src/api/routes.ts"), output.find("tests/services.test.ts"))

    def test_topology_summary_skips_test_path_fallback_when_file_roles_are_present(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_topology_summary":
                return [
                    {"fp": "tests/services.test.ts", "file_roles": [], "inbound": 0, "outbound": 11},
                    {"fp": "src/api/routes.ts", "file_roles": None, "inbound": 2, "outbound": 34},
                ]
            if op == "utility_cargo_schema_labels":
                return [{"labels": []}]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_topology_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/repo",
                    limit=10,
                )
            )
        self.assertLess(output.find("tests/services.test.ts"), output.find("src/api/routes.ts"))

    def test_topology_summary_demotes_docs_surface_when_roles_are_present(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_topology_summary":
                return [
                    {"fp": "docs/architecture.md", "file_roles": ["docs_surface"], "inbound": 0, "outbound": 11},
                    {"fp": "src/api/routes.ts", "file_roles": [], "inbound": 2, "outbound": 34},
                ]
            if op == "utility_cargo_schema_labels":
                return [{"labels": []}]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_topology_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/repo",
                    limit=10,
                )
            )
        self.assertLess(output.find("src/api/routes.ts"), output.find("docs/architecture.md"))

    def test_topology_summary_legacy_docs_path_fallback_still_applies_when_roles_missing(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_topology_summary":
                return [
                    {"fp": "docs/architecture.md", "file_roles": None, "inbound": 0, "outbound": 11},
                    {"fp": "src/api/routes.ts", "file_roles": None, "inbound": 2, "outbound": 34},
                ]
            if op == "utility_cargo_schema_labels":
                return [{"labels": []}]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_topology_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/repo",
                    limit=10,
                )
            )
        self.assertLess(output.find("src/api/routes.ts"), output.find("docs/architecture.md"))

    def test_topology_summary_legacy_benchmark_path_fallback_still_applies_when_roles_missing(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_topology_summary":
                return [
                    {"fp": "benchmarks/routes_benchmark.ts", "file_roles": None, "inbound": 0, "outbound": 11},
                    {"fp": "src/api/routes.ts", "file_roles": None, "inbound": 2, "outbound": 34},
                ]
            if op == "utility_cargo_schema_labels":
                return [{"labels": []}]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_topology_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/repo",
                    limit=10,
                )
            )
        self.assertLess(output.find("src/api/routes.ts"), output.find("benchmarks/routes_benchmark.ts"))

    def test_heuristic_flow_summary_includes_cargo_crate_context(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_heuristic_flow_summary":
                return [
                    {
                        "ui": "apps/web/src/pages/index.tsx",
                        "api": "crates/api/src/routes.rs",
                        "svc": "crates/core/src/service.rs",
                        "model": "crates/core/src/model.rs",
                    }
                ]
            if op == "utility_cargo_schema_labels":
                return [{"labels": ["CargoCrate"]}]
            if op == "utility_cargo_crates":
                return [
                    {"crate": "api", "crate_name": "api", "manifest_path": "crates/api/Cargo.toml"},
                    {"crate": "core", "crate_name": "core", "manifest_path": "crates/core/Cargo.toml"},
                ]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_heuristic_flow_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/rustws",
                    limit=10,
                    as_table=False,
                )
            )
        self.assertIn("Crate: api", output)
        self.assertIn("[api_crate=api, service_crate=core]", output)
        self.assertIn("crates/api/src/routes.rs", output)

    def test_heuristic_flow_summary_falls_back_to_cargo_dependencies(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_heuristic_flow_summary":
                return []
            if op == "utility_cargo_schema_labels":
                return [{"labels": ["CargoCrate"]}]
            if op == "utility_cargo_crates":
                return [
                    {"crate": "ts-pack-index", "crate_name": "ts-pack-index", "manifest_path": "crates/ts-pack-index/Cargo.toml"},
                    {"crate": "tree-sitter-language-pack", "crate_name": "tree-sitter-language-pack", "manifest_path": "crates/ts-pack-core/Cargo.toml"},
                ]
            if op == "utility_cargo_dependency_rows":
                return [
                    {
                        "src_crate": "ts-pack-index",
                        "src_manifest": "crates/ts-pack-index/Cargo.toml",
                        "dep_crate": "tree-sitter-language-pack",
                        "dep_manifest": "crates/ts-pack-core/Cargo.toml",
                    }
                ]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_heuristic_flow_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/rustws",
                    limit=10,
                    as_table=False,
                )
            )
        self.assertIn("Cargo crate dependencies", output)
        self.assertIn("Crate: ts-pack-index", output)
        self.assertIn("ts-pack-index -> tree-sitter-language-pack", output)

    def test_heuristic_flow_summary_uses_file_roles_before_test_path_fallback(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_heuristic_flow_summary":
                self.assertNotIn("CONTAINS 'test'", query)
                self.assertNotIn("CONTAINS 'spec'", query)
                return [
                    {
                        "ui": "tests/ui.test.tsx",
                        "ui_roles": [],
                        "api": "src/api/routes.ts",
                        "api_roles": None,
                        "svc": "src/service/core.ts",
                        "svc_roles": None,
                        "model": None,
                        "model_roles": None,
                    }
                ]
            if op == "utility_cargo_schema_labels":
                return [{"labels": []}]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_heuristic_flow_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/repo",
                    limit=10,
                    as_table=False,
                )
            )
        self.assertIn("tests/ui.test.tsx -> src/api/routes.ts -> src/service/core.ts", output)

    def test_heuristic_flow_summary_legacy_test_path_fallback_still_applies_when_roles_missing(self):
        async def fake_execute_read(session, query, **kwargs):
            op = kwargs.get("op")
            if op == "get_heuristic_flow_summary":
                return [
                    {
                        "ui": "tests/ui.test.tsx",
                        "ui_roles": None,
                        "api": "src/api/routes.ts",
                        "api_roles": None,
                        "svc": "src/service/core.ts",
                        "svc_roles": None,
                        "model": None,
                        "model_roles": None,
                    }
                ]
            if op == "utility_cargo_schema_labels":
                return [{"labels": []}]
            return []

        with mock.patch.object(self.module.graph_core, "_execute_read", side_effect=fake_execute_read):
            output = asyncio.run(
                self.module.get_heuristic_flow_summary_impl(
                    driver=FakeDriver(),
                    neo4j_db="neo4j",
                    workspace_id="/tmp/repo",
                    limit=10,
                    as_table=False,
                )
            )
        self.assertEqual(output, "No heuristic paths found.")


if __name__ == "__main__":
    unittest.main()
