"""Shared REST/MCP learning gateway; published facts and analyzer inference stay distinct."""
from __future__ import annotations

import json

from graphrag_core.repository_learning.snapshot import (
    LearningSnapshotError, canonical, validate_request,
)
from graphrag_core.telemetry import operation as telemetry_operation

MAX_RESPONSE_BYTES = 48000
_IDENTITY_KEYS = ('project_id', 'run_id', 'manifest_sha256', 'snapshot_sha256')


def _envelope(operation: str, status: str) -> dict:
    return {'version': 1, 'operation': operation, 'status': status}


def _bind_profile(profile: dict, snapshot: dict) -> None:
    """Refuse another scope/revision and bind file evidence to parent-owned citations."""
    metadata = profile.get('metadata', {})
    if (profile.get('repo_name') != snapshot['citation']['project_id']
            or not isinstance(metadata, dict)
            or any(metadata.get(key) != snapshot['citation'][key] for key in _IDENTITY_KEYS)
            or metadata.get('coverage') != snapshot['coverage']):
        raise ValueError('Analyzer profile identity does not match captured evidence')
    _bind_evidence(profile, [snapshot])


def _bind_evidence(analysis: dict, snapshots: list[dict]) -> None:
    sources = {}
    for snapshot in snapshots:
        for source in snapshot['sources']:
            sources.setdefault(source['file_path'], []).append(source)

    def visit(value):
        if isinstance(value, dict):
            if 'citation' in value or 'citations' in value:
                raise ValueError('Analyzer-supplied citations are not authoritative')
            # Evidence paths are file paths. Architecture directory fields are not.
            for key in ('evidence', 'evidence_items'):
                if key not in value:
                    continue
                if not isinstance(value[key], list):
                    raise ValueError('Malformed analyzer evidence')
                for evidence in value[key]:
                    if not isinstance(evidence, dict):
                        raise ValueError('Malformed analyzer evidence')
                    path = evidence.get('path')
                    if path is None and ('citation' in evidence or 'citations' in evidence):
                        raise ValueError('Analyzer-supplied citation has no captured file path')
                    evidence.pop('citation', None)
                    evidence.pop('citations', None)
                    for item in evidence.values():
                        visit(item)
                    if path is not None:
                        if path not in sources:
                            raise ValueError('Analyzer evidence is outside the captured files')
                        citations = [{key: source[key] for key in
                            ('project_id', 'run_id', 'file_path', 'source_sha256',
                             'facts_sha256', 'outline_sha256')} for source in sources[path]]
                        # An outer comparison path can refer to both scopes; retain both.
                        evidence['citation' if len(citations) == 1 else 'citations'] = (
                            citations[0] if len(citations) == 1 else citations)
            for key, item in tuple(value.items()):
                if key not in {'evidence', 'evidence_items', 'citation', 'citations'}:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(analysis)


class RepositoryLearningGateway:
    """Analyze immutable snapshots after releasing storage locks; no learning writes."""

    def __init__(self, runtime, adapter):
        self.runtime = runtime
        self.adapter = adapter

    async def profile(self, project_id: str, *, max_files: int = 20) -> dict:
        return await self._analyze('profile', [project_id], max_files)

    async def compare(self, reference_project_id: str, target_project_id: str,
                      *, max_files: int = 20) -> dict:
        return await self._analyze('compare', [reference_project_id, target_project_id], max_files)

    async def _analyze(self, operation: str, project_ids: list[str], max_files: int) -> dict:
        try:
            validate_request(project_ids, max_files)
        except ValueError:
            return _envelope(operation, 'invalid-request')
        try:
            availability = self.adapter.availability()
            if availability.get('status') != 'configured':
                return _envelope(operation, 'disabled')
            if availability.get('error'):
                return _envelope(operation, 'analyzer-unavailable')
            snapshots = await self.runtime.capture_learning_snapshots(project_ids, max_files=max_files)
            if any(not snapshot['sources'] for snapshot in snapshots):
                return _envelope(operation, 'empty-publication')
            return await self.analyze_captured(operation, snapshots)
        except LearningSnapshotError as error:
            return _envelope(operation, error.code)
        except Exception:
            return _envelope(operation, 'analyzer-unavailable')

    async def analyze_captured(self, operation: str, snapshots: list[dict]) -> dict:
        """Reuse verified captured or retained contexts without another graph read."""
        name = ({'profile': 'analyzer.profile', 'compare': 'analyzer.compare'}.get(operation, 'other')
                if type(operation) is str else 'other')
        with telemetry_operation('analyzer', name, backend='repoanalyzer') as scope:
            result = await self._analyze_captured(operation, snapshots)
            status = result.get('status')
            scope.set_outcome('ok' if status == 'ok' else 'partial' if status == 'partial'
                              else 'unavailable' if status in {'disabled', 'analyzer-unavailable'} else 'error')
            return result

    async def _analyze_captured(self, operation: str, snapshots: list[dict]) -> dict:
        try:
            if operation not in {'profile', 'compare'} or len(snapshots) != (1 if operation == 'profile' else 2):
                return _envelope(operation, 'invalid-request')
            if operation == 'profile':
                response = await self.adapter.analyze('profile', payload=json.loads(canonical(snapshots[0]['payload'])))
            else:
                response = await self.adapter.analyze(
                    'compare', reference_payload=json.loads(canonical(snapshots[0]['payload'])),
                    target_payload=json.loads(canonical(snapshots[1]['payload'])))
            if response.get('ok') is not True:
                error = response.get('error', '')
                error = error.replace('_', '-') if isinstance(error, str) else ''
                allowed = {'disabled', 'timeout', 'input-too-large', 'output-too-large',
                           'invalid-response', 'missing-dependency', 'backend-unavailable',
                           'invalid-configuration', 'unsupported-backend', 'backend-failed',
                           'runtime-initialization-refused', 'network-access-refused', 'filesystem-write-refused'}
                result = _envelope(operation, 'analyzer-unavailable')
                result['error'] = error if error in allowed else 'analyzer-error'
                return result
            # Detach before adding citations, and reject non-JSON/nonfinite backend values.
            analysis = json.loads(canonical(response['result']))
            backend = json.loads(canonical(response['backend']))
            if not isinstance(analysis, dict) or not isinstance(backend, dict) or backend.get('name') != 'RepoAnalyzer':
                raise ValueError('Invalid analyzer result')
            if operation == 'profile':
                _bind_profile(analysis, snapshots[0])
            else:
                _bind_profile(analysis['reference_profile'], snapshots[0])
                _bind_profile(analysis['target_profile'], snapshots[1])
                _bind_evidence({key: value for key, value in analysis.items()
                                if key not in {'reference_profile', 'target_profile'}}, snapshots)
            result = _envelope(operation, 'ok' if all(snapshot['coverage']['complete'] for snapshot in snapshots)
                               else 'partial')
            result.update(backend=backend, result=analysis,
                          findings_status='analyzer-inference', evidence_status='verified-published-snapshot',
                          publications=[snapshot['citation'] for snapshot in snapshots],
                          coverage=[snapshot['coverage'] for snapshot in snapshots],
                          sources=[source for snapshot in snapshots for source in snapshot['sources']])
            if len(canonical(result).encode()) > MAX_RESPONSE_BYTES:
                return _envelope(operation, 'response-too-large')
            return result
        except LearningSnapshotError as error:
            return _envelope(operation, error.code)
        except (KeyError, TypeError, ValueError, AttributeError):
            return _envelope(operation, 'invalid-analyzer-result')
        except Exception:
            # Optional integrations must never expose IO exceptions, source or credentials.
            return _envelope(operation, 'analyzer-unavailable')


def _configured_gateway():
    from graphrag_core.repository_learning.adapter import RepoAnalyzerAdapter
    adapter = RepoAnalyzerAdapter.from_environment()
    if adapter.availability().get('status') != 'configured':
        return None
    from memory.embedded_runtime import get_embedded_runtime
    return RepositoryLearningGateway(get_embedded_runtime(), adapter)


async def get_repository_learning_profile(project_id: str, max_files: int = 20) -> dict:
    """Read a bounded RepoAnalyzer profile of one embedded publication."""
    try:
        gateway = _configured_gateway()
        return await gateway.profile(project_id, max_files=max_files) if gateway else _envelope('profile', 'disabled')
    except Exception:
        return _envelope('profile', 'analyzer-unavailable')


async def compare_repository_learning_profiles(reference_project_id: str, target_project_id: str,
                                              max_files: int = 20) -> dict:
    """Compare two captured publications without reindexing or loading models."""
    try:
        gateway = _configured_gateway()
        return (await gateway.compare(reference_project_id, target_project_id, max_files=max_files)
                if gateway else _envelope('compare', 'disabled'))
    except Exception:
        return _envelope('compare', 'analyzer-unavailable')
