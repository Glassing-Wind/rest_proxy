"""Overview helpers for project and directory graph summaries."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from _helpers import get_memory_modules, get_project_id, get_workspace_path
from graphrag_core.indexing import watcher as index_watcher
from tools.brain.graph import core as graph_core
from .core import _SYMBOL_FILTER_CYPHER


_SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
    ".runtime",
}


def _normalize_pkg_name(name: str) -> str:
    return (name or "").strip().replace("-", "_").lower()


def _repo_name_from_url(raw_url: str) -> str:
    path = urlparse(raw_url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else ""
    if name.endswith(".git"):
        name = name[:-4]
    return name


def _parse_repo_linked_dependencies(project_path: str) -> list[dict[str, str]]:
    deps: list[dict[str, str]] = []
    req_path = os.path.join(project_path, "requirements.txt")
    if not os.path.exists(req_path):
        return deps

    git_re = re.compile(
        r"git\+(?P<url>[^@#\s]+(?:\.git)?)(?:@(?P<rev>[^#\s]+))?"
        r"#egg=(?P<egg>[A-Za-z0-9_.-]+)(?:&subdirectory=(?P<subdir>[^\s]+))?"
    )

    try:
        with open(req_path, "r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                match = git_re.search(line)
                if not match:
                    continue
                url = match.group("url") or ""
                deps.append(
                    {
                        "package": match.group("egg") or "",
                        "repo_name": _repo_name_from_url(url),
                        "repo_url": url,
                        "rev": match.group("rev") or "",
                        "subdirectory": match.group("subdir") or "",
                    }
                )
    except Exception:
        return []

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for dep in deps:
        key = (_normalize_pkg_name(dep.get("package", "")), dep.get("repo_name", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(dep)
    return out


def _load_indexed_project_paths() -> dict[str, str]:
    data = index_watcher.load_indexed_projects()
    out: dict[str, str] = {}
    for entry in data.values():
        path = entry.get("project_path")
        if isinstance(path, str) and path:
            out[os.path.basename(path.rstrip(os.sep))] = path
    return out


def _find_repo_link_evidence(project_path: str, package_name: str, limit: int = 4) -> list[str]:
    pkg = _normalize_pkg_name(package_name)
    if not pkg or not os.path.isdir(project_path):
        return []
    patterns = (
        re.compile(rf"^\s*import\s+{re.escape(pkg)}(?:\s|$|,)", re.M),
        re.compile(rf"^\s*from\s+{re.escape(pkg)}(?:\.|\s+import\s+)", re.M),
    )
    hits: list[str] = []
    for root, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        for filename in sorted(filenames):
            if not filename.endswith((".py", ".pyi")):
                continue
            abs_path = os.path.join(root, filename)
            try:
                if os.path.getsize(abs_path) > 512_000:
                    continue
                with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except Exception:
                continue
            if any(p.search(text) for p in patterns):
                hits.append(os.path.relpath(abs_path, project_path))
                if len(hits) >= limit:
                    return hits
    return hits


def _summarize_repo_linked_dependencies(project_path: str) -> list[str]:
    linked = _parse_repo_linked_dependencies(project_path)
    if not linked:
        return []

    indexed_paths = _load_indexed_project_paths()
    lines: list[str] = []
    for dep in linked[:5]:
        package = dep.get("package") or "(unknown)"
        repo_name = dep.get("repo_name") or package
        repo_path = indexed_paths.get(repo_name, "")
        evidence = _find_repo_link_evidence(project_path, package)
        base = f"  - `{package}` from `{repo_name}`"
        if repo_path:
            base += f" → local indexed repo `{repo_path}`"
        if dep.get("subdirectory"):
            base += f" (subdir `{dep['subdirectory']}`)"
        lines.append(base)
        if evidence:
            lines.append(f"    imported by {', '.join(f'`{item}`' for item in evidence)}")
    return lines


def _summarize_repo_linked_dependencies_for_directory(
    project_path: str,
    directory_path: str,
) -> list[str]:
    linked = _parse_repo_linked_dependencies(project_path)
    if not linked:
        return []

    dir_prefix = directory_path.strip("./")
    if dir_prefix:
        dir_prefix = dir_prefix.rstrip("/") + "/"
    indexed_paths = _load_indexed_project_paths()
    lines: list[str] = []
    for dep in linked[:5]:
        package = dep.get("package") or "(unknown)"
        evidence = _find_repo_link_evidence(project_path, package, limit=8)
        if dir_prefix:
            evidence = [item for item in evidence if item.startswith(dir_prefix)]
        if not evidence:
            continue
        repo_name = dep.get("repo_name") or package
        repo_path = indexed_paths.get(repo_name, "")
        base = f"- `{package}` from `{repo_name}`"
        if repo_path:
            base += f" → local indexed repo `{repo_path}`"
        if dep.get("subdirectory"):
            base += f" (subdir `{dep['subdirectory']}`)"
        lines.append(base)
        lines.append(f"- evidence in {', '.join(f'`{item}`' for item in evidence[:4])}")
    return lines


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


async def has_cargo_build_context(session, project_id: str) -> bool:
    rows = await graph_core._execute_read(
        session,
        """
        MATCH (f:File {project_id:$p})
        WHERE f.filepath ENDS WITH 'Cargo.toml'
        RETURN count(f) AS n
        """,
        p=project_id,
        op="cargo_context_presence",
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
    schema_labels = await graph_core._execute_read(
        session,
        """
        CALL db.labels() YIELD label
        RETURN collect(label) AS labels
        """,
        op="apple_context_schema_labels",
    )
    schema_rels = await graph_core._execute_read(
        session,
        """
        CALL db.relationshipTypes() YIELD relationshipType
        RETURN collect(relationshipType) AS rels
        """,
        op="apple_context_schema_relationship_types",
    )
    labels = set(schema_labels[0].get("labels") or []) if schema_labels else set()
    rels = set(schema_rels[0].get("rels") or []) if schema_rels else set()
    if "XcodeWorkspace" in labels and "REFERENCES_PROJECT" in rels:
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
    else:
        workspaces = []
    return targets, schemes, workspaces


async def load_cargo_build_context(session, project_id: str, dir_prefix: str = "", limit: int = 5):
    schema_labels = await graph_core._execute_read(
        session,
        """
        CALL db.labels() YIELD label
        RETURN collect(label) AS labels
        """,
        op="cargo_context_schema_labels",
    )
    schema_rels = await graph_core._execute_read(
        session,
        """
        CALL db.relationshipTypes() YIELD relationshipType
        RETURN collect(relationshipType) AS rels
        """,
        op="cargo_context_schema_relationship_types",
    )
    labels = set(schema_labels[0].get("labels") or []) if schema_labels else set()
    rels = set(schema_rels[0].get("rels") or []) if schema_rels else set()
    if "CargoCrate" not in labels:
        return [], [], []

    crates = await graph_core._execute_read(
        session,
        """
        MATCH (c:CargoCrate {project_id:$p})-[:DEFINED_IN_FILE]->(mf:File {project_id:$p})
        WHERE $dir = ''
           OR mf.filepath STARTS WITH $dir
           OR $dir STARTS WITH replace(mf.filepath, 'Cargo.toml', '')
        RETURN c.name AS crate,
               c.crate_name AS crate_name,
               mf.filepath AS manifest_path,
               count(DISTINCT mf) AS manifest_files
        ORDER BY crate
        LIMIT $limit
        """,
        p=project_id,
        dir=dir_prefix,
        limit=limit,
        op="cargo_context_crates",
    )

    if "CargoWorkspace" in labels and "HAS_PACKAGE" in rels:
        workspaces = await graph_core._execute_read(
            session,
            """
            MATCH (w:CargoWorkspace {project_id:$p})-[:HAS_PACKAGE]->(c:CargoCrate {project_id:$p})
            RETURN w.filepath AS workspace, collect(DISTINCT c.name)[..10] AS crates
            ORDER BY workspace
            LIMIT $limit
            """,
            p=project_id,
            limit=limit,
            op="cargo_context_workspaces",
        )
    else:
        workspaces = []

    if "DEPENDS_ON_PACKAGE" in rels:
        dependencies = await graph_core._execute_read(
            session,
            """
            MATCH (src:CargoCrate {project_id:$p})-[r:DEPENDS_ON_PACKAGE]->(tgt:CargoCrate {project_id:$p})
            MATCH (src)-[:DEFINED_IN_FILE]->(mf:File {project_id:$p})
            WHERE $dir = ''
               OR mf.filepath STARTS WITH $dir
               OR $dir STARTS WITH replace(mf.filepath, 'Cargo.toml', '')
            RETURN src.name AS crate, collect(DISTINCT tgt.name)[..10] AS deps
            ORDER BY crate
            LIMIT $limit
            """,
            p=project_id,
            dir=dir_prefix,
            limit=limit,
            op="cargo_context_dependencies",
        )
    else:
        dependencies = []

    return crates, workspaces, dependencies


async def load_cargo_directory_dependencies(session, project_id: str, dir_prefix: str = "", limit: int = 5):
    schema_labels = await graph_core._execute_read(
        session,
        """
        CALL db.labels() YIELD label
        RETURN collect(label) AS labels
        """,
        op="cargo_directory_schema_labels",
    )
    schema_rels = await graph_core._execute_read(
        session,
        """
        CALL db.relationshipTypes() YIELD relationshipType
        RETURN collect(relationshipType) AS rels
        """,
        op="cargo_directory_schema_relationship_types",
    )
    labels = set(schema_labels[0].get("labels") or []) if schema_labels else set()
    rels = set(schema_rels[0].get("rels") or []) if schema_rels else set()
    if "CargoCrate" not in labels or "DEPENDS_ON_PACKAGE" not in rels:
        return [], []

    outbound = await graph_core._execute_read(
        session,
        """
        MATCH (src:CargoCrate {project_id:$p})-[:DEFINED_IN_FILE]->(mf:File {project_id:$p})
        WHERE $dir <> ''
          AND (mf.filepath STARTS WITH $dir OR $dir STARTS WITH replace(mf.filepath, 'Cargo.toml', ''))
        MATCH (src)-[:DEPENDS_ON_PACKAGE]->(tgt:CargoCrate {project_id:$p})
        RETURN src.name AS crate, collect(DISTINCT tgt.name)[..10] AS deps
        ORDER BY crate
        LIMIT $limit
        """,
        p=project_id,
        dir=dir_prefix,
        limit=limit,
        op="cargo_directory_dependencies_outbound",
    )
    inbound = await graph_core._execute_read(
        session,
        """
        MATCH (tgt:CargoCrate {project_id:$p})-[:DEFINED_IN_FILE]->(mf:File {project_id:$p})
        WHERE $dir <> ''
          AND (mf.filepath STARTS WITH $dir OR $dir STARTS WITH replace(mf.filepath, 'Cargo.toml', ''))
        MATCH (src:CargoCrate {project_id:$p})-[:DEPENDS_ON_PACKAGE]->(tgt)
        RETURN tgt.name AS crate, collect(DISTINCT src.name)[..10] AS dependents
        ORDER BY crate
        LIMIT $limit
        """,
        p=project_id,
        dir=dir_prefix,
        limit=limit,
        op="cargo_directory_dependencies_inbound",
    )
    return outbound, inbound


async def get_directory_snapshot_impl(*, driver, neo4j_db: str, workspace_id: str, directory_path: str, limit: int = 5) -> str:
    project_id = get_project_id(workspace_id)
    project_path = get_workspace_path(workspace_id)
    dir_prefix = directory_path.strip("./")
    if dir_prefix:
        dir_prefix += "/"
    repo_linked_dependencies = _summarize_repo_linked_dependencies_for_directory(
        project_path,
        directory_path,
    )

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
        if await has_cargo_build_context(session, project_id):
            r_cargo_crates, r_cargo_workspaces, r_cargo_dependencies = await load_cargo_build_context(
                session, project_id, dir_prefix=dir_prefix, limit=limit
            )
            r_cargo_dep_out, r_cargo_dep_in = await load_cargo_directory_dependencies(
                session, project_id, dir_prefix=dir_prefix, limit=limit
            )
        else:
            r_cargo_crates, r_cargo_workspaces, r_cargo_dependencies = [], [], []
            r_cargo_dep_out, r_cargo_dep_in = [], []

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

    if r_cargo_crates or r_cargo_workspaces or r_cargo_dependencies:
        lines.append("\n### 🦀 Cargo Context")
        for rec in r_cargo_crates:
            manifest = rec.get("manifest_path") or "(external crate)"
            crate_name = rec.get("crate_name") or rec["crate"]
            lines.append(f"- crate `{rec['crate']}` ({crate_name}) via `{manifest}`")
        for rec in r_cargo_workspaces:
            crates = ", ".join(rec.get("crates") or [])
            lines.append(f"- workspace `{rec['workspace']}` includes {crates}")
        for rec in r_cargo_dependencies:
            deps = ", ".join(rec.get("deps") or [])
            lines.append(f"- crate `{rec['crate']}` depends on {deps}")
        for rec in r_cargo_dep_out:
            deps = ", ".join(rec.get("deps") or [])
            lines.append(f"- local crate `{rec['crate']}` depends on {deps}")
        for rec in r_cargo_dep_in:
            dependents = ", ".join(rec.get("dependents") or [])
            lines.append(f"- local crate `{rec['crate']}` is used by {dependents}")

    if repo_linked_dependencies:
        lines.append("\n### 🔗 Repo-Linked Dependencies")
        lines.extend(repo_linked_dependencies)

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
    repo_linked_dependencies = _summarize_repo_linked_dependencies(project_path)

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
        if await has_cargo_build_context(session, project_id):
            cargo_crates, cargo_workspaces, cargo_dependencies = await load_cargo_build_context(
                session, project_id, limit=5
            )
        else:
            cargo_crates, cargo_workspaces, cargo_dependencies = [], [], []

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
    if cargo_crates or cargo_workspaces or cargo_dependencies:
        lines.extend(["", "## Cargo Workspace Context"])
        for rec in cargo_crates:
            manifest = rec.get("manifest_path") or "(external crate)"
            crate_name = rec.get("crate_name") or rec["crate"]
            lines.append(f"  - crate `{rec['crate']}` ({crate_name}) via `{manifest}`")
        for rec in cargo_workspaces:
            crates = ", ".join(rec.get("crates") or [])
            lines.append(f"  - workspace `{rec['workspace']}` includes {crates}")
        for rec in cargo_dependencies:
            deps = ", ".join(rec.get("deps") or [])
            lines.append(f"  - crate `{rec['crate']}` depends on {deps}")
    if repo_linked_dependencies:
        lines.extend(["", "## Repo-Linked Dependencies"])
        lines.extend(repo_linked_dependencies)
    return "\n".join(lines)


async def get_repo_dependency_summary_impl(*, workspace_id: str) -> str:
    project_path = get_workspace_path(workspace_id)
    linked = _parse_repo_linked_dependencies(project_path)
    if not linked:
        return f"No repo-linked editable/path dependencies found for `{project_path}`."

    indexed_paths = _load_indexed_project_paths()
    lines = [f"# Repo Dependency Summary: `{os.path.basename(project_path.rstrip(os.sep))}`"]
    for dep in linked:
        package = dep.get("package") or "(unknown)"
        repo_name = dep.get("repo_name") or package
        repo_path = indexed_paths.get(repo_name, "")
        evidence = _find_repo_link_evidence(project_path, package, limit=8)
        lines.append("")
        line = f"- package `{package}` from repo `{repo_name}`"
        if repo_path:
            line += f" → local indexed repo `{repo_path}`"
        lines.append(line)
        if dep.get("subdirectory"):
            lines.append(f"- binding subdirectory: `{dep['subdirectory']}`")
        if dep.get("rev"):
            lines.append(f"- pinned revision: `{dep['rev']}`")
        if dep.get("repo_url"):
            lines.append(f"- source URL: `{dep['repo_url']}`")
        if evidence:
            lines.append(f"- imported by: {', '.join(f'`{item}`' for item in evidence)}")
        else:
            lines.append("- imported by: no direct Python import sites found")
    return "\n".join(lines)
