"""Best-effort Swift graph enrichment via parser-owned semantic facts."""

from __future__ import annotations

import os
from typing import Any

_TX_TIMEOUT = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
_TX_METADATA_BASE = {"source": "lm_proxy", "tool": "sourcekitten_swift"}


def _enabled() -> bool:
    return os.getenv("LM_PROXY_SWIFT_SOURCEKITTEN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _clean_name(name: str) -> str:
    return (name or "").strip()


def _base_name(name: str) -> str:
    return _clean_name(name).split("(", 1)[0]


def _load_ts_pack():
    import tree_sitter_language_pack as ts_pack

    return ts_pack


def _match_symbol_record(
    record: dict[str, Any], symbols: list[dict[str, Any]]
) -> dict[str, Any] | None:
    candidates = []
    for sym in symbols:
        sym_name = _clean_name(sym.get("name", ""))
        sym_base = _base_name(sym_name)
        record_name = _clean_name(record.get("name", ""))
        record_base = _base_name(record.get("base_name") or record_name)
        if record_base != sym_base and record_name != sym_name:
            continue
        start_line = int(sym.get("start_line", 0) or 0)
        end_line = int(sym.get("end_line", 0) or 0)
        record_start = int(record.get("start_line", 0) or 0)
        record_end = int(record.get("end_line", 0) or 0)
        overlap = min(record_end, end_line) - max(record_start, start_line)
        distance = abs(record_start - start_line) + abs(record_end - end_line)
        candidates.append(
            (
                0 if record_name == sym_name else 1,
                0 if overlap >= 0 else 1,
                distance,
                start_line,
                sym,
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[:4])
    return candidates[0][4]


def _load_swift_symbols(session, project_id: str, filepaths: list[str]) -> dict[str, list[dict[str, Any]]]:
    from neo4j import unit_of_work

    metadata = dict(_TX_METADATA_BASE)
    metadata["op"] = "load_swift_symbols"

    @unit_of_work(timeout=_TX_TIMEOUT, metadata=metadata)
    def _tx(tx):
        result = tx.run(
            """
            MATCH (f:File {project_id:$pid})-[:CONTAINS]->(s:Node)
            WHERE f.filepath IN $fps
            RETURN f.filepath AS filepath,
                   s.id AS sid,
                   s.name AS name,
                   s.kind AS kind,
                   s.start_line AS start_line,
                   s.end_line AS end_line
            ORDER BY filepath, start_line, end_line, name
            """,
            pid=project_id,
            fps=filepaths,
        )
        return result.data()

    rows = session.execute_read(_tx) if hasattr(session, "execute_read") else _tx(session)
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(row["filepath"], []).append(row)
    return out


def _write_swift_enrichment(session, project_id: str, symbol_rows: list[dict[str, Any]]) -> None:
    from neo4j import unit_of_work

    if not symbol_rows:
        return
    metadata = dict(_TX_METADATA_BASE)
    metadata["op"] = "write_swift_enrichment"

    @unit_of_work(timeout=_TX_TIMEOUT, metadata=metadata)
    def _tx(tx):
        tx.run(
            """
            UNWIND $rows AS row
            MATCH (s:Node {project_id:$pid, id:row.sid})
            SET s.swift_sourcekitten = true,
                s.swift_sourcekitten_kind = row.kind,
                s.swift_usr = CASE
                    WHEN row.usr IS NOT NULL AND trim(row.usr) <> '' THEN row.usr
                    ELSE s.swift_usr
                END,
                s.swift_inherited_types = row.inherited_types,
                s.doc_comment = CASE
                    WHEN (s.doc_comment IS NULL OR trim(s.doc_comment) = '')
                         AND row.doc_comment IS NOT NULL
                         AND trim(row.doc_comment) <> ''
                    THEN row.doc_comment
                    ELSE s.doc_comment
                END,
                s.swift_doc_comment = CASE
                    WHEN row.doc_comment IS NOT NULL AND trim(row.doc_comment) <> ''
                    THEN row.doc_comment
                    ELSE s.swift_doc_comment
                END
            FOREACH (_ IN CASE WHEN row.inherited_types IS NULL OR size(row.inherited_types) = 0 THEN [] ELSE [1] END |
                FOREACH (type_name IN row.inherited_types |
                    MERGE (t:SwiftTypeRef {project_id:$pid, name:type_name})
                    MERGE (s)-[:SWIFT_INHERITS_TYPE]->(t)
                )
            )
            """,
            pid=project_id,
            rows=symbol_rows,
        ).consume()

    if hasattr(session, "execute_write"):
        session.execute_write(_tx)
    else:
        _tx(session)


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
    if not _enabled():
        return {"enabled": False, "files": 0, "symbols": 0}

    root = os.path.abspath(project_path)
    swift_abs_paths = sorted(
        {
            os.path.abspath(str(path))
            for path in indexed_files
            if str(path).endswith(".swift") and os.path.isfile(str(path))
        }
    )
    if not swift_abs_paths:
        return {"enabled": True, "available": True, "files": 0, "symbols": 0}

    filepaths = [
        os.path.relpath(path, root).replace(os.sep, "/")
        for path in swift_abs_paths
        if path.startswith(root)
    ]
    ts_pack = _load_ts_pack()
    semantic_records = ts_pack.extract_swift_semantic_facts(project_path) or {}
    if not semantic_records:
        return {"enabled": True, "available": False, "files": 0, "symbols": 0}

    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session(database=neo4j_db) as session:
            graph_symbols = _load_swift_symbols(session, project_id, filepaths)
            updates: list[dict[str, Any]] = []
            files_with_matches = 0
            for _abs_path, rel_path in zip(swift_abs_paths, filepaths):
                records = semantic_records.get(rel_path, [])
                if not records:
                    continue
                matched_here = 0
                symbols = graph_symbols.get(rel_path, [])
                for record in records:
                    match = _match_symbol_record(record, symbols)
                    if not match:
                        continue
                    matched_here += 1
                    updates.append(
                        {
                            "sid": match["sid"],
                            "kind": record.get("kind"),
                            "usr": record.get("usr"),
                            "doc_comment": record.get("doc_comment"),
                            "inherited_types": record.get("inherited_types") or [],
                        }
                    )
                if matched_here:
                    files_with_matches += 1
            _write_swift_enrichment(session, project_id, updates)
            return {
                "enabled": True,
                "available": True,
                "files": files_with_matches,
                "symbols": len(updates),
            }
    finally:
        driver.close()
