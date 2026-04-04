"""Shared Neo4j transaction helpers."""

from __future__ import annotations

from typing import Any, Dict

from neo4j import unit_of_work


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

    if hasattr(session, "execute_write"):
        await session.execute_write(_tx)
    else:
        await _tx(session)

