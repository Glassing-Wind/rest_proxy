"""Shared Neo4j transaction helpers."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Dict

from neo4j import unit_of_work

from graphrag_core.telemetry import operation
from memory.embedded_ladybug import LadybugSession


def _managed_operation(session, name: str):
    # Embedded sessions already observe their complete managed transaction.
    return nullcontext() if isinstance(session, LadybugSession) else operation('graph', name, backend='neo4j')


def _build_metadata(
    base_metadata: Dict[str, Any],
    op: str | None,
    op_prefix: str | None,
) -> Dict[str, Any]:
    metadata = dict(base_metadata)
    op_value = op or "read"
    if op_prefix:
        op_value = f"{op_prefix}.{op_value}"
    metadata["op"] = op_value
    return metadata


async def execute_read(
    session,
    cypher: str,
    *,
    op: str | None = None,
    op_prefix: str | None = None,
    timeout_s: float = 30.0,
    base_metadata: Dict[str, Any] | None = None,
    **params: Any,
):
    metadata = _build_metadata(base_metadata or {}, op, op_prefix)

    @unit_of_work(timeout=timeout_s, metadata=metadata)
    async def _tx(tx):
        result = await tx.run(cypher, **params)
        return await result.data()

    # Caller metadata and Cypher belong to the driver, never to telemetry.
    with _managed_operation(session, 'graph.execute_read'):
        if hasattr(session, "execute_read"):
            return await session.execute_read(_tx)
        return await _tx(session)


async def execute_write(
    session,
    cypher: str,
    *,
    op: str | None = None,
    op_prefix: str | None = None,
    timeout_s: float = 30.0,
    base_metadata: Dict[str, Any] | None = None,
    **params: Any,
) -> None:
    metadata = _build_metadata(base_metadata or {}, op, op_prefix)

    @unit_of_work(timeout=timeout_s, metadata=metadata)
    async def _tx(tx):
        result = await tx.run(cypher, **params)
        await result.consume()

    with _managed_operation(session, 'graph.execute_write'):
        if hasattr(session, "execute_write"):
            await session.execute_write(_tx)
        else:
            await _tx(session)

