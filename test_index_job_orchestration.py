import asyncio
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "_jobs.py"


class FakeConsumeResult:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def consume(self):
        return None

    async def data(self):
        return list(self.rows)


class FakeTx:
    def __init__(self):
        self.calls = []

    async def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if "RETURN\n                              p.struct_active_run_id AS struct_active_run_id" in cypher:
            return FakeConsumeResult(
                [
                    {
                        "struct_active_run_id": "struct-1",
                        "semantic_active_run_id": "sem-1",
                        "semantic_active_struct_run_id": "struct-1",
                        "struct_index_status": "done",
                        "semantic_index_status": "done",
                    }
                ]
            )
        return FakeConsumeResult()


class FakeSession:
    def __init__(self, tx):
        self.tx = tx

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute_write(self, fn):
        return await fn(self.tx)

    async def execute_read(self, fn):
        return await fn(self.tx)


class FakeDriver:
    def __init__(self, tx):
        self.tx = tx

    def session(self, database=None):
        return FakeSession(self.tx)


def load_jobs_module(fake_driver):
    spec = importlib.util.spec_from_file_location("jobs_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    neo4j_mod = types.ModuleType("neo4j")
    neo4j_mod.unit_of_work = lambda *args, **kwargs: (lambda fn: fn)

    graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

    async def _require_driver():
        return fake_driver

    graph_bootstrap_mod.require_driver = _require_driver
    graph_bootstrap_mod._NEO4J_DB = "neo4j"

    sys.modules["neo4j"] = neo4j_mod
    sys.modules["graph_bootstrap"] = graph_bootstrap_mod
    spec.loader.exec_module(module)
    return module


class ImmediateFuture:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc

    def result(self, timeout=None):
        if self._exc:
            raise self._exc
        return self._result


class IndexJobOrchestrationTests(unittest.TestCase):
    def test_finalize_job_aligns_semantic_run_after_success(self):
        tx = FakeTx()
        module = load_jobs_module(FakeDriver(tx))
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        module._MAIN_LOOP = types.SimpleNamespace(is_running=lambda: True)

        def run_coro_immediately(coro, running_loop):
            self.assertTrue(running_loop.is_running())
            return ImmediateFuture(loop.run_until_complete(coro))

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            manifest_path = tmp.name
        self.addCleanup(lambda: os.path.exists(manifest_path) and os.remove(manifest_path))

        with module._JOBS_LOCK:
            module._JOBS.clear()
            module._JOBS["job1"] = {
                "status": "running",
                "project_path": "/tmp/repo",
                "project_id": "proj123",
                "struct_rc": 0,
                "sem_rc": 0,
                "logs": [],
                "started_at": 100.0,
                "finished_at": None,
                "cancel_requested": False,
            }

        with mock.patch("asyncio.run_coroutine_threadsafe", side_effect=run_coro_immediately):
            module._finalize_job("job1", manifest_path)

        with module._JOBS_LOCK:
            job = module._JOBS["job1"]
            self.assertEqual(job["status"], "done")
            self.assertIsNotNone(job["finished_at"])
            self.assertTrue(any("timestamps refreshed" in line for line in job["logs"]))
            self.assertEqual(job["run_summary"]["struct_active_run_id"], "struct-1")
            self.assertEqual(job["run_summary"]["semantic_active_run_id"], "sem-1")
            self.assertEqual(job["run_summary"]["semantic_active_struct_run_id"], "struct-1")

        cyphers = [cypher for cypher, _ in tx.calls]
        self.assertTrue(any("SET f.indexed_at = timestamp(), f.vector_indexed_at = timestamp()" in c for c in cyphers))
        align_queries = [c for c in cyphers if "semantic_active_struct_run_id = struct_run_id" in c]
        self.assertEqual(len(align_queries), 1)


if __name__ == "__main__":
    unittest.main()
