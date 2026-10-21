"""Explicit, revisioned pattern memory over captured declarations and provided text.

Pattern mining and historical reuse are separate from model training. No operation
modifies source files, reindexes a workspace or treats stored analysis as current.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from functools import wraps
import hashlib
import json
import os
import re

from graphrag_core.repository_learning.service import (
    MAX_RESPONSE_BYTES, _bind_profile, _configured_gateway,
)
from graphrag_core.repository_learning.snapshot import LearningSnapshotError, canonical, outline_hash, validate_request
from memory.embedded_ladybug import _finish_thread
from graphrag_core.telemetry import operation


def _result_outcome(result: dict) -> str:
    """Map sanitized envelopes to a fixed outcome without exporting their contents."""
    status = result.get('status')
    if not isinstance(status, str):
        return 'error'
    if status in {'stored', 'unchanged', 'learned', 'deleted', 'suggestions', 'ok'}:
        return 'ok'
    if status == 'not-learned':
        return 'empty'
    if status == 'partial':
        return 'partial'
    if status in {'conflict', 'publication-conflict'}:
        return 'conflict'
    if status in {'disabled', 'analyzer-unavailable', 'learning-unavailable', 'not-published',
                  'empty-publication', 'reference-not-learned', 'target-not-learned',
                  'reindex-required-for-outline-provenance', 'unsupported-fact-version'}:
        return 'unavailable'
    return 'error'


def _observe_learning(name: str):
    """Observe fixed public learning operations, including safely returned failures."""
    def decorate(function):
        @wraps(function)
        async def observed(*args, **kwargs):
            with operation('learning', name) as scope:
                result = await function(*args, **kwargs)
                scope.set_outcome(_result_outcome(result))
                return result
        return observed
    return decorate


async def _memory_read(store, project_id: str, revision: int | None = None) -> dict:
    with operation('persistence', 'learning.memory_read', backend='sqlite') as scope:
        result = await asyncio.to_thread(store.read, project_id, revision)
        scope.set_outcome(_result_outcome(result))
        return result


async def _memory_save(store, project_id: str, record: dict, expected_revision: int) -> dict:
    with operation('persistence', 'learning.save', backend='sqlite') as scope:
        result = await _finish_thread(lambda: store.save(project_id, record, expected_revision))
        scope.set_outcome(_result_outcome(result))
        return result


def _status(status: str, **values) -> dict:
    return {'version': 1, 'status': status, **values}


def _store():
    path = os.getenv('LM_PROXY_REPOSITORY_LEARNING_STATE', '').strip()
    if not path:
        return None
    from graphrag_core.repository_learning.pattern_store import RepositoryPatternStore
    return RepositoryPatternStore(path)


def _revision(value: int) -> None:
    if type(value) is not int or value < 0:
        raise ValueError('Use an explicit nonnegative memory revision')


def _source_label(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or '\0' in value:
        raise ValueError('Use a nonempty bounded source label')


def _pattern(kind, value, count, source, producer):
    identity = hashlib.sha256(canonical([kind, value]).encode()).hexdigest()
    return {'key': identity, 'kind': kind, 'value': value, 'occurrences': count,
            'source_id': source['source_id'], 'source_sha256': source['source_sha256'],
            'producer': producer}


def _naming(name: str) -> str | None:
    if name.startswith('__') and name.endswith('__'):
        return None
    name = name.strip('_')
    if re.fullmatch(r'[A-Z][A-Z0-9_]*', name):
        return 'upper_snake'
    if re.fullmatch(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)+', name):
        return 'snake_case'
    if re.fullmatch(r'[A-Z][a-zA-Z0-9]*', name):
        return 'PascalCase'
    if re.fullmatch(r'[a-z][a-zA-Z0-9]*', name) and any(char.isupper() for char in name):
        return 'camelCase'
    return None


def repository_record(snapshot: dict, analysis: dict) -> dict:
    """Keep per-source observations, distinguishing occurrences from source support."""
    patterns, sources = [], []
    by_path = {file['path']: file for file in snapshot['payload']['files']}
    for evidence in snapshot['sources']:
        source = {**evidence, 'source_id': evidence['file_path'], 'source_kind': 'published-source',
                  'original_availability': 'published-at-capture-not-stored-in-memory'}
        sources.append(source)
        file = by_path[source['source_id']]
        producer = 'rest-proxy-native-patterns-v1'
        patterns.append(_pattern('file_language', file['language'], 1, source, producer))
        kinds = Counter(symbol['kind'] for symbol in file['symbols'])
        for kind, count in sorted(kinds.items()):
            patterns.append(_pattern('declaration_kind', kind, count, source, producer))
        names = Counter((symbol['kind'], _naming(symbol['name'])) for symbol in file['symbols']
                        if _naming(symbol['name']))
        for (kind, style), count in sorted(names.items()):
            patterns.append(_pattern('declaration_naming', {'kind': kind, 'style': style}, count, source, producer))
    return {'version': 1, 'kind': 'repository', 'project_id': snapshot['citation']['project_id'],
            'sources': sources, 'patterns': patterns, 'backend': analysis['backend'],
            'context': snapshot, 'analysis': analysis['result'], 'coverage': snapshot['coverage'],
            'findings_status': 'historical-pattern-observations-and-inference'}


def validate_repository_record(record: dict) -> None:
    """Refuse a retained context that disagrees with its requested memory scope."""
    snapshot = record['context']
    citation = snapshot['citation']
    payload = json.loads(canonical(snapshot['payload']))
    metadata = payload['metadata']
    project = record['project_id']
    if (citation['project_id'] != project or payload['repo_name'] != project
            or any(metadata.get(key) != citation[key] for key in
                   ('project_id', 'run_id', 'manifest_sha256', 'snapshot_sha256'))
            or metadata.get('coverage') != snapshot['coverage']
            or record.get('coverage') != snapshot['coverage']):
        raise ValueError('Retained repository context identity mismatch')
    digest = metadata.pop('snapshot_sha256')
    if hashlib.sha256(canonical(payload).encode()).hexdigest() != digest:
        raise ValueError('Retained context content hash mismatch')
    sources = {source['source_id']: source for source in record['sources']}
    fields = ('project_id', 'run_id', 'source_sha256', 'facts_sha256', 'outline_sha256', 'symbols', 'total_symbols')
    if len(sources) != len(snapshot['sources']) or len(sources) != len(payload['files']):
        raise ValueError('Retained source coverage mismatch')
    for source in snapshot['sources']:
        retained = sources.get(source['file_path'])
        if (retained is None or retained.get('source_kind') != 'published-source'
                or any(retained.get(key) != source.get(key) for key in fields)
                or source['project_id'] != project or source['run_id'] != citation['run_id']
                or source['total_symbols'] != len(source['symbols'])):
            raise ValueError('Retained source citation mismatch')
    if {file['path'] for file in payload['files']} != set(sources):
        raise ValueError('Retained file selection mismatch')
    for file in payload['files']:
        source = sources[file['path']]
        if any(file['metadata'].get(key) != source[key] for key in
               ('project_id', 'run_id', 'source_sha256', 'facts_sha256', 'outline_sha256')):
            raise ValueError('Retained file context mismatch')
        outlines = [{**symbol['metadata'], 'kind': symbol['kind'], 'name': symbol['name'],
                     'signature': symbol['signature']} for symbol in file['symbols']]
        cited = [{key: symbol[key] for key in ('id', 'name', 'kind', 'start_line', 'end_line')}
                 for symbol in outlines]
        if (cited != source['symbols'] or outline_hash(outlines) != source['outline_sha256']
                or type(file['metadata']['symbol_count']) is not int
                or file['metadata']['symbol_count'] != len(outlines)
                or any(symbol['file_path'] != file['path'] for symbol in file['symbols'])):
            raise ValueError('Retained declarations do not match cited outlines')
    coverage = snapshot['coverage']
    if (type(coverage['included_symbols']) is not int
            or coverage['included_symbols'] != sum(len(source['symbols']) for source in sources.values())
            or type(coverage['included_files']) is not int or coverage['included_files'] != len(sources)):
        raise ValueError('Retained declaration coverage mismatch')


def validate_text_ast(response: dict, payload: dict) -> dict:
    """Check the actual matcher output against the retained original, before persistence."""
    result = json.loads(canonical(response['result']))
    if (response['backend'].get('name') != 'RepoAnalyzer' or result.get('mode') != 'pattern-regex-slice'
            or result.get('metadata') != payload['metadata'] or result.get('source_id') != payload['source_id']
            or result.get('source_sha256') != payload['source_sha256']):
        raise ValueError('Text analyzer identity mismatch')
    text = payload['text']
    ast = result['ast']
    if (ast.get('type') != 'document' or ast.get('content') != text
            or ast.get('start_char') != 0 or ast.get('end_char') != len(text)
            or ast.get('start_byte') != 0 or ast.get('end_byte') != len(text.encode())
            or ast.get('point_units') != 'unicode-code-points'
            or any(type(ast.get(key)) is not int for key in ('start_char', 'end_char', 'start_byte', 'end_byte'))
            or any(not isinstance(ast.get(key), list) or len(ast[key]) != 2
                   or any(type(value) is not int for value in ast[key])
                   for key in ('start_point', 'end_point'))
            or ast.get('start_point') != [0, 0]
            or ast.get('end_point') != [text.count('\n'), len(text.rsplit('\n', 1)[-1])]):
        raise ValueError('Document does not match original')
    children = ast['children']
    if (not isinstance(children, list) or len(children) > 128
            or type(result['counts']['nodes']) is not int or result['counts']['nodes'] != len(children)):
        raise ValueError('Invalid document node coverage')
    for node in children:
        start, end = node['start_char'], node['end_char']
        if (type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(text)
                or node.get('content') != text[start:end]
                or node.get('start_byte') != len(text[:start].encode())
                or node.get('end_byte') != len(text[:end].encode())
                or type(node.get('start_byte')) is not int or type(node.get('end_byte')) is not int
                or node.get('point_units') != 'unicode-code-points'
                or not isinstance(node.get('pattern_name'), str) or not node['pattern_name']):
            raise ValueError('Text observation does not match original bytes')
        for key, offset in [('start_point', start), ('end_point', end)]:
            prefix = text[:offset]
            point = node.get(key)
            if (not isinstance(point, list) or len(point) != 2 or any(type(value) is not int for value in point)
                    or point != [prefix.count('\n'), len(prefix.rsplit('\n', 1)[-1])]):
                raise ValueError('Text observation point does not match original')
    if result['counts']['patterns'] != dict(Counter(node['pattern_name'] for node in children)):
        raise ValueError('Text pattern counts do not match the retained nodes')
    if any(type(value) is not int for value in result['counts']['patterns'].values()):
        raise ValueError('Text pattern counts must be integers')
    return result


def configuration_patterns(response: dict, source: dict, parser: str) -> tuple[dict, list]:
    """Validate typed configuration identity and derive normalized declaration counts."""
    analysis = json.loads(canonical(response['result']))
    if (response['backend'].get('name') != 'GithubAnalyzer'
            or analysis.get('source_id') != source['source_id']
            or analysis.get('source_sha256') != source['source_sha256']
            or analysis.get('parser') != parser or parser not in {'requirements', 'gitignore'}
            or analysis.get('metadata') != {'project_id': source['project_id'], 'source_kind': 'provided-text'}):
        raise ValueError('Configuration analyzer identity mismatch')
    observations = analysis['observations']
    if observations.get('is_valid') is not True:
        raise ValueError('Invalid configuration observations')
    values = []
    if parser == 'requirements':
        for kind in ('requirements', 'constraints'):
            if not isinstance(observations[kind], list):
                raise ValueError('Invalid dependency observation list')
            for item in observations[kind]:
                if (not isinstance(item, dict) or not isinstance(item.get('name'), str)
                        or not isinstance(item.get('version'), str) or type(item.get('is_editable')) is not bool
                        or not isinstance(item.get('extras'), list)
                        or any(not isinstance(extra, str) for extra in item['extras'])):
                    raise ValueError('Invalid dependency observation')
                values.append((kind, {key: item[key] for key in ('name', 'version', 'is_editable', 'extras')}))
    else:
        if not isinstance(observations['patterns'], list):
            raise ValueError('Invalid ignore declaration list')
        for item in observations['patterns']:
            if (not isinstance(item, dict) or not isinstance(item.get('pattern'), str)
                    or type(item.get('negated')) is not bool or type(item.get('directory_only')) is not bool):
                raise ValueError('Invalid ignore declaration')
            values.append(('ignore_rule', {key: item[key] for key in ('pattern', 'negated', 'directory_only')}))
    if len(values) > 256:
        raise ValueError('Configuration exceeds observation budget')
    counts = Counter((kind, canonical(value)) for kind, value in values)
    patterns = [_pattern('configuration/' + kind, json.loads(value), count, source,
                         'GithubAnalyzer/typed-configuration-v1') for (kind, value), count in sorted(counts.items())]
    return analysis, patterns


def validate_learning_record(record: dict) -> None:
    """Recheck retained originals/context and derived observations before historical reuse."""
    if record['kind'] == 'repository':
        validate_repository_record(record)
        profile = json.loads(canonical(record['analysis']))
        _bind_profile(profile, record['context'])
        if record['backend'].get('name') != 'RepoAnalyzer' or canonical(profile) != canonical(record['analysis']):
            raise ValueError('Retained profile identity or citations disagree with captured evidence')
        expected = repository_record(record['context'], {'backend': record['backend'], 'result': record['analysis']})
        patterns = expected['patterns']
    else:
        if len(record['sources']) != 1:
            raise ValueError('Provided-text memory requires exactly one original')
        source = record['sources'][0]
        text = source['original']
        if (source['source_kind'] != 'provided-text' or source['project_id'] != record['project_id']
                or not isinstance(text, str) or len(text.encode()) > 16384
                or hashlib.sha256(text.encode()).hexdigest() != source['source_sha256']):
            raise ValueError('Retained original does not match provided-text identity')
        response = {'backend': record['backend'], 'result': record['analysis']}
        if record['kind'] == 'provided-text':
            result = validate_text_ast(response, {'text': text, 'source_id': source['source_id'],
                'source_sha256': source['source_sha256'],
                'metadata': {'project_id': record['project_id'], 'source_kind': 'provided-text'}})
            counts = Counter(node['pattern_name'] for node in result['ast']['children'])
            patterns = [_pattern('plaintext_pattern', name, count, source, 'RepoAnalyzer/plaintext-pattern-regex-slice')
                        for name, count in sorted(counts.items())]
        else:
            _, patterns = configuration_patterns(response, source, record['analysis']['parser'])
    if canonical(record['patterns']) != canonical(patterns):
        raise ValueError('Retained pattern counts do not match verified observations')


@_observe_learning('learning.repository')
async def learn_repository_patterns(project_id: str, expected_run_id: str,
                                    expected_revision: int, max_files: int = 20) -> dict:
    """Mine/persist native structural observations and an actual RepoAnalyzer profile."""
    try:
        validate_request([project_id], max_files)
        _revision(expected_revision)
        if not isinstance(expected_run_id, str) or not expected_run_id or len(expected_run_id) > 128:
            raise ValueError('An explicit publication run is required')
        store = _store()
        if store is None:
            return _status('disabled')
        gateway = _configured_gateway()
        if gateway is None:
            return _status('analyzer-unavailable')
        snapshots = await gateway.runtime.capture_learning_snapshots([project_id], max_files=max_files)
        snapshot = snapshots[0]
        if snapshot['citation']['run_id'] != expected_run_id:
            return _status('publication-conflict')
        result = await gateway.analyze_captured('profile', snapshots)
        if result['status'] not in {'ok', 'partial'}:
            return result
        record = repository_record(snapshot, result)
        validate_learning_record(record)
        # Fence publication and memory CAS while retaining the existing owner lock.
        saved = await gateway.runtime.commit_learning_record(store, project_id, expected_run_id,
                                                            record, expected_revision)
        return _status(saved.pop('status'), **saved, coverage=snapshot['coverage'],
                       publication=snapshot['citation'], patterns=len(record['patterns']))
    except LearningSnapshotError as error:
        return _status(error.code)
    except ValueError:
        return _status('invalid-learning-input')
    except Exception:
        return _status('learning-unavailable')


@_observe_learning('learning.text')
async def learn_text_patterns(project_id: str, source_id: str, text: str, expected_revision: int) -> dict:
    """Persist actual RepoAnalyzer authored-pattern AST observations of explicit provided text."""
    try:
        validate_request([project_id], 1)
        _source_label(source_id)
        _revision(expected_revision)
        if not isinstance(text, str) or len(text.encode()) > 16384:
            raise ValueError('Provided text exceeds UTF8 byte budget')
        store = _store()
        if store is None:
            return _status('disabled')
        from graphrag_core.repository_learning.adapter import RepoAnalyzerAdapter
        adapter = RepoAnalyzerAdapter.from_environment()
        payload = {'text': text, 'source_id': source_id, 'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
                   'metadata': {'project_id': project_id, 'source_kind': 'provided-text'}}
        with operation('analyzer', 'analyzer.plaintext_ast', backend='repoanalyzer') as scope:
            response = await adapter.analyze('plaintext_ast', payload=payload)
            if response.get('ok') is not True:
                scope.set_outcome('unavailable')
                return _status('analyzer-unavailable')
            analysis = validate_text_ast(response, payload)
        source = {'source_kind': 'provided-text', 'project_id': project_id, 'source_id': source_id,
                  'source_sha256': payload['source_sha256'], 'original': text,
                  'original_availability': 'stored', 'parser_backend': 'custom'}
        counts = Counter(node['pattern_name'] for node in analysis['ast']['children'])
        patterns = [_pattern('plaintext_pattern', name, count, source, 'RepoAnalyzer/plaintext-pattern-regex-slice')
                    for name, count in sorted(counts.items())]
        record = {'version': 1, 'kind': 'provided-text', 'project_id': project_id, 'sources': [source],
                  'patterns': patterns, 'backend': response['backend'], 'analysis': analysis,
                  'findings_status': 'authored-pattern-observations-not-adaptive-training'}
        validate_learning_record(record)
        saved = await _memory_save(store, project_id, record, expected_revision)
        return _status(saved.pop('status'), **saved, patterns=len(patterns), nodes=len(analysis['ast']['children']))
    except ValueError:
        return _status('invalid-learning-input')
    except Exception:
        return _status('learning-unavailable')


@_observe_learning('learning.configuration')
async def learn_configuration_patterns(project_id: str, source_id: str, text: str,
                                       parser: str, expected_revision: int) -> dict:
    """Persist genuine GithubAnalyzer typed observations of explicitly provided configuration."""
    try:
        validate_request([project_id], 1)
        _source_label(source_id)
        _revision(expected_revision)
        if parser not in {'requirements', 'gitignore'} or not isinstance(text, str) or len(text.encode()) > 16384:
            raise ValueError('Invalid configuration parser or UTF8 byte budget')
        store = _store()
        if store is None:
            return _status('disabled')
        from graphrag_core.repository_learning.github_configuration import GithubConfigurationAdapter
        adapter = GithubConfigurationAdapter.from_environment()
        with operation('analyzer', 'analyzer.configuration', backend='githubanalyzer') as scope:
            response = await adapter.analyze_configuration(project_id, source_id, text, parser)
            if response.get('ok') is not True:
                scope.set_outcome('unavailable')
                return _status('analyzer-unavailable')
            digest = hashlib.sha256(text.encode()).hexdigest()
            source = {'source_kind': 'provided-text', 'project_id': project_id, 'source_id': source_id,
                      'source_sha256': digest, 'original': text, 'original_availability': 'stored',
                      'parser_backend': 'GithubAnalyzer/' + parser}
            if response['result'].get('observations', {}).get('is_valid') is not True:
                scope.set_outcome('error')
                return _status('configuration-invalid')
            analysis, patterns = configuration_patterns(response, source, parser)
        record = {'version': 1, 'kind': 'configuration', 'project_id': project_id, 'sources': [source],
                  'patterns': patterns, 'backend': response['backend'], 'analysis': analysis,
                  'findings_status': 'declared-configuration-not-resolved-runtime-behavior'}
        validate_learning_record(record)
        saved = await _memory_save(store, project_id, record, expected_revision)
        return _status(saved.pop('status'), **saved, parser=parser, patterns=len(patterns), observations=sum(item['occurrences'] for item in patterns))
    except ValueError:
        return _status('invalid-learning-input')
    except Exception:
        return _status('learning-unavailable')


def pattern_summary(record: dict, *, limit: int, offset: int) -> dict:
    groups = {}
    for pattern in record['patterns']:
        item = groups.setdefault(pattern['key'], {'key': pattern['key'], 'kind': pattern['kind'],
                                                 'value': pattern['value'], 'occurrences': 0, 'sources': []})
        item['occurrences'] += pattern['occurrences']
        source = next(source for source in record['sources'] if source['source_id'] == pattern['source_id'])
        item['sources'].append({key: value for key, value in source.items() if key not in {'original', 'symbols'}})
    items = sorted(groups.values(), key=lambda item: (item['kind'], item['key']))
    for item in items:
        item['distinct_sources'] = len({source['source_id'] for source in item['sources']})
    return {'patterns': items[offset:offset + limit], 'total_patterns': len(items),
            'next_offset': offset + limit if offset + limit < len(items) else None}


def transfer_candidates(reference: dict, target: dict) -> dict:
    """Propose only observed reference patterns absent from the explicit target sample."""
    known = {pattern['key'] for pattern in target['patterns']}
    summary = pattern_summary(reference, limit=1024, offset=0)['patterns']
    missing = [pattern for pattern in summary if pattern['key'] not in known]
    candidates = []
    for pattern in missing[:20]:
        candidates.append({'key': pattern['key'], 'kind': pattern['kind'], 'value': pattern['value'],
                           'reference_occurrences': pattern['occurrences'],
                           'reference_distinct_sources': pattern['distinct_sources'],
                           'reference_evidence': pattern['sources'][:3],
                           'evidence_truncated': len(pattern['sources']) > 3,
                           'target_observation': 'not-observed-in-selected-target-evidence',
                           'status': 'candidate-for-review'})
    return {'candidates': candidates, 'total_candidates': len(missing), 'truncated': len(missing) > 20,
            'limitations': ['Absence in sampled or retained target evidence is not repository-wide absence.',
                            'Reference conventions are observations, not proof that adoption improves this target.']}


@_observe_learning('learning.read')
async def get_learned_repository_patterns(project_id: str, revision: int | None = None,
                                          limit: int = 20, offset: int = 0) -> dict:
    """Read stored observations without opening an analyzer, graph owner or model."""
    try:
        validate_request([project_id], 1)
        if revision is not None:
            _revision(revision)
        if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or not 0 <= offset <= 1024:
            raise ValueError('Invalid pattern read limits')
        store = _store()
        if store is None:
            return _status('disabled')
        value = await _memory_read(store, project_id, revision)
        if value['status'] != 'stored':
            return _status(value.pop('status'), **value)
        record = value.pop('record')
        validate_learning_record(record)
        result = _status('learned', project_id=project_id, revision=value['revision'], record_id=value['record_id'],
                         source_kind=record['kind'], freshness='historical-not-current-repository',
                         backend=record['backend'], findings_status=record['findings_status'],
                         **pattern_summary(record, limit=limit, offset=offset))
        if record['kind'] == 'repository':
            result.update(coverage=record['coverage'], publication=record['context']['citation'],
                          profile=record['analysis'])
        elif record['kind'] == 'provided-text':
            result['ast'] = record['analysis']['ast']
            result['limitations'] = record['analysis']['limitations']
        elif record['kind'] == 'configuration':
            result['limitations'] = record['analysis']['limitations']
        if len(canonical(result).encode()) > MAX_RESPONSE_BYTES:
            return _status('response-too-large')
        return result
    except ValueError:
        return _status('invalid-learning-input')
    except Exception:
        return _status('learning-unavailable')


@_observe_learning('learning.suggest')
async def suggest_learned_repository_patterns(reference_project_id: str, target_project_id: str,
                                             reference_revision: int, max_files: int = 20) -> dict:
    """Compare a retained reference context against a captured target; never change files."""
    try:
        validate_request([reference_project_id, target_project_id], max_files)
        _revision(reference_revision)
        if not reference_revision:
            raise ValueError('Select an explicit positive reference memory revision')
        store = _store()
        if store is None:
            return _status('disabled')
        retained = await _memory_read(store, reference_project_id, reference_revision)
        if retained['status'] != 'stored':
            return _status('reference-not-learned')
        reference = retained['record']
        validate_learning_record(reference)
        if reference['kind'] != 'repository':
            retained_target = await _memory_read(store, target_project_id)
            if retained_target['status'] != 'stored':
                return _status('target-not-learned')
            target_record = retained_target['record']
            validate_learning_record(target_record)
            if target_record['kind'] != reference['kind']:
                return _status('target-kind-mismatch')
            result = _status('suggestions', reference_project_id=reference_project_id,
                             target_project_id=target_project_id,
                             reference_revision=reference_revision,
                             target_revision=retained_target['revision'],
                             freshness='historical-retained-evidence',
                             transfer=transfer_candidates(reference, target_record))
            return result if len(canonical(result).encode()) <= MAX_RESPONSE_BYTES else _status('response-too-large')
        gateway = _configured_gateway()
        if gateway is None:
            return _status('analyzer-unavailable')
        target = (await gateway.runtime.capture_learning_snapshots([target_project_id], max_files=max_files))[0]
        result = await gateway.analyze_captured('compare', [reference['context'], target])
        if result['status'] not in {'ok', 'partial'}:
            return result
        result['reference_memory'] = {'revision': reference_revision, 'record_id': retained['record_id'],
                                      'freshness': 'historical-retained-reference'}
        result['transfer'] = transfer_candidates(reference, repository_record(target, {
            'backend': result['backend'], 'result': result['result']['target_profile']}))
        return result if len(canonical(result).encode()) <= MAX_RESPONSE_BYTES else _status('response-too-large')
    except LearningSnapshotError as error:
        return _status(error.code)
    except ValueError:
        return _status('invalid-learning-input')
    except Exception:
        return _status('learning-unavailable')


@_observe_learning('learning.delete')
async def delete_repository_learning(project_id: str, expected_revision: int) -> dict:
    """Explicitly delete one project's learning history with memory revision fencing."""
    try:
        validate_request([project_id], 1)
        _revision(expected_revision)
        store = _store()
        if store is None:
            return _status('disabled')
        with operation('persistence', 'learning.memory_delete', backend='sqlite') as scope:
            value = await _finish_thread(lambda: store.delete(project_id, expected_revision))
            scope.set_outcome(_result_outcome(value))
        return _status(value.pop('status'), **value)
    except ValueError:
        return _status('invalid-learning-input')
    except Exception:
        return _status('learning-unavailable')
