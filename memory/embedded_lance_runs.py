"""Experimental run-scoped LanceDB storage; failures never fall back to appends."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import Path

from memory.embedded_ladybug import _finish_thread
from memory.embedded_owner import EmbeddedOwnerLock


def _literal(value: str) -> str:
    if not isinstance(value, str) or not value or '\0' in value or len(value) > 1024:
        raise ValueError('Invalid scope value')
    return "'" + value.replace("'", "''") + "'"


def _scope(project_id: str, run_id: str) -> str:
    return f'project_id = {_literal(project_id)} AND run_id = {_literal(run_id)}'


class LanceRunStore:
    def __init__(self, path: str, dimension: int):
        import lancedb
        import pyarrow as pa

        if not 1 <= dimension <= 4096:
            raise ValueError('Invalid embedding dimension')
        self.dimension = dimension
        self._lock = asyncio.Lock()
        self._closed = False
        self._owner = EmbeddedOwnerLock(path)
        try:
            Path(path).mkdir(parents=True, exist_ok=True, mode=0o700)
            self._db = lancedb.connect(path)
            schema = pa.schema([
                ('id', pa.string()), ('project_id', pa.string()), ('run_id', pa.string()),
                ('ref_id', pa.string()), ('file_path', pa.string()), ('content', pa.string()),
                ('source_sha256', pa.string()), ('metadata', pa.string()),
                ('vector', pa.list_(pa.float32(), dimension)),
            ])
            names = self._db.list_tables().tables
            self._table = (self._db.open_table('run_chunks') if 'run_chunks' in names
                           else self._db.create_table('run_chunks', schema=schema))
            if self._table.schema != schema:
                raise RuntimeError('Incompatible LanceDB run-chunk schema or dimension')
        except BaseException:
            self._owner.close()
            raise

    def validate_vector(self, vector):
        values = [float(x) for x in vector]
        if len(values) != self.dimension or not all(math.isfinite(x) for x in values):
            raise ValueError('Invalid vector dimension or nonfinite values')
        norm = math.hypot(*values)
        if norm == 0 or not math.isfinite(norm):
            raise ValueError('Cosine retrieval requires a finite nonzero vector norm')
        # Cosine is scale invariant; normalization prevents float32 storage overflow.
        return [x / norm for x in values]

    async def _call(self, operation):
        async with self._lock:
            if self._closed:
                raise RuntimeError('Vector store is closed')
            return await _finish_thread(operation)

    async def stage_run(self, project_id: str, run_id: str, chunks: list[dict]) -> int:
        _scope(project_id, run_id)
        rows = []
        refs = set()
        for chunk in chunks:
            self.validate_vector(chunk['vector'])
            ref = chunk['ref_id']
            if not isinstance(ref, str) or not ref or ref in refs:
                raise ValueError('Chunk refs must be nonempty and unique within a run')
            refs.add(ref)
            identity = json.dumps([project_id, run_id, ref], separators=(',', ':'))
            rows.append({'id': hashlib.sha256(identity.encode()).hexdigest(),
                         'project_id': project_id, 'run_id': run_id, 'ref_id': ref,
                         'file_path': chunk['file_path'], 'content': chunk['content'],
                         'source_sha256': chunk['source_sha256'],
                         'metadata': json.dumps(chunk['metadata'], sort_keys=True),
                         'vector': self.validate_vector(chunk['vector'])})

        def write():
            from lancedb.index import FTS

            if rows:
                self._table.merge_insert('id').when_matched_update_all().when_not_matched_insert_all().execute(rows)
                # Make newly staged text searchable before graph publication.
                self._table.create_index('content', config=FTS(), replace=True)
            count = self._table.count_rows(_scope(project_id, run_id))
            if count != len(rows):
                raise RuntimeError('Staged run row count does not match the supplied complete run')
            return count

        return await self._call(write)

    async def search(self, project_id: str, run_id: str, *, text: str = '', vector=None,
                     mode: str = 'hybrid', limit: int = 10) -> list[dict]:
        predicate = _scope(project_id, run_id)
        if mode not in {'text', 'vector', 'hybrid'} or not 1 <= limit <= 100:
            raise ValueError('Invalid search mode or limit')
        if mode in {'vector', 'hybrid'}:
            vector = self.validate_vector(vector if vector is not None else [])
        if mode in {'text', 'hybrid'} and not text.strip():
            raise ValueError('Text query is required')

        def read():
            from lancedb.rerankers import RRFReranker

            if self._table.count_rows(predicate) == 0:
                return []
            if mode == 'text':
                query = self._table.search(text, query_type='fts')
            elif mode == 'vector':
                query = self._table.search(vector, query_type='vector').metric('cosine')
            else:
                query = self._table.search(query_type='hybrid').vector(vector).text(text).metric('cosine')
                query = query.rerank(RRFReranker())
            rows = query.where(predicate, prefilter=True).limit(limit).to_list()
            for row in rows:
                row.pop('vector', None)
                row['metadata'] = json.loads(row['metadata'])
                if row['project_id'] != project_id or row['run_id'] != run_id:
                    raise RuntimeError('Retrieval escaped the published scope')
            return rows

        return await self._call(read)

    async def count(self, project_id: str, run_id: str) -> int:
        return await self._call(lambda: self._table.count_rows(_scope(project_id, run_id)))

    async def cleanup(self, project_id: str, keep_run: str | None) -> None:
        predicate = f'project_id = {_literal(project_id)}'
        if keep_run is not None:
            predicate += f' AND run_id != {_literal(keep_run)}'
        await self._call(lambda: self._table.delete(predicate))

    async def close(self):
        async with self._lock:
            if not self._closed:
                self._table = None
                self._db = None
                self._closed = True
                self._owner.close()
