"""Capture bounded, verified analyzer context in one published read transaction.

The caller owns the graph runtime and its operation lock. No live files, encoders,
user annotations or independent database handles participate in this snapshot.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import PurePosixPath

from memory.embedded_schema import SYMBOL_LABELS

MAX_FILES = 64
MAX_SYMBOLS_PER_FILE = 128
MAX_INPUT_BYTES = 256 * 1024


class LearningSnapshotError(RuntimeError):
    """A fixed public failure code without storage exception or source text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def validate_request(project_ids: list[str], max_files: int) -> None:
    if type(max_files) is not int or not 1 <= max_files <= MAX_FILES:
        raise ValueError('Use max_files 1..64')
    if not isinstance(project_ids, list) or not 1 <= len(project_ids) <= 2:
        raise ValueError('Use one or two explicit project IDs')
    for project in project_ids:
        if (not isinstance(project, str) or not project or len(project) > 128
                or ':' in project or '\0' in project):
            raise ValueError('Use nonempty project IDs of at most 128 characters without colons')


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def outline_hash(symbols: list[dict]) -> str:
    """Hash exactly the persisted declaration fields, independent of query ordering."""
    keys = ('id', 'kind', 'name', 'start_line', 'end_line', 'signature')
    return _digest(canonical(sorted(
        ({key: symbol[key] for key in keys} for symbol in symbols), key=lambda symbol: symbol['id'])))


def _manifest(encoded: str) -> dict:
    manifest = json.loads(encoded)
    unsigned = {key: value for key, value in manifest.items() if key != 'sha256'}
    if manifest.get('sha256') != _digest(canonical(unsigned)):
        raise LearningSnapshotError('invalid-evidence')
    files = manifest['files']
    paths = [file['path'] for file in files]
    if len(set(paths)) != len(paths) or len(files) > 5000:
        raise LearningSnapshotError('invalid-evidence')
    for path in paths:
        relative = PurePosixPath(path)
        if (not path or relative.is_absolute() or '..' in relative.parts
                or str(relative) != path or len(path) > 4096):
            raise LearningSnapshotError('invalid-evidence')
    return manifest


_SYMBOL_QUERY = ' UNION ALL '.join(
    f'MATCH (f:File {{id:$fid}})-[:CONTAINS]->(s:{label}) '
    f"RETURN '{label}' AS kind, s.id AS id, s.name AS name, "
    's.project_id AS project_id, f.project_id AS file_project_id, '
    's.start_line AS start_line, s.end_line AS end_line, s.signature AS signature LIMIT 129'
    for label in SYMBOL_LABELS
)


async def _file(tx, project: str, run: str, entry: dict) -> tuple[dict, dict]:
    path = entry['path']
    fid = f'{project}:file:{path}'
    rows = await (await tx.run(
        'MATCH (s:SourceEvidence {id:$fid}) RETURN s.project_id AS project_id, '
        's.path AS path, s.run_id AS run_id, s.sha256 AS sha256, s.content AS content, '
        's.language AS language, s.facts_json AS facts', fid=fid,
    )).data()
    if len(rows) != 1:
        raise LearningSnapshotError('invalid-evidence')
    row = rows[0]
    if (row['project_id'] != project or row['run_id'] != run or row['path'] != path
            or row['language'] != entry['language'] or row['sha256'] != entry['sha256']
            or _digest(row['content']) != entry['sha256']
            or _digest(row['facts']) != entry.get('facts_sha256')):
        raise LearningSnapshotError('invalid-evidence')
    facts = json.loads(row['facts'])
    if facts.get('version') != 1:
        raise LearningSnapshotError('unsupported-fact-version')
    if not entry.get('outline_sha256'):
        raise LearningSnapshotError('reindex-required-for-outline-provenance')
    symbols = await (await tx.run(_SYMBOL_QUERY, fid=fid)).data()
    if len(symbols) > MAX_SYMBOLS_PER_FILE:
        raise LearningSnapshotError('snapshot-too-large')
    lines = max(1, len(row['content'].splitlines()))
    names = set()
    for symbol in symbols:
        if (symbol['project_id'] != project or symbol['file_project_id'] != project
                or not symbol['id'].startswith(project + ':symbol:') or symbol['id'] in names
                or not isinstance(symbol['name'], str) or not 1 <= len(symbol['name']) <= 512
                or type(symbol['start_line']) is not int or type(symbol['end_line']) is not int
                or not 1 <= symbol['start_line'] <= symbol['end_line'] <= lines):
            raise LearningSnapshotError('invalid-evidence')
        if symbol['signature'] is not None and len(symbol['signature']) > 2048:
            raise LearningSnapshotError('snapshot-too-large')
        names.add(symbol['id'])
    if outline_hash(symbols) != entry['outline_sha256']:
        raise LearningSnapshotError('invalid-evidence')
    symbols.sort(key=lambda symbol: (symbol['start_line'], symbol['kind'], symbol['id']))
    citation = {'project_id': project, 'run_id': run, 'file_path': path,
                'source_sha256': entry['sha256'], 'facts_sha256': entry['facts_sha256'],
                'outline_sha256': entry['outline_sha256'],
                'total_symbols': len(symbols),
                'symbols': [{key: symbol[key] for key in
                             ('id', 'name', 'kind', 'start_line', 'end_line')}
                            for symbol in symbols]}
    file_context = {
        'path': path, 'language': row['language'],
        'symbols': [{'name': symbol['name'], 'kind': symbol['kind'], 'file_path': path,
                     'signature': symbol['signature'],
                     'metadata': {key: symbol[key] for key in ('id', 'start_line', 'end_line')}}
                    for symbol in symbols],
        'metadata': {'project_id': project, 'run_id': run,
                     'source_sha256': entry['sha256'], 'facts_sha256': entry['facts_sha256'],
                     'outline_sha256': entry['outline_sha256'],
                     'symbol_count': len(symbols), 'source': 'published-native-outlines',
                     'observed_calls': len(facts['calls']), 'observed_imports': len(facts['imports']),
                     'call_resolution': 'syntactic-observation'},
    }
    return file_context, citation


async def capture_learning_snapshots(driver, project_ids: list[str], *, max_files: int = 20) -> list[dict]:
    """Capture one/two scopes atomically, with explicit partial file coverage.

Selected files include all retained declarations (up to 128 per file). Larger
inputs fail explicitly. The returned payload contains no original source text.
"""
    validate_request(project_ids, max_files)

    async def capture(tx):
        snapshots = []
        for project in project_ids:
            rows = await (await tx.run(
                'MATCH (p:OutlinePublication {id:$project}) RETURN p.run_id AS run_id, '
                'p.root_path AS root_path, p.manifest_json AS manifest', project=project,
            )).data()
            if not rows:
                raise LearningSnapshotError('not-published')
            publication = rows[0]
            manifest = _manifest(publication['manifest'])
            files, sources = [], []
            for entry in sorted(manifest['files'], key=lambda file: file['path'])[:max_files]:
                file_context, source = await _file(tx, project, publication['run_id'], entry)
                files.append(file_context)
                sources.append(source)
            complete = len(files) == len(manifest['files'])
            if complete and sum(source['total_symbols'] for source in sources) != manifest['symbols']:
                raise LearningSnapshotError('invalid-evidence')
            coverage = {'published_files': len(manifest['files']), 'included_files': len(files),
                        'omitted_files': len(manifest['files']) - len(files), 'complete': complete,
                        'file_selection': 'path-order', 'max_files': max_files,
                        'included_symbols': sum(source['total_symbols'] for source in sources),
                        'scope': 'selected-published-files',
                        'limitations': ['Selected native declarations; complete AST is not retained.',
                                        'Parser binary identity was not recorded in this publication.',
                                        'No adaptive pattern training or custom plaintext parsing in this operation.']}
            directory_counts = Counter(
                file['path'].split('/')[0] for file in files if '/' in file['path'])
            citation = {'project_id': project, 'run_id': publication['run_id'],
                        'manifest_sha256': manifest['sha256']}
            payload = {'repo_name': project, 'root_path': publication['root_path'], 'files': files,
                       'architecture': {'top_level_directories': [
                           {'path': path, 'file_count': count}
                           for path, count in sorted(directory_counts.items(), key=lambda item: (-item[1], item[0]))]},
                       'metadata': {**citation, 'coverage': coverage,
                                    'source': 'rest-proxy-published-evidence',
                                    'fact_contract': 'embedded-parser-facts-v1'}}
            citation['snapshot_sha256'] = _digest(canonical(payload))
            payload['metadata']['snapshot_sha256'] = citation['snapshot_sha256']
            snapshots.append({'payload': payload, 'citation': citation,
                              'coverage': coverage, 'sources': sources})
            if len(canonical(snapshots).encode()) > MAX_INPUT_BYTES:
                raise LearningSnapshotError('snapshot-too-large')
        return snapshots

    try:
        async with driver.session() as session:
            return await session.execute_read(capture)
    except LearningSnapshotError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise LearningSnapshotError('invalid-evidence') from None
