#!/usr/bin/env python3
"""Verify standards-based MCP roots integration through Streamable HTTP."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from mcp import types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import LATEST_PROTOCOL_VERSION


BASE_URL = os.environ.get("BRAIN_SERVER_BASE_URL") or (
    f"http://127.0.0.1:{os.environ.get('BRAIN_SERVER_PORT', '8001')}"
)
MCP_URL = f"{BASE_URL}/mcp"


def _tool_text(result: types.CallToolResult) -> str:
    return "\n".join(
        block.text for block in result.content if isinstance(block, types.TextContent)
    )


async def _run() -> None:
    with tempfile.TemporaryDirectory(prefix="graphrag-mcp-root-") as tmpdir:
        root_path = Path(tmpdir).resolve()

        async def list_roots(_context):
            return types.ListRootsResult(
                roots=[types.Root(uri=root_path.as_uri(), name="roots-smoke")]
            )

        async with streamable_http_client(MCP_URL) as (read_stream, write_stream, _):
            async with ClientSession(
                read_stream,
                write_stream,
                list_roots_callback=list_roots,
                client_info=types.Implementation(name="graphrag-roots-smoke", version="1.0"),
            ) as session:
                initialize_result = await session.initialize()
                assert initialize_result.protocolVersion == LATEST_PROTOCOL_VERSION, initialize_result

                watch_result = await session.call_tool("watch_project", {})
                watch_text = _tool_text(watch_result)
                assert not watch_result.isError, watch_text
                assert "## MCP Client Roots Watch Sync" in watch_text, watch_text
                assert "Roots advertised: 1" in watch_text, watch_text
                assert str(root_path) in watch_text, watch_text

                unwatch_result = await session.call_tool(
                    "unwatch_project", {"workspace_id": str(root_path)}
                )
                unwatch_text = _tool_text(unwatch_result)
                assert not unwatch_result.isError, unwatch_text
                assert "Removed pinned watch for project" in unwatch_text, unwatch_text

    print("MCP roots integration check passed")
    print(f"- client root advertised and pinned: {root_path}")
    print("- temporary root watch removed")


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"MCP roots integration check failed: {exc}")
        raise SystemExit(1)
