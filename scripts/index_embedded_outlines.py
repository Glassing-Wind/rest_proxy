#!/usr/bin/env python3
"""Index an explicit JSON array of relative source paths into a Ladybug outline DB.

Experimental standalone structural acceptance; no embeddings, call graph or MCP
job routing. This process owns the database for the complete operation.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))



async def run(args):
    from graphrag_core.indexing.embedded_outlines import index_outline_manifest
    from memory.embedded_ladybug import LadybugGraphDriver

    paths = json.loads(args.manifest.read_text())
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise ValueError('Manifest must be a JSON array of relative source paths')
    driver = LadybugGraphDriver(str(args.database))
    try:
        await driver.initialize_schema()
        return await index_outline_manifest(driver, str(args.root), args.project_id, paths)
    finally:
        await driver.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), sort_keys=True))
        return 0
    except Exception as exc:
        # Paths, source contents and native exception text can contain private data.
        print(f'Embedded outline indexing failed: {type(exc).__name__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
