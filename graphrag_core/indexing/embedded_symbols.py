"""Exact published symbol context with bounded static caller/callee evidence."""
import json

from graphrag_core.indexing.embedded_outlines import read_outline_file
from graphrag_core.indexing.embedded_relationships import read_relationships
from memory.embedded_schema import SYMBOL_LABELS


def bounded(result):
    if len(json.dumps(result, ensure_ascii=False).encode()) > 48000:
        raise ValueError('Symbol context exceeds output byte budget; narrow the selection')
    return result


async def symbol_context(driver, project_id: str, symbol_name: str, *, file_path: str = '',
                         signature: str = '', include_source: bool = True,
                         max_lines: int = 80, max_chars: int = 4000, full_source: bool = False):
    if not symbol_name or len(symbol_name) > 512:
        raise ValueError('Use an exact symbol name of at most 512 characters')

    async def read(tx):
        matches = []
        for label in SYMBOL_LABELS:
            matches.extend(await (await tx.run(
                f'MATCH (f:File)-[:CONTAINS]->(s:{label}), (p:OutlinePublication {{id:$project}}) '
                'WHERE f.project_id=$project AND s.project_id=$project AND s.name=$name '
                'AND ($file="" OR f.path=$file) '
                'AND ($signature="" OR coalesce(s.signature, "") CONTAINS $signature) '
                f"RETURN '{label}' AS kind, s.id AS id, s.name AS name, f.path AS file_path, "
                's.start_line AS start_line, s.end_line AS end_line, s.signature AS signature, p.run_id AS run_id '
                'ORDER BY file_path, start_line, id LIMIT 21',
                project=project_id, name=symbol_name, file=file_path, signature=signature,
            )).data())
        return sorted(matches, key=lambda row: (row['file_path'], row['start_line'], row['kind'], row['id']))

    async with driver.session() as session:
        matches = await session.execute_read(read)
    if not matches:
        return bounded({'project_id': project_id, 'status': 'symbol_not_published', 'symbol_name': symbol_name})
    if len(matches) != 1:
        return bounded({'project_id': project_id, 'status': 'ambiguous', 'candidates': matches[:20],
                        'more_candidates': len(matches) > 20})
    symbol = matches[0]
    lines = min(max_lines, symbol['end_line'] - symbol['start_line'] + 1) if full_source else max_lines
    evidence = await read_outline_file(driver, project_id, symbol['file_path'], start_line=symbol['start_line'],
                                       max_lines=lines if include_source else 1, max_chars=max_chars)
    if evidence is None or evidence['run_id'] != symbol['run_id']:
        raise RuntimeError('Symbol context does not match its source publication')
    calls = {}
    for direction in ('in', 'out'):
        calls[direction] = await read_relationships(driver, project_id, kind='calls', file_path=symbol['file_path'],
                                                    direction=direction, limit=20, symbol_id=symbol['id'])
    if any(page is None or page['run_id'] != symbol['run_id'] for page in calls.values()):
        raise RuntimeError('Symbol relationships do not match its source publication')
    result = {'project_id': project_id, 'status': 'published', 'symbol': symbol,
              'run_id': symbol['run_id'], 'source_sha256': evidence['source_sha256'],
              'callers': calls['in'], 'callees': calls['out'], 'semantics': 'static-source-candidates',
              'call_resolution_scope': 'python-module-function-bindings-v1', 'call_graph_complete': False}
    if include_source:
        result['source'] = {key: evidence[key] for key in ('source', 'origin', 'total_lines', 'next_start_line')}
    return bounded(result)


async def call_chain(driver, project_id: str, symbol_name: str, *, depth: int = 3,
                     direction: str = 'down', file_path: str = '', signature: str = ''):
    """Traverse cited static candidates with bounded work and deterministic cycle handling."""
    if direction not in {'up', 'down'} or not 1 <= depth <= 5:
        raise ValueError('Use direction up/down and depth 1..5')
    root = await symbol_context(driver, project_id, symbol_name, file_path=file_path,
                                signature=signature, include_source=False)
    if root['status'] != 'published':
        return root
    run_id = root['run_id']
    result = {'project_id': project_id, 'status': 'published', 'run_id': run_id,
              'root': root['symbol'], 'source_sha256': root['source_sha256'],
              'direction': direction, 'depth': depth, 'relationships': [],
              'semantics': 'static-source-candidates', 'call_graph_complete': False,
              'call_resolution_scope': root['call_resolution_scope'],
              'truncated': False, 'truncation_reasons': [], 'expanded_symbols': 0}
    frontier = [root['symbol']['id']]
    seen = set(frontier)
    edges = set()
    reasons = set()
    halted = False
    for hop in range(1, depth + 1):
        next_frontier = []
        for symbol_id in frontier:
            page = await read_relationships(driver, project_id, kind='calls',
                                            direction='out' if direction == 'down' else 'in',
                                            limit=20, symbol_id=symbol_id)
            if page is None or page['run_id'] != run_id:
                raise RuntimeError('Call chain changed publication during traversal')
            if page.get('status'):
                result['status'] = page['status']
                return bounded(result)
            result['expanded_symbols'] += 1
            if page['next_cursor']:
                reasons.add('adjacency-limit-20')
            for link in page['relationships']:
                if link['id'] in edges:
                    continue
                target = link['callee_id'] if direction == 'down' else link['caller_id']
                if target not in seen and len(seen) >= 64:
                    reasons.add('symbol-limit-64')
                    continue
                if len(edges) >= 128:
                    reasons.add('relationship-limit-128')
                    halted = True
                    break
                candidate = dict(link, hop=hop, revisits_symbol=target in seen)
                result['relationships'].append(candidate)
                if len(json.dumps(result, ensure_ascii=False).encode()) > 44000:
                    result['relationships'].pop()
                    reasons.add('output-byte-budget')
                    halted = True
                    break
                edges.add(link['id'])
                if target not in seen:
                    seen.add(target)
                    next_frontier.append(target)
            if halted:
                break
        if halted or not next_frontier:
            break
        frontier = next_frontier
    result['truncated'] = bool(reasons)
    result['truncation_reasons'] = sorted(reasons)
    result['discovered_symbols'] = len(seen)
    result['depth_is_horizon'] = True
    return bounded(result)
