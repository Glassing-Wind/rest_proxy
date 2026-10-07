#!/usr/bin/env python3
"""Watch this project: inspect owner readiness/scope, then explicitly enable project intent."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graphrag_core.embedded_session_client import validate_endpoint, decode  # noqa: E402


async def run(url: str, project_id: str, enable: bool) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    validate_endpoint(url)
    async with asyncio.timeout(30):
        async with streamable_http_client(url) as streams:
            async with ClientSession(*streams[:2]) as session:
                await session.initialize()
                preview = decode(await session.call_tool('configure_embedded_project_watch', {'project_id': project_id}))
                if not enable or not preview.get('ready'):
                    return preview
                return decode(await session.call_tool('configure_embedded_project_watch',
                    {'project_id': project_id, 'enable': True, 'expected_revision': preview['revision'],
                     'expected_run_id': preview['run_id']}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_id')
    parser.add_argument('--mcp-url', default=os.getenv('LM_PROXY_EMBEDDED_OWNER_MCP_URL', ''))
    parser.add_argument('--enable', action='store_true', help='Enable this project only when readiness checks pass')
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args.mcp_url, args.project_id, args.enable))
    except Exception as error:
        print(f'Watch setup failed ({type(error).__name__}); inspect owner configuration.', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get('ready') and (not args.enable or result.get('status') == 'enabled') else 1


if __name__ == '__main__':
    raise SystemExit(main())
