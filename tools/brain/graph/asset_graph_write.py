"""Write-phase helpers for asset graph persistence."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable


ExecuteWrite = Callable[..., Awaitable[None]]


async def clear_existing_edges(session, execute_write: ExecuteWrite, project_id: str, write_timeout_s: float) -> None:
    statements = [
        "MATCH (a:File {project_id:$p})-[r:ASSET_LINKS]->() DELETE r",
        "MATCH (a:File {project_id:$p})-[r:CALLS_API]->() DELETE r",
        "MATCH (a:File {project_id:$p})-[r:CALLS_API_ROUTE]->() DELETE r",
        "MATCH (r:ApiRoute {project_id:$p})-[rel:HANDLED_BY]->() DELETE rel",
        "MATCH (a:File {project_id:$p})-[r:CALLS_SERVICE]->() DELETE r",
        "MATCH (a:File {project_id:$p})-[r:CALLS_DB]->() DELETE r",
        "MATCH (a:File {project_id:$p})-[r:CALLS_API_EXTERNAL]->() DELETE r",
        "MATCH (a:File {project_id:$p})-[r:USES_ASSET|USES_COLOR_ASSET|USES_XIB|USES_STORYBOARD]->() DELETE r",
        "MATCH (r:Resource {project_id:$p})-[rel:BACKED_BY_FILE]->() DELETE rel",
        "MATCH (:XcodeTarget {project_id:$p})-[rel:BUNDLES_FILE]->() DELETE rel",
        "MATCH (r:Resource {project_id:$p})-[rel:BUNDLED_IN_TARGET]->() DELETE rel",
        "MATCH (:XcodeWorkspace {project_id:$p})-[rel:REFERENCES_PROJECT]->() DELETE rel",
        "MATCH (:XcodeScheme {project_id:$p})-[rel:BUILDS_TARGET]->() DELETE rel",
        "MATCH (:XcodeScheme {project_id:$p})-[rel:DEFINED_IN_FILE]->() DELETE rel",
        "MATCH (t:XcodeTarget {project_id:$p}) DELETE t",
        "MATCH (s:XcodeScheme {project_id:$p}) DELETE s",
        "MATCH (w:XcodeWorkspace {project_id:$p}) DELETE w",
    ]
    for query in statements:
        await execute_write(session, query, p=project_id, timeout=write_timeout_s)


async def write_file_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    rel_name: str,
    edges: list[tuple[str, str]],
) -> None:
    if not edges:
        return
    query = f"""
    UNWIND $batch AS edge
    MATCH (a:File {{id: edge.src}})
    MATCH (b:File {{id: edge.tgt}})
    MERGE (a)-[:{rel_name}]->(b)
    """
    for i in range(0, len(edges), batch_size):
        batch = [{"src": s, "tgt": t} for s, t in edges[i : i + batch_size]]
        async with write_semaphore:
            await execute_write(session, query, batch=batch, timeout=write_timeout_s)


async def write_api_route_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    project_id: str,
    api_route_edges: list[tuple[str, str, str]],
    api_route_handler_edges: list[tuple[str, str, str]],
) -> None:
    if api_route_edges:
        query = """
        UNWIND $batch AS edge
        MATCH (a:File {id: edge.src})
        MERGE (r:ApiRoute {project_id: edge.project_id, path: edge.path, method: edge.method})
        ON CREATE SET r.name = edge.method + ' ' + edge.path
        SET r.filepath = edge.path
        MERGE (a)-[:CALLS_API_ROUTE]->(r)
        """
        for i in range(0, len(api_route_edges), batch_size):
            batch = [{"src": s, "path": p, "method": m, "project_id": project_id} for s, p, m in api_route_edges[i : i + batch_size]]
            async with write_semaphore:
                await execute_write(session, query, batch=batch, timeout=write_timeout_s)
    if api_route_handler_edges:
        query = """
        UNWIND $batch AS edge
        MATCH (r:ApiRoute {project_id: edge.project_id, path: edge.path, method: edge.method})
        MATCH (b:File {id: edge.tgt})
        MERGE (r)-[:HANDLED_BY]->(b)
        """
        for i in range(0, len(api_route_handler_edges), batch_size):
            batch = [{"path": p, "method": m, "tgt": t, "project_id": project_id} for p, m, t in api_route_handler_edges[i : i + batch_size]]
            async with write_semaphore:
                await execute_write(session, query, batch=batch, timeout=write_timeout_s)


async def write_external_api_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    project_id: str,
    external_edges: list[tuple[str, str]],
) -> None:
    if not external_edges:
        return
    query = """
    UNWIND $batch AS edge
    MATCH (a:File {id: edge.src})
    MERGE (ext:ExternalAPI {project_id: edge.project_id, url: edge.url})
    ON CREATE SET ext.name = edge.url
    MERGE (a)-[:CALLS_API_EXTERNAL]->(ext)
    """
    for i in range(0, len(external_edges), batch_size):
        batch = [{"src": s, "url": u, "project_id": project_id} for s, u in external_edges[i : i + batch_size]]
        async with write_semaphore:
            await execute_write(session, query, batch=batch, timeout=write_timeout_s)
