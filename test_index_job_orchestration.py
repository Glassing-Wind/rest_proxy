import asyncio
import importlib.util
import os
import sys
import tempfile
import time
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
        if "p.struct_active_run_id AS struct_active_run_id" in cypher:
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


class FakeAsyncpgConnection:
    async def fetch(self, *args, **kwargs):
        return []

    async def close(self):
        return None


def load_jobs_module(fake_driver):
    spec = importlib.util.spec_from_file_location("jobs_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    neo4j_mod = types.ModuleType("neo4j")
    neo4j_mod.unit_of_work = lambda *args, **kwargs: lambda fn: fn
    asyncpg_mod = types.ModuleType("asyncpg")

    async def _connect(*args, **kwargs):
        return FakeAsyncpgConnection()

    asyncpg_mod.connect = _connect

    graph_bootstrap_mod = types.ModuleType("graph_bootstrap")

    async def _require_driver():
        return fake_driver

    graph_bootstrap_mod.require_driver = _require_driver
    graph_bootstrap_mod._NEO4J_DB = "neo4j"

    sys.modules["neo4j"] = neo4j_mod
    sys.modules["asyncpg"] = asyncpg_mod
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
        self.addCleanup(
            lambda: os.path.exists(manifest_path) and os.remove(manifest_path)
        )

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

        with mock.patch(
            "asyncio.run_coroutine_threadsafe", side_effect=run_coro_immediately
        ):
            module._finalize_job("job1", manifest_path)

        with module._JOBS_LOCK:
            job = module._JOBS["job1"]
            self.assertEqual(job["status"], "done")
            self.assertIsNotNone(job["finished_at"])
            self.assertTrue(any("timestamps refreshed" in line for line in job["logs"]))
            self.assertEqual(job["run_summary"]["struct_active_run_id"], "struct-1")
            self.assertEqual(job["run_summary"]["semantic_active_run_id"], "sem-1")
            self.assertEqual(
                job["run_summary"]["semantic_active_struct_run_id"], "struct-1"
            )

        cyphers = [cypher for cypher, _ in tx.calls]
        self.assertTrue(
            any(
                "SET f.indexed_at = timestamp(), f.vector_indexed_at = timestamp()" in c
                for c in cyphers
            )
        )
        align_queries = [
            c for c in cyphers if "semantic_active_struct_run_id = struct_run_id" in c
        ]
        self.assertEqual(len(align_queries), 1)
        self.assertIn(
            "MATCH (sr:IndexRun {project_id:$pid, phase:'struct'})", align_queries[0]
        )

    def test_reconcile_finished_job_runs_post_index_maintenance(self):
        tx = FakeTx()
        module = load_jobs_module(FakeDriver(tx))
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        module._MAIN_LOOP = types.SimpleNamespace(is_running=lambda: True)

        def run_coro_immediately(coro, running_loop):
            self.assertTrue(running_loop.is_running())
            return ImmediateFuture(loop.run_until_complete(coro))

        with module._JOBS_LOCK:
            module._JOBS.clear()
            module._JOBS["job2"] = {
                "status": "done",
                "project_path": "/tmp/repo",
                "project_id": "proj123",
                "struct_rc": 0,
                "sem_rc": 0,
                "logs": [],
                "started_at": 100.0,
                "finished_at": 200.0,
                "cancel_requested": False,
            }

        with mock.patch(
            "asyncio.run_coroutine_threadsafe", side_effect=run_coro_immediately
        ):
            job = module._reconcile_job_process_state("job2")

        self.assertIsNotNone(job)
        self.assertIsNotNone(job.get("post_index_maintenance_done"))
        self.assertEqual(
            job["run_summary"]["semantic_active_struct_run_id"], "struct-1"
        )
        self.assertTrue(any("timestamps refreshed" in line for line in job["logs"]))

        cyphers = [cypher for cypher, _ in tx.calls]
        self.assertTrue(
            any("semantic_active_struct_run_id = struct_run_id" in c for c in cyphers)
        )
        self.assertTrue(
            any(
                "MATCH (sr:IndexRun {project_id:$pid, phase:'struct'})" in c
                for c in cyphers
            )
        )

    def test_global_index_capacity_lock_blocks_second_running_job(self):
        tx = FakeTx()
        module = load_jobs_module(FakeDriver(tx))
        with tempfile.TemporaryDirectory() as tmp:
            module._PROJECT_LOCKS_DIR = Path(tmp)
            with mock.patch.dict(
                os.environ, {"LM_PROXY_MAX_CONCURRENT_INDEX_JOBS": "1"}
            ):
                claimed, blocking = module.claim_index_capacity_lock(
                    "job1",
                    project_id="proj1",
                    project_path="/tmp/repo1",
                )
                self.assertTrue(claimed)
                self.assertIsNone(blocking)

                with module._JOBS_LOCK:
                    module._JOBS.clear()
                    module._JOBS["job1"] = {
                        "status": "running",
                        "project_id": "proj1",
                        "project_path": "/tmp/repo1",
                        "struct_pid": os.getpid(),
                        "sem_pid": None,
                    }

                claimed, blocking = module.claim_index_capacity_lock(
                    "job2",
                    project_id="proj2",
                    project_path="/tmp/repo2",
                )
                self.assertFalse(claimed)
                self.assertEqual(blocking["project_id"], "proj1")

                module._release_index_capacity_lock("job1")
                claimed, blocking = module.claim_index_capacity_lock(
                    "job2",
                    project_id="proj2",
                    project_path="/tmp/repo2",
                )
                self.assertTrue(claimed)
                self.assertIsNone(blocking)

    def test_post_index_maintenance_handles_same_running_loop(self):
        tx = FakeTx()
        module = load_jobs_module(FakeDriver(tx))

        with module._JOBS_LOCK:
            module._JOBS.clear()
            module._JOBS["job3"] = {
                "status": "done",
                "project_path": "/tmp/repo",
                "project_id": "proj123",
                "struct_rc": 0,
                "sem_rc": 0,
                "logs": [],
                "started_at": 100.0,
                "finished_at": 200.0,
                "cancel_requested": False,
            }

        async def _run_inside_loop():
            module._MAIN_LOOP = asyncio.get_running_loop()
            module._run_post_index_maintenance("job3")
            await asyncio.sleep(0)

        asyncio.run(_run_inside_loop())

        with module._JOBS_LOCK:
            job = module._JOBS["job3"]
            self.assertIsNotNone(job.get("post_index_maintenance_done"))
            self.assertIsNone(job.get("post_index_maintenance_pending"))
            self.assertEqual(
                job["run_summary"]["semantic_active_struct_run_id"], "struct-1"
            )
            self.assertTrue(any("timestamps refreshed" in line for line in job["logs"]))

        cyphers = [cypher for cypher, _ in tx.calls]
        self.assertTrue(
            any("semantic_active_struct_run_id = struct_run_id" in c for c in cyphers)
        )
        self.assertTrue(
            any(
                "MATCH (sr:IndexRun {project_id:$pid, phase:'struct'})" in c
                for c in cyphers
            )
        )

    def test_post_index_maintenance_cancellation_leaves_job_retryable(self):
        tx = FakeTx()
        module = load_jobs_module(FakeDriver(tx))

        created_tasks = []

        class FakeTask:
            def __init__(self):
                self.callbacks = []

            def add_done_callback(self, callback):
                self.callbacks.append(callback)

            def result(self):
                raise asyncio.CancelledError()

        class FakeLoop:
            def create_task(self, coro):
                try:
                    coro.close()
                except Exception:
                    pass
                task = FakeTask()
                created_tasks.append(task)
                return task

        with module._JOBS_LOCK:
            module._JOBS.clear()
            module._JOBS["job4"] = {
                "status": "done",
                "project_path": "/tmp/repo",
                "project_id": "proj123",
                "struct_rc": 0,
                "sem_rc": 0,
                "logs": [],
                "started_at": 100.0,
                "finished_at": 200.0,
                "cancel_requested": False,
            }

        fake_loop = FakeLoop()
        module._MAIN_LOOP = fake_loop
        with mock.patch("asyncio.get_running_loop", return_value=fake_loop):
            module._run_post_index_maintenance("job4")
        self.assertEqual(len(created_tasks), 1)
        created_tasks[0].callbacks[0](created_tasks[0])

        with module._JOBS_LOCK:
            job = module._JOBS["job4"]
            self.assertIsNone(job.get("post_index_maintenance_done"))
            self.assertIsNone(job.get("post_index_maintenance_pending"))
            self.assertIsNone(job.get("post_index_maintenance_error"))
            self.assertNotIn("run_summary", job)

    def test_post_index_maintenance_uses_blocking_fallback_without_registered_main_loop(
        self,
    ):
        tx = FakeTx()
        module = load_jobs_module(FakeDriver(tx))
        fake_loop = object()

        with module._JOBS_LOCK:
            module._JOBS.clear()
            module._JOBS["job5"] = {
                "status": "done",
                "project_path": "/tmp/repo",
                "project_id": "proj123",
                "struct_rc": 0,
                "sem_rc": 0,
                "logs": [],
                "started_at": 100.0,
                "finished_at": 200.0,
                "cancel_requested": False,
            }

        module._MAIN_LOOP = None
        expected_summary = {
            "struct_active_run_id": "struct-2",
            "semantic_active_run_id": "sem-2",
            "semantic_active_struct_run_id": "struct-2",
            "struct_index_status": "done",
            "semantic_index_status": "done",
        }

        with mock.patch("asyncio.get_running_loop", return_value=fake_loop):

            def _run_and_close(coro, **kwargs):
                coro.close()
                return expected_summary

            with mock.patch.object(
                module, "_run_coro_blocking", side_effect=_run_and_close
            ) as run_blocking:
                module._run_post_index_maintenance("job5")

        run_blocking.assert_called_once()
        with module._JOBS_LOCK:
            job = module._JOBS["job5"]
            self.assertEqual(
                job["run_summary"]["semantic_active_struct_run_id"], "struct-2"
            )
            self.assertIsNotNone(job.get("post_index_maintenance_done"))
            self.assertIsNone(job.get("post_index_maintenance_pending"))

    def test_post_index_maintenance_retries_stale_pending_marker(self):
        tx = FakeTx()
        module = load_jobs_module(FakeDriver(tx))

        with module._JOBS_LOCK:
            module._JOBS.clear()
            module._JOBS["job6"] = {
                "status": "done",
                "project_path": "/tmp/repo",
                "project_id": "proj123",
                "struct_rc": 0,
                "sem_rc": 0,
                "logs": [],
                "started_at": 100.0,
                "finished_at": 200.0,
                "cancel_requested": False,
                "post_index_maintenance_pending": time.time() - 31,
            }

        expected_summary = {
            "struct_active_run_id": "struct-3",
            "semantic_active_run_id": "sem-3",
            "semantic_active_struct_run_id": "struct-3",
            "struct_index_status": "done",
            "semantic_index_status": "done",
        }
        module._MAIN_LOOP = None
        with mock.patch("asyncio.get_running_loop", side_effect=RuntimeError()):

            def _run_and_close(coro, **kwargs):
                coro.close()
                return expected_summary

            with mock.patch.object(
                module, "_run_coro_blocking", side_effect=_run_and_close
            ) as run_blocking:
                module._run_post_index_maintenance("job6")

        run_blocking.assert_called_once()
        with module._JOBS_LOCK:
            job = module._JOBS["job6"]
            self.assertEqual(
                job["run_summary"]["semantic_active_struct_run_id"], "struct-3"
            )
            self.assertIsNotNone(job.get("post_index_maintenance_done"))
            self.assertIsNone(job.get("post_index_maintenance_pending"))


if __name__ == "__main__":
    unittest.main()
