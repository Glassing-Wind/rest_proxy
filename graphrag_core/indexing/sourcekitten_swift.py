"""Best-effort Swift graph enrichment via ts-pack Rust bindings."""

from __future__ import annotations


def enrich_swift_graph(
    *,
    project_path: str,
    project_id: str,
    indexed_files: list[str],
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str = "proxy",
) -> dict[str, int | bool | str]:
    import tree_sitter_language_pack as ts_pack

    result = ts_pack.enrich_swift_graph(
        project_path=project_path,
        project_id=project_id,
        indexed_files=indexed_files,
        neo4j_uri=neo4j_uri,
        neo4j_user=neo4j_user,
        neo4j_pass=neo4j_pass,
        neo4j_db=neo4j_db,
    )
    return dict(result or {})
