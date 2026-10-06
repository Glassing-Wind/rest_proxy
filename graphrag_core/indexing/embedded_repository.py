"""Experimental single-owner graph/vector publication and scoped retrieval.

Embeddings are supplied by an explicit caller; no inference provider is selected.
Explicit MCP dispatch lives in memory.embedded_runtime; complete graph query parity
is not implemented here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path

from graphrag_core.indexing.embedded_outlines import (
    build_outline_snapshot, publish_outline_snapshot, read_outline_file,
    read_outline_publication,
)
from memory.embedded_ladybug import LadybugGraphDriver
from memory.embedded_lance_runs import LanceRunStore
from memory.embedded_schema import SYMBOL_LABELS
from memory import embedded_jobs


class EmbeddedRepositoryOwner:
    def __init__(self, root: str, dimension: int):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.dimension = dimension
        self._lock = asyncio.Lock()
        self.graph = None
        self.vectors = None

    async def __aenter__(self):
        if self.graph is not None:
            raise RuntimeError('Owner cannot be entered twice')
        self.graph = LadybugGraphDriver(str(self.root / 'graph'))
        try:
            await self.graph.initialize_schema()
            await embedded_jobs.recover_attempts(self.graph)
            self.vectors = LanceRunStore(str(self.root / 'vectors'), self.dimension)
        except BaseException:
            await self.graph.close()
            self.graph = None
            raise
        return self

    async def __aexit__(self, *args):
        async with self._lock:
            try:
                if self.vectors is not None:
                    await self.vectors.close()
            finally:
                if self.graph is not None:
                    await self.graph.close()
                self.graph = self.vectors = None

    def _require_open(self):
        if self.graph is None or self.vectors is None:
            raise RuntimeError('Repository owner is not open')

    async def index(self, source_root: str, project_id: str, paths: list[str], *, embed,
                    encoder_id: str, encoder_metadata: dict | None = None) -> dict:
        if encoder_metadata is not None:
            encoded = json.dumps(encoder_metadata, sort_keys=True, allow_nan=False)
            if not isinstance(encoder_metadata, dict) or len(encoded.encode()) > 8192:
                raise ValueError('Encoder metadata must be a bounded JSON object')
            encoder_metadata = json.loads(encoded)
        if not project_id or len(project_id) > 128 or ':' in project_id:
            raise ValueError('A nonempty project ID without colons is required')
        if not encoder_id or len(encoder_id) > 512:
            raise ValueError('An explicit embedding encoder identity is required')
        async with self._lock:
            self._require_open()
            attempt_id = str(uuid.uuid4())
            try:
                await embedded_jobs.start_attempt(self.graph, project_id, attempt_id, str(Path(source_root).resolve()))
                return await self._index_attempt(source_root, project_id, paths, embed=embed,
                                                 encoder_id=encoder_id, encoder_metadata=encoder_metadata,
                                                 attempt_id=attempt_id)
            except BaseException as error:
                try:
                    await embedded_jobs.drain(embedded_jobs.finish_failed_attempt(
                        self.graph, project_id, attempt_id, cancelled=isinstance(error, asyncio.CancelledError),
                        error_type=type(error).__name__))
                except Exception:
                    # Preserve the indexing error; reopen reconciles an unfinished journal entry.
                    pass
                raise

    async def _index_attempt(self, source_root, project_id, paths, *, embed, encoder_id,
                             encoder_metadata, attempt_id):
        snapshot = await asyncio.to_thread(build_outline_snapshot, source_root, project_id, paths)
        snapshot['attempt_id'] = attempt_id
        await embedded_jobs.update_attempt(self.graph, project_id, attempt_id, phase='chunking', run_id=snapshot['run_id'])

        def chunk_sources():
            import tree_sitter_language_pack as ts_pack

            chunks = []
            for file in snapshot['files']:
                payload = ts_pack.build_semantic_payload(
                    file['content'], file['language'], file['path'], project_id, chunk_max_size=2048,
                )
                for chunk in payload['chunks']:
                    metadata = dict(chunk['metadata'])
                    metadata['content_sha256'] = hashlib.sha256(chunk['text'].encode()).hexdigest()
                    chunks.append({'ref_id': chunk['ref_id'], 'file_path': file['path'],
                                   'content': chunk['text'], 'source_sha256': file['sha256'],
                                   'metadata': metadata})
            if len(chunks) > 50000:
                raise ValueError('Snapshot exceeds 50000 chunks')
            return chunks

        chunks = await asyncio.to_thread(chunk_sources)
        await embedded_jobs.update_attempt(self.graph, project_id, attempt_id, phase='embedding', run_id=snapshot['run_id'])
        # Bound embed calls; callers choose the actual provider and model.
        for offset in range(0, len(chunks), 64):
            batch = chunks[offset:offset + 64]
            vectors = await embed([chunk['content'] for chunk in batch])
            if len(vectors) != len(batch):
                raise ValueError('Embedding provider returned the wrong row count')
            for chunk, vector in zip(batch, vectors):
                self.vectors.validate_vector(vector)
                chunk['vector'] = vector
        await embedded_jobs.update_attempt(self.graph, project_id, attempt_id, phase='staging', run_id=snapshot['run_id'])
        count = await self.vectors.stage_run(project_id, snapshot['run_id'], chunks)
        snapshot['manifest']['retrieval'] = {'run_id': snapshot['run_id'], 'chunks': count,
                                              'dimension': self.dimension, 'encoder_id': encoder_id}
        if encoder_metadata is not None:
            snapshot['manifest']['retrieval']['encoder_metadata'] = encoder_metadata
        snapshot['manifest'].pop('sha256')
        canonical = json.dumps(snapshot['manifest'], sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        snapshot['manifest']['sha256'] = hashlib.sha256(canonical.encode()).hexdigest()
        # The graph receipt is the sole visibility switch after durable vector staging.
        await embedded_jobs.update_attempt(self.graph, project_id, attempt_id, phase='publishing', run_id=snapshot['run_id'])
        result = await publish_outline_snapshot(self.graph, snapshot)
        return dict(result, attempt_id=attempt_id)

    async def search(self, project_id: str, *, encoder_id: str, text: str = '', vector=None,
                     mode: str = 'hybrid', limit: int = 10) -> list[dict]:
        async with self._lock:
            self._require_open()
            publication = await read_outline_publication(self.graph, project_id)
            if publication is None:
                return []
            retrieval = publication['manifest'].get('retrieval')
            if not retrieval or retrieval['encoder_id'] != encoder_id:
                raise ValueError('Query encoder must match the published embedding identity')
            rows = await self.vectors.search(project_id, publication['run_id'], text=text,
                                              vector=vector, mode=mode, limit=limit)
            hashes = {file['path']: file['sha256'] for file in publication['manifest']['files']}
            for row in rows:
                if hashes.get(row['file_path']) != row['source_sha256']:
                    raise RuntimeError('Retrieved source citation does not match the publication')
                if hashlib.sha256(row['content'].encode()).hexdigest() != row['metadata']['content_sha256']:
                    raise RuntimeError('Retrieved chunk content hash mismatch')
            return rows

    async def describe_file(self, project_id: str, file_path: str, **bounds):
        async with self._lock:
            self._require_open()
            return await read_outline_file(self.graph, project_id, file_path, **bounds)

    async def overview(self, project_id: str) -> dict | None:
        """Return bounded publication metadata without loading source or an encoder."""
        async with self._lock:
            self._require_open()
            publication = await read_outline_publication(self.graph, project_id)
            if publication is None:
                return None
            manifest = publication['manifest']
            return {'project_id': project_id, 'run_id': publication['run_id'],
                    'files': len(manifest['files']),
                    'symbols': manifest['symbols'],
                    'manifest_sha256': manifest['sha256'],
                    'retrieval': manifest.get('retrieval'),
                    'relationships': {key: value for key, value in manifest.get('relationships', {}).items() if key != 'ids'},
                    'capabilities': ['published-source', 'file-outlines', 'text', 'vector', 'hybrid']
                                    + (['static-relationships-v1'] if manifest.get('relationships') else [])}

    async def symbol_context(self, project_id: str, symbol_name: str, **bounds):
        from graphrag_core.indexing.embedded_symbols import symbol_context
        async with self._lock:
            self._require_open()
            return await symbol_context(self.graph, project_id, symbol_name, **bounds)

    async def workspace_activity(self, project_id: str, **values):
        from memory.embedded_activity import workspace_activity
        async with self._lock:
            self._require_open()
            return await workspace_activity(self.graph, project_id, **values)

    async def indexing_attempt(self, project_id: str):
        async with self._lock:
            self._require_open()
            return await embedded_jobs.read_attempt(self.graph, project_id)

    async def project_metadata(self, project_id: str, **values):
        from memory.embedded_metadata import project_metadata
        async with self._lock:
            self._require_open()
            return await project_metadata(self.graph, project_id, **values)

    async def call_chain(self, project_id: str, symbol_name: str, **bounds):
        from graphrag_core.indexing.embedded_symbols import call_chain
        async with self._lock:
            self._require_open()
            return await call_chain(self.graph, project_id, symbol_name, **bounds)

    async def relationships(self, project_id: str, **bounds):
        from graphrag_core.indexing.embedded_relationships import read_relationships
        async with self._lock:
            self._require_open()
            return await read_relationships(self.graph, project_id, **bounds)

    async def file_facts(self, project_id: str, file_path: str, *, limit: int = 50, offset: int = 0):
        from graphrag_core.indexing.embedded_facts import read_file_facts
        async with self._lock:
            self._require_open()
            return await read_file_facts(self.graph, project_id, file_path, limit=limit, offset=offset)

    async def list_projects(self, *, limit: int = 25, after: str = '') -> dict:
        from graphrag_core.indexing.embedded_projects import list_projects
        async with self._lock:
            self._require_open()
            return await list_projects(self.graph, limit=limit, after=after)

    async def resolve_project(self, workspace_id: str) -> dict | None:
        from graphrag_core.indexing.embedded_projects import resolve_project
        async with self._lock:
            self._require_open()
            return await resolve_project(self.graph, workspace_id)

    async def cleanup_unpublished(self, project_id: str):
        async with self._lock:
            self._require_open()
            publication = await read_outline_publication(self.graph, project_id)
            await self.vectors.cleanup(project_id, publication['run_id'] if publication else None)

    async def delete_project(self, project_id: str):
        async with self._lock:
            self._require_open()
            async def delete(tx):
                for label in ('File', 'SourceEvidence', *SYMBOL_LABELS):
                    await tx.run(f'MATCH (n:{label} {{project_id:$project}}) DETACH DELETE n', project=project_id)
                await tx.run('MATCH (a:WorkspaceActivity {id:$project}) DELETE a', project=project_id)
                await tx.run('MATCH (j:EmbeddedIndexAttempt {id:$project}) DELETE j', project=project_id)
                await tx.run('MATCH (m:WorkspaceMetadata {id:$project}) DELETE m', project=project_id)
                await tx.run('MATCH (p:OutlinePublication {id:$project}) DELETE p', project=project_id)
            async with self.graph.session() as session:
                await session.execute_write(delete)
            # Graph deletion hides the project even if later physical vector cleanup fails.
            await self.vectors.cleanup(project_id, None)
