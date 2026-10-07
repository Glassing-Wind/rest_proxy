"""Bounded cited route declarations; no inferred service/database flow."""
import hashlib
import json

from graphrag_core.indexing.embedded_outlines import read_outline_publication

MAX_FILES = 100
MAX_READ_BYTES = 8 * 1024 * 1024


async def route_overview(driver, project_id: str, *, limit: int = 20,
                         api_contains: str | None = None, include_tests: bool = False):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Route overview limit must be 1..100')
    if api_contains is not None and (not isinstance(api_contains, str) or len(api_contains) > 512):
        raise ValueError('API filter must be at most 512 characters')
    publication = await read_outline_publication(driver, project_id)
    if publication is None:
        return {'project_id': project_id, 'status': 'not_published'}
    manifest = publication['manifest']
    files = sorted(manifest['files'], key=lambda row: row['path'])
    result = {'project_id': project_id, 'run_id': publication['run_id'], 'status': 'published',
              'semantics': 'declared-route-observations', 'coverage_complete': False,
              'unsupported': ['service-hops', 'database-hops', 'runtime-routing', 'all-frameworks'],
              'published_files': len(files), 'scanned_files': 0, 'routes': [],
              'matched_observations': 0, 'excluded_test_files': 0,
              'filters': {'api_contains': api_contains, 'include_tests': include_tests},
              'truncated': False, 'truncation_reasons': [], 'scan_next_file': None}
    used = 0
    for item in files[:MAX_FILES]:
        if not item.get('facts_sha256'):
            return dict(result, status='reindex-required-for-fact-contract-v1',
                        routes=[])
        async with driver.session() as session:
            async def read(tx):
                rows = await (await tx.run(
                    'MATCH (s:SourceEvidence {id:$fid}), (p:OutlinePublication {id:$project}) '
                    'WHERE s.project_id=$project AND s.run_id=p.run_id '
                    'RETURN size(s.content) AS source_chars, size(s.facts_json) AS fact_chars, '
                    's.run_id AS run_id', fid=f"{project_id}:file:{item['path']}", project=project_id,
                )).data()
                if not rows or rows[0]['run_id'] != publication['run_id']:
                    raise RuntimeError('Route observations do not match their publication')
                if any(type(rows[0][key]) is not int or rows[0][key] < 0
                       for key in ('source_chars', 'fact_chars')):
                    raise RuntimeError('Route source/facts are unavailable')
                # Four bytes per character is a conservative UTF-8 retrieval ceiling.
                if used + 4 * (rows[0]['source_chars'] + rows[0]['fact_chars']) > MAX_READ_BYTES:
                    return None
                return (await (await tx.run(
                    'MATCH (s:SourceEvidence {id:$fid}) '
                    'RETURN s.content AS content, s.facts_json AS facts, s.sha256 AS sha256, '
                    's.run_id AS run_id', fid=f"{project_id}:file:{item['path']}",
                )).data())[0]
            row = await session.execute_read(read)
        if row is None:
            result['truncation_reasons'].append('read-byte-budget')
            break
        used += len(row['content'].encode()) + len(row['facts'].encode())
        if (row['run_id'] != publication['run_id'] or row['sha256'] != item['sha256']
                or hashlib.sha256(row['content'].encode()).hexdigest() != item['sha256']):
            raise RuntimeError('Route source does not match its publication hash')
        if hashlib.sha256(row['facts'].encode()).hexdigest() != item['facts_sha256']:
            raise RuntimeError('Route facts do not match their publication hash')
        facts = json.loads(row['facts'])
        if facts.get('version') != 1:
            return dict(result, status='reindex-required-for-fact-contract-v1',
                        routes=[])
        result['scanned_files'] += 1
        path = item['path']
        parts = path.lower().split('/')
        name = parts[-1]
        is_test = any(part in {'test', 'tests', '__tests__'} for part in parts[:-1]) or (
            name.startswith('test_') or '.test.' in name or '.spec.' in name or name.endswith('_test.py'))
        if is_test and not include_tests:
            result['excluded_test_files'] += 1
            continue
        if api_contains and api_contains not in path:
            continue
        for observation in facts.get('native', {}).get('route_defs', []):
            result['matched_observations'] += 1
            entry = {'file_path': path, 'source_sha256': item['sha256'],
                     'facts_sha256': item['facts_sha256'], 'run_id': publication['run_id'],
                     'observation': observation, 'start_line': None, 'end_line': None}
            # Native route declarations currently have no verified line spans.
            if len(result['routes']) >= limit:
                if 'row-limit' not in result['truncation_reasons']:
                    result['truncation_reasons'].append('row-limit')
                continue
            result['routes'].append(entry)
            if len(json.dumps(result, ensure_ascii=False).encode()) > 44000:
                result['routes'].pop()
                if 'output-byte-budget' not in result['truncation_reasons']:
                    result['truncation_reasons'].append('output-byte-budget')
    scanned = result['scanned_files']
    if scanned < len(files):
        result['scan_next_file'] = files[scanned]['path']
        if scanned == MAX_FILES:
            result['truncation_reasons'].append('file-scan-limit')
    result['scan_read_bytes'] = used
    result['truncated'] = bool(result['truncation_reasons'])
    if len(json.dumps(result, ensure_ascii=False).encode()) > 48000:
        raise ValueError('Route overview exceeds output byte budget')
    return result
