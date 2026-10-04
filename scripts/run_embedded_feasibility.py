#!/usr/bin/env python3
"""Disposable compatibility probes; records failures without claiming backend parity."""
from __future__ import annotations

import argparse
import ast
import asyncio
from importlib.metadata import version
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def probe() -> dict:
    from memory.embedded_kuzu import KuzuGraphDriver
    from memory.embedded_lancedb import LanceVectorStore

    checks = {}
    with tempfile.TemporaryDirectory() as temp:
        graph = KuzuGraphDriver(str(Path(temp) / 'graph'))
        try:
            async with graph.session() as session:
                await session.run("CREATE (:File {id:'f', project_id:'p'})")
                await session.run("CREATE (:Function {id:'fn', name:'current', project_id:'p'})")
                await session.run("MATCH (f:File), (s:Function) CREATE (f)-[:CONTAINS]->(s)")
                tree = ast.parse((ROOT / 'tools/brain/code_intel/file_describe.py').read_text())
                query = next(node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                             and isinstance(node.value, str) and 'RETURN head([label' in node.value)
                try:
                    rows = await (await session.run(query, fid='f')).data()
                    checks['actual_describe_file_query'] = {'passed': len(rows) == 1}
                except Exception as exc:
                    checks['actual_describe_file_query'] = {'passed': False, 'error': str(exc)[:500]}
                async def failing_write(tx):
                    await tx.run("CREATE (:File {id:'rollback', project_id:'p'})")
                    raise RuntimeError('deliberate rollback probe')
                try:
                    await session.execute_write(failing_write)
                except RuntimeError:
                    pass
                rows = await (await session.run("MATCH (f:File {id:'rollback'}) RETURN f.id AS id")).data()
                checks['write_transaction_rollback'] = {'passed': not rows, 'remaining_rows': len(rows)}
            checks['independent_session_connections'] = {
                'passed': graph.session()._conn is not graph.session()._conn}
        finally:
            await graph.close()
        store = LanceVectorStore(str(Path(temp) / 'vectors'))
        chunk = {'id': 'c', 'project_id': 'p', 'file_path': 'file.py', 'chunk_index': 0,
                 'content': 'authenticate token', 'vector': [0.1] * 768}
        try:
            await store.upsert_chunks([chunk])
            await store.upsert_chunks([{**chunk, 'content': 'updated authenticate token'}])
            checks['vector_upsert_idempotent'] = {'passed': await store.count('p') == 1}
            checks['lexical_retrieval'] = {'passed': bool(await store.search_text('authenticate', 'p'))}
            checks['project_scope'] = {'passed': not await store.search_vector([0.1] * 768, 'other')}
        except Exception as exc:
            checks['vector_store'] = {'passed': False, 'error': str(exc)[:500]}
    return {'versions': {name: version(name) for name in ('kuzu', 'lancedb', 'pyarrow')},
            'checks': checks, 'adoption_ready': False,
            'limits': ['Small disposable fixtures, not a query-corpus or performance benchmark.',
                       'No end-to-end indexing, memory/store routing, RRF, or ANN index validation.',
                       'Kuzu upstream is archived; graph engine selection remains open.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
