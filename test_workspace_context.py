import asyncio
import contextvars
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent
MODULE_PATH = REPO_ROOT / "tools" / "workspace_context.py"


def load_module():
    spec = importlib.util.spec_from_file_location("tools.workspace_context", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None

    jobs_mod = types.ModuleType("_jobs")
    jobs_mod.client_session_id = contextvars.ContextVar("client_session_id", default=None)
    jobs_mod._MAIN_LOOP = None

    with mock.patch.dict(sys.modules, {"_jobs": jobs_mod, "tools.workspace_context": module}):
        spec.loader.exec_module(module)
    return module, jobs_mod


class WorkspaceContextTests(unittest.TestCase):
    def test_rebind_session_sync_swallows_optional_dependency_failures(self):
        module, jobs_mod = load_module()

        async def run_case():
            jobs_mod.client_session_id.set("session-1")
            with mock.patch.object(module, "resolve", side_effect=ModuleNotFoundError("dotenv")):
                module.rebind_session_sync("/tmp/missing-workspace")
                await asyncio.sleep(0)

        asyncio.run(run_case())


if __name__ == "__main__":
    unittest.main()
