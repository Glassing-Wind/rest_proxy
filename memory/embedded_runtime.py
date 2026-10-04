"""Opt-in process owner for embedded MCP operations; engines remain lazy imports."""
from __future__ import annotations

import asyncio
import hashlib
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

    async def close(self):
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
