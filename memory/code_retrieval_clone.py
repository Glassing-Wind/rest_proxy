"""Optional clone-aware deduplication for code retrieval results."""

from __future__ import annotations

import hashlib
import os

from memory import code_retrieval_loaders as retrieval_loaders
from memory import retrieval_metadata


async def apply_clone_dedup(
    all_results: list[dict],
    *,
    pids: list[str],
    include_debug: bool = False,
) -> list[dict]:
    """Apply optional Neo4j-backed clone deduplication, failing open on errors."""
    try:
        import graph_bootstrap

        driver = await graph_bootstrap.require_driver()
        if driver:
            for r in all_results:
                meta = r.get("_meta")
                if not isinstance(meta, dict):
                    meta = retrieval_metadata.coerce_meta(r)

            by_project: dict[str, list[dict]] = {}
            for idx, r in enumerate(all_results):
                fp = r.get("file_path")
                line = (r.get("_meta") or {}).get("start_line")
                pid = r.get("project_id")
                if not fp or not isinstance(line, int) or not pid:
                    continue
                by_project.setdefault(pid, []).append(
                    {"idx": idx, "fp": fp, "line": line}
                )

            clone_map: dict[int, str | None] = {}
            for pid, items in by_project.items():
                async with driver.session(
                    database=graph_bootstrap._NEO4J_DB
                ) as session:
                    records = await retrieval_loaders.execute_graph_read(
                        session,
                        """
                        UNWIND $items AS item
                        MATCH (f:File {project_id:$pid, filepath:item.fp})-[:CONTAINS]->(s)
                        WHERE (s:Function OR s:Method OR s:Class OR s:Struct)
                          AND s.start_line <= item.line AND s.end_line >= item.line
                        OPTIONAL MATCH (s)-[:MEMBER_OF_CLONE_GROUP]->(g:CloneGroup)
                        WITH item, s, g
                        ORDER BY (s.end_line - s.start_line) ASC
                        WITH item, collect(g.id)[0] AS gid
                        RETURN item.idx AS idx, gid
                        """,
                        items=items,
                        pid=pid,
                        op="clone_dedup_map",
                    )
                for rec in records:
                    clone_map[int(rec["idx"])] = rec.get("gid")

            file_group_map: dict[str, str] = {}
            file_group_source = (
                os.getenv("LM_PROXY_FILE_CLONE_SOURCE", "function")
                .strip()
                .lower()
            )
            if file_group_source not in {"chunk", "function", "hybrid"}:
                file_group_source = "function"

            for pid, items in by_project.items():
                async with driver.session(
                    database=graph_bootstrap._NEO4J_DB
                ) as session:
                    file_records = []
                    if file_group_source == "chunk":
                        file_records = await retrieval_loaders.execute_graph_read(
                            session,
                            """
                            UNWIND $items AS item
                            MATCH (f:File {project_id:$pid, filepath:item.fp})
                            OPTIONAL MATCH (f)-[:MEMBER_OF_FILE_CLONE_GROUP]->(g:FileCloneGroup)
                            RETURN item.fp AS fp, collect(g.id)[0] AS gid
                            """,
                            items=items,
                            pid=pid,
                            op="clone_dedup_file_map",
                        )

                    func_records = []
                    if file_group_source in {"function", "hybrid"}:
                        func_records = await retrieval_loaders.execute_graph_read(
                            session,
                            """
                            UNWIND $items AS item
                            MATCH (f:File {project_id:$pid, filepath:item.fp})-[:CONTAINS]->(s)
                            WHERE (s:Function OR s:Method OR s:Class OR s:Struct)
                              AND s.start_line <= item.line AND s.end_line >= item.line
                            OPTIONAL MATCH (s)-[:MEMBER_OF_CLONE_GROUP]->(g:CloneGroup)
                            WITH item, collect(DISTINCT g.id) AS gids
                            RETURN item.fp AS fp, gids
                            """,
                            items=items,
                            pid=pid,
                            op="clone_dedup_file_map_function",
                        )

                    func_group_map: dict[str, str] = {}
                    for row in func_records:
                        fp = row.get("fp")
                        gids = [g for g in (row.get("gids") or []) if g]
                        if not fp or not gids:
                            continue
                        gids.sort()
                        gid = hashlib.md5(
                            "|".join(gids).encode()
                        ).hexdigest()[:12]
                        func_group_map[fp] = gid

                    if func_group_map:
                        for fp, gid in func_group_map.items():
                            file_group_map[fp] = gid

                    for row in file_records:
                        fp = row.get("fp")
                        gid = row.get("gid")
                        if fp and gid and fp not in file_group_map:
                            file_group_map[fp] = gid

            debug_clone = os.getenv(
                "LM_PROXY_CLONE_DEBUG", "0"
            ).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

            debug_lines: list[str] = []
            if debug_clone:
                debug_lines.append(
                    f"clone_dedup file_groups={len(file_group_map)}"
                )

            if file_group_map:
                seen_file_gids: set[str] = set()
                deduped_by_file: list[dict] = []
                for r in all_results:
                    fp = r.get("file_path")
                    file_gid = file_group_map.get(fp) if fp else None
                    if debug_clone and fp:
                        debug_lines.append(
                            f"clone_dedup file={fp} gid={file_gid}"
                        )
                    if file_gid:
                        if file_gid in seen_file_gids:
                            continue
                        seen_file_gids.add(file_gid)
                    deduped_by_file.append(r)
                all_results = deduped_by_file

            seen_gids: set[str] = set()
            deduped: list[dict] = []
            for idx, r in enumerate(all_results):
                gid = clone_map.get(idx)
                if gid:
                    if gid in seen_gids:
                        continue
                    seen_gids.add(gid)
                deduped.append(r)
            all_results = deduped
            if debug_clone and debug_lines and include_debug:
                all_results.insert(
                    0,
                    {
                        "file_path": "[clone_dedup_debug]",
                        "content": "\n".join(debug_lines[:20]),
                        "rrf": 1.0,
                        "project_id": pids[0] if pids else "",
                    },
                )
    except Exception:
        pass
    return all_results
