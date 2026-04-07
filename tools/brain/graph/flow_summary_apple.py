"""Apple build summary helpers."""

from __future__ import annotations

from pathlib import PurePath

from _helpers import get_project_id
from tools.brain.graph import core as graph_core


async def _has_apple_build_files(session, project_id: str) -> bool:
    rows = await graph_core._execute_read(
        session,
        """
        MATCH (f:File {project_id:$p})
        WHERE f.filepath ENDS WITH '.xcodeproj/project.pbxproj'
           OR f.filepath ENDS WITH '.xcworkspace/contents.xcworkspacedata'
           OR f.filepath ENDS WITH '.xcscheme'
           OR f.filepath ENDS WITH '.storyboard'
           OR f.filepath ENDS WITH '.xib'
           OR f.filepath CONTAINS '.xcassets/'
        RETURN count(f) AS n
        """,
        p=project_id,
        op="apple_build_presence",
    )
    return bool(rows and rows[0].get("n"))


_APPLE_BUILD_QUERY = """
MATCH (res:Resource {project_id:$p})-[:BUNDLED_IN_TARGET]->(target:XcodeTarget {project_id:$p})
OPTIONAL MATCH (src:File {project_id:$p})-[rel:USES_ASSET|USES_COLOR_ASSET|USES_XIB|USES_STORYBOARD]->(res)
OPTIONAL MATCH (res)-[:BACKED_BY_FILE]->(backing:File {project_id:$p})
OPTIONAL MATCH (scheme:XcodeScheme {project_id:$p})-[:BUILDS_TARGET]->(target)
OPTIONAL MATCH (scheme)-[:DEFINED_IN_FILE]->(scheme_file:File {project_id:$p})
OPTIONAL MATCH (workspace:XcodeWorkspace {project_id:$p})-[:REFERENCES_PROJECT]->(project_file:File {project_id:$p})
WHERE project_file.filepath = target.project_file OR project_file IS NULL
RETURN src.filepath AS src,
   type(rel) AS rel,
   res.name AS resource,
   res.kind AS kind,
   backing.filepath AS backing,
   target.name AS target,
   target.project_file AS project_file,
   scheme.name AS scheme,
   scheme_file.filepath AS scheme_file,
   workspace.filepath AS workspace
ORDER BY src, resource, backing, target, scheme, workspace
LIMIT $limit
"""


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
    if backing:
        parts.append(backing)
    if target:
        parts.append(f"target={target}")
    if scheme:
        parts.append(f"scheme={scheme}")
    if workspace:
        parts.append(f"workspace={workspace}")
    return " -> ".join(parts)


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
    if any([source_contains, resource_contains, target_contains, scheme_contains, workspace_contains]):
        query_limit = max(limit * 10, 300)

    async with driver.session(database=neo4j_db) as session:
        if not await _has_apple_build_files(session, project_id):
            return "No Apple build graph paths found."
        result = await graph_core._execute_read(
            session,
            _APPLE_BUILD_QUERY,
            p=project_id,
            limit=query_limit,
            op="get_apple_build_summary",
        )

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

    rows = [r for r in rows if r[2] and r[5]]
    if not rows:
        return "No Apple build graph paths found."

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
