#!/usr/bin/env python3
"""Inspect bounded published source and symbols in an experimental outline DB."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def run(args):
    from graphrag_core.indexing.embedded_outlines import read_outline_file
    from memory.embedded_ladybug import LadybugGraphDriver

    if not args.database.exists():
        raise ValueError('Outline database does not exist')
    driver = LadybugGraphDriver(str(args.database))
    try:
        return await read_outline_file(driver, args.project_id, args.file,
                                       start_line=args.start_line, max_lines=args.max_lines)
    finally:
        await driver.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--file', required=True)
    parser.add_argument('--start-line', type=int, default=1)
    parser.add_argument('--max-lines', type=int, default=40)
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, sort_keys=True))
        return 0 if result is not None else 2
    except Exception as exc:
        print(f'Embedded outline inspection failed: {type(exc).__name__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
