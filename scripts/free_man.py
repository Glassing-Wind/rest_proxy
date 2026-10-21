"""Explicit standalone STDIO reader for one Free Man source corpus.

Run as python -m scripts.free_man --corpus /absolute/canonical/representation.
Loads no .env, account or runtime configuration. Starts only its own STDIO MCP
session, with no HTTP listener or operational tool registration. The client must
launch this process explicitly; same-account execution is not an OS sandbox.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys


async def _serve(server) -> None:
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', required=True, help='Explicit canonical absolute corpus representation')
    args = parser.parse_args(argv)
    try:
        from graphrag_core.lessons_corpus.mcp import create_server

        server = create_server(args.corpus)
        asyncio.run(_serve(server))
    except KeyboardInterrupt:
        return 0
    except Exception:
        print(json.dumps({'error': 'free-man-corpus-unavailable'}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
