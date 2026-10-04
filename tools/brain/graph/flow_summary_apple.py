"""Apple build summary helpers."""

from __future__ import annotations

from pathlib import PurePath

from _helpers import get_project_id
from tools.brain.graph import core as graph_core
from tools.brain.graph_contract import node_label, rel_type


FILE_LABEL = node_label("file")
RESOURCE_LABEL = node_label("resource")
XCODE_TARGET_LABEL = node_label("xcode_target")
XCODE_WORKSPACE_LABEL = node_label("xcode_workspace")
XCODE_SCHEME_LABEL = node_label("xcode_scheme")
REL_BUNDLED_IN_TARGET = rel_type("bundled_in_target")
REL_BACKED_BY_FILE = rel_type("backed_by_file")
REL_BUNDLES_FILE = rel_type("bundles_file")
REL_REFERENCES_PROJECT = rel_type("references_project")
REL_BUILDS_TARGET = rel_type("builds_target")
REL_DEFINED_IN_FILE = rel_type("defined_in_file")


def _schema_cypher(text: str) -> str:
    replacements = {
        "__FILE__": FILE_LABEL,
        "__RESOURCE__": RESOURCE_LABEL,
        "__XCODE_TARGET__": XCODE_TARGET_LABEL,
        "__XCODE_WORKSPACE__": XCODE_WORKSPACE_LABEL,
        "__XCODE_SCHEME__": XCODE_SCHEME_LABEL,
        "__BUNDLED_IN_TARGET__": REL_BUNDLED_IN_TARGET,
        "__BACKED_BY_FILE__": REL_BACKED_BY_FILE,
        "__BUNDLES_FILE__": REL_BUNDLES_FILE,
        "__REFERENCES_PROJECT__": REL_REFERENCES_PROJECT,
        "__BUILDS_TARGET__": REL_BUILDS_TARGET,
        "__DEFINED_IN_FILE__": REL_DEFINED_IN_FILE,
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _file_path_expr(alias: str = "f") -> str:
    return f"coalesce({alias}.filepath, {alias}.file_path)"


async def _has_apple_build_files(session, project_id: str) -> bool:
    rows = await graph_core._execute_read(
        session,
        _schema_cypher(f"""
        MATCH (f:__FILE__ {{project_id:$p}})
        WHERE {_file_path_expr('f')} ENDS WITH '.xcodeproj/project.pbxproj'
           OR {_file_path_expr('f')} ENDS WITH '.xcworkspace/contents.xcworkspacedata'
           OR {_file_path_expr('f')} ENDS WITH '.xcscheme'
           OR {_file_path_expr('f')} ENDS WITH '.storyboard'
           OR {_file_path_expr('f')} ENDS WITH '.xib'
           OR {_file_path_expr('f')} CONTAINS '.xcassets/'
        RETURN count(f) AS n
        """),
        p=project_id,
        op="apple_build_presence",
    )
    return bool(rows and rows[0].get("n"))


async def _get_graph_schema_info(session) -> tuple[set[str], set[str]]:
    labels_rows = await graph_core._execute_read(
        session,
        """
        CALL db.labels() YIELD label
        RETURN collect(label) AS labels
        """,
        op="graph_schema_labels",
    )
    rel_rows = await graph_core._execute_read(
        session,
        """
        CALL db.relationshipTypes() YIELD relationshipType
        RETURN collect(relationshipType) AS rels
        """,
        op="graph_schema_relationship_types",
    )
    labels = set(labels_rows[0].get("labels") or []) if labels_rows else set()
    rels = set(rel_rows[0].get("rels") or []) if rel_rows else set()
    return labels, rels


async def _has_apple_resource_graph(session, project_id: str, *, labels: set[str], rels: set[str]) -> bool:
    if RESOURCE_LABEL not in labels or REL_BUNDLED_IN_TARGET not in rels:
        return False
    rows = await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (r:__RESOURCE__ {project_id:$p})
        RETURN count(r) AS n
        """),
        p=project_id,
        op="apple_resource_presence",
    )
    return bool(rows and rows[0].get("n"))


def _apple_resource_rel_types(rels: set[str]) -> list[str]:
    ordered = ["USES_ASSET", "USES_COLOR_ASSET", "USES_XIB", "USES_STORYBOARD"]
    return [rel for rel in ordered if rel in rels]


async def _has_apple_workspace_graph(session, project_id: str, *, labels: set[str], rels: set[str]) -> bool:
    if XCODE_WORKSPACE_LABEL not in labels or REL_REFERENCES_PROJECT not in rels:
        return False
    rows = await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (w:__XCODE_WORKSPACE__ {project_id:$p})
        RETURN count(w) AS n
        """),
        p=project_id,
        op="apple_workspace_presence",
    )
    return bool(rows and rows[0].get("n"))


async def _get_apple_graph_coverage(session, project_id: str) -> dict[str, int]:
    rows = await graph_core._execute_read(
        session,
        _schema_cypher(f"""
        CALL () {{
          MATCH (f:__FILE__ {{project_id:$p}})
          RETURN
            count(CASE WHEN {_file_path_expr('f')} ENDS WITH '.xcodeproj/project.pbxproj' THEN 1 END) AS project_files,
            count(CASE WHEN {_file_path_expr('f')} ENDS WITH '.xcworkspace/contents.xcworkspacedata' THEN 1 END) AS workspace_files,
            count(CASE WHEN {_file_path_expr('f')} ENDS WITH '.xcscheme' THEN 1 END) AS scheme_files,
            count(CASE WHEN {_file_path_expr('f')} ENDS WITH '.storyboard' OR {_file_path_expr('f')} ENDS WITH '.xib' OR {_file_path_expr('f')} CONTAINS '.xcassets/' THEN 1 END) AS resource_files
        }}
        CALL () {{
          MATCH (t:__XCODE_TARGET__ {{project_id:$p}})
          RETURN count(t) AS targets
        }}
        CALL () {{
          MATCH (s:__XCODE_SCHEME__ {{project_id:$p}})
          RETURN count(s) AS schemes
        }}
        CALL () {{
          MATCH (w:__XCODE_WORKSPACE__ {{project_id:$p}})
          RETURN count(w) AS workspaces
        }}
        CALL () {{
          MATCH (r:__RESOURCE__ {{project_id:$p}})
          RETURN count(r) AS resources
        }}
        CALL () {{
          MATCH (:__XCODE_TARGET__ {{project_id:$p}})-[rel:__BUNDLES_FILE__]->(:__FILE__ {{project_id:$p}})
          RETURN count(rel) AS bundles_file_edges
        }}
        CALL () {{
          MATCH (:__XCODE_SCHEME__ {{project_id:$p}})-[rel:__BUILDS_TARGET__]->(:__XCODE_TARGET__ {{project_id:$p}})
          RETURN count(rel) AS builds_target_edges
        }}
        CALL () {{
          MATCH (:__XCODE_WORKSPACE__ {{project_id:$p}})-[rel:__REFERENCES_PROJECT__]->(:__FILE__ {{project_id:$p}})
          RETURN count(rel) AS references_project_edges
        }}
        RETURN project_files, workspace_files, scheme_files, resource_files,
               targets, schemes, workspaces, resources,
               bundles_file_edges, builds_target_edges, references_project_edges
        """),
        p=project_id,
        op="apple_graph_coverage",
    )
    return dict(rows[0]) if rows else {}


def _describe_apple_graph_gaps(coverage: dict[str, int]) -> list[str]:
    notes: list[str] = []
    if coverage.get("project_files", 0) > 0 and coverage.get("targets", 0) == 0:
        notes.append("project files exist, but no XcodeTarget nodes were materialized")
    if coverage.get("scheme_files", 0) > 0 and coverage.get("schemes", 0) == 0:
        notes.append("scheme files exist, but no XcodeScheme nodes were materialized")
    if coverage.get("workspace_files", 0) > 0 and coverage.get("workspaces", 0) == 0:
        notes.append("workspace files exist, but no XcodeWorkspace nodes were materialized")
    if coverage.get("scheme_files", 0) > 0 and coverage.get("builds_target_edges", 0) == 0:
        notes.append("scheme files exist, but no BUILDS_TARGET edges were created")
    if coverage.get("workspace_files", 0) > 0 and coverage.get("references_project_edges", 0) == 0:
        notes.append("workspace files exist, but no REFERENCES_PROJECT edges were created")
    if coverage.get("resource_files", 0) > 0 and coverage.get("resources", 0) == 0:
        notes.append("resource-like files exist, but no Resource nodes were materialized")
    if coverage.get("resource_files", 0) > 0 and coverage.get("bundles_file_edges", 0) == 0:
        notes.append("resource-like files exist, but no BUNDLES_FILE edges were created")
    return notes


def _format_apple_graph_diagnostics(coverage: dict[str, int]) -> str:
    lines = [
        "Apple build files detected, but graph coverage is partial.",
        (
            "Files: "
            f"project={coverage.get('project_files', 0)} "
            f"scheme={coverage.get('scheme_files', 0)} "
            f"workspace={coverage.get('workspace_files', 0)} "
            f"resource_like={coverage.get('resource_files', 0)}"
        ),
        (
            "Graph: "
            f"targets={coverage.get('targets', 0)} "
            f"schemes={coverage.get('schemes', 0)} "
            f"workspaces={coverage.get('workspaces', 0)} "
            f"resources={coverage.get('resources', 0)}"
        ),
        (
            "Edges: "
            f"bundles_file={coverage.get('bundles_file_edges', 0)} "
            f"builds_target={coverage.get('builds_target_edges', 0)} "
            f"references_project={coverage.get('references_project_edges', 0)}"
        ),
    ]
    for note in _describe_apple_graph_gaps(coverage):
        lines.append(f"- {note}")
    return "\n".join(lines)


async def _get_apple_workspace_project_refs(session, project_id: str) -> list[tuple[str, str]]:
    rows = await graph_core._execute_read(
        session,
        _schema_cypher(f"""
        MATCH (workspace:__XCODE_WORKSPACE__ {{project_id:$p}})
              -[:__REFERENCES_PROJECT__]->
              (project_file:__FILE__ {{project_id:$p}})
        RETURN {_file_path_expr('workspace')} AS workspace,
               {_file_path_expr('project_file')} AS project_file
        ORDER BY workspace, project_file
        """),
        p=project_id,
        op="apple_workspace_projects",
    )
    return [
        (row.get("workspace"), row.get("project_file"))
        for row in rows
        if row.get("workspace") and row.get("project_file")
    ]


def _apple_build_query(
    *,
    include_resources: bool,
    include_workspaces: bool,
    resource_rel_types: list[str] | None = None,
) -> str:
    resource_rel_types = resource_rel_types or []
    lines = ["MATCH (target:__XCODE_TARGET__ {project_id:$p})"]
    if include_resources:
        lines.extend(
            [
                "OPTIONAL MATCH (res:__RESOURCE__ {project_id:$p})-[:__BUNDLED_IN_TARGET__]->(target)",
                "OPTIONAL MATCH (res)-[:__BACKED_BY_FILE__]->(res_backing:__FILE__ {project_id:$p})",
            ]
        )
        if resource_rel_types:
            rel_expr = "|".join(resource_rel_types)
            lines.append(
                f"OPTIONAL MATCH (src:__FILE__ {{project_id:$p}})-[rel:{rel_expr}]->(res)"
            )
        else:
            lines.append("WITH target, res, res_backing, null AS src, null AS rel")
        lines.append("WITH target, src, rel, res, res_backing")
    else:
        lines.append("WITH target, null AS src, null AS rel, null AS res, null AS res_backing")
    if include_resources:
        lines.append("WITH target, src, rel, res, res_backing, null AS bundled")
    else:
        lines.append("OPTIONAL MATCH (target)-[:__BUNDLES_FILE__]->(bundled:__FILE__ {project_id:$p})")
    lines.extend(
        [
            "OPTIONAL MATCH (scheme:__XCODE_SCHEME__ {project_id:$p})-[:__BUILDS_TARGET__]->(target)",
            "OPTIONAL MATCH (scheme)-[:__DEFINED_IN_FILE__]->(scheme_file:__FILE__ {project_id:$p})",
        ]
    )
    if include_workspaces:
        lines.extend(
            [
                "OPTIONAL MATCH (workspace:__XCODE_WORKSPACE__ {project_id:$p})-[:__REFERENCES_PROJECT__]->(project_file:__FILE__ {project_id:$p})",
                f"WHERE {_file_path_expr('project_file')} = target.project_file OR project_file IS NULL",
            ]
        )
    else:
        lines.append("WITH target, src, rel, res, res_backing, bundled, scheme, scheme_file, null AS workspace")
    lines.extend(
        [
            "RETURN src.filepath AS src,",
            "   type(rel) AS rel,",
            "   res.name AS resource,",
            "   res.kind AS kind,",
            "   coalesce(res_backing.filepath, bundled.filepath) AS backing,",
            "   target.name AS target,",
            "   target.project_file AS project_file,",
            "   scheme.name AS scheme,",
            f"   {_file_path_expr('scheme_file')} AS scheme_file,",
            f"   {_file_path_expr('workspace')} AS workspace",
            "ORDER BY src, resource, backing, target, scheme, workspace",
            "LIMIT $limit",
        ]
    )
    return "\n".join(lines)


def _apple_rank_key(
    row,
    *,
    source_contains: str | None,
    resource_contains: str | None,
    target_contains: str | None,
    scheme_contains: str | None,
    workspace_contains: str | None,
):
    src, _rel, resource, _kind, backing, target, project_file, scheme, _scheme_file, workspace = row
    score = 0
    if target_contains and target and target_contains in target:
        score += 20
    if scheme_contains and scheme and scheme_contains in scheme:
        score += 16
    if workspace_contains and workspace and workspace_contains in workspace:
        score += 12
    if source_contains and src and source_contains in src:
        score += 10
    if resource_contains and resource and resource_contains in resource:
        score += 8
    if src and target and PurePath(src).parts[:2] == PurePath(project_file or "").parts[:2]:
        score += 6
    if backing and target and PurePath(backing).parts[:2] == PurePath(project_file or "").parts[:2]:
        score += 5
    if scheme:
        score += 3
    if workspace:
        score += 2
    if backing:
        score += 1
    return (-score, target or "", scheme or "", src or "", resource or "", backing or "")


def _format_apple_build_line(row) -> str:
    src, rel, resource, _kind, backing, target, _project_file, scheme, _scheme_file, workspace = row
    parts = []
    if src:
        parts.append(src)
    if rel and resource:
        parts.append(f"{rel}:{resource}")
    elif resource:
        parts.append(resource)
    elif backing:
        parts.append(f"BUNDLES_FILE:{backing}")
    if backing and (not parts or parts[-1] != f"BUNDLES_FILE:{backing}"):
        parts.append(backing)
    if target:
        parts.append(f"target={target}")
    if scheme:
        parts.append(f"scheme={scheme}")
    if workspace:
        parts.append(f"workspace={workspace}")
    return " -> ".join(parts)


def _apple_workspace_overview_lines(
    workspace_project_refs: list[tuple[str, str]],
) -> list[str]:
    workspace_projects: dict[str, set[str]] = {}
    for workspace, project_file in workspace_project_refs:
        workspace_projects.setdefault(workspace, set()).add(project_file)

    multi_project_workspaces = {
        workspace: sorted(projects)
        for workspace, projects in workspace_projects.items()
        if len(projects) > 1
    }
    if not multi_project_workspaces:
        return []

    lines = ["Workspace Overview:"]
    for workspace, projects in sorted(multi_project_workspaces.items()):
        lines.append(f"- {workspace}")
        for project_file in projects:
            lines.append(f"  -> {project_file}")
    lines.append("")
    return lines


def _apple_group_dedupe_key(row, *, group_norm: str) -> tuple[str | None, ...]:
    src, rel, resource, kind, backing, target, project_file, scheme, _scheme_file, workspace = row
    if group_norm == "scheme":
        return (src, rel, resource, kind, backing, target, project_file, scheme, workspace)
    return (src, rel, resource, kind, backing, target, project_file, workspace)


def _choose_preferred_apple_row(
    current,
    candidate,
    *,
    group_norm: str,
    target_contains: str | None,
    scheme_contains: str | None,
) -> tuple[str | None, ...]:
    if current is None:
        return candidate
    if group_norm == "scheme":
        return current

    current_target = current[5] or ""
    current_scheme = current[7] or ""
    candidate_target = candidate[5] or ""
    candidate_scheme = candidate[7] or ""

    def _score(target: str, scheme: str) -> tuple[int, int, int, str]:
        target_norm = target.casefold()
        scheme_norm = scheme.casefold()
        return (
            1 if target and scheme and target_norm == scheme_norm else 0,
            1 if target_contains and target_contains in target else 0,
            1 if scheme_contains and scheme_contains in scheme else 0,
            scheme or "",
        )

    current_score = _score(current_target, current_scheme)
    candidate_score = _score(candidate_target, candidate_scheme)
    if candidate_score > current_score:
        return candidate
    return current


def _dedupe_apple_rows(
    rows: list[tuple[str | None, ...]],
    *,
    group_norm: str,
    target_contains: str | None,
    scheme_contains: str | None,
) -> list[tuple[str | None, ...]]:
    selected: dict[tuple[str | None, ...], tuple[str | None, ...]] = {}
    for row in rows:
        key = _apple_group_dedupe_key(row, group_norm=group_norm)
        selected[key] = _choose_preferred_apple_row(
            selected.get(key),
            row,
            group_norm=group_norm,
            target_contains=target_contains,
            scheme_contains=scheme_contains,
        )
    return list(selected.values())


async def get_apple_build_summary_impl(
    *,
    driver,
    neo4j_db: str,
    workspace_id: str,
    source_contains: str | None = None,
    resource_contains: str | None = None,
    target_contains: str | None = None,
    scheme_contains: str | None = None,
    workspace_contains: str | None = None,
    limit: int = 20,
    as_table: bool = False,
    group_by: str = "target",
) -> str:
    project_id = get_project_id(workspace_id)
    query_limit = max(limit, 50)
    coverage: dict[str, int] | None = None
    workspace_project_refs: list[tuple[str, str]] = []
    if any([source_contains, resource_contains, target_contains, scheme_contains, workspace_contains]):
        query_limit = max(limit * 10, 300)

    async with driver.session(database=neo4j_db) as session:
        if not await _has_apple_build_files(session, project_id):
            return "No Apple build graph paths found."
        labels, rels = await _get_graph_schema_info(session)
        resource_rel_types = _apple_resource_rel_types(rels)
        query = _apple_build_query(
            include_resources=await _has_apple_resource_graph(session, project_id, labels=labels, rels=rels),
            include_workspaces=await _has_apple_workspace_graph(session, project_id, labels=labels, rels=rels),
            resource_rel_types=resource_rel_types,
        )
        result = await graph_core._execute_read(
            session,
            _schema_cypher(query),
            p=project_id,
            limit=query_limit,
            op="get_apple_build_summary",
        )
        coverage = await _get_apple_graph_coverage(session, project_id)
        workspace_project_refs = await _get_apple_workspace_project_refs(session, project_id)

    rows = [
        (
            row.get("src"),
            row.get("rel"),
            row.get("resource"),
            row.get("kind"),
            row.get("backing"),
            row.get("target"),
            row.get("project_file"),
            row.get("scheme"),
            row.get("scheme_file"),
            row.get("workspace"),
        )
        for row in result
    ]

    if source_contains:
        rows = [r for r in rows if r[0] and source_contains in r[0]]
    if resource_contains:
        rows = [r for r in rows if r[2] and resource_contains in r[2]]
    if target_contains:
        rows = [r for r in rows if r[5] and target_contains in r[5]]
    if scheme_contains:
        rows = [r for r in rows if r[7] and scheme_contains in r[7]]
    if workspace_contains:
        rows = [r for r in rows if r[9] and workspace_contains in r[9]]

    rows = [r for r in rows if r[5] and (r[2] or r[4])]
    if not rows:
        coverage = coverage or {}
        return _format_apple_graph_diagnostics(coverage)

    group_norm = (group_by or "target").strip().lower()
    if group_norm not in {"target", "scheme", "source", "none"}:
        group_norm = "target"

    rows = sorted(
        rows,
        key=lambda row: _apple_rank_key(
            row,
            source_contains=source_contains,
            resource_contains=resource_contains,
            target_contains=target_contains,
            scheme_contains=scheme_contains,
            workspace_contains=workspace_contains,
        ),
    )
    rows = _dedupe_apple_rows(
        rows,
        group_norm=group_norm,
        target_contains=target_contains,
        scheme_contains=scheme_contains,
    )

    if as_table:
        output = [
            "| Source | Link | Resource | Kind | Backing File | Target | Project | Scheme | Scheme File | Workspace |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for src, rel, resource, kind, backing, target, project_file, scheme, scheme_file, workspace in rows[:limit]:
            output.append(
                f"| {src or ''} | {rel or ''} | {resource or ''} | {kind or ''} | {backing or ''} | {target or ''} | {project_file or ''} | {scheme or ''} | {scheme_file or ''} | {workspace or ''} |"
            )
        return "\n".join(output)

    if group_norm == "none":
        output = []
        seen = set()
        for row in rows:
            line = _format_apple_build_line(row)
            if not line or line in seen:
                continue
            seen.add(line)
            output.append(line)
            if limit and len(output) >= limit:
                break
        return "\n".join(output)

    key_index = {"source": 0, "target": 5, "scheme": 7}[group_norm]
    grouped: dict[str, list[tuple[str | None, ...]]] = {}
    for row in rows:
        group_value = row[key_index]
        if not group_value:
            continue
        grouped.setdefault(group_value, []).append(row)

    output = []
    emitted = 0
    output.extend(_apple_workspace_overview_lines(workspace_project_refs))
    for group_value in grouped:
        output.append(f"{group_norm.title()}: {group_value}")
        seen = set()
        for row in grouped[group_value]:
            line = _format_apple_build_line(row)
            if not line or line in seen:
                continue
            seen.add(line)
            output.append(line)
            emitted += 1
            if limit and emitted >= limit:
                return "\n".join(output)
    return "\n".join(output)
