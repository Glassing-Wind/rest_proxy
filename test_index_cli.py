import asyncio
import sys
import types
import unittest
from unittest import mock

from _index_cli import run_index_workspace_cli


class IndexCliTests(unittest.TestCase):
    def _modules(self, *, started, jobs, final_status="final status"):
        indexing = types.ModuleType("tools.hands.indexing")
        indexing.index_workspace = mock.AsyncMock(return_value=started)
        indexing.get_index_status = mock.AsyncMock(return_value=final_status)
        jobs_module = types.ModuleType("_jobs")
        jobs_module.load_job_record = mock.Mock(side_effect=jobs)
        return indexing, jobs_module

    def test_waits_for_terminal_job_and_reports_final_status(self):
        indexing, jobs = self._modules(
            started="Indexing started.\n  job_id: abc123",
            jobs=[{"status": "running"}, {"status": "done"}],
            final_status="Job abc123: DONE",
        )
        output = []
        with mock.patch.dict(
            sys.modules,
            {"tools.hands.indexing": indexing, "_jobs": jobs},
        ):
            exit_code = asyncio.run(
                run_index_workspace_cli(
                    "/tmp/repo", emit=output.append, poll_interval=0.01
                )
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Job abc123: DONE", output)
        indexing.get_index_status.assert_awaited_once_with("abc123")

    def test_returns_failure_for_failed_job(self):
        indexing, jobs = self._modules(
            started="job_id: failed1",
            jobs=[{"status": "failed"}],
            final_status="Job failed1: FAILED",
        )
        with mock.patch.dict(
            sys.modules,
            {"tools.hands.indexing": indexing, "_jobs": jobs},
        ):
            exit_code = asyncio.run(
                run_index_workspace_cli("/tmp/repo", emit=lambda value: None)
            )

        self.assertEqual(exit_code, 1)

    def test_waits_for_post_index_maintenance_after_children_finish(self):
        indexing, jobs = self._modules(
            started="job_id: maintained1",
            jobs=[
                {
                    "status": "done",
                    "struct_rc": 0,
                    "sem_rc": 0,
                    "project_path": "/tmp/repo",
                },
                {
                    "status": "done",
                    "struct_rc": 0,
                    "sem_rc": 0,
                    "project_path": "/tmp/repo",
                    "post_index_maintenance_done": 200.0,
                },
            ],
            final_status="Job maintained1: DONE",
        )
        with mock.patch.dict(
            sys.modules,
            {"tools.hands.indexing": indexing, "_jobs": jobs},
        ):
            exit_code = asyncio.run(
                run_index_workspace_cli(
                    "/tmp/repo", emit=lambda value: None, poll_interval=0.01
                )
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(jobs.load_job_record.call_count, 2)

    def test_returns_failure_for_post_index_maintenance_error(self):
        indexing, jobs = self._modules(
            started="job_id: maintenance-failed",
            jobs=[
                {
                    "status": "done",
                    "struct_rc": 0,
                    "sem_rc": 0,
                    "project_path": "/tmp/repo",
                    "post_index_maintenance_error": "role promotion failed",
                }
            ],
            final_status="Job maintenance-failed: DONE\n  maintenance: FAILED",
        )
        with mock.patch.dict(
            sys.modules,
            {"tools.hands.indexing": indexing, "_jobs": jobs},
        ):
            exit_code = asyncio.run(
                run_index_workspace_cli("/tmp/repo", emit=lambda value: None)
            )

        self.assertEqual(exit_code, 1)

    def test_returns_failure_when_start_does_not_return_job_id(self):
        indexing, jobs = self._modules(
            started="Error: workspace does not exist", jobs=[]
        )
        with mock.patch.dict(
            sys.modules,
            {"tools.hands.indexing": indexing, "_jobs": jobs},
        ):
            exit_code = asyncio.run(
                run_index_workspace_cli("/missing", emit=lambda value: None)
            )

        self.assertEqual(exit_code, 1)
        jobs.load_job_record.assert_not_called()

    def test_returns_failure_for_unknown_blocking_job(self):
        indexing, jobs = self._modules(
            started="Indexing already running.\n  job_id: unknown", jobs=[]
        )
        with mock.patch.dict(
            sys.modules,
            {"tools.hands.indexing": indexing, "_jobs": jobs},
        ):
            exit_code = asyncio.run(
                run_index_workspace_cli("/tmp/repo", emit=lambda value: None)
            )

        self.assertEqual(exit_code, 1)
        jobs.load_job_record.assert_not_called()

    def test_does_not_wait_for_unrelated_capacity_blocking_job(self):
        indexing, jobs = self._modules(
            started=(
                "Another indexing job is already running.\n"
                "  job_id: other-project-job\n"
                "  project: /tmp/other"
            ),
            jobs=[{"status": "running"}],
        )
        with mock.patch.dict(
            sys.modules,
            {"tools.hands.indexing": indexing, "_jobs": jobs},
        ):
            exit_code = asyncio.run(
                run_index_workspace_cli("/tmp/repo", emit=lambda value: None)
            )

        self.assertEqual(exit_code, 1)
        jobs.load_job_record.assert_not_called()

    def test_rejects_relative_project_path_before_starting(self):
        indexing, jobs = self._modules(started="job_id: should-not-run", jobs=[])
        with mock.patch.dict(
            sys.modules,
            {"tools.hands.indexing": indexing, "_jobs": jobs},
        ):
            exit_code = asyncio.run(
                run_index_workspace_cli("relative/repo", emit=lambda value: None)
            )

        self.assertEqual(exit_code, 1)
        indexing.index_workspace.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
