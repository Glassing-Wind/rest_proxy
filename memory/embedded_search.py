"""Bounded native search policy for the common workspace tool; no legacy storage."""
from __future__ import annotations

import json
from pathlib import PurePosixPath

from memory.retrieval_metadata import passes_filters, path_allowed

OUTPUT_BYTES = 48 * 1024
CANDIDATE_LIMIT = 100


def prepare_search(workspace_ids, query, *, k=5, include_metadata=False,
                   dedupe_files=True, include_debug=False, max_per_file=0,
                   max_per_dir=2, meta_boost=0.005, mode='precise', fallback='none',
                   fallback_ratio=0.4, fallback_max=12, fallback_glob='',
                   exclude_tests=True, languages=None, min_imports=0, min_symbols=0,
                   require_diagnostics=False, require_context=False, crate_contains=None,
                   include_paths=None, exclude_paths=None):
    """Validate before opening engines/models; return a request or explicit refusal."""
    if not isinstance(workspace_ids, list) or not 1 <= len(workspace_ids) <= 8:
        raise ValueError('Use 1..8 explicit workspace IDs or roots')
    if any(not isinstance(value, str) or not value.strip() or '\0' in value
           or len(value.encode('utf-8')) > 4096 for value in workspace_ids):
        raise ValueError('Workspace IDs/roots must be nonempty strings of at most 4096 UTF-8 bytes')
    if not isinstance(query, str) or not query.strip() or len(query.encode('utf-8')) > 8192:
        raise ValueError('Query must be nonempty and at most 8192 UTF-8 bytes')
    for name, value, low, high in [('k', k, 1, 100), ('max_per_file', max_per_file, 0, 100),
                                 ('max_per_dir', max_per_dir, 0, 100),
                                 ('min_imports', min_imports, 0, 50000),
                                 ('min_symbols', min_symbols, 0, 50000)]:
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{name} must be an integer in {low}..{high}')
    for name, value in [('include_metadata', include_metadata), ('dedupe_files', dedupe_files),
                        ('include_debug', include_debug), ('exclude_tests', exclude_tests),
                        ('require_diagnostics', require_diagnostics), ('require_context', require_context)]:
        if type(value) is not bool:
            raise ValueError(f'{name} must be boolean')
    for name, values, count, length in [('languages', languages, 32, 64),
                                       ('include_paths', include_paths, 16, 256),
                                       ('exclude_paths', exclude_paths, 16, 256)]:
        if values is not None and (not isinstance(values, list) or len(values) > count
                or any(not isinstance(value, str) or not value or '\0' in value
                       or len(value.encode('utf-8')) > length for value in values)):
            raise ValueError(f'{name} must be a bounded list of nonempty strings')
    unsupported = []
    if mode == 'broad':
        unsupported.append('mode')
    elif mode not in {'precise', 'text', 'vector', 'hybrid'}:
        raise ValueError('Embedded mode must be precise, text, vector or hybrid')
    for name, value, default in [('include_debug', include_debug, False),
                                 ('meta_boost', meta_boost, 0.005),
                                 ('fallback', fallback, 'none'),
                                 ('fallback_ratio', fallback_ratio, 0.4),
                                 ('fallback_max', fallback_max, 12),
                                 ('fallback_glob', fallback_glob, '')]:
        if value != default and not (name == 'meta_boost' and value == 0):
            unsupported.append(name)
    if crate_contains:
        unsupported.append('crate_contains')
    if unsupported:
        return {'backend': 'embedded', 'status': 'unsupported-options', 'options': unsupported,
                'message': 'Use the bounded embedded profile; broad/legacy ranking, debug, '
                           'Cargo and grep fallback policies are not implemented here.'}
    return {'workspace_ids': list(dict.fromkeys(workspace_ids)), 'query': query, 'k': k,
            'requested_mode': mode, 'mode': 'hybrid' if mode == 'precise' else mode,
            'include_metadata': include_metadata, 'dedupe_files': dedupe_files,
            'max_per_file': max_per_file, 'max_per_dir': max_per_dir,
            'exclude_tests': exclude_tests, 'languages': languages,
            'min_imports': min_imports, 'min_symbols': min_symbols,
            'require_diagnostics': require_diagnostics, 'require_context': require_context,
            'include_paths': include_paths, 'exclude_paths': list(exclude_paths or [])}


def _encoded_size(payload):
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8'))


async def workspace_search(owner, retrieve, request):
    """Called under the process runtime lock; retrieve does not reacquire that lock."""
    projects = []
    candidates = []
    seen = set()
    exclusions = request['exclude_paths'] + (['*test*', '*tests*', '*Test*', '*Tests*']
                                             if request['exclude_tests'] else [])
    for workspace in request['workspace_ids']:
        project = await owner.resolve_project(workspace)
        if project is None:
            projects.append({'workspace_id': workspace, 'status': 'not_published'})
            continue
        project_id = project['project_id']
        if project_id in seen:
            continue
        seen.add(project_id)
        publication = await owner.overview(project_id)
        if publication is None or project['run_id'] != publication['run_id']:
            raise RuntimeError('Publication changed during embedded search')
        entry = {'project_id': project_id, 'workspace_path': project['workspace_path'],
                 'matched_by': project.get('matched_by'), 'status': 'published',
                 'run_id': publication['run_id'], 'manifest_sha256': publication['manifest_sha256']}
        projects.append(entry)
        if not publication.get('retrieval'):
            entry['status'] = 'retrieval_unavailable'
            continue
        rows = await retrieve(project_id, publication, request['query'], request['mode'], CANDIDATE_LIMIT)
        current = await owner.overview(project_id)
        if current is None or current['run_id'] != publication['run_id'] \
                or current['manifest_sha256'] != publication['manifest_sha256']:
            raise RuntimeError('Publication changed during embedded search')
        entry['candidates_read'] = len(rows)
        entry['candidate_limit_reached'] = len(rows) == CANDIDATE_LIMIT
        filtered = []
        for row in rows:
            if row['project_id'] != project_id or row['run_id'] != publication['run_id']:
                raise RuntimeError('Search result escaped the resolved publication')
            metadata = row['metadata']
            if not path_allowed(row['file_path'], include_paths=request['include_paths'],
                                exclude_paths=exclusions) or not passes_filters(
                    metadata, languages=request['languages'], min_imports=request['min_imports'],
                    min_symbols=request['min_symbols'], require_diagnostics=request['require_diagnostics'],
                    require_context=request['require_context']):
                continue
            result = {key: row[key] for key in ('project_id', 'run_id', 'ref_id', 'file_path',
                                                'content', 'source_sha256')}
            result['content_sha256'] = metadata['content_sha256']
            result['origin'] = 'stored_index_chunk'
            if request['include_metadata']:
                result['metadata'] = metadata
            filtered.append(result)
        entry['candidates_after_filters'] = len(filtered)
        candidates.append(filtered)

    # Native scores from separate project queries are not treated as comparable.
    merged = [rows[rank] for rank in range(max((len(rows) for rows in candidates), default=0))
              for rows in candidates if rank < len(rows)]
    results = []
    file_counts = {}
    dir_counts = {}
    for row in merged:
        file_key = (row['project_id'], row['file_path'])
        parts = PurePosixPath(row['file_path']).parts
        dir_key = (row['project_id'], parts[0] if len(parts) > 1 else '.')
        per_file = 1 if request['dedupe_files'] else request['max_per_file']
        if per_file and file_counts.get(file_key, 0) >= per_file:
            continue
        if request['max_per_dir'] and dir_counts.get(dir_key, 0) >= request['max_per_dir']:
            continue
        file_counts[file_key] = file_counts.get(file_key, 0) + 1
        dir_counts[dir_key] = dir_counts.get(dir_key, 0) + 1
        results.append(row)
    selected = results[:request['k']]
    payload = {'backend': 'embedded', 'status': 'published' if seen else 'not_published',
               'requested_mode': request['requested_mode'], 'mode': request['mode'],
               'projects': projects, 'results': selected, 'requested_limit': request['k'],
               'candidate_limit_per_project': CANDIDATE_LIMIT, 'coverage_complete': False,
               'ranking': 'per-project native rank, round-robin across resolved projects',
               'legacy_policy_parity': False, 'applied_meta_boost': 0,
               'result_limit_reached': len(results) > request['k'], 'output_truncated': False,
               'limits': ['Only published manifest chunks are searched; filtered candidate windows are bounded.',
                          'Legacy metadata boosting, implementation-intent expansion and clone reranking are not applied.',
                          'Stored chunk hashes do not establish current-file freshness or exact file line ranges.']}
    while _encoded_size(payload) > OUTPUT_BYTES and payload['results']:
        payload['results'] = payload['results'][:-1]
        payload['output_truncated'] = True
    if _encoded_size(payload) > OUTPUT_BYTES:
        raise ValueError('Embedded search receipt exceeds 48 KiB; narrow the workspace scope')
    return payload
