import json
import tempfile
import threading
import unittest
from pathlib import Path

import _jobs


class JobStatePersistenceTests(unittest.TestCase):
    def test_persist_job_state_uses_unique_temp_files_for_concurrent_writers(self):
        job_id = "racejob1"
        original_runtime_dir = _jobs._RUNTIME_JOBS_DIR
        original_jobs = dict(_jobs._JOBS)
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                _jobs._RUNTIME_JOBS_DIR = Path(tmpdir)
                with _jobs._JOBS_LOCK:
                    _jobs._JOBS.clear()
                    _jobs._JOBS[job_id] = {
                        "status": "running",
                        "session_id": None,
                        "project_id": "proj123",
                        "project_path": "/tmp/repo",
                        "file_count": 2,
                        "struct_rc": None,
                        "sem_rc": None,
                        "started_at": 100.0,
                        "logs": [],
                    }

                errors = []

                def persist_many() -> None:
                    for _ in range(25):
                        try:
                            _jobs._persist_job_state(job_id)
                        except Exception as exc:
                            errors.append(exc)

                threads = [threading.Thread(target=persist_many) for _ in range(8)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

                self.assertEqual([], errors)
                state_path = Path(tmpdir) / job_id / "state.json"
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertEqual("running", payload["status"])
                self.assertEqual([], list(state_path.parent.glob("state.json.tmp.*")))
            finally:
                _jobs._RUNTIME_JOBS_DIR = original_runtime_dir
                with _jobs._JOBS_LOCK:
                    _jobs._JOBS.clear()
                    _jobs._JOBS.update(original_jobs)

    def test_persist_job_state_handles_cancel_finalize_interleaving(self):
        job_id = "racejob2"
        original_runtime_dir = _jobs._RUNTIME_JOBS_DIR
        original_jobs = dict(_jobs._JOBS)
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                _jobs._RUNTIME_JOBS_DIR = Path(tmpdir)
                with _jobs._JOBS_LOCK:
                    _jobs._JOBS.clear()
                    _jobs._JOBS[job_id] = {
                        "status": "running",
                        "session_id": None,
                        "project_id": "proj123",
                        "project_path": "/tmp/repo",
                        "file_count": 2,
                        "struct_rc": 0,
                        "sem_rc": 0,
                        "started_at": 100.0,
                        "finished_at": None,
                        "cancel_requested": False,
                        "logs": [],
                    }

                errors = []

                def persist_many() -> None:
                    for _ in range(20):
                        try:
                            _jobs._persist_job_state(job_id)
                        except Exception as exc:
                            errors.append(exc)

                def mark_cancelling() -> None:
                    for _ in range(10):
                        try:
                            with _jobs._JOBS_LOCK:
                                job = _jobs._JOBS[job_id]
                                job["cancel_requested"] = True
                                job["status"] = "cancelling"
                            _jobs._persist_job_state(job_id)
                        except Exception as exc:
                            errors.append(exc)

                def finalize_cancelled() -> None:
                    for _ in range(10):
                        try:
                            with _jobs._JOBS_LOCK:
                                job = _jobs._JOBS[job_id]
                                if job.get("cancel_requested"):
                                    job["status"] = "cancelled"
                                    job["finished_at"] = 200.0
                            _jobs._persist_job_state(job_id)
                        except Exception as exc:
                            errors.append(exc)

                threads = [threading.Thread(target=persist_many) for _ in range(4)]
                threads.append(threading.Thread(target=mark_cancelling))
                threads.append(threading.Thread(target=finalize_cancelled))
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()

                self.assertEqual([], errors)
                state_path = Path(tmpdir) / job_id / "state.json"
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertTrue(payload["cancel_requested"])
                self.assertIn(payload["status"], {"cancelling", "cancelled"})
                self.assertEqual([], list(state_path.parent.glob("state.json.tmp.*")))
            finally:
                _jobs._RUNTIME_JOBS_DIR = original_runtime_dir
                with _jobs._JOBS_LOCK:
                    _jobs._JOBS.clear()
                    _jobs._JOBS.update(original_jobs)


if __name__ == "__main__":
    unittest.main()
