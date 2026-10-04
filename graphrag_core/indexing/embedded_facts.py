"""Bounded syntactic facts for published evidence; no inferred callee resolution."""
from __future__ import annotations

from bisect import bisect_right
import hashlib
import json

CALL_QUERIES = {
    'python': '(call function: (_) @target) @call',
    'javascript': '(call_expression function: (_) @target) @call',
    'typescript': '(call_expression function: (_) @target) @call',
    'tsx': '(call_expression function: (_) @target) @call',
}


def build_file_facts(ts_pack, source: str, language: str, file_path: str,
                     result: dict, symbols: list[dict]) -> dict:
    """Preserve native imports/routes and exact-span call observations before publication."""
    native = ts_pack.extract_file_facts(source, language, file_path)
    imports = result.get('imports') or []
    if len(imports) > 1000:
        raise ValueError('File exceeds 1000 imports')
    calls = []
    if language in CALL_QUERIES:
        extraction = ts_pack.extract(source, {'language': language, 'patterns': {
            'calls': {'query': CALL_QUERIES[language], 'capture_output': 'Text', 'max_results': 10001},
        }})
        matches = extraction['results']['calls']['matches']
        if len(matches) > 10000:
            raise ValueError('File exceeds 10000 call observations')
        raw = source.encode()
        line_starts = [0] + [i + 1 for i, byte in enumerate(raw) if byte == 10]
        for match in matches:
            captures = {capture['name']: capture for capture in match['captures']}
            call, target = captures['call'], captures['target']
            start = call['start_byte']
            end = start + len(call['text'].encode())
            if raw[start:end] != call['text'].encode():
                raise ValueError('Call observation does not match original source bytes')
            owners = [symbol for symbol in symbols if symbol['kind'] in {'Function', 'Method'}
                      and symbol['start_byte'] <= start and end <= symbol['end_byte']]
            owner = min(owners, key=lambda symbol: (symbol['end_byte'] - symbol['start_byte'], symbol['id'])) if owners else None
            calls.append({'target': target['text'][:512], 'target_truncated': len(target['text']) > 512,
                          'start_byte': start, 'end_byte': end,
                          'start_line': bisect_right(line_starts, start),
                          'end_line': bisect_right(line_starts, max(start, end - 1)),
                          'owner_id': owner['id'] if owner else None,
                          'owner_name': owner['name'] if owner else None,
                          'resolution': 'syntactic-observation'})
    return {'version': 1, 'imports': imports, 'calls': calls, 'native': native,
            'call_support': 'supported' if language in CALL_QUERIES else 'unsupported-language'}


async def read_file_facts(driver, project_id: str, file_path: str, *, limit: int = 50, offset: int = 0) -> dict | None:
    if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
        raise ValueError('Use fact limit 1..100 per group and offset 0..10000')

    async def read(tx):
        return await (await tx.run(
            'MATCH (s:SourceEvidence {id:$fid}), (p:OutlinePublication {id:$project}) '
            'WHERE s.project_id=$project AND s.run_id=p.run_id '
            'RETURN s.facts_json AS facts, s.sha256 AS sha256, s.run_id AS run_id, '
            's.content AS content, p.manifest_json AS manifest',
            fid=f'{project_id}:file:{file_path}', project=project_id,
        )).data()

    async with driver.session() as session:
        rows = await session.execute_read(read)
    if not rows:
        return None
    row = rows[0]
    manifest = json.loads(row['manifest'])
    expected = next((file['sha256'] for file in manifest['files'] if file['path'] == file_path), None)
    if expected != row['sha256'] or hashlib.sha256(row['content'].encode()).hexdigest() != expected:
        raise RuntimeError('Stored fact source evidence does not match its publication')
    facts = json.loads(row['facts'])
    fact_hash = next((file.get('facts_sha256') for file in manifest['files'] if file['path'] == file_path), None)
    if fact_hash is not None and hashlib.sha256(row['facts'].encode()).hexdigest() != fact_hash:
        raise RuntimeError('Stored parser facts do not match their publication hash')
    if facts.get('version') != 1 or fact_hash is None:
        return {'project_id': project_id, 'file_path': file_path, 'run_id': row['run_id'],
                'source_sha256': row['sha256'], 'status': 'reindex-required-for-fact-contract-v1'}
    groups = {'imports': facts['imports'], 'calls': facts['calls']}
    groups.update({('native_' + key if key in groups else key): value
                   for key, value in facts['native'].items()})
    # Native fact groups may have scalars; preserve bounded top-level metadata.
    output = {key: value[offset:offset + limit] if isinstance(value, list) else value for key, value in groups.items()}
    counts = {key: len(value) for key, value in groups.items() if isinstance(value, list)}
    encoded = json.dumps(output, ensure_ascii=False)
    if len(encoded.encode()) > 24000:
        raise ValueError('Facts exceed output byte budget; reduce limit')
    return {'project_id': project_id, 'file_path': file_path, 'run_id': row['run_id'],
            'source_sha256': row['sha256'], 'origin': 'published parser observations',
            'call_support': facts['call_support'], 'facts': output, 'counts': counts,
            'offset': offset, 'next_offset': offset + limit if any(count > offset + limit for count in counts.values()) else None,
            'truncated_groups': [key for key, count in counts.items() if count > offset + limit]}
