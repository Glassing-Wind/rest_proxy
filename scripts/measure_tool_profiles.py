#!/usr/bin/env python3
"""Measure serialized MCP schemas; token counts are encoding-specific, not billed usage."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def measure(encoding: str) -> dict:
    from mcp.server.fastmcp import FastMCP
    from tools import register_all
    from tools.brain.primary import apply_primary_tool_filter
    import tiktoken

    tokenizer = tiktoken.get_encoding(encoding)
    mcp = FastMCP('schema-measurement')
    # Set after imports that may load .env. No process-wide saved configuration changes.
    previous = os.environ.get('LM_PROXY_TOOL_PROFILE')
    try:
        os.environ['LM_PROXY_TOOL_PROFILE'] = 'all'
        register_all(mcp)
    finally:
        if previous is None:
            os.environ.pop('LM_PROXY_TOOL_PROFILE', None)
        else:
            os.environ['LM_PROXY_TOOL_PROFILE'] = previous
    rows = {}
    for profile in ('all', 'primary'):
        if profile == 'primary':
            apply_primary_tool_filter(mcp)
        schemas = [t.model_dump(mode='json', exclude_none=True) for t in asyncio.run(mcp.list_tools())]
        serialized = json.dumps(schemas, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        rows[profile] = {'tools': len(schemas), 'utf8_bytes': len(serialized.encode()),
                         'schema_tokens': len(tokenizer.encode(serialized, disallowed_special=()))}
    return {'encoding': encoding, 'method': 'compact JSON of FastMCP tools/list entries',
            'limitation': 'Not model-specific unless encoding is verified; not per-turn/billed usage.',
            'profiles': rows,
            'schema_token_reduction_percent': round(
                100 * (1 - rows['primary']['schema_tokens'] / rows['all']['schema_tokens']), 2)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--encoding', default='o200k_base')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    payload = json.dumps(measure(args.encoding), indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    print(payload, end='')
