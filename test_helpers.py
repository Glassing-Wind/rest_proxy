import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import _helpers


class HelpersTests(unittest.TestCase):
    def test_workspace_registry_resolves_project_id_from_canonicalized_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            alias_root = Path(tmpdir) / "alias"
            alias_root.mkdir()
            alias = alias_root / "repo"
            os.symlink(repo, alias)

            with mock.patch.object(_helpers.WorkspaceRegistry, "_mapping", {}, create=True), mock.patch.object(
                _helpers.WorkspaceRegistry, "_initialized", True, create=True
            ), mock.patch.object(_helpers.WorkspaceRegistry, "_persist", return_value=None):
                _helpers.WorkspaceRegistry.register("repo-id", "proj123", str(repo))
                self.assertEqual(_helpers.WorkspaceRegistry.resolve_id(str(alias)), "proj123")
                self.assertEqual(_helpers.get_project_id(str(alias)), "proj123")
                self.assertEqual(_helpers.get_workspace_path(str(alias)), str(repo))

    def test_workspace_registry_falls_back_to_unique_indexed_basename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            missing_alias = Path(tmpdir) / "missing" / "repo"

            watcher_mod = types.ModuleType("graphrag_core.indexing.watcher")
            watcher_mod.load_indexed_projects = lambda: {
                "proj456": {"project_path": str(repo)},
            }
            graphrag_mod = types.ModuleType("graphrag_core")
            indexing_mod = types.ModuleType("graphrag_core.indexing")
            indexing_mod.watcher = watcher_mod

            with mock.patch.object(_helpers.WorkspaceRegistry, "_mapping", {}, create=True), mock.patch.object(
                _helpers.WorkspaceRegistry, "_initialized", True, create=True
            ), mock.patch.dict(
                sys.modules,
                {
                    "graphrag_core": graphrag_mod,
                    "graphrag_core.indexing": indexing_mod,
                    "graphrag_core.indexing.watcher": watcher_mod,
                },
            ):
                self.assertEqual(_helpers.WorkspaceRegistry.resolve_id(str(missing_alias)), "proj456")
                self.assertEqual(_helpers.get_project_id(str(missing_alias)), "proj456")
                self.assertEqual(_helpers.get_workspace_path(str(missing_alias)), str(repo))


if __name__ == "__main__":
    unittest.main()
