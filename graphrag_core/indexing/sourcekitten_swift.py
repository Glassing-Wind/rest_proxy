"""Best-effort Swift graph enrichment via SourceKitten."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_TX_TIMEOUT = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
_TX_METADATA_BASE = {"source": "lm_proxy", "tool": "sourcekitten_swift"}


@dataclass
class SwiftSymbolRecord:
    filepath: str
    name: str
    base_name: str
    kind: str
    start_line: int
    end_line: int
    usr: str | None
    doc_comment: str | None
    inherited_types: list[str]


def _enabled() -> bool:
    return os.getenv("LM_PROXY_SWIFT_SOURCEKITTEN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _which(name: str) -> str | None:
    hit = shutil.which(name)
    if hit:
        return hit
    py_bin = os.path.dirname(sys.executable)
    candidate = os.path.join(py_bin, name)
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    for path in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}", f"/usr/bin/{name}"):
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _line_number(raw: bytes, offset: int) -> int:
    return raw[: max(0, offset)].count(b"\n") + 1


def _clean_name(name: str) -> str:
    return (name or "").strip()


def _base_name(name: str) -> str:
    return _clean_name(name).split("(", 1)[0]


def _clean_inherited_type_name(name: str) -> str:
    cleaned = _clean_name(name)
    if not cleaned:
        return ""
    cleaned = cleaned.split("<", 1)[0].strip()
    cleaned = cleaned.split(".", 1)[-1].strip()
    cleaned = cleaned.split(":", 1)[0].strip()
    cleaned = cleaned.split("&", 1)[0].strip()
    return cleaned


def _extract_symbol_records_from_structure_data(
    structure: dict[str, Any], filepath: str, raw: bytes
) -> list[SwiftSymbolRecord]:
    records: list[SwiftSymbolRecord] = []

    def _walk(items: list[dict[str, Any]]) -> None:
        for item in items or []:
            kind = _clean_name(item.get("key.kind", ""))
            name = _clean_name(item.get("key.name", ""))
            offset = int(item.get("key.offset", 0) or 0)
            length = int(item.get("key.length", 0) or 0)
            start_line = _line_number(raw, offset)
            end_line = _line_number(raw, offset + max(0, length))
            inherited_types = []
            for entry in item.get("key.inheritedtypes") or []:
                inherited = _clean_inherited_type_name((entry or {}).get("key.name", ""))
                if inherited:
                    inherited_types.append(inherited)
            if kind.startswith("source.lang.swift.decl") and name:
                records.append(
                    SwiftSymbolRecord(
                        filepath=filepath,
                        name=name,
                        base_name=_base_name(name),
                        kind=kind,
                        start_line=start_line,
                        end_line=end_line,
                        usr=_clean_name(item.get("key.usr", "")) or None,
                        doc_comment=_clean_name(item.get("key.doc.comment", "")) or None,
                        inherited_types=sorted(set(inherited_types)),
                    )
                )
            _walk(item.get("key.substructure") or [])

    _walk(structure.get("key.substructure") or [])
    return records


def _extract_swift_symbol_records(file_path: str) -> list[SwiftSymbolRecord]:
    sourcekitten = _which("sourcekitten")
    if not sourcekitten:
        return []
    try:
        with open(file_path, "rb") as fh:
            raw = fh.read()
        proc = subprocess.run(
            [sourcekitten, "structure", "--file", file_path],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        data = json.loads(proc.stdout)
        return _extract_symbol_records_from_structure_data(data, file_path, raw)
    except Exception:
        return []


def _clean_path_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [part for part in shlex.split(value) if part]
    if isinstance(value, list):
        return [str(part) for part in value if str(part).strip()]
    return []


def _clean_define_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [part for part in value.split() if part]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _xcode_build_settings(project_file: str, scheme_name: str) -> list[dict[str, Any]]:
    xcodebuild = _which("xcodebuild")
    if not xcodebuild:
        return []
    project_bundle = project_file
    if project_bundle.endswith("/project.pbxproj"):
        project_bundle = str(Path(project_bundle).parent)
    try:
        proc = subprocess.run(
            [
                xcodebuild,
                "-project",
                project_bundle,
                "-scheme",
                scheme_name,
                "-destination",
                "platform=macOS",
                "-showBuildSettings",
                "-json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        return json.loads(proc.stdout)
    except Exception:
        return []


def _compiler_args_from_build_settings(build_settings: dict[str, Any]) -> list[str]:
    args: list[str] = []
    sdkroot = build_settings.get("SDKROOT")
    if sdkroot:
        args.extend(["-sdk", str(sdkroot)])
    module_name = build_settings.get("PRODUCT_MODULE_NAME") or build_settings.get("TARGET_NAME")
    if module_name:
        args.extend(["-module-name", str(module_name)])
    for define in _clean_define_list(build_settings.get("SWIFT_ACTIVE_COMPILATION_CONDITIONS")):
        args.extend(["-D", define])
    for path in _clean_path_list(build_settings.get("FRAMEWORK_SEARCH_PATHS")):
        args.extend(["-F", path])
    for path in _clean_path_list(build_settings.get("HEADER_SEARCH_PATHS")):
        args.extend(["-I", path])
    for path in _clean_path_list(build_settings.get("SWIFT_INCLUDE_PATHS")):
        args.extend(["-I", path])
    other_swift_flags = _clean_path_list(build_settings.get("OTHER_SWIFT_FLAGS"))
    args.extend(other_swift_flags)
    return args


def _candidate_xcode_projects(project_path: str, indexed_files: list[str]) -> list[str]:
    discovered = {
        str(path)
        for path in indexed_files
        if str(path).endswith(".xcodeproj/project.pbxproj") and os.path.isfile(str(path))
    }
    root = Path(project_path)
    if root.is_dir():
        for path in root.rglob("*.xcodeproj"):
            pbxproj = path / "project.pbxproj"
            if pbxproj.is_file():
                discovered.add(str(pbxproj))
    return sorted(discovered)


def _target_swift_files(project_root: str, target_name: str) -> list[str]:
    target_dir = os.path.join(project_root, target_name)
    if os.path.isdir(target_dir):
        return sorted(str(path) for path in Path(target_dir).rglob("*.swift"))
    return []


def _semantic_index_records(file_path: str, compiler_args: list[str], target_files: list[str]) -> list[SwiftSymbolRecord]:
    sourcekitten = _which("sourcekitten")
    if not sourcekitten or not target_files:
        return []
    try:
        proc = subprocess.run(
            ["sourcekitten", "index", "--file", file_path, "--", *compiler_args, *target_files],
            capture_output=True,
            text=True,
            timeout=180,
            check=True,
        )
        data = json.loads(proc.stdout)
    except Exception:
        return []

    records: list[SwiftSymbolRecord] = []
    seen: set[tuple[str, str]] = set()
    stack: list[Any] = [data]
    while stack:
        item = stack.pop(0)
        if isinstance(item, dict):
            name = _clean_name(item.get("key.name", ""))
            usr = _clean_name(item.get("key.usr", ""))
            if name and usr:
                key = (name, usr)
                if key not in seen:
                    seen.add(key)
                    records.append(
                        SwiftSymbolRecord(
                            filepath=file_path,
                            name=name,
                            base_name=_base_name(name),
                            kind=_clean_name(item.get("key.kind", "")),
                            start_line=0,
                            end_line=0,
                            usr=usr,
                            doc_comment=None,
                            inherited_types=[],
                        )
                    )
            for value in item.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack[:0] = item
    return records


def _semantic_symbol_records_for_files(
    *, project_path: str, indexed_files: list[str]
) -> dict[str, list[SwiftSymbolRecord]]:
    project_root = os.path.abspath(project_path)
    results: dict[str, list[SwiftSymbolRecord]] = {}
    for project_file in _candidate_xcode_projects(project_path, indexed_files):
        scheme_name = Path(project_file).parent.stem
        for entry in _xcode_build_settings(project_file, scheme_name):
            build_settings = entry.get("buildSettings") or {}
            target_name = entry.get("target") or build_settings.get("TARGET_NAME")
            if not target_name:
                continue
            target_files = _target_swift_files(project_root, str(target_name))
            if not target_files:
                continue
            compiler_args = _compiler_args_from_build_settings(build_settings)
            if not compiler_args:
                continue
            for abs_path in target_files:
                rel_path = os.path.relpath(abs_path, project_root).replace(os.sep, "/")
                records = _semantic_index_records(abs_path, compiler_args, target_files)
                if records:
                    results[rel_path] = records
    return results


def _match_symbol_record(
    record: SwiftSymbolRecord, symbols: list[dict[str, Any]]
) -> dict[str, Any] | None:
    candidates = []
    for sym in symbols:
        sym_name = _clean_name(sym.get("name", ""))
        sym_base = _base_name(sym_name)
        if record.base_name != sym_base and record.name != sym_name:
            continue
        start_line = int(sym.get("start_line", 0) or 0)
        end_line = int(sym.get("end_line", 0) or 0)
        overlap = min(record.end_line, end_line) - max(record.start_line, start_line)
        distance = abs(record.start_line - start_line) + abs(record.end_line - end_line)
        candidates.append(
            (
                0 if record.name == sym_name else 1,
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
    from neo4j import GraphDatabase

    if not _enabled():
        return {"enabled": False, "files": 0, "symbols": 0}

    sourcekitten = _which("sourcekitten")
    if not sourcekitten:
        return {"enabled": True, "available": False, "files": 0, "symbols": 0}

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

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        with driver.session(database=neo4j_db) as session:
            graph_symbols = _load_swift_symbols(session, project_id, filepaths)
            semantic_records = _semantic_symbol_records_for_files(
                project_path=project_path,
                indexed_files=indexed_files,
            )
            updates: list[dict[str, Any]] = []
            files_with_matches = 0
            for abs_path, rel_path in zip(swift_abs_paths, filepaths):
                records = _extract_swift_symbol_records(abs_path)
                if not records:
                    continue
                matched_here = 0
                symbols = graph_symbols.get(rel_path, [])
                semantic_by_name = semantic_records.get(rel_path, [])
                semantic_usr_by_base_name: dict[str, str] = {}
                for semantic in semantic_by_name:
                    if semantic.base_name and semantic.usr and semantic.base_name not in semantic_usr_by_base_name:
                        semantic_usr_by_base_name[semantic.base_name] = semantic.usr
                for record in records:
                    match = _match_symbol_record(record, symbols)
                    if not match:
                        continue
                    matched_here += 1
                    updates.append(
                        {
                            "sid": match["sid"],
                            "kind": record.kind,
                            "usr": semantic_usr_by_base_name.get(record.base_name) or record.usr,
                            "doc_comment": record.doc_comment,
                            "inherited_types": record.inherited_types,
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
