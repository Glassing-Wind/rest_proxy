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
