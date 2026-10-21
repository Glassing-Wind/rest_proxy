"""Launch-capability ownership and shutdown using only owned ephemeral listeners.

No environment variables are required. Fixtures use private temporary directories;
one child CLI binds an unused loopback port, then this test terminates that child.
Existing services and user state are never touched.
Run with .venv/bin/python test_evidence_workspace_launch.py.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from graphrag_core.evidence_workspace.contracts import WorkspaceError
from scripts import evidence_workspace as launch


REPO_ROOT = Path(__file__).resolve().parent


class EvidenceWorkspaceLaunchAcceptance(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.path = self.root / "launch.json"
        self.url = "http://127.0.0.1:8765/#token=synthetic-only-launch-capability"

    def test_private_launch_records_owner_and_cleanup_removes_only_owned_file(self):
        identity = launch._write_launch(self.path, self.url)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(identity, (self.path.stat().st_dev, self.path.stat().st_ino))
        self.assertEqual(json.loads(self.path.read_text()), {"url": self.url, "pid": os.getpid()})
        launch._clear_launch(self.path, identity)
        self.assertFalse(self.path.exists())
        launch._clear_launch(self.path, identity)

    def test_existing_launch_is_preserved_and_never_overwritten(self):
        existing = b'{"pid":123,"url":"synthetic existing launch"}'
        self.path.write_bytes(existing)
        os.chmod(self.path, 0o600)
        identity = (self.path.stat().st_dev, self.path.stat().st_ino)
        with self.assertRaises(WorkspaceError):
            launch._write_launch(self.path, self.url)
        self.assertEqual(self.path.read_bytes(), existing)
        self.assertEqual((self.path.stat().st_dev, self.path.stat().st_ino), identity)

    @unittest.skipUnless(os.name == "posix", "POSIX hard links")
    def test_hardlink_target_is_never_truncated_or_repermissioned(self):
        target = self.root / "another-private-file"
        target.write_bytes(b"Do not replace this unrelated synthetic file.")
        os.chmod(target, 0o640)
        original_bytes = target.read_bytes()
        original_mode = target.stat().st_mode
        os.link(target, self.path)
        with self.assertRaises(WorkspaceError):
            launch._write_launch(self.path, self.url)
        self.assertEqual(target.read_bytes(), original_bytes)
        self.assertEqual(target.stat().st_mode, original_mode)
        self.assertEqual(self.path.stat().st_ino, target.stat().st_ino)

    @unittest.skipUnless(os.name == "posix", "POSIX symlinks")
    def test_symlink_target_is_never_modified(self):
        target = self.root / "symlink-target"
        target.write_bytes(b"Unrelated synthetic target")
        original = target.read_bytes()
        self.path.symlink_to(target)
        with self.assertRaises(WorkspaceError):
            launch._write_launch(self.path, self.url)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(target.read_bytes(), original)

    def test_replacement_launch_is_not_deleted_by_previous_owner_cleanup(self):
        identity = launch._write_launch(self.path, self.url)
        owned_file = self.root / "previous-launch.json"
        self.path.rename(owned_file)
        replacement = b'{"pid":456,"url":"synthetic replacement launch"}'
        self.path.write_bytes(replacement)
        os.chmod(self.path, 0o600)
        self.assertNotEqual((self.path.stat().st_dev, self.path.stat().st_ino), identity)
        launch._clear_launch(self.path, identity)
        self.assertEqual(self.path.read_bytes(), replacement)
        self.assertEqual(json.loads(owned_file.read_text())["url"], self.url)

    def test_concurrent_launches_have_one_owner_without_partial_overwrite(self):
        barrier = threading.Barrier(2)

        def create(index):
            barrier.wait(timeout=5)
            try:
                identity = launch._write_launch(self.path, self.url + str(index))
                return index, identity
            except WorkspaceError:
                return index, None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        winners = [(index, identity) for index, identity in results if identity is not None]
        self.assertEqual(len(winners), 1)
        index, identity = winners[0]
        self.assertEqual(json.loads(self.path.read_text())["url"], self.url + str(index))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        launch._clear_launch(self.path, identity)
        self.assertFalse(self.path.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX SIGTERM and stdout readiness selection")
    def test_cli_sigterm_cleans_launch_and_preserves_saved_case(self):
        state = self.root / "owned-child-workspace"
        child = subprocess.Popen(
            [sys.executable, "-m", "scripts.evidence_workspace", "--state", str(state),
             "--port", "0", "--demo"],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 8
            readable, _, _ = select.select([child.stdout], [], [], 8)
            self.assertTrue(readable, "The owned child must announce startup within eight seconds")
            first_line = child.stdout.readline()
            self.assertTrue(first_line.startswith("Evidence viewer listening at http://127.0.0.1:"), first_line)
            public_url = first_line.removeprefix("Evidence viewer listening at ").strip()
            self.assertNotIn("token", public_url)
            opener = build_opener(ProxyHandler({}))  # This probe stays on loopback even with proxy env vars.
            ready = False
            while time.monotonic() < deadline and child.poll() is None:
                try:
                    with opener.open(public_url + "live", timeout=0.5) as response:
                        ready = json.load(response).get("surface") == "local-evidence-workspace"
                    if ready:
                        break
                except (OSError, URLError):
                    time.sleep(0.02)
            self.assertTrue(ready, "Only this child viewer must become ready within the startup bound")
            self.assertTrue((state / "launch.json").is_file())
            child.terminate()
            child.communicate(timeout=5)
            self.assertEqual(child.returncode, 143)
            self.assertFalse((state / "launch.json").exists())
            database = state / "workspaces.sqlite3"
            self.assertTrue(database.is_file())
            db = sqlite3.connect(database)
            try:
                self.assertEqual(db.execute("SELECT count(*) FROM cases").fetchone()[0], 1)
            finally:
                db.close()
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.communicate(timeout=2)
            if child.stdout is not None:
                child.stdout.close()
            if child.stderr is not None:
                child.stderr.close()


if __name__ == "__main__":
    unittest.main()
