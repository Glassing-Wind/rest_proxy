"""Helpers for symbol and call-graph queries."""

from __future__ import annotations

import os


SYMBOL_CONTEXT_CYPHER = """
    MATCH (s {name: $name, project_id: $pid})
    WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum OR s:EnumCase OR s:Method
    OPTIONAL MATCH (s)<-[:CONTAINS]-(parent:File)
    OPTIONAL MATCH (caller)-[:CALLS|CALLS_INFERRED]->(s)
    OPTIONAL MATCH (s)-[:CALLS|CALLS_INFERRED]->(callee)
    RETURN
      labels(s)[0]  AS kind,
      s.filepath    AS filepath,
      s.start_line  AS start_line,
      s.end_line    AS end_line,
      s.signature   AS signature,
      parent.filepath AS parent_file,
      collect(DISTINCT {name: caller.name, file: caller.filepath,
                        line: caller.start_line})[..10] AS callers,
      collect(DISTINCT {name: callee.name, file: callee.filepath})[..10] AS callees
    LIMIT 1
"""


CALL_CHAIN_RESOLVE_CYPHER = """
    MATCH (s)
    WHERE s.project_id = $pid
      AND (s:Function OR s:Method OR s:Class OR s:Struct OR s:Trait OR s:Enum)
      AND ($file_path IS NULL OR s.filepath = $file_path)
      AND ($signature IS NULL OR (s.signature IS NOT NULL AND s.signature CONTAINS $signature))
    OPTIONAL MATCH (s)<-[:CALLS|CALLS_INFERRED]-(caller)
    WITH s,
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
           s.signature AS signature, s.filepath AS filepath, rank,
           CASE
             WHEN s.filepath IS NULL THEN 2
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


VISUALIZE_SUBGRAPH_FOCUS_CYPHER = """
    MATCH (n {name: $name, project_id: $pid})
    WHERE n:Function OR n:Class OR n:Struct OR n:Enum OR n:Trait OR n:File
    RETURN n.id AS id, labels(n)[0] AS kind, n.name AS name,
           n.filepath AS fp, n.start_line AS sl
    LIMIT 3
"""


VISUALIZE_SUBGRAPH_NEIGHBORS_CYPHER = """
    MATCH (n {id: $fid})
     OPTIONAL MATCH (parent:File)-[:CONTAINS]->(n)
     OPTIONAL MATCH (n)<-[:CALLS|CALLS_INFERRED]-(caller)
         WHERE caller:File OR caller:Function OR caller:Class OR caller:Method
     OPTIONAL MATCH (n)<-[:IMPORTS]-(importer:File)
    OPTIONAL MATCH (n)-[:CALLS|CALLS_INFERRED]->(callee)
        WHERE callee:Function OR callee:Class OR callee:Struct
    RETURN
      parent.id AS parent_id, parent.name AS parent_name, parent.filepath AS parent_fp,
      collect(DISTINCT {id: caller.id, name: caller.name, fp: caller.filepath})[..6]  AS callers,
      collect(DISTINCT {id: importer.id, name: importer.name, fp: importer.filepath})[..6] AS importers,
      collect(DISTINCT {id: callee.id, name: callee.name, kind: labels(callee)[0],
                        fp: callee.filepath})[..8] AS callees
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
            candidate.get("path_rank", 99),
            len(candidate.get("qualified_name") or candidate.get("name") or ""),
        ),
    )
    if normalized_file_path:
        file_matches = [
            c for c in ranked_candidates if c.get("filepath") == normalized_file_path
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

    path_filter = ""
    if is_backend_root:
        path_filter = (
            " AND NOT (coalesce(hop.filepath, '') CONTAINS '/public/'"
            " OR coalesce(hop.filepath, '') STARTS WITH 'public/'"
            " OR coalesce(hop.filepath, '') ENDS WITH '.html'"
            " OR coalesce(hop.filepath, '') ENDS WITH '.css')"
        )

    cypher = (
        "MATCH (start) WHERE elementId(start) = $eid "
        "MATCH path = (start)"
        f"{edge_pattern}"
        " WHERE (hop:Function OR hop:Method OR hop:Class OR hop:Struct OR hop:Trait OR hop:Enum)"
        + path_filter
        + " RETURN [n IN nodes(path) | n.name] AS chain,"
        "        [n IN nodes(path) | n.filepath] AS files"
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

    callers = [c for c in (rec["callers"] or []) if c.get("name")]
    callees = [c for c in (rec["callees"] or []) if c.get("name")]

    if callers:
        out.append(f"**Called by** ({len(callers)}):")
        for caller in callers:
            line = f":{caller['line']}" if caller.get("line") else ""
            out.append(f"  - `{caller['name']}`{line}  in {caller.get('file', '?')}")
    if callees:
        out.append(f"\n**Calls** ({len(callees)}):")
        for callee in callees:
            out.append(f"  - `{callee['name']}`  in {callee.get('file', '?')}")
    return out


def format_call_chain_rows(
    rows: list[dict],
    *,
    resolved_name: str,
    symbol_name: str,
    direction: str,
    depth: int,
) -> str:
    def is_low_value_name(name: str | None) -> bool:
        normalized = (name or "").strip().lower()
        return normalized in {"", "unnamed", "<anonymous>", "anonymous"}

    seen: set[str] = set()
    header_name = resolved_name or symbol_name
    out = [f"## Call chain: `{header_name}` ({direction}, depth={depth})\n"]
    if resolved_name and resolved_name != symbol_name:
        out.append(f"Resolved `{symbol_name}` → `{resolved_name}`\n")
    emitted = 0
    for rec in rows:
        chain = rec["chain"]
        files = rec["files"]
        for index in range(1, len(chain)):
            name = chain[index]
            if is_low_value_name(name):
                continue
            key = "→".join(chain[: index + 1])
            if key in seen:
                continue
            seen.add(key)
            pad = "  " * index
            filepath = files[index] or "?"
            out.append(f"{pad}{'└─' if index > 1 else '  '} `{name}`  ({filepath})")
            emitted += 1
    if emitted == 0:
        hop_label = "callers" if direction == "up" else "callees"
        return (
            f"`{header_name}` resolved but no named {hop_label} within {depth} hops.\n"
            "The graph may only contain anonymous wrapper nodes on this path."
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
