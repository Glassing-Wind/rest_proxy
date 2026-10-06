"""Opt-in process owner for embedded MCP operations; engines remain lazy imports."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path

from memory.storage_config import embedded_graph_selected


class EmbeddedRuntime:
    def __init__(self, state: str, dimension: int, artifact: str = ''):
        if not 1 <= dimension <= 4096:
            raise ValueError('Embedded dimension must be 1..4096')
        self.state = str(Path(state).resolve())
        self.dimension = dimension
        self.artifact = artifact
        self._lock = asyncio.Lock()
        self._owner = None
        self._encoder = None
        self._closed = False
        self._watch_task = None
        self._watch_cursor = ""
        self._watch_results = {}

    async def _get_owner(self):
        if self._closed:
            raise RuntimeError('Embedded runtime is closed')
        if self._owner is None:
            from graphrag_core.indexing.embedded_repository import EmbeddedRepositoryOwner
            candidate = EmbeddedRepositoryOwner(self.state, self.dimension)
            await candidate.__aenter__()
            self._owner = candidate
        return self._owner

    async def _get_encoder(self):
        if self._encoder is None:
            if not self.artifact:
                raise RuntimeError('Set LM_PROXY_EMBEDDED_MODEL_ARTIFACT for real embedding operations')
            from local_embeddings.strict_lmstudio import StrictLMStudioEncoder

            def fingerprint():
                with Path(self.artifact).open('rb') as stream:
                    return hashlib.file_digest(stream, 'sha256').hexdigest()

            candidate = StrictLMStudioEncoder(await asyncio.to_thread(fingerprint))
            try:
                await candidate.connect()
                if candidate.dimension != self.dimension:
                    raise ValueError('Embedding dimension does not match the configured embedded store')
            except BaseException:
                await candidate.close()
                raise
            self._encoder = candidate
        return self._encoder

    async def index(self, source_root: str, project_id: str, paths: list[str]):
        async with self._lock:
            owner = await self._get_owner()
            encoder = await self._get_encoder()
            return await owner.index(source_root, project_id, paths, embed=encoder.embed_texts,
                                     encoder_id=encoder.encoder_id, encoder_metadata=encoder.descriptor)

    async def search(self, project_id: str, query: str, mode: str, limit: int):
        if mode not in {'text', 'vector', 'hybrid'} or not 1 <= limit <= 100 or not 1 <= len(query) <= 8192:
            raise ValueError('Use text/vector/hybrid, limit 1..100, and query length 1..8192')
        async with self._lock:
            owner = await self._get_owner()
            # Text search needs no model process; use the stored publication identity.
            publication = await owner.overview(project_id)
            if publication is None:
                return []
            if not publication['retrieval']:
                raise RuntimeError('Publication has no vector retrieval capability')
            encoder_id = publication['retrieval']['encoder_id']
            vector = None
            if mode != 'text':
                encoder = await self._get_encoder()
                if encoder.encoder_id != encoder_id:
                    raise ValueError('Query encoder must match the published embedding identity')
                vector = (await encoder.embed_texts([query]))[0]
            return await owner.search(project_id, encoder_id=encoder_id, text=query,
                                      vector=vector, mode=mode, limit=limit)

    async def describe_file(self, project_id: str, file_path: str, **bounds):
        async with self._lock:
            return await (await self._get_owner()).describe_file(project_id, file_path, **bounds)

    async def overview(self, project_id: str):
        async with self._lock:
            return await (await self._get_owner()).overview(project_id)

    async def workspace_symbol_context(self, workspace_id: str, symbol_name: str,
                                       *, call_chain: bool = False, **bounds):
        async with self._lock:
            owner = await self._get_owner()
            project = await owner.resolve_project(workspace_id)
            if project is None:
                return {'workspace_id': workspace_id, 'status': 'not_published'}
            path = bounds.get('file_path') or ''
            if path and Path(path).is_absolute():
                try:
                    bounds['file_path'] = str(Path(path).resolve().relative_to(project['workspace_path']))
                except ValueError:
                    raise ValueError('Symbol file path is outside the published workspace') from None
            operation = owner.call_chain if call_chain else owner.symbol_context
            return await operation(project['project_id'], symbol_name, **bounds)

    async def resolve_session(self, session_id: str):
        async with self._lock:
            return await (await self._get_owner()).resolve_session(session_id)

    async def workspace_activity(self, project_id: str, **values):
        async with self._lock:
            result = await (await self._get_owner()).workspace_activity(project_id, **values)
            result['embedded_watch_worker_active'] = bool(self._watch_task and not self._watch_task.done()
                and result.get('status') == 'published' and result.get('watch_requested'))
            result['last_watch_result'] = self._watch_results.get(project_id)
            return result

    async def indexing_attempt(self, project_id: str):
        async with self._lock:
            return await (await self._get_owner()).indexing_attempt(project_id)

    async def project_metadata(self, project_id: str, **values):
        async with self._lock:
            return await (await self._get_owner()).project_metadata(project_id, **values)

    async def relationships(self, project_id: str, **bounds):
        async with self._lock:
            return await (await self._get_owner()).relationships(project_id, **bounds)

    async def file_facts(self, project_id: str, file_path: str, *, limit: int = 50, offset: int = 0):
        async with self._lock:
            return await (await self._get_owner()).file_facts(project_id, file_path, limit=limit, offset=offset)

    async def list_projects(self, *, limit: int = 25, after: str = ''):
        async with self._lock:
            return await (await self._get_owner()).list_projects(limit=limit, after=after)

    async def resolve_project(self, workspace_id: str):
        async with self._lock:
            return await (await self._get_owner()).resolve_project(workspace_id)

    async def workspace_overview(self, workspace_id: str):
        async with self._lock:
            owner = await self._get_owner()
            project = await owner.resolve_project(workspace_id)
            if project is None:
                return None
            overview = await owner.overview(project['project_id'])
            return dict(overview, workspace_id=workspace_id, workspace_path=project['workspace_path'])

    async def graph_driver(self):
        async with self._lock:
            return (await self._get_owner()).graph

    async def configure_project_watch(self, project_id: str, *, enable: bool = False,
                                      expected_revision: int | None = None, expected_run_id: str = ''):
        from memory.embedded_watch import changed_paths
        async with self._lock:
            owner = await self._get_owner()
            setup = await owner.watch_setup(project_id)
            if setup is None:
                return {'project_id': project_id, 'status': 'not_published'}
            activity, manifest = setup['activity'], setup['manifest']
            permitted = os.getenv('LM_PROXY_EMBEDDED_WATCH_ENABLED', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
            active = bool(self._watch_task and not self._watch_task.done())
            result = {'project_id': project_id, 'status': 'preview', 'run_id': setup['run_id'],
                      'revision': activity['revision'], 'workspace_path': activity['workspace_path'],
                      'service_permission': permitted, 'service_running': active, 'model_ready': False,
                      'scope': {'selection': 'current-published-manifest', 'total_files': len(manifest['files']),
                                'paths': [file['path'] for file in manifest['files'][:20]],
                                'more_paths': len(manifest['files']) > 20,
                                'new_files_enrolled': False}, 'blockers': []}
            if not permitted:
                result['blockers'].append('Enable LM_PROXY_EMBEDDED_WATCH_ENABLED on the owning service')
            if not active:
                result['blockers'].append('Restart the owning service with embedded watching enabled')
            try:
                changed, paths = await asyncio.to_thread(changed_paths, activity['workspace_path'], manifest['files'])
                result['scope']['changes_pending'] = changed
                result['scope']['missing_files'] = len(manifest['files']) - len(paths)
            except Exception as error:
                result['blockers'].append('Source scan failed: ' + type(error).__name__)
            if not manifest['files']:
                result['blockers'].append('Current manifest is empty; index the intended files first')
            try:
                if self._encoder is None and not self.artifact:
                    result['blockers'].append('Set LM_PROXY_EMBEDDED_MODEL_ARTIFACT on the owning service')
                    raise RuntimeError('Model artifact not configured')
                encoder = await self._get_encoder()
                result['model_ready'] = encoder.encoder_id == manifest.get('retrieval', {}).get('encoder_id')
                if not result['model_ready']:
                    result['blockers'].append('Configured encoder does not match the publication')
                else:
                    await encoder.embed_texts(['FIRE watch readiness probe'])
            except Exception as error:
                result['model_ready'] = False
                result['blockers'].append('Model readiness failed: ' + type(error).__name__)
            result['ready'] = not result['blockers']
            if enable:
                if not result['ready']:
                    result['status'] = 'blocked'
                elif expected_revision != activity['revision'] or expected_run_id != setup['run_id']:
                    result['status'] = 'conflict'
                else:
                    updated = await owner.workspace_activity(project_id, watch_requested=True,
                        expected_revision=expected_revision, expected_run_id=expected_run_id)
                    result['status'] = 'enabled' if updated['status'] == 'published' else updated['status']
                    result['revision'] = updated['revision']
            return result

    async def watch_tick(self):
        from memory.embedded_watch import changed_paths
        async with self._lock:
            owner = await self._get_owner()
            page = await owner.list_projects(limit=100, after=self._watch_cursor)
            self._watch_cursor = page['next_cursor'] or ''
            for project in page['projects']:
                pid = project['project_id']
                try:
                    plan = await owner.watch_plan(pid)
                    if plan is None:
                        continue
                    changed, paths = await asyncio.to_thread(changed_paths, plan['root_path'], plan['manifest']['files'])
                    status = 'unchanged'
                    if changed:
                        encoder = await self._get_encoder()
                        if plan['manifest'].get('retrieval', {}).get('encoder_id') != encoder.encoder_id:
                            raise ValueError('Watcher encoder must match publication identity')
                        await owner.index(plan['root_path'], pid, paths, embed=encoder.embed_texts,
                                          encoder_id=encoder.encoder_id, encoder_metadata=encoder.descriptor)
                        status = 'published'
                    self._watch_results[pid] = status
                except Exception as error:
                    self._watch_results[pid] = type(error).__name__
                if len(self._watch_results) > 100:
                    self._watch_results.pop(next(iter(self._watch_results)))

    async def start_watch(self, interval: float = 30):
        if not 1 <= interval <= 3600:
            raise ValueError('Watch interval must be 1..3600 seconds')
        if self._closed:
            raise RuntimeError('Embedded runtime is closed')
        if self._watch_task and not self._watch_task.done():
            return self._watch_task
        async def poll():
            while True:
                try:
                    await self.watch_tick()
                except Exception as error:
                    logging.getLogger(__name__).warning('Embedded watch cycle failed: %s', type(error).__name__)
                await asyncio.sleep(interval)
        self._watch_task = asyncio.create_task(poll())
        return self._watch_task

    async def stop_watch(self):
        task, self._watch_task = self._watch_task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def close(self):
        await self.stop_watch()
        async with self._lock:
            self._closed = True
            try:
                if self._owner is not None:
                    await self._owner.__aexit__(None, None, None)
            finally:
                if self._encoder is not None:
                    await self._encoder.close()
                self._owner = self._encoder = None


_runtime = None


def get_embedded_runtime() -> EmbeddedRuntime:
    """Return the explicit process configuration; no model/engine opens at import."""
    global _runtime
    state = os.getenv('LM_PROXY_EMBEDDED_STATE', '').strip()
    if not embedded_graph_selected() or not state:
        raise RuntimeError('Embedded MCP tools require embedded selection and LM_PROXY_EMBEDDED_STATE')
    if os.getenv('LM_PROXY_GRAPH_BACKEND', '').strip().lower() == 'kuzu':
        raise RuntimeError('The Kuzu backend is retired; select ladybug')
    if _runtime is None:
        _runtime = EmbeddedRuntime(state, int(os.getenv('LM_PROXY_MEMORY_EMBEDDING_DIM', '768')),
                                   os.getenv('LM_PROXY_EMBEDDED_MODEL_ARTIFACT', '').strip())
    return _runtime


async def close_embedded_runtime():
    """Drain outstanding operations and close the single process owner."""
    global _runtime
    current = _runtime
    if current is not None:
        await current.close()
        if _runtime is current:
            _runtime = None
