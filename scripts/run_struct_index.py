#!/usr/bin/env python3
"""Thin wrapper around the Rust-owned ts-pack structural index and finalizer."""

import argparse
import os
import sys

from dotenv import load_dotenv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(REPO_ROOT, ".env"))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tree_sitter_language_pack as ts_pack


def _metric_status_line(label: str, payload: dict, suffix: str) -> str:
    status = (payload or {}).get("status") or "unknown"
    updated = int((payload or {}).get("updated") or 0)
    extras: list[str] = []
    estimated_bytes = (payload or {}).get("estimated_bytes_max")
    sampling_size = (payload or {}).get("sampling_size")
    concurrency = (payload or {}).get("concurrency")
    threshold = (payload or {}).get("threshold")
    threshold_bytes = (payload or {}).get("threshold_bytes_max")
    if status == "ok":
        if estimated_bytes is not None:
            extras.append(f"estimated_bytes_max={estimated_bytes}")
        if sampling_size is not None:
            extras.append(f"sampling_size={sampling_size}")
        if concurrency is not None:
            extras.append(f"concurrency={concurrency}")
        extra_text = f" ({', '.join(extras)})" if extras else ""
        return f"[ts-pack:{label}] Done — {suffix.format(updated=updated)}{extra_text}"
    if status == "skipped":
        reason = (payload or {}).get("reason") or "unknown"
        file_nodes = int((payload or {}).get("file_nodes") or 0)
        rel_total = int((payload or {}).get("file_rel_total") or 0)
        rel_counts = (payload or {}).get("file_rel_counts") or {}
        if threshold is not None:
            extras.append(f"threshold={threshold}")
        if estimated_bytes is not None:
            extras.append(f"estimated_bytes_max={estimated_bytes}")
        if threshold_bytes is not None:
            extras.append(f"threshold_bytes_max={threshold_bytes}")
        if sampling_size is not None:
            extras.append(f"sampling_size={sampling_size}")
        if concurrency is not None:
            extras.append(f"concurrency={concurrency}")
        extra_text = f" extras={{{', '.join(extras)}}}" if extras else ""
        return (
            f"[ts-pack:{label}] Skipped — reason={reason} file_nodes={file_nodes} "
            f"file_rels={rel_total} rel_breakdown={rel_counts}{extra_text}"
        )
    if status == "failed":
        error = (payload or {}).get("error") or "unknown"
        if estimated_bytes is not None:
            extras.append(f"estimated_bytes_max={estimated_bytes}")
        if sampling_size is not None:
            extras.append(f"sampling_size={sampling_size}")
        if concurrency is not None:
            extras.append(f"concurrency={concurrency}")
        extra_text = f" ({', '.join(extras)})" if extras else ""
        return f"[ts-pack:{label}] Failed — {error}{extra_text}"
    return f"[ts-pack:{label}] Unknown status — {payload}"


def _count_file_metric(neo4j_uri: str, neo4j_user: str, neo4j_pass: str, neo4j_db: str, project_id: str, property_name: str) -> int:
    import neo4j

    driver = neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session(database=neo4j_db) as session:
            record = session.run(
                f"MATCH (f:File {{project_id:$pid}}) WHERE f.{property_name} IS NOT NULL RETURN count(f) AS n",
                pid=project_id,
            ).single()
            return int(record["n"]) if record else 0
    finally:
        driver.close()


def _count_isolated_files(neo4j_uri: str, neo4j_user: str, neo4j_pass: str, neo4j_db: str, project_id: str) -> int:
    import neo4j

    driver = neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session(database=neo4j_db) as session:
            record = session.run(
                "MATCH (f:File {project_id:$pid}) WHERE f.isolated = true RETURN count(f) AS n",
                pid=project_id,
            ).single()
            return int(record["n"]) if record else 0
    finally:
        driver.close()


def _set_struct_run_status(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str,
    project_id: str,
    status: str,
    *,
    error: str | None = None,
) -> None:
    import neo4j

    driver = neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session(database=neo4j_db) as session:
            record = session.run(
                """
                MATCH (p:Project {id:$pid})
                OPTIONAL MATCH (r:IndexRun {id:p.struct_index_run_id})
                SET p.struct_index_status = $status,
                    p.struct_index_finished_at = timestamp()
                FOREACH (_ IN CASE WHEN $status = 'done' THEN [1] ELSE [] END |
                    SET p.struct_active_run_id = p.struct_index_run_id,
                        p.struct_last_successful_run_id = p.struct_index_run_id,
                        p.struct_last_successful_finished_at = timestamp()
                )
                FOREACH (_ IN CASE WHEN r IS NULL THEN [] ELSE [1] END |
                    SET r.status = $status,
                        r.finished_at = timestamp()
                )
                FOREACH (_ IN CASE WHEN r IS NULL OR $status <> 'done' THEN [] ELSE [1] END |
                    SET r.promoted_at = timestamp()
                )
                FOREACH (_ IN CASE WHEN $error IS NULL THEN [1] ELSE [] END |
                    REMOVE p.struct_index_error
                )
                FOREACH (_ IN CASE WHEN r IS NULL OR $error IS NULL THEN [] ELSE [1] END |
                    REMOVE r.error
                )
                FOREACH (_ IN CASE WHEN $error IS NULL THEN [] ELSE [1] END |
                    SET p.struct_index_error = $error
                )
                FOREACH (_ IN CASE WHEN r IS NULL OR $error IS NULL THEN [] ELSE [1] END |
                    SET r.error = $error
                )
                RETURN p.struct_index_run_id AS run_id
                """,
                pid=project_id,
                status=status,
                error=(error[:2000] if error else None),
            ).single()
            if not record:
                return
    finally:
        driver.close()


def _get_struct_run_id(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str,
    project_id: str,
) -> str | None:
    import neo4j

    driver = neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session(database=neo4j_db) as session:
            record = session.run(
                "MATCH (p:Project {id:$pid}) RETURN p.struct_index_run_id AS run_id",
                pid=project_id,
            ).single()
            if not record:
                return None
            return record.get("run_id")
    finally:
        driver.close()


def _verify_struct_shadow_graph_clean(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str,
    project_id: str,
    run_id: str,
) -> None:
    import neo4j

    stale_node_query = """
    MATCH (n {project_id:$pid})
    WHERE (
        n:File OR n:Import OR n:CloneGroup OR n:FileCloneGroup OR n:Model
        OR n:ExternalAPI OR n:ApiRoute OR n:CargoCrate OR n:CargoWorkspace
        OR n:XcodeTarget OR n:XcodeWorkspace OR n:XcodeScheme OR n:Resource
        OR (n:Node AND NOT n:Chunk AND NOT n:File AND NOT n:Import)
    )
      AND coalesce(n.last_seen_run, '') <> $run_id
    RETURN labels(n) AS labels, count(n) AS stale_count
    ORDER BY stale_count DESC
    LIMIT 10
    """
    stale_rel_query = """
    MATCH ()-[r]->()
    WHERE r.project_id = $pid
      AND coalesce(r.last_seen_run, '') <> $run_id
    RETURN type(r) AS rel_type, count(r) AS stale_count
    ORDER BY stale_count DESC
    LIMIT 10
    """

    driver = neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session(database=neo4j_db) as session:
            stale_nodes = list(session.run(stale_node_query, pid=project_id, run_id=run_id))
            stale_rels = list(session.run(stale_rel_query, pid=project_id, run_id=run_id))
    finally:
        driver.close()

    if stale_nodes or stale_rels:
        node_bits = [
            f"labels={record['labels']} count={record['stale_count']}" for record in stale_nodes
        ]
        rel_bits = [
            f"type={record['rel_type']} count={record['stale_count']}" for record in stale_rels
        ]
        detail = "; ".join(node_bits + rel_bits)
        raise RuntimeError(
            "Shadow graph cleanup invariant failed after finalize/prune: "
            f"project={project_id} run={run_id} stale={detail}"
        )


def _promote_struct_shadow_graph(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str,
    canonical_project_id: str,
    shadow_project_id: str,
    run_id: str,
) -> None:
    import neo4j

    driver = neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session(database=neo4j_db) as session:
            session.run(
                """
                MATCH (old {project_id:$canonical_pid})
                WHERE NOT old:Chunk AND NOT old:Project AND NOT old:IndexRun
                DETACH DELETE old
                """,
                canonical_pid=canonical_project_id,
            ).consume()
            session.run(
                """
                MATCH (n {project_id:$shadow_pid})
                WHERE NOT n:Chunk AND NOT n:Project AND NOT n:IndexRun
                SET n.project_id = $canonical_pid,
                    n.last_promoted_run = $run_id,
                    n.id = coalesce(n.stable_id, n.id),
                    n.file_id = coalesce(n.stable_file_id, n.file_id)
                """,
                canonical_pid=canonical_project_id,
                shadow_pid=shadow_project_id,
                run_id=run_id,
            ).consume()
            session.run(
                """
                MATCH ()-[r]->()
                WHERE r.project_id = $shadow_pid
                SET r.project_id = $canonical_pid,
                    r.last_promoted_run = $run_id
                """,
                canonical_pid=canonical_project_id,
                shadow_pid=shadow_project_id,
                run_id=run_id,
            ).consume()
    finally:
        driver.close()


def _verify_shadow_namespace_cleared(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str,
    shadow_project_id: str,
) -> None:
    import neo4j

    driver = neo4j.GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session(database=neo4j_db) as session:
            node_record = session.run(
                "MATCH (n {project_id:$pid}) RETURN count(n) AS n",
                pid=shadow_project_id,
            ).single()
            rel_record = session.run(
                "MATCH ()-[r]->() WHERE r.project_id = $pid RETURN count(r) AS n",
                pid=shadow_project_id,
            ).single()
    finally:
        driver.close()

    node_count = int(node_record["n"]) if node_record else 0
    rel_count = int(rel_record["n"]) if rel_record else 0
    if node_count or rel_count:
        raise RuntimeError(
            "Shadow namespace cleanup failed after promotion: "
            f"project={shadow_project_id} nodes={node_count} rels={rel_count}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Structural (Rust-native) indexer")
    parser.add_argument("project_path")
    parser.add_argument("project_id")
    parser.add_argument("--manifest-file", required=True)
    parser.add_argument(
        "--neo4j-uri", default=os.getenv("LM_PROXY_NEO4J_URI", "bolt://127.0.0.1:7687")
    )
    parser.add_argument(
        "--neo4j-user", default=os.getenv("LM_PROXY_NEO4J_USER", "neo4j")
    )
    parser.add_argument(
        "--neo4j-pass", default=os.getenv("LM_PROXY_NEO4J_PASSWORD", "password")
    )
    parser.add_argument("--neo4j-db", default=os.getenv("LM_PROXY_NEO4J_DB", "proxy"))
    args = parser.parse_args()

    print(
        "[ts-pack:struct] NOTE: For aligned indexing, run the MCP tool "
        "index_workspace() which generates a shared manifest for struct/semantic.",
        file=sys.stderr,
        flush=True,
    )

    if not os.path.exists(args.manifest_file):
        print(
            f"[ts-pack:struct] ERROR: manifest not found: {args.manifest_file}",
            file=sys.stderr,
        )
        return 1

    print(
        f"[ts-pack:struct] Starting — project={args.project_id} "
        f"manifest={args.manifest_file}",
        file=sys.stderr,
        flush=True,
    )
    run_id = f"{args.project_id}:{os.getpid()}:{int(os.times().elapsed * 1_000_000_000)}"
    shadow_project_id = f"{args.project_id}::shadow::{run_id}"

    try:
        files = ts_pack.index_workspace(
            path=args.project_path,
            project_id=shadow_project_id,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_pass=args.neo4j_pass,
            manifest_file=args.manifest_file,
            status_project_id=args.project_id,
            run_id=run_id,
        )
        print(
            f"[ts-pack:struct] Done — {len(files)} files indexed.",
            file=sys.stderr,
            flush=True,
        )
    except Exception as exc:
        print(f"[ts-pack:struct] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1

    try:
        finalize = ts_pack.finalize_struct_graph(
            project_path=args.project_path,
            project_id=shadow_project_id,
            manifest_file=args.manifest_file,
            indexed_files=[str(fp) for fp in files],
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_pass=args.neo4j_pass,
            neo4j_db=args.neo4j_db,
            run_id=run_id,
        )
        if finalize.get("manifest_added"):
            print(
                f"[ts-pack:struct] Added {finalize.get('manifest_added')} manifest-only File nodes.",
                file=sys.stderr,
                flush=True,
            )
        if finalize.get("parsed_marked"):
            print(
                f"[ts-pack:struct] Marked {finalize.get('parsed_marked')} File nodes as parsed.",
                file=sys.stderr,
                flush=True,
            )
        if finalize.get("file_path_aliased"):
            print(
                f"[ts-pack:struct] Synced file_path alias on {finalize.get('file_path_aliased')} File nodes.",
                file=sys.stderr,
                flush=True,
            )
        if finalize.get("file_call_edges") is not None:
            print(
                f"[ts-pack:file-graph] Derived {finalize.get('file_call_edges')} CALLS_FILE edge(s) from symbol calls.",
                file=sys.stderr,
                flush=True,
            )
        if finalize.get("file_graph_links") is not None:
            print(
                f"[ts-pack:file-graph] Materialized {finalize.get('file_graph_links')} FILE_GRAPH_LINK edge(s) for GDS.",
                file=sys.stderr,
                flush=True,
            )
        enrichment = finalize.get("swift_enrichment") or {}
        if enrichment.get("enabled") and enrichment.get("available", True):
            print(
                "[ts-pack:struct] SourceKitten Swift enrichment "
                f"matched {enrichment.get('symbols', 0)} symbol(s) "
                f"across {enrichment.get('files', 0)} file(s).",
                file=sys.stderr,
                flush=True,
            )
        elif enrichment.get("enabled") and not enrichment.get("available", True):
            print(
                "[ts-pack:struct] SourceKitten Swift enrichment skipped — binary not available.",
                file=sys.stderr,
                flush=True,
            )
        pagerank_count = _count_file_metric(
            args.neo4j_uri, args.neo4j_user, args.neo4j_pass, args.neo4j_db, shadow_project_id, "pagerank"
        )
        print(f"[ts-pack:pagerank] Done — pagerank written to {pagerank_count} File nodes.", file=sys.stderr, flush=True)
        print(
            _metric_status_line(
                "leiden",
                finalize.get("louvain") or {},
                "community written to {updated} File nodes.",
            ),
            file=sys.stderr,
            flush=True,
        )
        print(
            _metric_status_line(
                "betweenness",
                finalize.get("betweenness") or {},
                "betweenness written to {updated} File nodes.",
            ),
            file=sys.stderr,
            flush=True,
        )
        print(
            _metric_status_line(
                "wcc",
                finalize.get("isolated") or {},
                "{updated} isolated File nodes marked.",
            ),
            file=sys.stderr,
            flush=True,
        )
        ts_pack.prune_struct_shadow_graph(
            project_id=shadow_project_id,
            run_id=run_id,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_pass=args.neo4j_pass,
        )
        print(
            f"[ts-pack:shadow] Done — stale structural graph data pruned for run {run_id}.",
            file=sys.stderr,
            flush=True,
        )
        _verify_struct_shadow_graph_clean(
            args.neo4j_uri,
            args.neo4j_user,
            args.neo4j_pass,
            args.neo4j_db,
            shadow_project_id,
            run_id,
        )
        print(
            f"[ts-pack:shadow] Verified — no stale structural graph data remains for run {run_id}.",
            file=sys.stderr,
            flush=True,
        )
        _promote_struct_shadow_graph(
            args.neo4j_uri,
            args.neo4j_user,
            args.neo4j_pass,
            args.neo4j_db,
            args.project_id,
            shadow_project_id,
            run_id,
        )
        print(
            f"[ts-pack:shadow] Promoted — shadow namespace {shadow_project_id} is now canonical {args.project_id}.",
            file=sys.stderr,
            flush=True,
        )
        _verify_shadow_namespace_cleared(
            args.neo4j_uri,
            args.neo4j_user,
            args.neo4j_pass,
            args.neo4j_db,
            shadow_project_id,
        )
        print(
            f"[ts-pack:shadow] Cleared — no remaining nodes or relationships under {shadow_project_id}.",
            file=sys.stderr,
            flush=True,
        )
        _set_struct_run_status(
            args.neo4j_uri,
            args.neo4j_user,
            args.neo4j_pass,
            args.neo4j_db,
            args.project_id,
            "done",
        )
    except Exception as exc:
        _set_struct_run_status(
            args.neo4j_uri,
            args.neo4j_user,
            args.neo4j_pass,
            args.neo4j_db,
            args.project_id,
            "finalize_failed",
            error=str(exc),
        )
        print(
            f"[ts-pack:struct] WARNING: Rust graph finalization failed: {exc}",
            file=sys.stderr,
            flush=True,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
