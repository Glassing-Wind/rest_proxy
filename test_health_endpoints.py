"""Offline health route/lifespan acceptance; no .env, services or model calls.

Run with .venv/bin/python test_health_endpoints.py. Imports and application state
use temporary configuration/runtime directories, and readiness probes are injected.
"""

from contextlib import ExitStack, contextmanager
import importlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


class HealthEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = ExitStack()
        cls.addClassCleanup(cls.resources.close)
        temporary = cls.resources.enter_context(tempfile.TemporaryDirectory(prefix="health-endpoints-"))
        cls.directory = Path(temporary)
        cls.resources.enter_context(patch.dict(os.environ, {
            "PYTHON_DOTENV_DISABLED": "1",
            "LM_PROXY_MEMORY_ENABLED": "0",
            "LM_PROXY_MEMORY_MODE": "off",
            "LM_PROXY_MEMORY_ENABLE_PERSISTENCE": "0",
            "LM_PROXY_MEMORY_ENABLE_REDIS": "0",
            "LM_PROXY_GRAPH_ENABLED": "0",
            "LM_PROXY_WATCHER_ENABLED": "0",
            "LM_PROXY_EMBEDDED_WATCH_ENABLED": "0",
            "LM_PROXY_RUNTIME_DIR": str(cls.directory / "runtime"),
            "LM_PROXY_CONFIG_DIR": str(cls.directory / "config"),
            "LM_PROXY_DEBUG": "false",
        }, clear=True))
        # load_env also considers shared infrastructure files outside the repo.
        cls.resources.enter_context(patch("graphrag_core.config.load_env"))
        cls.resources.enter_context(patch("resource.setrlimit"))
        cls.brain = importlib.import_module("brain_server")
        cls.proxy = importlib.import_module("proxy.app")
        cls.indexing = importlib.import_module("tools.hands.indexing")
        cls.jobs = importlib.import_module("_jobs")
        cls.config = importlib.import_module("proxy.config")
        cls.memory_store = importlib.import_module("memory.store")
        cls.embedded_runtime = importlib.import_module("memory.embedded_runtime")
        cls.readiness_module = importlib.import_module("graphrag_core.readiness")
        cls.applications = (("brain", cls.brain, ()), ("proxy", cls.proxy, ("inference",)))

    @contextmanager
    def isolated_application(self, name, module, *, required=None, fail=False):
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        monitors, defaults, cleanup_order = [], [], []
        probes = {dependency: AsyncMock() for dependency in self.readiness_module.DEPENDENCIES}
        if fail:
            probes["neo4j"].side_effect = self.readiness_module.ProbeFailure("not_configured")
        environment = {} if required is None else {"LM_PROXY_READY_REQUIRED": required}

        def monitor_factory(*, default_required=()):
            defaults.append(default_required)
            monitor = self.readiness_module.ReadinessMonitor(
                default_required=default_required, probes=probes, environ=environment)
            monitors.append(monitor)
            return monitor

        def cleanup_hook(label):
            async def cleanup():
                self.assertFalse(module.app.state.readiness.transport_active)
                cleanup_order.append(label)
            return cleanup

        with ExitStack() as resources:
            resources.enter_context(patch.object(module, "ReadinessMonitor", side_effect=monitor_factory))
            resources.enter_context(patch.object(module.app.state, "readiness", None, create=True))
            resources.enter_context(patch.object(self.config, "_memory_store", None))
            if name == "brain":
                resources.enter_context(patch.object(self.jobs, "register_main_loop"))
                resources.enter_context(patch.object(self.indexing, "load_watched_config", AsyncMock()))
                resources.enter_context(patch.object(self.indexing, "start_watcher", AsyncMock()))
                resources.enter_context(patch.object(
                    self.indexing, "stop_watcher", AsyncMock(side_effect=cleanup_hook("watcher"))))
                resources.enter_context(patch.object(
                    self.embedded_runtime, "close_embedded_runtime",
                    AsyncMock(side_effect=cleanup_hook("embedded"))))
                resources.enter_context(patch.object(
                    self.memory_store, "close_pool", AsyncMock(side_effect=cleanup_hook("memory"))))
                # The SDK manager is single-use. A fresh manager represents the
                # process restart while retaining the real MCP run context.
                manager = StreamableHTTPSessionManager(app=module.mcp._mcp_server)
                resources.enter_context(patch.object(module.mcp, "_session_manager", manager))
            else:
                store = SimpleNamespace(open_pool=AsyncMock(), close_pool=AsyncMock(
                    side_effect=cleanup_hook("memory")))
                resources.enter_context(patch.object(module, "_MEMORY_ENABLED", True))
                resources.enter_context(patch.object(module, "_MEMORY_PERSIST_ENABLED", True))
                resources.enter_context(patch.object(module, "_memory_store", store))
                resources.enter_context(patch.object(module, "_RUNTIME_DIR", self.directory / "runtime"))
                resources.enter_context(patch.object(
                    module, "_PROXY_PID_FILE", self.directory / "runtime" / "proxy.pid"))
            yield SimpleNamespace(monitors=monitors, defaults=defaults,
                                  probes=probes, cleanup_order=cleanup_order)

    def assert_live_without_probes(self, client, fixture, application):
        response = client.get("/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"live": True, "application": application})
        self.assertTrue(all(probe.await_count == 0 for probe in fixture.probes.values()))

    def assert_ready_and_details(self, client, *, status):
        response = client.get("/ready")
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.headers["cache-control"], "no-store")
        details = client.get("/health/details")
        self.assertEqual(details.status_code, 200)
        self.assertEqual(details.headers["cache-control"], "no-store")
        readiness = response.json()
        diagnostics = details.json()
        self.assertNotIn("telemetry", readiness)
        telemetry = diagnostics.pop("telemetry")
        self.assertEqual(set(telemetry), {"enabled", "state", "exporter", "delivery"})
        self.assertIs(telemetry["enabled"], False)
        self.assertEqual(telemetry["state"], "disabled")
        self.assertEqual(telemetry["exporter"], "none")
        self.assertIsNone(telemetry["delivery"])
        self.assertEqual(readiness, diagnostics)
        self.assertEqual(response.json()["ready"], status == 200)
        return response.json()

    def test_liveness_and_legacy_health_without_lifespan(self):
        from fastapi.testclient import TestClient

        for name, module, expected_default in self.applications:
            with self.subTest(application=name), self.isolated_application(name, module) as fixture:
                # Deliberately do not enter TestClient: readiness must not infer
                # initialized transport from a reachable request handler.
                client = TestClient(module.app)
                try:
                    self.assert_live_without_probes(client, fixture, "mcp" if name == "brain" else "inference")
                    self.assertEqual(fixture.monitors, [])
                    payload = self.assert_ready_and_details(client, status=503)
                    self.assertEqual(payload["transport"], {"status": "inactive"})
                    self.assertEqual(payload["required"], list(expected_default))
                    self.assertTrue(all(probe.await_count == 0 for probe in fixture.probes.values()))
                    legacy = client.get("/health")
                    self.assertEqual(legacy.status_code, 200)
                    self.assertNotIn("ready", legacy.json())
                    if name == "brain":
                        root = client.get("/")
                        self.assertEqual(root.status_code, 200)
                        self.assertEqual(root.json()["status"], "online")
                        self.assertEqual(root.json()["server"], legacy.json()["server"])
                        self.assertEqual(root.json()["transport_path"], "/mcp")
                    else:
                        self.assertTrue(legacy.json()["ok"])
                finally:
                    client.close()

    def test_active_default_readiness_and_new_monitor_after_restart(self):
        from fastapi.testclient import TestClient

        for name, module, expected_default in self.applications:
            previous = None
            for _ in range(2):
                with self.subTest(application=name), self.isolated_application(name, module) as fixture:
                    with TestClient(module.app) as client:
                        self.assertEqual(fixture.defaults, [expected_default])
                        monitor = fixture.monitors[0]
                        self.assertIsNot(monitor, previous)
                        self.assert_live_without_probes(
                            client, fixture, "mcp" if name == "brain" else "inference")
                        payload = self.assert_ready_and_details(client, status=200)
                        self.assertEqual(payload["transport"], {"status": "active"})
                        self.assertEqual(payload["required"], list(expected_default))
                        for dependency, probe in fixture.probes.items():
                            self.assertEqual(probe.await_count, int(dependency in expected_default))
                    self.assertFalse(monitor.transport_active)
                    self.assertEqual(fixture.cleanup_order,
                                     ["watcher", "embedded", "memory"] if name == "brain" else ["memory"])
                    previous = monitor

    def test_declared_dependency_failure_is_503_and_details_stays_200(self):
        from fastapi.testclient import TestClient

        for name, module, expected_default in self.applications:
            with self.subTest(application=name), self.isolated_application(
                    name, module, required="neo4j", fail=True) as fixture:
                with TestClient(module.app) as client:
                    self.assertEqual(fixture.defaults, [expected_default])
                    self.assert_live_without_probes(client, fixture, "mcp" if name == "brain" else "inference")
                    payload = self.assert_ready_and_details(client, status=503)
                    self.assertEqual(payload["required"], ["neo4j"])
                    self.assertEqual(payload["transport"], {"status": "active"})
                    self.assertEqual(payload["dependencies"]["neo4j"]["status"], "unready")
                    self.assertEqual(payload["dependencies"]["neo4j"]["reason"], "not_configured")
                    self.assertEqual(fixture.probes["neo4j"].await_count, 1)
                self.assertFalse(fixture.monitors[0].transport_active)

    def test_brain_health_routes_precede_the_mcp_catchall(self):
        from starlette.routing import Mount

        routes = self.brain.app.routes
        mount = next(index for index, route in enumerate(routes) if isinstance(route, Mount))
        for path in ("/", "/health", "/live", "/ready", "/health/details"):
            self.assertLess(next(index for index, route in enumerate(routes) if route.path == path), mount)


if __name__ == "__main__":
    unittest.main()
