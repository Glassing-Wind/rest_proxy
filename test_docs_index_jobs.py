import asyncio
import types
import unittest
from unittest import mock

from tools.brain.docs import index_jobs


class FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class FakeThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


class DocsIndexJobTests(unittest.TestCase):
    def test_download_documentation_records_session_and_runtime(self):
        mcp = FakeMCP()
        index_jobs.register(mcp)
        token = index_jobs.client_session_id.set("session-123")
        index_jobs._JOBS.clear()
        runtime = {
            "cmd": ["/env/bin/python"],
            "python": "/env/bin/python",
            "source": "managed",
            "conda_env": "lmproxy",
        }
        proc = types.SimpleNamespace(stdout=None, stderr=None)
        try:
            with mock.patch.object(
                index_jobs, "resolve_python_runtime", return_value=runtime
            ), mock.patch.object(index_jobs.subprocess, "Popen", return_value=proc), mock.patch.object(
                index_jobs.threading, "Thread", FakeThread
            ), mock.patch("builtins.open", mock.mock_open()), mock.patch.object(
                index_jobs.os, "makedirs"
            ):
                output = asyncio.run(
                    mcp.tools["download_documentation"](
                        ["https://example.com/docs"], "example"
                    )
                )
        finally:
            index_jobs.client_session_id.reset(token)

        job_id = next(iter(index_jobs._JOBS))
        self.assertEqual(index_jobs._JOBS[job_id]["session_id"], "session-123")
        self.assertIn("/env/bin/python [managed]", output)


if __name__ == "__main__":
    unittest.main()
