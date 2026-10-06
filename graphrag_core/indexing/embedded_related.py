"""Bounded related-file evidence from one owned embedded publication."""
import json

from graphrag_core.indexing.embedded_outlines import read_outline_file
from graphrag_core.indexing.embedded_relationships import read_relationships


async def related_files(driver, project_id: str, file_path: str) -> dict:
    evidence = await read_outline_file(driver, project_id, file_path, max_lines=1, max_chars=256)
    if evidence is None:
        return {'project_id': project_id, 'status': 'file_not_published', 'file_path': file_path}
    result = {'project_id': project_id, 'status': 'published', 'file_path': file_path,
              'run_id': evidence['run_id'], 'source_sha256': evidence['source_sha256'],
              'semantics': 'static-source-candidates', 'coverage_complete': False,
              'groups': [], 'truncated': False,
              'unsupported': ['symbol-import-usage', 'implicit-imports', 'crate-build-resource-links',
                              'runtime-dispatch', 'same-directory-heuristics']}
    for kind in ('imports', 'calls', 'http_routes'):
        for direction in ('out', 'in'):
            try:
                page = await read_relationships(driver, project_id, kind=kind, file_path=file_path,
                                                direction=direction, limit=10)
            except ValueError as exc:
                if 'byte budget' not in str(exc):
                    raise
                page = await read_relationships(driver, project_id, kind=kind, file_path=file_path,
                                                direction=direction, limit=1)
            if page is None or page['run_id'] != result['run_id']:
                raise RuntimeError('Related files do not match their source publication')
            if page.get('status'):
                return dict(result, status=page['status'], groups=[])
            group = {'kind': kind, 'direction': direction, 'relationships': [],
                     'next_cursor': page['next_cursor'], 'truncated': bool(page['next_cursor'])}
            result['groups'].append(group)
            last_seen = ''
            for link in page['relationships']:
                if link['source_file'] == link['target_file']:
                    last_seen = link['id']
                    continue
                group['relationships'].append(link)
                if len(json.dumps(result, ensure_ascii=False).encode()) > 44000:
                    group['relationships'].pop()
                    group['next_cursor'] = last_seen
                    group['truncated'] = True
                    break
                last_seen = link['id']
            result['truncated'] |= group['truncated']
    if len(json.dumps(result, ensure_ascii=False).encode()) > 48000:
        raise ValueError('Related-file output exceeds byte budget')
    return result
