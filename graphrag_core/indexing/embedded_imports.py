"""Cited declared-import overview; does not invent symbol-binding or implicit edges."""
import hashlib
import json
from collections import Counter, defaultdict

from graphrag_core.indexing.embedded_outlines import read_outline_publication

MAX_FILES = 100
MAX_READ_BYTES = 8 * 1024 * 1024


async def import_overview(driver, project_id: str, *, limit: int = 20, include_implicit: bool = False):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Import overview limit must be 1..100')
    publication = await read_outline_publication(driver, project_id)
    if publication is None:
        return {'project_id': project_id, 'status': 'not_published'}
    manifest = publication['manifest']
    files = sorted(manifest['files'], key=lambda row: row['path'])
    result = {'project_id': project_id, 'run_id': publication['run_id'], 'status': 'published',
              'semantics': 'declared-import-observations', 'resolved_symbol_edges': False,
              'implicit': {'requested': bool(include_implicit), 'status': 'unsupported'},
              'scope': 'scanned published files only', 'published_files': len(files),
              'scanned_files': 0, 'truncated': False, 'truncation_reasons': [],
              'declarations': 0, 'named_items': 0, 'wildcard_declarations': 0,
              'top_named_imports': [], 'top_files': [], 'scan_next_file': None}
    counts = Counter()
    citations = defaultdict(list)
    file_rows = []
    used = 0
    for item in files[:MAX_FILES]:
        if not item.get('facts_sha256'):
            return dict(result, status='reindex-required-for-fact-contract-v1',
                        top_named_imports=[], top_files=[])
        async with driver.session() as session:
            async def read(tx):
                rows = await (await tx.run(
                    'MATCH (s:SourceEvidence {id:$fid}), (p:OutlinePublication {id:$project}) '
                    'WHERE s.project_id=$project AND s.run_id=p.run_id '
                    'RETURN size(s.content) AS source_chars, size(s.facts_json) AS fact_chars, '
                    's.run_id AS run_id', fid=f"{project_id}:file:{item['path']}", project=project_id,
                )).data()
                if not rows or rows[0]['run_id'] != publication['run_id']:
                    raise RuntimeError('Import observations do not match their publication')
                if any(type(rows[0][key]) is not int or rows[0][key] < 0
                       for key in ('source_chars', 'fact_chars')):
                    raise RuntimeError('Import source/facts are unavailable')
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
            raise RuntimeError('Import source does not match its publication hash')
        if hashlib.sha256(row['facts'].encode()).hexdigest() != item['facts_sha256']:
            raise RuntimeError('Import facts do not match their publication hash')
        facts = json.loads(row['facts'])
        if facts.get('version') != 1:
            return dict(result, status='reindex-required-for-fact-contract-v1',
                        top_named_imports=[], top_files=[])
        imports = facts['imports']
        result['scanned_files'] += 1
        result['declarations'] += len(imports)
        named = 0
        for observation in imports:
            result['wildcard_declarations'] += bool(observation.get('is_wildcard'))
            source = observation.get('source') or ''
            span = observation.get('span') or {}
            citation = {'file_path': item['path'], 'source_sha256': item['sha256'],
                        'facts_sha256': item['facts_sha256'], 'run_id': publication['run_id'],
                        'start_line': span['start_line'] + 1 if type(span.get('start_line')) is int else None,
                        'end_line': span['end_line'] + 1 if type(span.get('end_line')) is int else None}
            for name in observation.get('items') or []:
                if not isinstance(name, str) or len(name) > 512 or len(source) > 512:
                    raise ValueError('Import identifier exceeds overview bounds')
                key = (source, name)
                counts[key] += 1
                named += 1
                if len(citations[key]) < 3:
                    citations[key].append(citation)
        result['named_items'] += named
        if imports:
            file_rows.append({'file_path': item['path'], 'source_sha256': item['sha256'],
                              'facts_sha256': item['facts_sha256'], 'declarations': len(imports),
                              'named_items': named})
    scanned = result['scanned_files']
    if scanned < len(files):
        result['truncated'] = True
        result['scan_next_file'] = files[scanned]['path']
        if scanned == MAX_FILES:
            result['truncation_reasons'].append('file-scan-limit')
    result['scan_read_bytes'] = used
    for (source, name), count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]:
        entry = {'source': source, 'name': name, 'observations': count, 'citations': citations[(source, name)],
                 'citations_complete': count <= 3}
        result['top_named_imports'].append(entry)
        if len(json.dumps(result, ensure_ascii=False).encode()) > 42000:
            result['top_named_imports'].pop()
            result['truncation_reasons'].append('output-byte-budget')
            break
    for entry in sorted(file_rows, key=lambda row: (-row['named_items'], -row['declarations'], row['file_path']))[:limit]:
        result['top_files'].append(entry)
        if len(json.dumps(result, ensure_ascii=False).encode()) > 44000:
            result['top_files'].pop()
            result['truncation_reasons'].append('output-byte-budget')
            break
    result['ranked_rows_truncated'] = len(counts) > len(result['top_named_imports']) or len(file_rows) > len(result['top_files'])
    if result['ranked_rows_truncated'] and not result['truncation_reasons']:
        result['truncation_reasons'].append('ranked-row-limit')
    result['truncated'] |= result['ranked_rows_truncated']
    if len(json.dumps(result, ensure_ascii=False).encode()) > 48000:
        raise ValueError('Import overview exceeds output byte budget')
    return result
