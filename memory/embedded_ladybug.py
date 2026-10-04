"""Experimental Ladybug graph transactions and application file-outline reads.

One driver owns a Database object. Sessions use separate connections, with all
operations serialized within this owner. Cross-process ownership and the full
Neo4j query/schema corpus remain outside this adapter's acceptance scope.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any


async def _finish_thread(operation):
    """Drain native work before cancellation can trigger rollback or close."""
    task = asyncio.create_task(asyncio.to_thread(operation))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    # Retrieve native exceptions even when cancellation arrived concurrently.
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class LadybugResult:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows

    async def data(self) -> list[dict[str, Any]]:
        return self._rows

    async def consume(self) -> None:
        return None


class LadybugTransaction:
    def __init__(self, connection):
        self._connection = connection
        self._lock = asyncio.Lock()
        self._active = True

    async def run(self, cypher: str, **params: Any) -> LadybugResult:
        async with self._lock:
            if not self._active:
                raise RuntimeError('Transaction is closed')

            def execute():
                result = self._connection.execute(cypher, parameters=params)
                try:
                    columns = result.get_column_names()
                    return LadybugResult([dict(zip(columns, row)) for row in result.get_all()])
                finally:
                    result.close()

            return await _finish_thread(execute)


class LadybugSession:
    def __init__(self, driver):
        self._driver = driver
        self._connection = None
        self._closed = False

    async def __aenter__(self):
        async with self._driver._lock:
            self._driver._require_open()
            if self._closed or self._connection is not None:
                raise RuntimeError('Session cannot be entered twice')
            # Connection construction is small; establish ownership before any await.
            self._connection = self._driver._engine.Connection(self._driver._database)
            self._driver._sessions.add(self)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        async with self._driver._lock:
            try:
                if self._connection is not None:
                    await _finish_thread(self._connection.close)
            finally:
                self._connection = None
                self._closed = True
                self._driver._sessions.discard(self)
        return False

    async def _execute(self, callback, read_only: bool, *args, **kwargs):
        async with self._driver._lock:
            self._driver._require_open()
            if self._closed or self._connection is None:
                raise RuntimeError('Session is not open')
            tx = LadybugTransaction(self._connection)
            try:
                await tx.run('BEGIN TRANSACTION READ ONLY' if read_only else 'BEGIN TRANSACTION')
                result = await callback(tx, *args, **kwargs)
                await tx.run('COMMIT')
                return result
            except BaseException:
                # Includes cancellation: native operations finish before rollback.
                try:
                    await tx.run('ROLLBACK')
                except Exception:
                    # Closing a poisoned connection must not mask the original failure.
                    await _finish_thread(self._connection.close)
                    self._connection = None
                    self._closed = True
                    self._driver._sessions.discard(self)
                raise
            finally:
                tx._active = False

    async def execute_read(self, callback, *args, **kwargs):
        return await self._execute(callback, True, *args, **kwargs)

    async def execute_write(self, callback, *args, **kwargs):
        return await self._execute(callback, False, *args, **kwargs)


class LadybugGraphDriver:
    def __init__(self, db_path: str, *, buffer_pool_size: int = 64 * 1024 * 1024):
        import ladybug

        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        from memory.embedded_owner import EmbeddedOwnerLock
        self.db_path = str(Path(db_path).resolve())
        self._owner = EmbeddedOwnerLock(self.db_path)
        self._engine = ladybug
        try:
            self._database = ladybug.Database(self.db_path, buffer_pool_size=buffer_pool_size,
                                             max_num_threads=2)
        except BaseException:
            self._owner.close()
            raise
        self._lock = asyncio.Lock()
        self._sessions = set()
        self._closed = False

    async def initialize_schema(self) -> None:
        from memory.embedded_schema import SCHEMA_COLUMNS, SCHEMA_STATEMENTS, SCHEMA_VERSION

        async def initialize(tx):
            for statement in SCHEMA_STATEMENTS:
                await tx.run(statement)
            for table, expected in SCHEMA_COLUMNS.items():
                columns = await (await tx.run(f"CALL table_info('{table}') RETURN *")).data()
                actual = {column['name']: column['type'] for column in columns}
                if actual != expected or not any(
                    column['name'] == 'id' and column['primary key'] for column in columns
                ):
                    raise RuntimeError(f'Incompatible Ladybug schema table: {table}')
            rows = await (await tx.run(
                "MATCH (s:EmbeddedSchema {id:'file_outline'}) RETURN s.version AS version"
            )).data()
            if rows and rows != [{'version': SCHEMA_VERSION}]:
                raise RuntimeError('Unsupported Ladybug file-outline schema version')
            if not rows:
                await tx.run("CREATE (:EmbeddedSchema {id:'file_outline', version:$version})",
                             version=SCHEMA_VERSION)

        async with self.session() as session:
            await session.execute_write(initialize)

    async def describe_file_symbols(self, file_id: str) -> list[dict[str, Any]]:
        from memory.embedded_schema import FILE_SYMBOL_QUERY

        async def read(tx):
            return await (await tx.run(FILE_SYMBOL_QUERY, fid=file_id)).data()

        async with self.session() as session:
            rows = await session.execute_read(read)
        return sorted(rows, key=lambda row: (row['start'] is None, row['start'] or 0,
                                            row['kind'], row['id']))

    def _require_open(self):
        if self._closed:
            raise RuntimeError('Driver is closed')

    def session(self, database=None):
        self._require_open()
        return LadybugSession(self)

    async def close(self):
        async with self._lock:
            if self._closed:
                return
            if self._sessions:
                raise RuntimeError('Close sessions before closing the driver')
            try:
                await _finish_thread(self._database.close)
            except asyncio.CancelledError:
                # Native close completed before _finish_thread propagates cancellation.
                self._closed = True
                self._owner.close()
                raise
            self._closed = True
            self._owner.close()
