import json
import tempfile
import threading
import socket
import sys
import types
import subprocess
import unittest
from pathlib import Path
from unittest import mock

import _jobs


class JobStatePersistenceTests(unittest.TestCase):
    def test_persisted_identity_can_cancel_a_real_owned_worker(self):
        proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            started = _jobs._process_started_at(proc.pid)
            self.assertIsNotNone(started)
            job = {'struct_pid': proc.pid, 'worker_host': socket.gethostname(),
                   'struct_process_started_at': started}
            self.assertEqual(_jobs.terminate_index_worker(job, 'struct'), 'signalled')
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.returncode)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    def test_cancellation_never_falls_back_to_a_stopped_child_pid(self):
        proc = mock.Mock()
        proc.poll.return_value = 0
        with mock.patch('os.kill') as kill:
            self.assertEqual(_jobs.terminate_index_worker({'struct_proc': proc, 'struct_pid': 123}, 'struct'),
                             'already_stopped')
        proc.terminate.assert_not_called()
        kill.assert_not_called()

    def test_persisted_cancellation_requires_host_and_creation_time(self):
        worker = mock.Mock()
        worker.create_time.return_value = 44.0
        psutil = types.SimpleNamespace(Process=mock.Mock(return_value=worker))
        job = {'struct_pid': 123, 'worker_host': socket.gethostname(), 'struct_process_started_at': 33.0}
        with mock.patch.dict(sys.modules, {'psutil': psutil}):
            self.assertEqual(_jobs.terminate_index_worker(job, 'struct'), 'identity_unverified')
            worker.terminate.assert_not_called()
            job['struct_process_started_at'] = 44.0
            self.assertEqual(_jobs.terminate_index_worker(job, 'struct'), 'signalled')
            worker.terminate.assert_called_once()
            job['worker_host'] = 'other-host'
            self.assertEqual(_jobs.terminate_index_worker(job, 'struct'), 'identity_unverified')
            job.pop('worker_host')
            self.assertEqual(_jobs.terminate_index_worker(job, 'struct'), 'identity_unverified')

    def test_process_inspection_failure_cannot_reclaim_worker(self):
        with mock.patch('os.kill', side_effect=PermissionError()):
            self.assertTrue(_jobs._process_alive(123))
        with mock.patch('os.kill', side_effect=ProcessLookupError()):
            self.assertFalse(_jobs._process_alive(123))

    def test_recovery_requires_publication_not_native_parse_completion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log = Path(tmpdir) / 'struct.log'
            parsed = ('[ts-pack-index] Done — 1 files | parse=0.1s nodes=0.1s '
                      'imports=0.1s rels=0.1s calls=0.1s total=0.5s\n')
            log.write_text(parsed)
            self.assertIsNone(_jobs._infer_return_code_from_log(str(log), _jobs._STRUCT_DONE_RE))
            log.write_text(parsed + '[ts-pack:shadow] Promoted — fixture\n')
            self.assertIsNone(_jobs._infer_return_code_from_log(str(log), _jobs._STRUCT_DONE_RE))
            log.write_text(log.read_text() + '[ts-pack:timing] struct_total: 1.0s\n')
            self.assertEqual(0, _jobs._infer_return_code_from_log(str(log), _jobs._STRUCT_DONE_RE))
            log.write_text(parsed + '[ts-pack:struct] Completed — publication and status recorded.\n')
            self.assertEqual(0, _jobs._infer_return_code_from_log(str(log), _jobs._STRUCT_DONE_RE))
            log.write_text(log.read_text() + '[ts-pack:index] Processing ERROR_HANDLER.py\n')
            self.assertEqual(0, _jobs._infer_return_code_from_log(str(log), _jobs._STRUCT_DONE_RE))
            log.write_text(log.read_text() + '[ts-pack:struct] ERROR: failed\n')
            self.assertEqual(1, _jobs._infer_return_code_from_log(str(log), _jobs._STRUCT_DONE_RE))

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
                self.assertEqual(job_id, payload["job_id"])
                self.assertEqual("running", payload["status"])
                self.assertEqual([], list(state_path.parent.glob("state.json.tmp.*")))
            finally:
                _jobs._RUNTIME_JOBS_DIR = original_runtime_dir
                with _jobs._JOBS_LOCK:
                    _jobs._JOBS.clear()
                    _jobs._JOBS.update(original_jobs)

    def test_load_persisted_job_backfills_job_id_for_legacy_state(self):
        job_id = "legacyjob"
        original_runtime_dir = _jobs._RUNTIME_JOBS_DIR
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                _jobs._RUNTIME_JOBS_DIR = Path(tmpdir)
                state_path = Path(tmpdir) / job_id / "state.json"
                state_path.parent.mkdir(parents=True)
                state_path.write_text(
                    json.dumps({"status": "running", "project_id": "proj123"}),
                    encoding="utf-8",
                )

                payload = _jobs._load_persisted_job(job_id)

                self.assertIsNotNone(payload)
                assert payload is not None
                self.assertEqual(job_id, payload["job_id"])
            finally:
                _jobs._RUNTIME_JOBS_DIR = original_runtime_dir

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

    def test_load_job_record_reconciles_persisted_running_job_with_dead_pids(self):
        job_id = "stalejob1"
        original_runtime_dir = _jobs._RUNTIME_JOBS_DIR
        original_jobs = dict(_jobs._JOBS)
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                runtime_dir = Path(tmpdir)
                _jobs._RUNTIME_JOBS_DIR = runtime_dir
                job_dir = runtime_dir / job_id
                job_dir.mkdir(parents=True, exist_ok=True)
                struct_log = job_dir / "struct.log"
                struct_log.write_text(
                    "[ts-pack-index] Done — 1 files | parse=0.1s nodes=0.1s imports=0.1s rels=0.1s calls=0.1s total=0.5s\n"
                    "[ts-pack:struct] Completed — publication and status recorded.\n",
                    encoding="utf-8",
                )
                semantic_log = job_dir / "semantic.log"
                semantic_log.write_text(
                    "[lm-proxy:indexer] Done — 1 new / 0 skipped / 1 files in 0.5s (parsed=1 skipped_files=0)\n",
                    encoding="utf-8",
                )
                payload = {
                    "status": "running",
                    "session_id": None,
                    "project_id": "proj123",
                    "project_path": "/tmp/repo",
                    "file_count": 1,
                    "struct_rc": None,
                    "sem_rc": None,
                    "started_at": 100.0,
                    "finished_at": None,
                    "cancel_requested": False,
                    "logs": [],
                    "struct_pid": 999991,
                    "sem_pid": 999992,
                    "struct_log_path": str(struct_log),
                    "semantic_log_path": str(semantic_log),
                    "manifest_path": str(job_dir / "manifest.json"),
                }
                (job_dir / "state.json").write_text(json.dumps(payload), encoding="utf-8")

                with _jobs._JOBS_LOCK:
                    _jobs._JOBS.clear()

                with mock.patch.object(_jobs, "_run_post_index_maintenance") as maintenance:
                    job = _jobs.load_job_record(job_id)

                self.assertIsNotNone(job)
                assert job is not None
                self.assertEqual("done", job["status"])
                self.assertEqual(0, job["struct_rc"])
                self.assertEqual(0, job["sem_rc"])
                self.assertIsNotNone(job["finished_at"])
                maintenance.assert_called_once_with(job_id)

                persisted = _jobs._load_persisted_job(job_id)
                self.assertIsNotNone(persisted)
                assert persisted is not None
                self.assertEqual("done", persisted["status"])
                self.assertEqual(0, persisted["struct_rc"])
                self.assertEqual(0, persisted["sem_rc"])
            finally:
                _jobs._RUNTIME_JOBS_DIR = original_runtime_dir
                with _jobs._JOBS_LOCK:
                    _jobs._JOBS.clear()
                    _jobs._JOBS.update(original_jobs)


if __name__ == "__main__":
    unittest.main()
