"""Owned, atomic Ladybug outline snapshots built from native ts-pack results.

Does not implement call/import/route graph parity or vector publication. Explicit
file manifests define replacement scope; files omitted from the manifest vanish
from this project's published outline. Parsing finishes before any graph write.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import uuid

from memory.embedded_schema import SYMBOL_LABELS
from ts_diagnostics import normalize_ts_pack_result


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def build_outline_snapshot(root_path: str, project_id: str, paths: list[str]) -> dict:
    import tree_sitter_language_pack as ts_pack

    if not project_id or len(project_id) > 128 or ':' in project_id:
        raise ValueError('A nonempty project ID without colons is required')
    root = Path(root_path).resolve(strict=True)
    if not root.is_dir() or len(paths) > 5000 or len(set(paths)) != len(paths):
        raise ValueError('Invalid root, duplicate manifest paths, or manifest exceeds 5000 files')
    files = []
    used_bytes = 0
    fact_count = 0
    fact_bytes = 0
    symbol_count = 0
    skipped_count = 0
    for relative in sorted(paths):
        rel = Path(relative)
        if rel.is_absolute() or '..' in rel.parts or relative != rel.as_posix():
            raise ValueError('Manifest paths must be normalized relative paths')
        path = root / rel
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError('Manifest symlinks or non-files are not supported')
        with path.open('rb') as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        used_bytes += len(raw)
        if len(raw) > 2 * 1024 * 1024 or used_bytes > 64 * 1024 * 1024 or b'\0' in raw:
            raise ValueError('Source snapshot is binary or exceeds byte limits')
        source = raw.decode('utf-8')
        language = ts_pack.detect_language(str(path))
        if not language:
            raise ValueError('Manifest contains a file without a detected parser language')
        ts_pack.get_parser(language)
        config = ts_pack.ProcessConfig(language)
        config.diagnostics = True
        result = normalize_ts_pack_result(source, language, ts_pack.process(source, config))
        if (result.get('metrics') or {}).get('error_count', 0):
            raise ValueError('Source parsing errors prevent outline publication')
        file_id = f'{project_id}:file:{relative}'
        symbols = []

        def walk(items, ancestry=()):
            nonlocal symbol_count, skipped_count
            for item in items:
                name = str(item.get('name') or '')
                kind = item.get('kind')
                span = item.get('span') or {}
                if kind in SYMBOL_LABELS and name:
                    start = span.get('start_line')
                    end = span.get('end_line')
                    if not isinstance(start, int) or not isinstance(end, int) or end < start:
                        raise ValueError('Symbol is missing a valid source span')
                    identity = _json([file_id, kind, ancestry, name,
                                      span.get('start_byte'), span.get('end_byte')])
                    symbols.append({'id': project_id + ':symbol:' + hashlib.sha256(
                        identity.encode()).hexdigest(), 'kind': kind, 'name': name,
                        'start': start + 1, 'end': end + 1, 'signature': item.get('signature'),
                        'start_byte': span['start_byte'], 'end_byte': span['end_byte']})
                    symbol_count += 1
                    if symbol_count > 25000:
                        raise ValueError('Outline exceeds 25000 symbols')
                else:
                    skipped_count += 1
                walk(item.get('children') or [], ancestry + (name,))

        walk(result.get('structure') or [])
        from graphrag_core.indexing.embedded_facts import build_file_facts
        facts = build_file_facts(ts_pack, source, language, relative, result, symbols)
        facts_json = _json(facts)
        fact_count += len(facts['calls']) + len(facts['imports'])
        fact_bytes += len(facts_json.encode())
        if fact_count > 50000 or fact_bytes > 64 * 1024 * 1024:
            raise ValueError('Snapshot exceeds bounded parser fact budget')
        files.append({'id': file_id, 'path': relative, 'sha256': hashlib.sha256(raw).hexdigest(),
                      'content': source, 'language': language, 'symbols': symbols,
                      'facts_json': facts_json,
                      'facts_sha256': hashlib.sha256(facts_json.encode()).hexdigest()})
    from graphrag_core.indexing.embedded_relationships import build_relationships
    relationships = build_relationships(files)
    manifest = {'files': [{key: file[key] for key in ('path', 'sha256', 'language', 'facts_sha256')}
                          for file in files], 'symbols': symbol_count,
                'unsupported_symbols': skipped_count, 'source_bytes': used_bytes,
                'relationships': {'version': 1, 'count': len(relationships),
                                  'symbol_import_resolution': 'python-unique-imported-function-v1',
                                  'counts': {kind: sum(link['kind'] == kind for link in relationships)
                                             for kind in ('calls', 'imports', 'symbol_imports', 'http_routes')},
                                  'ids': [link['id'] for link in relationships]}}
    manifest['sha256'] = hashlib.sha256(_json(manifest).encode()).hexdigest()
    return {'project_id': project_id, 'root_path': str(root), 'run_id': str(uuid.uuid4()),
            'files': files, 'relationships': relationships, 'manifest': manifest}


async def publish_outline_snapshot(driver, snapshot: dict) -> dict:
    """Replace one project's outlines, originals and receipt in a single transaction."""
    project = snapshot['project_id']
    if not project or ':' in project:
        raise ValueError('Invalid project scope')
    for file in snapshot['files']:
        if file['id'] != f"{project}:file:{file['path']}":
            raise ValueError('File identity is outside the publication scope')
        if any(not symbol['id'].startswith(project + ':symbol:') for symbol in file['symbols']):
            raise ValueError('Symbol identity is outside the publication scope')

    from graphrag_core.indexing.embedded_relationships import canonical
    files_by_id = {file['id']: file for file in snapshot['files']}
    relationships = snapshot.get('relationships', [])
    contract = snapshot['manifest'].get('relationships')
    if (relationships or contract) and (not contract or contract.get('version') != 1 or contract['count'] != len(relationships)
                          or contract['ids'] != [link['id'] for link in relationships]):
        raise ValueError('Relationship manifest does not match the snapshot')
    for link in relationships:
        payload = json.loads(link['payload_json'])
        source = files_by_id.get(link['source_id'])
        target = files_by_id.get(link['target_id'])
        if (not source or not target or link['kind'] not in {'calls', 'imports', 'symbol_imports', 'http_routes'}
                or (link['kind'] == 'symbol_imports' and (
                    contract.get('symbol_import_resolution') != 'python-unique-imported-function-v1'
                    or not payload.get('callee_id') or not payload.get('imported_name')
                    or not payload.get('local_name')))
                or link['payload_json'] != canonical(payload)
                or hashlib.sha256(canonical(payload).encode()).hexdigest() != link['id']
                or payload['source_file'] != source['path'] or payload['target_file'] != target['path']
                or payload['source_sha256'] != source['sha256'] or payload['target_sha256'] != target['sha256']
                or payload['kind'] != link['kind']
                or (payload.get('callee_id') is not None and payload['callee_id'] not in {symbol['id'] for symbol in target['symbols']})
                or (payload.get('caller_id') is not None and payload['caller_id'] not in {symbol['id'] for symbol in source['symbols']})):
            raise ValueError('Relationship endpoints/evidence are outside the snapshot')


    async def publish(tx):
        for label in ('File', 'SourceEvidence', *SYMBOL_LABELS):
            await tx.run(f'MATCH (n:{label} {{project_id:$project}}) DETACH DELETE n', project=project)
        for file in snapshot['files']:
            await tx.run('CREATE (:File {id:$id, path:$path, project_id:$project})',
                         id=file['id'], path=file['path'], project=project)
            await tx.run(
                'CREATE (:SourceEvidence {id:$id, project_id:$project, run_id:$run, '
                'path:$path, sha256:$sha, content:$content, language:$language, facts_json:$facts})',
                id=file['id'], project=project, run=snapshot['run_id'], path=file['path'],
                sha=file['sha256'], content=file['content'], language=file['language'],
                facts=file['facts_json'],
            )
            for symbol in file['symbols']:
                kind = symbol['kind']
                if kind not in SYMBOL_LABELS:
                    raise ValueError('Unsupported symbol label')
                await tx.run(f'CREATE (:{kind} {{id:$id, name:$name, start_line:$start, '
                             'end_line:$end_line, signature:$signature, project_id:$project})',
                             **{k: symbol[k] for k in ('id', 'name', 'start', 'signature')},
                             end_line=symbol['end'],
                             project=project)
                await tx.run(f'MATCH (f:File {{id:$file}}), (s:{kind} {{id:$symbol}}) '
                             'CREATE (f)-[:CONTAINS]->(s)', file=file['id'], symbol=symbol['id'])
        for link in snapshot.get('relationships', []):
            await tx.run(
                'MATCH (a:File {id:$source}), (b:File {id:$target}) '
                'CREATE (a)-[:EVIDENCE_LINK {id:$id, kind:$kind, project_id:$project, '
                'run_id:$run, payload_json:$payload}]->(b)', source=link['source_id'],
                target=link['target_id'], id=link['id'], kind=link['kind'], project=project,
                run=snapshot['run_id'], payload=link['payload_json'],
            )
        await tx.run(
            'MERGE (p:OutlinePublication {id:$project}) '
            'SET p.run_id=$run, p.root_path=$root, p.manifest_json=$manifest',
            project=project, run=snapshot['run_id'], root=snapshot['root_path'],
            manifest=_json(snapshot['manifest']),
        )
        if snapshot.get('attempt_id'):
            import time
            rows = await (await tx.run('MATCH (j:EmbeddedIndexAttempt {id:$project}) '
                                      'WHERE j.attempt_id=$attempt AND j.run_id=$run AND j.status="running" '
                                      'RETURN j.id AS id', project=project,
                                      attempt=snapshot['attempt_id'], run=snapshot['run_id'])).data()
            if not rows:
                raise RuntimeError('Publication attempt identity no longer matches the journal')
            await tx.run('MATCH (j:EmbeddedIndexAttempt {id:$project}) '
                         'SET j.status="published", j.phase="published", j.error_type="", j.updated_ms=$updated',
                         project=project, updated=time.time_ns() // 1000000)

    async with driver.session() as session:
        await session.execute_write(publish)
    return {'project_id': project, 'run_id': snapshot['run_id'], 'manifest': snapshot['manifest']}


async def index_outline_manifest(driver, root_path: str, project_id: str, paths: list[str]) -> dict:
    snapshot = await asyncio.to_thread(build_outline_snapshot, root_path, project_id, paths)
    return await publish_outline_snapshot(driver, snapshot)


async def read_outline_publication(driver, project_id: str) -> dict | None:
    async def read(tx):
        return await (await tx.run('MATCH (p:OutlinePublication {id:$project}) '
                                  'RETURN p.run_id AS run_id, p.manifest_json AS manifest',
                                  project=project_id)).data()
    async with driver.session() as session:
        rows = await session.execute_read(read)
    return {'run_id': rows[0]['run_id'], 'manifest': json.loads(rows[0]['manifest'])} if rows else None


async def read_outline_file(
    driver, project_id: str, file_path: str, *, start_line: int = 1,
    max_lines: int = 80, max_chars: int = 12000,
) -> dict | None:
    """Return bounded original source and outlines from one published read transaction."""
    from memory.embedded_schema import FILE_SYMBOL_QUERY

    if start_line < 1 or not 1 <= max_lines <= 200 or not 256 <= max_chars <= 20000:
        raise ValueError('Invalid source bounds')
    fid = f'{project_id}:file:{file_path}'

    async def read(tx):
        evidence = await (await tx.run(
            'MATCH (s:SourceEvidence {id:$fid}), (p:OutlinePublication {id:$project}) '
            'WHERE s.project_id=$project AND s.run_id=p.run_id '
            'RETURN s.content AS content, s.sha256 AS sha256, s.run_id AS run_id',
            fid=fid, project=project_id,
        )).data()
        if not evidence:
            return None
        symbols = await (await tx.run(FILE_SYMBOL_QUERY, fid=fid)).data()
        return evidence[0], symbols

    async with driver.session() as session:
        result = await session.execute_read(read)
    if result is None:
        return None
    evidence, symbols = result
    if hashlib.sha256(evidence['content'].encode()).hexdigest() != evidence['sha256']:
        raise RuntimeError('Stored source evidence hash mismatch')
    lines = evidence['content'].splitlines()
    excerpt = []
    used = 0
    next_line = start_line
    for line in range(start_line, min(len(lines) + 1, start_line + max_lines)):
        rendered = f'{line}: {lines[line - 1]}'
        if used + len(rendered) + 1 > max_chars:
            break
        excerpt.append(rendered)
        used += len(rendered) + 1
        next_line = line + 1
    return {'project_id': project_id, 'file_path': file_path, 'run_id': evidence['run_id'],
            'source_sha256': evidence['sha256'], 'origin': 'published source snapshot',
            'source': '\n'.join(excerpt), 'total_lines': len(lines),
            'next_start_line': next_line if next_line <= len(lines) else None,
            'total_symbols': len(symbols),
            'symbols': sorted(symbols, key=lambda s: (s['start'] is None, s['start'] or 0,
                                                    s['kind'], s['id']))[:40]}
