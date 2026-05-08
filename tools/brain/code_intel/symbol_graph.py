"""Helpers for symbol and call-graph queries."""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

try:
    from tools.brain.graph_contract import node_label, rel_type
except ModuleNotFoundError:
    _GRAPH_CONTRACT_PATH = Path(__file__).resolve().parents[1] / "graph_contract.py"
    _GRAPH_CONTRACT_SPEC = importlib.util.spec_from_file_location(
        "_brain_graph_contract", _GRAPH_CONTRACT_PATH
    )
    _GRAPH_CONTRACT = importlib.util.module_from_spec(_GRAPH_CONTRACT_SPEC)
    assert _GRAPH_CONTRACT_SPEC and _GRAPH_CONTRACT_SPEC.loader
    _GRAPH_CONTRACT_SPEC.loader.exec_module(_GRAPH_CONTRACT)
    node_label = _GRAPH_CONTRACT.node_label
    rel_type = _GRAPH_CONTRACT.rel_type


FILE_LABEL = node_label("file")
IMPORT_LABEL = node_label("import")
EXTERNAL_SYMBOL_LABEL = node_label("external_symbol")
REL_CONTAINS = rel_type("contains")
REL_CALLS = rel_type("calls")
REL_CALLS_INFERRED = rel_type("calls_inferred")
REL_CALLS_EXTERNAL_SYMBOL = rel_type("calls_external_symbol")
REL_IMPLEMENTS_TYPE = rel_type("implements_type")
REL_IMPORTS = rel_type("imports")


SYMBOL_CONTEXT_CYPHER = f"""
    MATCH (s)
    WHERE (s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum OR s:EnumCase OR s:Method
       OR s:Protocol OR s:Interface OR s:Extension OR s:TypeAlias OR s:AssociatedType)
      AND s.project_id = $pid
      AND (s.name = $name OR s.qualified_name = $name)
      AND (
        $file_path IS NULL
        OR s.filepath = $file_path
        OR s.filepath ENDS WITH ('/' + $file_path)
      )
      AND ($signature IS NULL OR (s.signature IS NOT NULL AND s.signature CONTAINS $signature))
    OPTIONAL MATCH (s)<-[:{REL_CONTAINS}]-(parent:{FILE_LABEL})
    OPTIONAL MATCH (caller)-[:{REL_CALLS}|{REL_CALLS_INFERRED}]->(s)
    OPTIONAL MATCH (s)-[:{REL_CALLS}|{REL_CALLS_INFERRED}]->(callee)
    OPTIONAL MATCH (s)-[:{REL_CALLS_EXTERNAL_SYMBOL}]->(external_callee:{EXTERNAL_SYMBOL_LABEL})
    RETURN
      head([label IN labels(s) WHERE label <> 'Node']) AS kind,
      s.name AS name,
      s.qualified_name AS qualified_name,
      s.filepath    AS filepath,
      s.start_line  AS start_line,
      s.end_line    AS end_line,
      s.signature   AS signature,
      parent.filepath AS parent_file,
      parent.semantic_file_roles AS file_roles,
      collect(DISTINCT {{name: caller.name, file: caller.filepath,
                        line: caller.start_line}})[..10] AS callers,
      collect(DISTINCT {{name: callee.name, file: callee.filepath}})[..10] AS callees,
      collect(DISTINCT {{name: external_callee.name,
                        qualified_name: external_callee.qualified_name,
                        language: external_callee.language}})[..10] AS external_callees,
      count(DISTINCT caller) AS callers_in,
      count(DISTINCT callee) AS callees_out
    LIMIT 12
"""


CALL_CHAIN_RESOLVE_CYPHER = f"""
    MATCH (s)
    WHERE s.project_id = $pid
      AND (s:Function OR s:Method OR s:Class OR s:Struct OR s:Trait OR s:Enum
           OR s:Protocol OR s:Interface OR s:Extension OR s:TypeAlias OR s:AssociatedType)
      AND (
        $file_path IS NULL
        OR s.filepath = $file_path
        OR s.filepath ENDS WITH ('/' + $file_path)
      )
      AND ($signature IS NULL OR (s.signature IS NOT NULL AND s.signature CONTAINS $signature))
    OPTIONAL MATCH (s)<-[:{REL_CONTAINS}]-(parent:{FILE_LABEL})
    OPTIONAL MATCH (s)<-[:{REL_CALLS}|{REL_CALLS_INFERRED}]-(caller)
    WITH s, parent,
         CASE
           WHEN s.name = $name THEN 0
           WHEN s.qualified_name = $name THEN 0
           WHEN s.name ENDS WITH ('.' + $name) THEN 1
           WHEN s.qualified_name ENDS WITH ('.' + $name) THEN 1
           WHEN s.name STARTS WITH ($name + '(') THEN 2
           WHEN s.qualified_name STARTS WITH ($name + '(') THEN 2
           WHEN s.name CONTAINS ('.' + $name + '(') THEN 3
           WHEN s.qualified_name CONTAINS ('.' + $name + '(') THEN 3
           WHEN s.signature IS NOT NULL AND s.signature CONTAINS $name THEN 4
           WHEN s.qualified_name IS NOT NULL AND s.qualified_name CONTAINS $name THEN 5
           ELSE 99
         END AS rank,
         count(DISTINCT caller) AS callers_in
    WHERE rank < 99
    RETURN elementId(s) AS eid, s.name AS name, s.qualified_name AS qualified_name,
           s.signature AS signature, s.filepath AS filepath,
           head([label IN labels(s) WHERE label <> 'Node']) AS kind, rank,
           parent.semantic_file_roles AS file_roles,
           CASE
             WHEN s.filepath IS NULL THEN 2
             WHEN parent.semantic_file_roles IS NOT NULL
               AND any(role IN parent.semantic_file_roles WHERE role IN ['generated_surface', 'binding_surface'])
               THEN 3
             WHEN parent.semantic_file_roles IS NOT NULL
               AND any(role IN parent.semantic_file_roles WHERE role IN ['test_surface', 'example_surface', 'benchmark_surface'])
               THEN 4
             WHEN parent.semantic_file_roles IS NOT NULL
               AND 'support_surface' IN parent.semantic_file_roles
               THEN 3
             WHEN parent.semantic_file_roles IS NOT NULL
               THEN 1
             WHEN s.filepath CONTAINS '/tests/' OR s.filepath CONTAINS '/test/' OR s.filepath CONTAINS '/e2e/'
               OR s.filepath CONTAINS '/fixtures/' OR s.filepath CONTAINS '.spec.' OR s.filepath CONTAINS '.stories.'
               THEN 4
             WHEN s.filepath CONTAINS '/gen/' OR s.filepath CONTAINS '/generated/' OR s.filepath CONTAINS 'PreGeneratedSPM'
               OR s.filepath CONTAINS '.gen.' OR s.filepath CONTAINS '_generated.'
               THEN 3
             WHEN s.filepath CONTAINS '/api/' OR s.filepath CONTAINS '/routes/' OR s.filepath CONTAINS '/services/' OR s.filepath CONTAINS '/db/'
               OR s.filepath STARTS WITH 'api/' OR s.filepath STARTS WITH 'routes/' OR s.filepath STARTS WITH 'services/' OR s.filepath STARTS WITH 'db/'
               THEN 0
             WHEN s.filepath CONTAINS '/public/' OR s.filepath STARTS WITH 'public/' OR s.filepath ENDS WITH '.html' OR s.filepath ENDS WITH '.css'
               THEN 3
             ELSE 1
           END AS path_rank
    ORDER BY rank ASC, path_rank ASC, callers_in DESC, size(coalesce(s.qualified_name, s.name)) ASC
    LIMIT 5
"""


VISUALIZE_SUBGRAPH_FOCUS_CYPHER = f"""
    MATCH (n {{name: $name, project_id: $pid}})
    WHERE n:Function OR n:Class OR n:Struct OR n:Enum OR n:Trait
       OR n:Protocol OR n:Interface OR n:Extension OR n:TypeAlias OR n:AssociatedType
       OR n:{FILE_LABEL}
    OPTIONAL MATCH (n)<-[:{REL_CONTAINS}]-(parent:{FILE_LABEL})
    RETURN n.id AS id, head([label IN labels(n) WHERE label <> 'Node']) AS kind, n.name AS name,
           n.filepath AS fp, n.start_line AS sl,
           coalesce(n.semantic_file_roles, parent.semantic_file_roles) AS file_roles
    LIMIT 12
"""


VISUALIZE_SUBGRAPH_NEIGHBORS_CYPHER = f"""
    MATCH (n {{id: $fid}})
     OPTIONAL MATCH (parent:{FILE_LABEL})-[:{REL_CONTAINS}]->(n)
     OPTIONAL MATCH (n)<-[:{REL_CALLS}|{REL_CALLS_INFERRED}]-(caller)
        WHERE caller:{FILE_LABEL} OR caller:Function OR caller:Class OR caller:Method
           OR caller:Struct OR caller:Trait OR caller:Enum OR caller:Protocol
           OR caller:Interface OR caller:Extension OR caller:TypeAlias OR caller:AssociatedType
     OPTIONAL MATCH (caller)<-[:{REL_CONTAINS}]-(caller_parent:{FILE_LABEL})
     OPTIONAL MATCH (n)<-[:{REL_IMPORTS}]-(importer:{FILE_LABEL})
    OPTIONAL MATCH (n)-[:{REL_CALLS}|{REL_CALLS_INFERRED}]->(callee)
        WHERE callee:Function OR callee:Class OR callee:Struct OR callee:Method
           OR callee:Trait OR callee:Enum OR callee:Protocol OR callee:Interface
           OR callee:Extension OR callee:TypeAlias OR callee:AssociatedType
    OPTIONAL MATCH (callee)<-[:{REL_CONTAINS}]-(callee_parent:{FILE_LABEL})
    RETURN
      parent.id AS parent_id, parent.name AS parent_name, parent.filepath AS parent_fp,
      parent.semantic_file_roles AS parent_file_roles,
      collect(DISTINCT {{id: caller.id, name: caller.name, fp: caller.filepath,
                        file_roles: coalesce(caller.semantic_file_roles, caller_parent.semantic_file_roles)}})[..6]  AS callers,
      collect(DISTINCT {{id: importer.id, name: importer.name, fp: importer.filepath,
                        file_roles: importer.semantic_file_roles}})[..6] AS importers,
      collect(DISTINCT {{id: callee.id, name: callee.name, kind: head([label IN labels(callee) WHERE label <> 'Node']),
                        fp: callee.filepath,
                        file_roles: coalesce(callee.semantic_file_roles, callee_parent.semantic_file_roles)}})[..8] AS callees
    LIMIT 1
"""


def normalize_query_file_path(workspace_id: str, file_path: str | None) -> str | None:
    if not file_path:
        return None
    raw_path = file_path.strip()
    if not raw_path:
        return None
    if os.path.isabs(raw_path):
        if os.path.exists(workspace_id):
            project_root = os.path.realpath(workspace_id)
            abs_path = os.path.realpath(raw_path)
            try:
                normalized = os.path.relpath(abs_path, project_root)
            except ValueError:
                normalized = raw_path
        else:
            normalized = raw_path
    else:
        normalized = raw_path
    normalized = normalized.replace(os.sep, "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or None


def _file_roles_present(raw_roles) -> bool:
    return isinstance(raw_roles, list)


def _normalize_file_roles(raw_roles) -> set[str]:
    if not _file_roles_present(raw_roles):
        return set()
    return {
        str(role).strip().lower()
        for role in raw_roles
        if str(role).strip()
    }


def _symbol_path_penalty(filepath: str | None, raw_roles=None) -> int:
    normalized = (filepath or "").replace("\\", "/").lower()
    roles = _normalize_file_roles(raw_roles)
    roles_known = _file_roles_present(raw_roles)
    if not normalized:
        return 6
    if "generated_surface" in roles or "binding_surface" in roles:
        return 5
    if {"test_surface", "example_surface", "benchmark_surface"} & roles:
        return 4
    if "support_surface" in roles:
        return 3
    if any(
        token in normalized
        for token in (
            "/pregeneratedspm/",
            "/vendors/",
            "vendors/",
            "/generated/",
            "/gen/",
            ".gen.ts",
            ".generated.ts",
            ".generated.js",
            "_generated.swift",
            ".grpc.swift",
            ".pb.swift",
        )
    ):
        return 5
    if not roles_known and any(
        token in normalized
        for token in (
            "/e2e/",
            "/tests/",
            "/test/",
            ".spec.",
            ".stories.",
            "/storybook/",
            "/fixtures/",
            "/examples/",
        )
    ):
        return 4
    if any(
        token in normalized
        for token in (
            "/public/assets/",
            "src/public/assets/",
            "/components/icons/",
        )
    ):
        return 3
    if "packages/" in normalized and "/src/" in normalized:
        return 0
    if any(
        token in normalized
        for token in (
            "/src/server/",
            "/server/",
            "/src/runtime/",
            "/runtime/",
            "/src/config/",
            "/config/",
            "/src/app/",
            "/app/",
            "/src/api/",
            "/api/",
            "/src/services/",
            "/services/",
            "/src/db/",
            "/db/",
            "/src/lib/",
            "/lib/",
        )
    ):
        return 1
    if "/src/" in normalized or normalized.startswith("src/"):
        return 2
    return 3


def _symbol_kind_rank(kind: str | None) -> int:
    return {
        "Function": 0,
        "Method": 0,
        "Class": 1,
        "Struct": 1,
        "Enum": 2,
        "Protocol": 3,
        "Interface": 3,
        "Trait": 3,
        "TypeAlias": 4,
        "AssociatedType": 4,
        "Extension": 5,
        "EnumCase": 6,
    }.get(kind or "", 7)


def _symbol_role_rank(candidate: dict) -> int:
    filepath = (candidate.get("filepath") or "").replace("\\", "/").lower()
    signature = (candidate.get("signature") or "").strip().lower()
    if filepath.endswith("/lib.rs") or filepath == "src/lib.rs":
        return 0
    if filepath.endswith("/__init__.py") or filepath == "__init__.py":
        return 1
    if signature.startswith("pub ") or signature.startswith("public "):
        return 1
    if filepath.endswith("/mod.rs") or filepath == "src/mod.rs":
        return 2
    if "/cli/" in filepath or "/bin/" in filepath:
        return 5
    if filepath.endswith("/main.rs"):
        return 3
    return 3


def _symbol_context_score(candidate: dict, *, symbol_name: str) -> int:
    filepath = (candidate.get("filepath") or "").replace("\\", "/")
    qualified_name = candidate.get("qualified_name") or ""
    signature = candidate.get("signature") or ""
    score = 0
    if qualified_name == symbol_name:
        score += 80
    elif qualified_name.endswith(f"::{symbol_name}") or qualified_name.endswith(f".{symbol_name}"):
        score += 55
    elif qualified_name and symbol_name in qualified_name:
        score += 20
    if signature and symbol_name in signature:
        score += 35
        if signature.lstrip().startswith(("pub ", "public ")):
            score += 20
    path_penalty = _symbol_path_penalty(filepath, candidate.get("file_roles"))
    score -= path_penalty * 18
    role_rank = _symbol_role_rank(candidate)
    score -= role_rank * 10
    if filepath.endswith("/lib.rs") or filepath == "src/lib.rs":
        score += 18
    if filepath.endswith("/main.rs"):
        score += 8
    score += min(int(candidate.get("callers_in") or 0), 8) * 2
    score += min(int(candidate.get("callees_out") or 0), 8)
    score -= _symbol_kind_rank(candidate.get("kind")) * 3
    return score


def _symbol_context_reason_parts(candidate: dict, *, symbol_name: str) -> list[str]:
    filepath = (candidate.get("filepath") or "").replace("\\", "/")
    qualified_name = candidate.get("qualified_name") or ""
    signature = candidate.get("signature") or ""
    parts: list[str] = []
    if qualified_name == symbol_name:
        parts.append("exact-qualified")
    elif qualified_name.endswith(f"::{symbol_name}") or qualified_name.endswith(f".{symbol_name}"):
        parts.append("qualified-suffix")
    if signature and symbol_name in signature:
        parts.append("signature-match")
    if signature.lstrip().startswith(("pub ", "public ")):
        parts.append("public")
    if filepath.endswith("/lib.rs") or filepath == "src/lib.rs":
        parts.append("library-entrypoint")
    if filepath.endswith("/mod.rs") or filepath == "src/mod.rs":
        parts.append("module-root")
    if filepath.endswith("/main.rs"):
        parts.append("runtime-entrypoint")
    elif "/cli/" in filepath or "/bin/" in filepath:
        parts.append("usage-heavy")
    penalty = _symbol_path_penalty(filepath, candidate.get("file_roles"))
    if penalty >= 5:
        parts.append("generated")
    elif penalty >= 4:
        parts.append("test-or-example")
    elif penalty <= 2:
        parts.append("runtime")
    return parts


def rank_symbol_context_candidates(
    candidates: list[dict],
    *,
    symbol_name: str,
    normalized_file_path: str | None,
    normalized_signature: str | None,
) -> list[dict]:
    ranked: list[dict] = []
    for candidate in candidates:
        candidate = dict(candidate)
        filepath = candidate.get("filepath")
        signature = candidate.get("signature") or ""
        candidate["file_match"] = filepath == normalized_file_path if normalized_file_path else False
        candidate["signature_match"] = bool(
            normalized_signature and signature and normalized_signature in signature
        )
        candidate["symbol_context_score"] = _symbol_context_score(candidate, symbol_name=symbol_name)
        candidate["symbol_context_reasons"] = _symbol_context_reason_parts(
            candidate, symbol_name=symbol_name
        )
        ranked.append(candidate)

    ranked.sort(
        key=lambda candidate: (
            0 if candidate.get("file_match") else 1,
            0 if candidate.get("signature_match") else 1,
            -int(candidate.get("symbol_context_score") or 0),
            _symbol_path_penalty(candidate.get("filepath"), candidate.get("file_roles")),
            _symbol_role_rank(candidate),
            _symbol_kind_rank(candidate.get("kind")),
            -(candidate.get("callers_in") or 0),
            -(candidate.get("callees_out") or 0),
            len(candidate.get("filepath") or ""),
            candidate.get("start_line") or 0,
        )
    )
    deduped: list[dict] = []
    seen_keys: set[tuple[str, int | None, int | None, str, str]] = set()
    for candidate in ranked:
        key = (
            str(candidate.get("filepath") or ""),
            candidate.get("start_line"),
            candidate.get("end_line"),
            str(candidate.get("signature") or ""),
            str(candidate.get("qualified_name") or candidate.get("name") or ""),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(candidate)
    return deduped


def pick_symbol_context_candidate(
    candidates: list[dict],
    *,
    symbol_name: str,
    normalized_file_path: str | None = None,
    normalized_signature: str | None = None,
) -> dict | None:
    if not candidates:
        return None

    ranked = rank_symbol_context_candidates(
        candidates,
        symbol_name=symbol_name,
        normalized_file_path=normalized_file_path,
        normalized_signature=normalized_signature,
    )
    return ranked[0]


def should_disambiguate_symbol_context(
    candidates: list[dict],
    *,
    symbol_name: str,
    normalized_file_path: str | None = None,
    normalized_signature: str | None = None,
) -> bool:
    normalized_name = (symbol_name or "").strip()
    if not normalized_name:
        return False
    if normalized_file_path or normalized_signature:
        return False
    if "." in normalized_name or "(" in normalized_name or len(normalized_name) > 18:
        return False
    ranked = rank_symbol_context_candidates(
        candidates,
        symbol_name=symbol_name,
        normalized_file_path=normalized_file_path,
        normalized_signature=normalized_signature,
    )
    if len(ranked) < 2:
        return False
    distinct_paths = {c.get("filepath") for c in candidates if c.get("filepath")}
    distinct_kinds = {c.get("kind") for c in candidates if c.get("kind")}
    top_score = int(ranked[0].get("symbol_context_score") or 0)
    second_score = int(ranked[1].get("symbol_context_score") or 0)
    top_penalty = _symbol_path_penalty(ranked[0].get("filepath"))
    second_penalty = _symbol_path_penalty(ranked[1].get("filepath"))
    top_signature = str(ranked[0].get("signature") or "").lstrip().lower()
    second_signature = str(ranked[1].get("signature") or "").lstrip().lower()
    if (
        top_penalty <= 2
        and second_penalty >= 4
        and (top_score - second_score) >= 4
        and not top_signature.startswith(("pub ", "public "))
        and (
            second_signature.startswith(("pub ", "public "))
            or second_penalty >= 5
        )
    ):
        return False
    if len(distinct_paths) >= 5 and (top_score - second_score) <= 24:
        return True
    if len(distinct_paths) >= 3 and len(distinct_kinds) >= 2 and (top_score - second_score) <= 16:
        return True
    return False


def format_symbol_context_ambiguity(
    candidates: list[dict],
    *,
    symbol_name: str,
    normalized_file_path: str | None = None,
    normalized_signature: str | None = None,
) -> str:
    ranked = rank_symbol_context_candidates(
        candidates,
        symbol_name=symbol_name,
        normalized_file_path=normalized_file_path,
        normalized_signature=normalized_signature,
    )
    lines = [
        f"Multiple exact matches found for `{symbol_name}`. Be more specific or use `list_symbol_matches`.",
        "",
        "Top matches:",
    ]
    for candidate in ranked[:6]:
        reasons = ", ".join(candidate.get("symbol_context_reasons") or [])
        score = candidate.get("symbol_context_score")
        lines.append(
            f"- [{candidate.get('kind') or 'Symbol'}] "
            f"{candidate.get('filepath') or 'unknown'}:{candidate.get('start_line') or 1}"
            f"{f' ({reasons}; score={score})' if reasons else ''}"
        )
    return "\n".join(lines)


def pick_call_chain_candidate(
    candidates: list[dict],
    *,
    normalized_file_path: str | None,
    normalized_signature: str | None,
) -> dict | None:
    if not candidates:
        return None
    ranked_candidates = sorted(
        candidates,
        key=lambda candidate: (
            candidate.get("rank", 99),
            min(candidate.get("path_rank", 99), _symbol_path_penalty(candidate.get("filepath"))),
            _symbol_kind_rank(candidate.get("kind")),
            -int(candidate.get("callers_in") or 0),
            len(candidate.get("qualified_name") or candidate.get("name") or ""),
        ),
    )
    if normalized_file_path:
        file_matches = [
            c
            for c in ranked_candidates
            if c.get("filepath") == normalized_file_path
            or str(c.get("filepath") or "").endswith(f"/{normalized_file_path}")
        ]
        if file_matches:
            return file_matches[0]
    if normalized_signature:
        signature_matches = [
            c
            for c in ranked_candidates
            if c.get("signature") and normalized_signature in c.get("signature")
        ]
        if signature_matches:
            return signature_matches[0]
    return ranked_candidates[0]


def build_swift_protocol_upward_fallback_cypher(depth: int) -> str:
    nested_depth = max(int(depth) - 1, 0)
    nested_pattern = (
        ""
        if nested_depth == 0
        else f"OPTIONAL MATCH path = (caller)-[:{REL_CALLS}|{REL_CALLS_INFERRED}*1..{nested_depth}]->(impl)\n"
    )
    nested_with = (
        "WITH start, impl, null AS path\n"
        if nested_depth == 0
        else (
            "WHERE caller IS NULL OR (\n"
            "  caller:Function OR caller:Method OR caller:Class OR caller:Struct OR caller:Trait OR caller:Enum\n"
            ")\n"
            "WITH start, impl, path\n"
        )
    )
    return (
        "MATCH (start) WHERE elementId(start) = $eid\n"
        f"MATCH (impl)-[:{REL_IMPLEMENTS_TYPE}]->(start)\n"
        "WHERE (impl:Struct OR impl:Class OR impl:Enum OR impl:TypeAlias)\n"
        + nested_pattern
        + nested_with
        + "RETURN CASE\n"
          "         WHEN path IS NULL THEN [start.name, impl.name]\n"
          "         ELSE [start.name] + [n IN reverse(nodes(path)) | n.name]\n"
          "       END AS chain,\n"
          "       CASE\n"
          "         WHEN path IS NULL THEN [start.filepath, impl.filepath]\n"
          "         ELSE [start.filepath] + [n IN reverse(nodes(path)) | n.filepath]\n"
          "       END AS files,\n"
          "       CASE\n"
          "         WHEN path IS NULL THEN [start.start_line, impl.start_line]\n"
          "         ELSE [start.start_line] + [n IN reverse(nodes(path)) | n.start_line]\n"
          "       END AS lines\n"
          "       ,CASE\n"
          "         WHEN path IS NULL THEN [\n"
          f"           head([({FILE_LABEL.lower()}_parent)-[:{REL_CONTAINS}]->(start) | {FILE_LABEL.lower()}_parent.semantic_file_roles]),\n"
          f"           head([({FILE_LABEL.lower()}_parent)-[:{REL_CONTAINS}]->(impl) | {FILE_LABEL.lower()}_parent.semantic_file_roles])\n"
          "         ]\n"
          "         ELSE [head([(_start_parent)-[:CONTAINS]->(start) | _start_parent.semantic_file_roles])] +\n"
          "              [n IN reverse(nodes(path)) |\n"
          "                 head([(_path_parent)-[:CONTAINS]->(n) | _path_parent.semantic_file_roles])]\n"
          "       END AS file_roles\n"
          "ORDER BY size(chain) ASC, files[1] ASC\n"
          "LIMIT 40"
    )


def pick_visualize_candidate(candidates: list[dict], *, symbol_name: str) -> dict | None:
    if not candidates:
        return None

    def _kind_rank(kind: str | None) -> int:
        return {
            "Struct": 0,
            "Class": 0,
            "Enum": 0,
            "Protocol": 1,
            "Interface": 1,
            "Trait": 1,
            "Function": 2,
            "Method": 2,
            "TypeAlias": 3,
            "Extension": 4,
            "AssociatedType": 4,
            "EnumCase": 5,
            "File": 6,
        }.get(kind or "", 7)

    ranked = sorted(
        candidates,
        key=lambda candidate: (
            0 if candidate.get("name") == symbol_name else 1,
            _symbol_path_penalty(candidate.get("fp"), candidate.get("file_roles")),
            _kind_rank(candidate.get("kind")),
            candidate.get("sl") or 0,
            len(candidate.get("fp") or ""),
        ),
    )
    return ranked[0]


def filter_visualize_neighbors(focus: dict, neighbors: dict) -> dict:
    focus_fp = (focus.get("fp") or "").replace("\\", "/")
    focus_prefix = ""
    if focus_fp:
        parts = focus_fp.split("/")
        if len(parts) >= 3 and parts[0] == "packages" and parts[2] == "src":
            focus_prefix = "/".join(parts[:3]) + "/"
        elif focus_fp.startswith("src/"):
            focus_prefix = "src/"

    def _low_value_name(name: str | None) -> bool:
        normalized = (name or "").strip().lower()
        return normalized in {"", "unnamed", "<anonymous>", "anonymous", "iife", "fn"}

    def _path_penalty(filepath: str | None) -> int:
        return _symbol_path_penalty(filepath, None)

    def _entry_path_penalty(entry: dict) -> int:
        penalty = _symbol_path_penalty(entry.get("fp"), entry.get("file_roles"))
        normalized = (entry.get("fp") or "").replace("\\", "/").lower()
        if penalty >= 4:
            return penalty
        if normalized.startswith(("script/", "scripts/", "nix/")) or "/script/" in normalized or "/scripts/" in normalized:
            return 4
        if any(token in normalized for token in ("/packages/ui/", "packages/ui/", "/packages/app/", "packages/app/", "/public/", "public/")):
            return 3
        if focus_prefix and normalized.startswith(focus_prefix.lower()):
            return 0
        if "/src/" in normalized or normalized.startswith("src/"):
            return 1
        return max(penalty, 2)

    def _sort_key(entry: dict) -> tuple:
        file_path = (entry.get("fp") or "").replace("\\", "/")
        return (
            0 if (focus_prefix and file_path.startswith(focus_prefix)) else 1,
            _entry_path_penalty(entry),
            file_path,
            entry.get("name") or "",
        )

    def _filter_group(items: list[dict], *, require_named: bool, limit: int) -> list[dict]:
        filtered_items = [
            entry
            for entry in sorted(items or [], key=_sort_key)
            if entry.get("id")
            and (not require_named or not _low_value_name(entry.get("name")))
            and _entry_path_penalty(entry) < 4
        ]
        deduped_items: list[dict] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for entry in filtered_items:
            fp = str(entry.get("fp") or "")
            key = (fp, "", "") if fp else (
                "",
                str(entry.get("name") or ""),
                str(entry.get("kind") or ""),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped_items.append(entry)
        filtered_items = deduped_items
        if focus_prefix:
            focused = [
                entry
                for entry in filtered_items
                if (entry.get("fp") or "").replace("\\", "/").startswith(focus_prefix)
            ]
            if focused:
                filtered_items = focused
        return filtered_items[:limit]

    filtered: dict = dict(neighbors or {})
    filtered["callees"] = _filter_group(
        neighbors.get("callees") or [],
        require_named=True,
        limit=6,
    )
    filtered["importers"] = _filter_group(
        neighbors.get("importers") or [],
        require_named=False,
        limit=4,
    )
    filtered["callers"] = _filter_group(
        neighbors.get("callers") or [],
        require_named=True,
        limit=6,
    )
    return filtered


def is_backend_filepath(filepath: str | None) -> bool:
    if not filepath:
        return False
    return (
        "/api/" in filepath
        or "/routes/" in filepath
        or "/services/" in filepath
        or "/db/" in filepath
        or filepath.startswith(("api/", "routes/", "services/", "db/"))
    )


def build_call_chain_path_cypher(direction: str, depth: int, *, is_backend_root: bool) -> tuple[str, str]:
    if direction == "up":
        hop_label = "caller"
        edge_pattern = f"<-[:CALLS|CALLS_INFERRED*1..{depth}]-(hop)"
    else:
        hop_label = "callee"
        edge_pattern = f"-[:CALLS|CALLS_INFERRED*1..{depth}]->(hop)"

    cypher = (
        "MATCH (start) WHERE elementId(start) = $eid "
        "MATCH path = (start)"
        f"{edge_pattern}"
        " WHERE (hop:Function OR hop:Method OR hop:Class OR hop:Struct OR hop:Trait OR hop:Enum)"
        + " RETURN [n IN nodes(path) | n.name] AS chain,"
        "        [n IN nodes(path) | n.filepath] AS files,"
        "        [n IN nodes(path) | n.start_line] AS lines,"
        "        [n IN nodes(path) |\n"
        "           head([(_path_parent)-[:CONTAINS]->(n) | _path_parent.semantic_file_roles])\n"
        "        ] AS file_roles"
        " LIMIT 40"
    )
    return hop_label, cypher


def format_symbol_context(rec: dict, symbol_name: str) -> list[str]:
    out = [
        f"## `{symbol_name}` ({rec['kind']})",
        f"**File:** `{rec['filepath']}`  Lines {rec['start_line']}–{rec['end_line']}",
    ]
    if rec["signature"]:
        out.append(f"**Signature:** `{rec['signature']}`\n")

    callers = [
        caller
        for caller in _dedupe_symbol_context_callers(rec["callers"] or [])
        if _is_language_compatible(rec.get("filepath"), caller.get("file"))
    ]
    callers = _suppress_symbol_context_self_aliases(
        callers,
        target_name=rec.get("name") or symbol_name,
        target_filepath=rec.get("filepath"),
        target_start_line=rec.get("start_line"),
    )
    callees = [
        callee
        for callee in (rec["callees"] or [])
        if callee.get("name")
        and _is_language_compatible(rec.get("filepath"), callee.get("file"))
    ]
    callees = _rank_symbol_context_callees(callees, target_filepath=rec.get("filepath"))
    external_callees = [c for c in (rec.get("external_callees") or []) if c.get("name")]

    if callers:
        out.append(f"**Called by** ({len(callers)}):")
        for caller in callers:
            line = f":{caller['line']}" if caller.get("line") else ""
            caller_name = str(caller.get("name") or "").strip()
            formatted_name = caller_name
            if caller_name and not _is_file_like_symbol_context_name(caller_name):
                formatted_name = f"`{caller_name}`"
            out.append(f"  - {formatted_name}{line}  in {caller.get('file', '?')}")
    if callees:
        out.append(f"\n**Calls** ({len(callees)}):")
        for callee in callees:
            out.append(f"  - `{callee['name']}`  in {callee.get('file', '?')}")
    if external_callees:
        out.append(f"\n**External Calls** ({len(external_callees)}):")
        for callee in external_callees:
            qualified_name = callee.get("qualified_name") or callee["name"]
            language = callee.get("language") or "external"
            out.append(f"  - `{qualified_name}` [{language}]")
    guidance = exact_call_graph_guidance(
        rec.get("filepath"),
        has_callers=bool(callers),
        has_callees=bool(callees),
    )
    if guidance:
        out.append(f"\n{guidance}")
    return out


def _dedupe_symbol_context_callers(callers: list[dict]) -> list[dict]:
    filtered = [dict(caller) for caller in callers if caller.get("name")]
    if not filtered:
        return []

    symbol_backed_files = {
        str(caller.get("file") or "")
        for caller in filtered
        if caller.get("file")
        and caller.get("name")
        and caller.get("name") != os.path.basename(str(caller.get("file") or ""))
    }
    if not symbol_backed_files:
        return filtered

    deduped: list[dict] = []
    seen: set[tuple[str, str, int | None]] = set()
    for caller in filtered:
        caller_file = str(caller.get("file") or "")
        caller_name = str(caller.get("name") or "")
        if caller_file in symbol_backed_files and caller_name == os.path.basename(caller_file):
            continue
        key = (caller_name, caller_file, caller.get("line"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(caller)
    return deduped


def _is_file_like_symbol_context_name(name: str | None) -> bool:
    value = str(name or "").strip()
    if not value:
        return False
    _, ext = os.path.splitext(value)
    return bool(ext and "/" not in value and "." in value)


def _suppress_symbol_context_self_aliases(
    callers: list[dict],
    *,
    target_name: str | None,
    target_filepath: str | None,
    target_start_line: int | None,
) -> list[dict]:
    normalized_name = str(target_name or "").strip()
    normalized_filepath = str(target_filepath or "")
    if not normalized_name or not normalized_filepath or not target_start_line:
        return callers
    filtered: list[dict] = []
    for caller in callers:
        caller_name = str(caller.get("name") or "").strip()
        caller_filepath = str(caller.get("file") or "")
        caller_line = caller.get("line")
        if (
            caller_name == normalized_name
            and caller_filepath == normalized_filepath
            and caller_line == target_start_line
        ):
            continue
        filtered.append(caller)
    return filtered


def _symbol_context_callee_rank(callee: dict, *, target_filepath: str | None) -> tuple:
    filepath = str(callee.get("file") or "").replace("\\", "/")
    normalized = filepath.lower()
    name = str(callee.get("name") or "")
    target_dir = ""
    if target_filepath:
        target_dir = os.path.dirname(str(target_filepath).replace("\\", "/"))

    bucket = _symbol_path_penalty(filepath, callee.get("file_roles"))
    if filepath and target_dir and filepath.startswith(target_dir + "/") and bucket < 4:
        bucket = 0

    if name.startswith(("with", "get", "set")) and bucket <= 2:
        helper_penalty = 1
    else:
        helper_penalty = 0
    if re.match(r"^[A-Z][A-Za-z0-9_]+$", name):
        behavior_penalty = 2
    elif name.startswith(("cached", "current", "default")):
        behavior_penalty = 1
    else:
        behavior_penalty = 0
    same_file = 0 if filepath and target_filepath and filepath == target_filepath else 1
    return (bucket, behavior_penalty, helper_penalty, same_file, filepath, name)


def _rank_symbol_context_callees(callees: list[dict], *, target_filepath: str | None) -> list[dict]:
    ranked = sorted(
        (dict(callee) for callee in callees),
        key=lambda callee: _symbol_context_callee_rank(callee, target_filepath=target_filepath),
    )
    if not target_filepath:
        return ranked
    same_file: list[dict] = []
    cross_file: list[dict] = []
    for callee in ranked:
        if str(callee.get("file") or "") == str(target_filepath):
            same_file.append(callee)
        else:
            cross_file.append(callee)

    if len(same_file) <= 2 or not cross_file:
        return ranked

    diversified: list[dict] = []
    diversified.extend(same_file[:2])
    diversified.extend(cross_file)
    diversified.extend(same_file[2:])
    return diversified


def _language_family(filepath: str | None) -> str | None:
    normalized = (filepath or "").replace("\\", "/").lower()
    if not normalized:
        return None
    _, ext = os.path.splitext(normalized)
    if ext in {".py", ".pyi"}:
        return "python"
    if ext in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        return "web"
    if ext in {".java", ".kt", ".kts", ".scala", ".groovy"}:
        return "jvm"
    if ext in {".swift", ".m", ".mm", ".h"}:
        return "apple"
    if ext in {".c", ".cc", ".cpp", ".cxx", ".hpp"}:
        return "native"
    if ext == ".rs":
        return "rust"
    if ext == ".go":
        return "go"
    if ext == ".rb":
        return "ruby"
    if ext == ".cs":
        return "csharp"
    return None


def _is_language_compatible(root_filepath: str | None, candidate_filepath: str | None) -> bool:
    root_family = _language_family(root_filepath)
    candidate_family = _language_family(candidate_filepath)
    if not root_family or not candidate_family:
        return True
    if root_family == candidate_family:
        return True
    if {root_family, candidate_family} <= {"apple", "native"}:
        return True
    return False


def exact_call_graph_guidance(
    filepath: str | None,
    *,
    has_callers: bool,
    has_callees: bool,
) -> str | None:
    normalized = (filepath or "").replace("\\", "/").lower()
    if not normalized.endswith(".py"):
        return None
    if has_callers or has_callees:
        return None
    return (
        "**Exactness note:** This Python symbol has no exact call-graph edges right now. "
        "Python call graph edges stay exact-only, so dynamic receiver calls may be intentionally absent here; use "
        "`find_references` or `search_codebase` for broader navigation."
    )


def format_call_chain_rows(
    rows: list[dict],
    *,
    resolved_name: str,
    symbol_name: str,
    direction: str,
    depth: int,
    resolved_filepath: str | None,
) -> str:
    LOW_VALUE_HELPER_NAMES = {"iife", "fn"}

    def is_low_value_name(name: str | None) -> bool:
        normalized = (name or "").strip().lower()
        return normalized in {"", "unnamed", "<anonymous>", "anonymous"} or normalized in LOW_VALUE_HELPER_NAMES

    from collections import OrderedDict

    def focus_prefix(filepath: str | None) -> str:
        normalized = (filepath or "").replace("\\", "/")
        if not normalized:
            return ""
        parts = normalized.split("/")
        if len(parts) >= 3 and parts[0] == "packages" and parts[2] == "src":
            return "/".join(parts[:3]) + "/"
        if normalized.startswith("src/"):
            return "src/"
        if len(parts) >= 2 and parts[0] in {"apps", "app", "crates", "packages"}:
            return "/".join(parts[:2]) + "/"
        return parts[0] + "/" if parts else ""

    def path_penalty(filepath: str | None, raw_roles) -> int:
        normalized = (filepath or "").replace("\\", "/").lower()
        penalty = _symbol_path_penalty(filepath, raw_roles)
        if not normalized or penalty >= 4:
            return penalty
        if normalized.startswith(("script/", "scripts/", "nix/")) or "/script/" in normalized or "/scripts/" in normalized:
            return 4
        if any(token in normalized for token in ("/packages/ui/", "packages/ui/", "/packages/app/", "packages/app/")):
            return 3
        if any(token in normalized for token in ("/public/", "public/", ".html", ".css")):
            return 3
        if any(token in normalized for token in ("/src/api/", "/src/services/", "/src/db/", "/src/server/", "/src/runtime/", "/src/project/")):
            return 0
        if "/src/" in normalized or normalized.startswith("src/"):
            return 1
        return 2

    def hop_penalty(filepath: str | None, raw_roles) -> int:
        penalty = _symbol_path_penalty(filepath, raw_roles)
        normalized = (filepath or "").replace("\\", "/").lower()
        if penalty >= 4:
            return penalty
        if root_is_backend and any(token in normalized for token in ("/public/", "public/", ".html", ".css")):
            return 3
        return penalty

    header_name = resolved_name or symbol_name
    out = [f"## Call chain: `{header_name}` ({direction}, depth={depth})\n"]
    if resolved_name and resolved_name != symbol_name:
        out.append(f"Resolved `{symbol_name}` → `{resolved_name}`\n")
    anonymous_hints: list[str] = []
    first_hop_groups: "OrderedDict[tuple[str, str], OrderedDict[tuple[str, str], None]]" = OrderedDict()
    first_hop_counts: dict[tuple[str, str], int] = {}
    first_hop_roles: dict[tuple[str, str], object] = {}
    terminal_paths = 0
    root_focus = focus_prefix(resolved_filepath)
    root_is_backend = is_backend_filepath(resolved_filepath)

    for rec in rows:
        chain = rec["chain"]
        files = rec["files"]
        lines = rec.get("lines") or []
        file_roles = rec.get("file_roles") or []
        if any(
            not _is_language_compatible(resolved_filepath, file_path)
            for file_path in files[1:]
            if file_path
        ):
            continue
        compact_chain: list[tuple[str | None, str | None, int | None, object]] = []
        for idx, name in enumerate(chain):
            file_path = files[idx] if idx < len(files) else None
            line = lines[idx] if idx < len(lines) else None
            roles = file_roles[idx] if idx < len(file_roles) else None
            if idx > 0 and is_low_value_name(name):
                hint = f"{file_path}:{line}" if file_path and line else (file_path or "?")
                if hint not in anonymous_hints:
                    anonymous_hints.append(hint)
                continue
            compact_chain.append((name, file_path, line, roles))
        chain = [entry[0] for entry in compact_chain]
        files = [entry[1] for entry in compact_chain]
        lines = [entry[2] for entry in compact_chain]
        file_roles = [entry[3] for entry in compact_chain]
        if any(
            hop_penalty(
                files[idx] if idx < len(files) else None,
                file_roles[idx] if idx < len(file_roles) else None,
            ) >= 4
            for idx in range(1, len(files))
        ):
            continue
        if root_is_backend and any(
            hop_penalty(
                files[idx] if idx < len(files) else None,
                file_roles[idx] if idx < len(file_roles) else None,
            ) >= 3
            for idx in range(1, len(files))
        ):
            continue
        if len(chain) < 2:
            continue
        first_name = chain[1]
        if is_low_value_name(first_name):
            filepath = files[1] or "?"
            line = lines[1] if len(lines) > 1 else None
            hint = f"{filepath}:{line}" if line else filepath
            if hint not in anonymous_hints:
                anonymous_hints.append(hint)
            continue
        first_file = files[1] or "?"
        first_key = (first_name, first_file)
        first_hop_groups.setdefault(first_key, OrderedDict())
        first_hop_counts[first_key] = first_hop_counts.get(first_key, 0) + 1
        first_hop_roles.setdefault(first_key, file_roles[1] if len(file_roles) > 1 else None)

        if len(chain) >= 3:
            child_name = chain[2]
            if is_low_value_name(child_name):
                filepath = files[2] or "?"
                line = lines[2] if len(lines) > 2 else None
                hint = f"{filepath}:{line}" if line else filepath
                if hint not in anonymous_hints:
                    anonymous_hints.append(hint)
                continue
            child_file = files[2] or "?"
            first_hop_groups[first_key][(child_name, child_file)] = None
        else:
            terminal_paths += 1

    emitted = 0
    max_first_hops = 12
    max_children_per_hop = 3
    ranked_first_hops = sorted(
        first_hop_groups.items(),
        key=lambda item: (
            0 if (root_focus and (item[0][1] or "").startswith(root_focus)) else 1,
            path_penalty(item[0][1], first_hop_roles.get(item[0])),
            -first_hop_counts.get(item[0], 0),
            item[0][1] or "",
            item[0][0] or "",
        ),
    )
    if root_focus and (resolved_name or symbol_name).strip().lower() in {"main"}:
        focused_first_hops = [
            item for item in ranked_first_hops if (item[0][1] or "").startswith(root_focus)
        ]
        if focused_first_hops:
            ranked_first_hops = focused_first_hops
    visible_first_hops = ranked_first_hops[:max_first_hops]
    hidden_first_hops = max(0, len(ranked_first_hops) - len(visible_first_hops))

    for (first_name, first_file), children in visible_first_hops:
        out.append(f"   `{first_name}`  ({first_file})")
        emitted += 1
        child_items = list(children.keys())
        for child_idx, (child_name, child_file) in enumerate(child_items[:max_children_per_hop]):
            out.append(f"    └─ `{child_name}`  ({child_file})")
            emitted += 1
        hidden_children = max(0, len(child_items) - max_children_per_hop)
        extra_paths = max(0, first_hop_counts.get((first_name, first_file), 0) - max(len(child_items), 1))
        if hidden_children or extra_paths:
            summary_bits = []
            if hidden_children:
                summary_bits.append(f"{hidden_children} more child call(s)")
            if extra_paths:
                summary_bits.append(f"{extra_paths} additional path(s)")
            out.append(f"    └─ … {', '.join(summary_bits)}")
            emitted += 1

    if hidden_first_hops:
        out.append(f"\n… {hidden_first_hops} more first-hop call(s) hidden")

    if emitted == 0:
        hop_label = "callers" if direction == "up" else "callees"
        hint_text = ""
        if anonymous_hints:
            shown = ", ".join(anonymous_hints[:3])
            extra = f" (+{len(anonymous_hints) - 3} more)" if len(anonymous_hints) > 3 else ""
            hint_text = f"\nAnonymous wrapper hops exist at: {shown}{extra}"
        return (
            f"`{header_name}` resolved but no named {hop_label} within {depth} hops.\n"
            "The graph may only contain anonymous wrapper nodes on this path."
            + hint_text
        )
    return "\n".join(out)


def render_subgraph_mermaid(focus: dict, neighbors: dict) -> str:
    node_counter = [0]
    node_map: dict[str, str] = {}

    def mermaid_id(neo_id: str) -> str:
        if neo_id not in node_map:
            node_counter[0] += 1
            node_map[neo_id] = f"n{node_counter[0]}"
        return node_map[neo_id]

    def safe_label(text: str) -> str:
        return text.replace('"', "'")

    def short_fp(filepath: str | None) -> str:
        if not filepath:
            return "?"
        parts = filepath.split("/")
        return "/".join(parts[-2:]) if len(parts) > 1 else filepath

    def node_shape(kind: str, mid: str, label: str) -> str:
        shapes = {
            "Function": f'{mid}("{safe_label(label)}")',
            "File": f'{mid}["{safe_label(label)}"]',
            "Class": f'{mid}(("{safe_label(label)}"))',
            "Struct": f'{mid}(("{safe_label(label)}"))',
            "Enum": f'{mid}{{"{safe_label(label)}"}}',
            "Trait": f'{mid}[/"{safe_label(label)}"/]',
        }
        return shapes.get(kind, f'{mid}["{safe_label(label)}"]')

    lines = ["graph LR"]
    lines += [
        "  classDef focus fill:#f4a261,stroke:#e76f51,color:#000",
        "  classDef file  fill:#264653,stroke:#2a9d8f,color:#fff",
        "  classDef func  fill:#2a9d8f,stroke:#264653,color:#fff",
        "  classDef cls   fill:#457b9d,stroke:#1d3557,color:#fff",
    ]
    focus_mid = mermaid_id(focus["id"])
    focus_label = f"{focus['name']}\n({short_fp(focus.get('fp'))}:{focus.get('sl') or '?'})"
    lines.append(f"  {node_shape(focus['kind'], focus_mid, focus_label)}")
    lines.append(f"  class {focus_mid} focus")

    if neighbors.get("parent_id"):
        parent_mid = mermaid_id(neighbors["parent_id"])
        parent_label = short_fp(neighbors.get("parent_fp")) or neighbors.get("parent_name", "?")
        lines.append(f"  {node_shape('File', parent_mid, parent_label)}")
        lines.append(f"  class {parent_mid} file")
        lines.append(f"  {parent_mid} -->|contains| {focus_mid}")

    for callee in neighbors.get("callees") or []:
        if not callee.get("id"):
            continue
        callee_mid = mermaid_id(callee["id"])
        callee_label = f"{callee['name']}\n({short_fp(callee.get('fp'))})"
        callee_kind = callee.get("kind", "Function")
        lines.append(f"  {node_shape(callee_kind, callee_mid, callee_label)}")
        lines.append(f"  class {callee_mid} {'func' if callee_kind == 'Function' else 'cls'}")
        lines.append(f"  {focus_mid} -->|calls| {callee_mid}")

    for importer in neighbors.get("importers") or []:
        if not importer.get("id"):
            continue
        importer_mid = mermaid_id(importer["id"])
        importer_label = short_fp(importer.get("fp")) or importer.get("name", "?")
        lines.append(f"  {node_shape('File', importer_mid, importer_label)}")
        lines.append(f"  class {importer_mid} file")
        lines.append(f"  {importer_mid} -->|imports| {focus_mid}")

    for caller in neighbors.get("callers") or []:
        if not caller.get("id") or caller.get("id") == neighbors.get("parent_id"):
            continue
        caller_mid = mermaid_id(caller["id"])
        caller_label = short_fp(caller.get("fp")) or caller.get("name", "?")
        lines.append(f"  {node_shape('File', caller_mid, caller_label)}")
        lines.append(f"  class {caller_mid} file")
        lines.append(f"  {caller_mid} -->|calls| {focus_mid}")

    if len(lines) <= 5:
        return ""
    return "```mermaid\n" + "\n".join(lines) + "\n```"


def format_subgraph_summary(focus: dict, neighbors: dict) -> str:
    focus_name = focus.get("name") or "?"
    focus_kind = focus.get("kind") or "Symbol"
    focus_fp = focus.get("fp") or "?"
    focus_line = focus.get("sl")
    line_suffix = f":{focus_line}" if focus_line else ""

    lines = [
        f"## Subgraph: `{focus_name}` ({focus_kind})",
        f"Focus file: `{focus_fp}{line_suffix}`",
        "",
        "## Inspect First",
        f"- inspect `{focus_fp}` first because it contains the focus symbol `{focus_name}`",
    ]

    callers = neighbors.get("callers") or []
    if callers:
        caller = callers[0]
        caller_fp = caller.get("fp") or caller.get("name") or "?"
        lines.append(f"- inspect `{caller_fp}` next because it calls `{focus_name}`")

    callees = neighbors.get("callees") or []
    if callees:
        callee = callees[0]
        callee_name = callee.get("name") or "?"
        callee_fp = callee.get("fp") or "?"
        lines.append(f"- inspect `{callee_name}` in `{callee_fp}` because it is the strongest outbound dependency")

    importers = neighbors.get("importers") or []
    if importers:
        importer = importers[0]
        importer_fp = importer.get("fp") or importer.get("name") or "?"
        lines.append(f"- inspect `{importer_fp}` because it imports the focus symbol into a wider module boundary")

    lines.extend(
        [
            "",
            "## Neighborhood",
            f"- callers: {len(callers)}",
            f"- callees: {len(callees)}",
            f"- importers: {len(importers)}",
        ]
    )
    return "\n".join(lines)
