#!/usr/bin/env python3
"""Verify that the local STDIO MCP transport remains usable after startup."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


async def _run() -> None:
    env = dict(os.environ)
    env["LM_PROXY_WATCHER_ENABLED"] = "0"
    with tempfile.TemporaryDirectory(prefix="graphrag-mcp-stdio-") as cwd:
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "mcp_server.py")],
            cwd=cwd,
            env=env,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                await asyncio.sleep(1.1)
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert "get_mcp_tool_catalog" in names, names

                result = await session.call_tool(
                    "get_mcp_tool_catalog", {"query": "choose a code search tool"}
                )
                assert not result.isError, result
                assert result.content, result

    print("MCP STDIO lifecycle check passed")
    print(f"- tools available after startup delay: {len(names)}")
    print("- post-delay tool call succeeded")


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"MCP STDIO lifecycle check failed: {exc}")
        raise SystemExit(1)
