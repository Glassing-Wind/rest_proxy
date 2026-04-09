"""Helpers for symbol and call-graph queries."""

from __future__ import annotations

import os


SYMBOL_CONTEXT_CYPHER = """
    MATCH (s {name: $name, project_id: $pid})
    WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum OR s:EnumCase OR s:Method
       OR s:Protocol OR s:Interface OR s:Extension OR s:TypeAlias OR s:AssociatedType
    OPTIONAL MATCH (s)<-[:CONTAINS]-(parent:File)
    OPTIONAL MATCH (caller)-[:CALLS|CALLS_INFERRED]->(s)
    OPTIONAL MATCH (s)-[:CALLS|CALLS_INFERRED]->(callee)
    RETURN
      head([label IN labels(s) WHERE label <> 'Node']) AS kind,
      s.filepath    AS filepath,
      s.start_line  AS start_line,
      s.end_line    AS end_line,
      s.signature   AS signature,
      parent.filepath AS parent_file,
      collect(DISTINCT {name: caller.name, file: caller.filepath,
                        line: caller.start_line})[..10] AS callers,
      collect(DISTINCT {name: callee.name, file: callee.filepath})[..10] AS callees,
      count(DISTINCT caller) AS callers_in,
      count(DISTINCT callee) AS callees_out
    LIMIT 12
"""


CALL_CHAIN_RESOLVE_CYPHER = """
    MATCH (s)
    WHERE s.project_id = $pid
      AND (s:Function OR s:Method OR s:Class OR s:Struct OR s:Trait OR s:Enum
           OR s:Protocol OR s:Interface OR s:Extension OR s:TypeAlias OR s:AssociatedType)
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


VISUALIZE_SUBGRAPH_FOCUS_CYPHER = """
    MATCH (n {name: $name, project_id: $pid})
    WHERE n:Function OR n:Class OR n:Struct OR n:Enum OR n:Trait
       OR n:Protocol OR n:Interface OR n:Extension OR n:TypeAlias OR n:AssociatedType
       OR n:File
    RETURN n.id AS id, head([label IN labels(n) WHERE label <> 'Node']) AS kind, n.name AS name,
           n.filepath AS fp, n.start_line AS sl
    LIMIT 12
"""


VISUALIZE_SUBGRAPH_NEIGHBORS_CYPHER = """
    MATCH (n {id: $fid})
     OPTIONAL MATCH (parent:File)-[:CONTAINS]->(n)
     OPTIONAL MATCH (n)<-[:CALLS|CALLS_INFERRED]-(caller)
        WHERE caller:File OR caller:Function OR caller:Class OR caller:Method
           OR caller:Struct OR caller:Trait OR caller:Enum OR caller:Protocol
           OR caller:Interface OR caller:Extension OR caller:TypeAlias OR caller:AssociatedType
     OPTIONAL MATCH (n)<-[:IMPORTS]-(importer:File)
    OPTIONAL MATCH (n)-[:CALLS|CALLS_INFERRED]->(callee)
        WHERE callee:Function OR callee:Class OR callee:Struct OR callee:Method
           OR callee:Trait OR callee:Enum OR callee:Protocol OR callee:Interface
           OR callee:Extension OR callee:TypeAlias OR callee:AssociatedType
    RETURN
      parent.id AS parent_id, parent.name AS parent_name, parent.filepath AS parent_fp,
      collect(DISTINCT {id: caller.id, name: caller.name, fp: caller.filepath})[..6]  AS callers,
      collect(DISTINCT {id: importer.id, name: importer.name, fp: importer.filepath})[..6] AS importers,
      collect(DISTINCT {id: callee.id, name: callee.name, kind: head([label IN labels(callee) WHERE label <> 'Node']),
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


def _symbol_path_penalty(filepath: str | None) -> int:
    normalized = (filepath or "").replace("\\", "/").lower()
    if not normalized:
        return 6
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
        )
    ):
        return 5
    if any(
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


def pick_symbol_context_candidate(candidates: list[dict], *, symbol_name: str) -> dict | None:
    if not candidates:
        return None

    ranked = sorted(
        candidates,
        key=lambda candidate: (
            0 if candidate.get("kind") in {"Function", "Method", "Class", "Struct"} else 1,
            _symbol_path_penalty(candidate.get("filepath")),
            _symbol_kind_rank(candidate.get("kind")),
            -(candidate.get("callers_in") or 0),
            -(candidate.get("callees_out") or 0),
            len(candidate.get("filepath") or ""),
            candidate.get("start_line") or 0,
        ),
    )
    return ranked[0]


def should_disambiguate_symbol_context(candidates: list[dict], *, symbol_name: str) -> bool:
    normalized_name = (symbol_name or "").strip()
    if not normalized_name:
        return False
    if "." in normalized_name or "(" in normalized_name or len(normalized_name) > 18:
        return False
    distinct_paths = {c.get("filepath") for c in candidates if c.get("filepath")}
    distinct_kinds = {c.get("kind") for c in candidates if c.get("kind")}
    if len(distinct_paths) >= 5:
        return True
    if len(distinct_paths) >= 3 and len(distinct_kinds) >= 2:
        return True
    return False


def format_symbol_context_ambiguity(candidates: list[dict], *, symbol_name: str) -> str:
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            _symbol_path_penalty(candidate.get("filepath")),
            _symbol_kind_rank(candidate.get("kind")),
            -(candidate.get("callers_in") or 0),
            len(candidate.get("filepath") or ""),
            candidate.get("start_line") or 0,
        ),
    )
    lines = [
        f"Multiple exact matches found for `{symbol_name}`. Be more specific or use `list_symbol_matches`.",
        "",
        "Top matches:",
    ]
    for candidate in ranked[:6]:
        lines.append(
            f"- [{candidate.get('kind') or 'Symbol'}] "
            f"{candidate.get('filepath') or 'unknown'}:{candidate.get('start_line') or 1}"
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
            -int(candidate.get("callers_in") or 0),
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


def pick_visualize_candidate(candidates: list[dict], *, symbol_name: str) -> dict | None:
    if not candidates:
        return None

    def _path_penalty(filepath: str | None) -> int:
        normalized = (filepath or "").lower()
        if not normalized:
            return 3
        if any(
            marker in normalized
            for marker in (
                "/pregeneratedspm/",
                "/vendors/",
                "/generated/",
                "generated.swift",
                "generated.ts",
                "generated.js",
            )
        ):
            return 3
        if any(marker in normalized for marker in ("/tests/", "/test/", "/fixtures/")):
            return 2
        return 0

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
            _path_penalty(candidate.get("fp")),
            _kind_rank(candidate.get("kind")),
            candidate.get("sl") or 0,
            len(candidate.get("fp") or ""),
        ),
    )
    return ranked[0]


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

    path_filter = (
        " AND NOT (coalesce(hop.filepath, '') CONTAINS '/tests/'"
        " OR coalesce(hop.filepath, '') CONTAINS '/test/'"
        " OR coalesce(hop.filepath, '') CONTAINS '/e2e/'"
        " OR coalesce(hop.filepath, '') CONTAINS '/fixtures/'"
        " OR coalesce(hop.filepath, '') CONTAINS '.spec.'"
        " OR coalesce(hop.filepath, '') CONTAINS '.stories.'"
        " OR coalesce(hop.filepath, '') CONTAINS '/gen/'"
        " OR coalesce(hop.filepath, '') CONTAINS '/generated/'"
        " OR coalesce(hop.filepath, '') CONTAINS 'PreGeneratedSPM'"
        " OR coalesce(hop.filepath, '') CONTAINS '/vendors/'"
        " OR coalesce(hop.filepath, '') STARTS WITH 'vendors/')"
    )
    if is_backend_root:
        path_filter += (
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
        "        [n IN nodes(path) | n.filepath] AS files,"
        "        [n IN nodes(path) | n.start_line] AS lines"
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

    def path_penalty(filepath: str | None) -> int:
        normalized = (filepath or "").replace("\\", "/").lower()
        if not normalized:
            return 6
        if any(token in normalized for token in ("/test/", "/tests/", "/e2e/", "/fixtures/", ".spec.", ".stories.")):
            return 5
        if any(token in normalized for token in ("/gen/", "/generated/", ".gen.", "_generated.", "pregeneratedspm")):
            return 4
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

    header_name = resolved_name or symbol_name
    out = [f"## Call chain: `{header_name}` ({direction}, depth={depth})\n"]
    if resolved_name and resolved_name != symbol_name:
        out.append(f"Resolved `{symbol_name}` → `{resolved_name}`\n")
    anonymous_hints: list[str] = []
    first_hop_groups: "OrderedDict[tuple[str, str], OrderedDict[tuple[str, str], None]]" = OrderedDict()
    first_hop_counts: dict[tuple[str, str], int] = {}
    terminal_paths = 0
    root_focus = focus_prefix(resolved_filepath)

    for rec in rows:
        chain = rec["chain"]
        files = rec["files"]
        lines = rec.get("lines") or []
        compact_chain: list[tuple[str | None, str | None, int | None]] = []
        for idx, name in enumerate(chain):
            file_path = files[idx] if idx < len(files) else None
            line = lines[idx] if idx < len(lines) else None
            if idx > 0 and is_low_value_name(name):
                hint = f"{file_path}:{line}" if file_path and line else (file_path or "?")
                if hint not in anonymous_hints:
                    anonymous_hints.append(hint)
                continue
            compact_chain.append((name, file_path, line))
        chain = [entry[0] for entry in compact_chain]
        files = [entry[1] for entry in compact_chain]
        lines = [entry[2] for entry in compact_chain]
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
            path_penalty(item[0][1]),
            -first_hop_counts.get(item[0], 0),
            item[0][1] or "",
            item[0][0] or "",
        ),
    )
    if root_focus:
        focused_first_hops = [
            item for item in ranked_first_hops if (item[0][1] or "").startswith(root_focus)
        ]
        if focused_first_hops:
            ranked_first_hops = focused_first_hops + [
                item for item in ranked_first_hops if item not in focused_first_hops
            ]
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
