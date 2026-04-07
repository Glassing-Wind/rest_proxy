"""Overview helpers for project and directory graph summaries."""

from __future__ import annotations

from _helpers import get_memory_modules, get_project_id, get_workspace_path
from tools.brain.graph import core as graph_core
from .core import _SYMBOL_FILTER_CYPHER


async def has_apple_build_context(session, project_id: str) -> bool:
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
        op="apple_context_presence",
    )
    return bool(rows and rows[0].get("n"))


async def load_apple_build_context(session, project_id: str, dir_prefix: str = "", limit: int = 5):
    targets = await graph_core._execute_read(
        session,
        """
        MATCH (t:XcodeTarget {project_id:$p})
        OPTIONAL MATCH (t)-[:BUNDLES_FILE]->(f:File {project_id:$p})
        WHERE $dir = '' OR f.filepath STARTS WITH $dir
        RETURN t.name AS target, t.project_file AS project_file, count(DISTINCT f) AS bundled_files
        ORDER BY bundled_files DESC, target
        LIMIT $limit
        """,
        p=project_id,
        dir=dir_prefix,
        limit=limit,
        op="apple_context_targets",
    )
    schemes = await graph_core._execute_read(
        session,
        """
        MATCH (s:XcodeScheme {project_id:$p})-[:BUILDS_TARGET]->(t:XcodeTarget {project_id:$p})
        RETURN s.name AS scheme, collect(DISTINCT t.name)[..10] AS targets
        ORDER BY scheme
        LIMIT $limit
        """,
        p=project_id,
        limit=limit,
        op="apple_context_schemes",
    )
    workspaces = await graph_core._execute_read(
        session,
        """
        MATCH (w:XcodeWorkspace {project_id:$p})-[:REFERENCES_PROJECT]->(f:File {project_id:$p})
        RETURN w.filepath AS workspace, collect(DISTINCT f.filepath)[..10] AS projects
        ORDER BY workspace
        LIMIT $limit
        """,
        p=project_id,
        limit=limit,
        op="apple_context_workspaces",
    )
    return targets, schemes, workspaces


async def get_directory_snapshot_impl(*, driver, neo4j_db: str, workspace_id: str, directory_path: str, limit: int = 5) -> str:
    project_id = get_project_id(workspace_id)
    dir_prefix = directory_path.strip("./")
    if dir_prefix:
        dir_prefix += "/"

    async with driver.session(database=neo4j_db) as session:
        r_files = await graph_core._execute_read(
            session,
            """
            MATCH (f:File {{project_id: $p}})
            WHERE f.filepath STARTS WITH $dir
            OPTIONAL MATCH (f)-[:CONTAINS]->(s)
            WHERE {filters}
            WITH f.filepath AS fp, count(s) AS sym_count, collect(s.name)[..3] AS samples
            ORDER BY sym_count DESC
            RETURN fp, sym_count, samples
            LIMIT $limit
        """.format(filters=_SYMBOL_FILTER_CYPHER),
            p=project_id,
            dir=dir_prefix,
            limit=limit,
            op="get_directory_snapshot_files",
        )
        r_inbound = await graph_core._execute_read(
            session,
            """
            MATCH (ext:File {project_id: $p})-[:IMPORTS]->(inner:File {project_id: $p})
            WHERE inner.filepath STARTS WITH $dir
              AND NOT ext.filepath STARTS WITH $dir
            RETURN ext.filepath AS caller, count(DISTINCT inner) AS n_imports
            ORDER BY n_imports DESC
            LIMIT $limit
        """,
            p=project_id,
            dir=dir_prefix,
            limit=limit,
            op="get_directory_snapshot_inbound",
        )
        r_outbound = await graph_core._execute_read(
            session,
            """
            MATCH (inner:File {project_id: $p})-[:IMPORTS]->(ext:File {project_id: $p})
            WHERE inner.filepath STARTS WITH $dir
              AND NOT ext.filepath STARTS WITH $dir
            RETURN ext.filepath AS dependency, count(DISTINCT inner) AS n_usages
            ORDER BY n_usages DESC
            LIMIT $limit
        """,
            p=project_id,
            dir=dir_prefix,
            limit=limit,
            op="get_directory_snapshot_outbound",
        )
        r_assets = await graph_core._execute_read(
            session,
            """
            MATCH (f:File {project_id: $p})
            WHERE f.filepath STARTS WITH $dir
            MATCH (f)-[r:ASSET_LINKS|CALLS_API]->(target:File {project_id: $p})
            RETURN f.filepath AS source, type(r) AS rel, target.filepath AS target
            ORDER BY source, rel
            LIMIT $limit
        """,
            p=project_id,
            dir=dir_prefix,
            limit=limit * 3,
            op="get_directory_snapshot_assets",
        )
        if await has_apple_build_context(session, project_id):
            r_apple_targets, r_apple_schemes, _ = await load_apple_build_context(
                session, project_id, dir_prefix=dir_prefix, limit=limit
            )
        else:
            r_apple_targets, r_apple_schemes = [], []

    lines = [f"# Directory Snapshot: `{directory_path or '.'}/`"]
    if not r_files:
        return f"No indexed files found in `{directory_path}`."

    lines.append("\n### 🏆 Top Files (by symbol density)")
    for rec in r_files:
        samples = ", ".join(rec["samples"])
        lines.append(f"- **{rec['fp']}** ({rec['sym_count']} symbols: {samples})")

    if r_assets:
        lines.append("\n### 🎨 UI & Assets (Linkages & Wiring)")
        for rec in r_assets:
            source = rec["source"]
            target = rec["target"]
            rel = rec["rel"]
            if rel == "ASSET_LINKS":
                lines.append(f"- `{source}` -> 📦 `{target}` (Asset/Style)")
            elif rel == "CALLS_API":
                lines.append(f"- `{source}` -> 🔌 `{target}` (API Endpoint)")

    if r_apple_targets or r_apple_schemes:
        lines.append("\n### 🍎 Apple Build Context")
        for rec in r_apple_targets:
            lines.append(
                f"- target `{rec['target']}` bundles {rec['bundled_files']} file(s)"
                + (f" via `{rec['project_file']}`" if rec.get("project_file") else "")
            )
        for rec in r_apple_schemes:
            targets = ", ".join(rec.get("targets") or [])
            lines.append(f"- scheme `{rec['scheme']}` builds {targets}")

    if r_inbound:
        lines.append("\n### 📥 Consumers (External files importing from here)")
        for rec in r_inbound:
            lines.append(f"- `{rec['caller']}` (imports {rec['n_imports']} files)")
    else:
        lines.append("\n### 📥 Consumers: None found.")

    if r_outbound:
        lines.append("\n### 📤 Dependencies (External files imported by here)")
        for rec in r_outbound:
            lines.append(f"- `{rec['dependency']}` (used by {rec['n_usages']} files)")
    else:
        lines.append("\n### 📤 Dependencies: None found.")

    return "\n".join(lines)


async def get_project_overview_impl(*, driver, neo4j_db: str, workspace_id: str) -> str:
    project_id = get_project_id(workspace_id)
    project_path = get_workspace_path(workspace_id)

    async with driver.session(database=neo4j_db) as session:
        r = await graph_core._execute_read(
            session,
            "MATCH (f:File {project_id:$p}) RETURN count(f) AS files",
            p=project_id,
            op="get_project_overview_file_count",
        )
        n_files = r[0]["files"] if r else 0

        r2 = await graph_core._execute_read(
            session,
            f"MATCH (s {{project_id:$p}}) WHERE {_SYMBOL_FILTER_CYPHER} RETURN count(s) AS syms",
            p=project_id,
            op="get_project_overview_symbol_count",
        )
        n_syms = r2[0]["syms"] if r2 else 0

        r3 = await graph_core._execute_read(
            session,
            """
            MATCH (f:File {{project_id: $p}})
            WITH f, CASE WHEN f.filepath CONTAINS '/'
                 THEN split(f.filepath, '/')[0] ELSE '(root)' END AS top_dir
            OPTIONAL MATCH (f)-[:CONTAINS]->(s)
            WHERE {filters}
            WITH top_dir, count(DISTINCT s) AS syms, count(DISTINCT f) AS files
            ORDER BY syms DESC LIMIT 6
            RETURN top_dir, files, syms
        """.format(filters=_SYMBOL_FILTER_CYPHER),
            p=project_id,
            op="get_project_overview_dirs",
        )
        dirs = [f"  📂 {rec['top_dir']}/  ({rec['files']} files, {rec['syms']} symbols)" for rec in r3]

        r4 = await graph_core._execute_read(
            session,
            """
            MATCH (f:File {{project_id: $p}})-[:CONTAINS]->(s)
            WHERE ({filters})
              AND NOT f.filepath CONTAINS 'test'
              AND NOT f.filepath CONTAINS 'spec'
            WITH f.filepath AS fp, count(s) AS n, collect(DISTINCT s.name)[..3] AS ex
            ORDER BY n DESC LIMIT 5
            RETURN fp, n, ex
        """.format(filters=_SYMBOL_FILTER_CYPHER),
            p=project_id,
            op="get_project_overview_key_files",
        )
        key_files = []
        for rec in r4:
            ex = ", ".join(e for e in rec["ex"] if e)
            key_files.append(f"  - {rec['fp']}  ({rec['n']} symbols: {ex})")

        if await has_apple_build_context(session, project_id):
            apple_targets, apple_schemes, apple_workspaces = await load_apple_build_context(
                session, project_id, limit=5
            )
        else:
            apple_targets, apple_schemes, apple_workspaces = [], [], []

        memory_store, _, _, _, _ = get_memory_modules()
        await memory_store.open_pool()
        async with memory_store._pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT count(*) FROM codebase_embeddings WHERE project_id=%s",
                    (project_id,),
                )
                row = await cur.fetchone()
            n_chunks = row[0] if row else 0

    lines = (
        [
            f"# Project Overview: {project_path.split('/')[-1]}",
            "",
            "## Health",
            f"  Files indexed:   {n_files}",
            f"  Symbols:         {n_syms}",
            "  Symbol kinds:    Function, Class, Struct, Trait, Enum",
            f"  Semantic chunks: {n_chunks}",
            "",
            "## Architecture (top-level directories by symbol density)",
        ]
        + dirs
        + [
            "",
            "## Key Files (most symbol-dense, non-test)",
        ]
        + key_files
        + [
            "",
            "## Next steps",
            f"  - search_codebase('{project_path}', '<your question>') — semantic search",
            f"  - describe_file('{project_path}', '<file_path>') — deep-dive a specific file",
            f"  - get_code_importance('{project_path}') — full ranked file list",
        ]
    )
    if apple_targets or apple_schemes or apple_workspaces:
        lines.extend(["", "## Apple Build Context"])
        for rec in apple_targets:
            lines.append(
                f"  - target `{rec['target']}` bundles {rec['bundled_files']} file(s)"
                + (f" via `{rec['project_file']}`" if rec.get("project_file") else "")
            )
        for rec in apple_schemes:
            targets = ", ".join(rec.get("targets") or [])
            lines.append(f"  - scheme `{rec['scheme']}` builds {targets}")
        for rec in apple_workspaces:
            projects = ", ".join(rec.get("projects") or [])
            lines.append(f"  - workspace `{rec['workspace']}` references {projects}")
    return "\n".join(lines)
