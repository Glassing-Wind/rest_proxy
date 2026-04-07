"""Apple/Xcode asset graph helpers."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import PurePosixPath
from typing import Awaitable, Callable
from xml.etree import ElementTree as ET


ExecuteWrite = Callable[..., Awaitable[None]]
RESOURCE_REL_BY_KIND = {
    "image": "USES_ASSET",
    "color": "USES_COLOR_ASSET",
    "nib": "USES_XIB",
    "storyboard": "USES_STORYBOARD",
}


def _read_text(abs_path: str) -> str:
    try:
        if os.path.getsize(abs_path) > 1_000_000:
            return ""
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


def collect_swift_resource_edges(
    project_path: str,
    file_facts: dict[str, dict[str, object]],
    files: dict[str, str],
) -> list[tuple[str, str, str, str | None, str]]:
    resource_catalog = discover_apple_resources(project_path)
    resource_edges: list[tuple[str, str, str, str | None, str]] = []
    for fp, fid in files.items():
        facts = file_facts.get(fp) or {}
        for ref in facts.get("resource_refs") or []:
            if not isinstance(ref, dict):
                continue
            kind = ref.get("kind")
            name = ref.get("name")
            if not isinstance(kind, str) or not isinstance(name, str):
                continue
            rel = RESOURCE_REL_BY_KIND.get(kind)
            if not rel:
                continue
            resource_edges.append((fid, rel, name, resource_catalog.get((kind, name)), kind))
    return list(set(resource_edges))


def discover_apple_resources(project_path: str) -> dict[tuple[str, str], str]:
    discovered: dict[tuple[str, str], str] = {}
    for current_root, dirnames, filenames in os.walk(project_path):
        rel_root = PurePosixPath(os.path.relpath(current_root, project_path))
        if rel_root == PurePosixPath("."):
            rel_root = PurePosixPath("")
        for dirname in list(dirnames):
            if dirname.endswith(".imageset"):
                name = dirname[:-9]
                contents = rel_root / dirname / "Contents.json"
                abs_contents = os.path.join(project_path, str(contents))
                discovered.setdefault(
                    ("image", name),
                    str(contents.as_posix()) if os.path.exists(abs_contents) else str((rel_root / dirname).as_posix()),
                )
            elif dirname.endswith(".colorset"):
                name = dirname[:-9]
                contents = rel_root / dirname / "Contents.json"
                abs_contents = os.path.join(project_path, str(contents))
                discovered.setdefault(
                    ("color", name),
                    str(contents.as_posix()) if os.path.exists(abs_contents) else str((rel_root / dirname).as_posix()),
                )
        for filename in filenames:
            if filename.endswith(".xib"):
                discovered.setdefault(("nib", PurePosixPath(filename).stem), str((rel_root / filename).as_posix()))
            elif filename.endswith(".storyboard"):
                discovered.setdefault(("storyboard", PurePosixPath(filename).stem), str((rel_root / filename).as_posix()))
    return discovered


def collect_xcode_target_edges(
    project_path: str,
    files: dict[str, str],
    resource_edges: list[tuple[str, str, str, str | None, str]],
) -> tuple[dict[str, dict[str, str]], list[tuple[str, str]], list[tuple[str, str, str]]]:
    targets, raw_memberships = parse_xcode_target_membership(project_path)
    if not targets or not raw_memberships:
        return targets, [], []

    file_edges: list[tuple[str, str]] = []
    resource_target_edges: list[tuple[str, str, str]] = []
    resource_by_path = {
        resource_path: (name, kind)
        for _, _, name, resource_path, kind in resource_edges
        if resource_path
    }
    resource_catalog = discover_apple_resources(project_path)
    for (kind, name), resource_path in resource_catalog.items():
        resource_by_path.setdefault(resource_path, (name, kind))
    for target_id, raw_path in raw_memberships:
        normalized = raw_path.replace("\\", "/").lstrip("./").strip('"')
        candidates = [normalized]
        project_file = targets.get(target_id, {}).get("project_file")
        if project_file:
            project_dir = PurePosixPath(project_file).parent
            if str(project_dir) not in {"", "."}:
                candidates.append(str((project_dir / normalized).as_posix()))
        target_path = next((candidate for candidate in candidates if candidate in files), None)
        target_file_id = files.get(target_path) if target_path else None
        if target_file_id:
            file_edges.append((target_id, target_file_id))
        resource_path = next((candidate for candidate in candidates if candidate in resource_by_path), None)
        resource_info = resource_by_path.get(resource_path or "")
        if resource_info:
            resource_target_edges.append((target_id, resource_info[0], resource_info[1]))
    return targets, list(dict.fromkeys(file_edges)), list(dict.fromkeys(resource_target_edges))


def collect_xcode_workspace_scheme_edges(
    project_path: str,
    files: dict[str, str],
    xcode_targets: dict[str, dict[str, str]],
) -> tuple[
    list[dict[str, str]],
    list[tuple[str, str]],
    list[dict[str, str]],
    list[tuple[str, str]],
    list[tuple[str, str]],
]:
    workspaces = parse_xcode_workspaces(project_path, files)
    schemes = parse_xcode_schemes(project_path, files, xcode_targets)
    workspace_rows = [
        {"workspace_path": workspace_path, "name": _workspace_display_name(workspace_path)}
        for workspace_path in sorted(workspaces.keys())
    ]
    workspace_project_edges = [
        (workspace_path, file_id)
        for workspace_path, project_files in workspaces.items()
        for file_id in sorted(project_files)
    ]
    scheme_rows = [
        {
            "scheme_path": scheme["scheme_path"],
            "name": scheme["name"],
            "container_path": scheme.get("container_path", ""),
        }
        for scheme in schemes
    ]
    scheme_target_edges = [
        (scheme["scheme_path"], target_id)
        for scheme in schemes
        for target_id in scheme["target_ids"]
    ]
    scheme_file_edges = [
        (scheme["scheme_path"], files[scheme["scheme_path"]])
        for scheme in schemes
        if scheme["scheme_path"] in files
    ]
    return workspace_rows, workspace_project_edges, scheme_rows, scheme_target_edges, scheme_file_edges


def _workspace_display_name(workspace_path: str) -> str:
    path = PurePosixPath(workspace_path)
    parent = path.parent
    if parent.name.endswith(".xcworkspace") and parent.parent.name.endswith(".xcodeproj"):
        return PurePosixPath(parent.parent.name).stem
    return parent.stem or path.stem


def parse_xcode_target_membership(project_path: str) -> tuple[dict[str, dict[str, str]], list[tuple[str, str]]]:
    project_files = sorted(PurePosixPath(path) for path in os.listdir(project_path) if path.endswith(".xcodeproj"))
    for root, dirnames, _ in os.walk(project_path):
        dirnames[:] = [dirname for dirname in dirnames if dirname not in {".git", ".build", "DerivedData", "node_modules"}]
        for dirname in dirnames:
            if dirname.endswith(".xcodeproj"):
                rel = os.path.relpath(os.path.join(root, dirname), project_path)
                project_files.append(PurePosixPath(rel))
    unique_projects = []
    seen_projects: set[str] = set()
    for rel in project_files:
        rel_str = rel.as_posix()
        if rel_str in seen_projects:
            continue
        seen_projects.add(rel_str)
        unique_projects.append(rel)

    targets: dict[str, dict[str, str]] = {}
    memberships: list[tuple[str, str]] = []
    for project_rel in unique_projects:
        pbxproj = os.path.join(project_path, project_rel.as_posix(), "project.pbxproj")
        if not os.path.exists(pbxproj):
            continue
        text = _read_text(pbxproj)
        if not text:
            continue
        targets.update(extract_xcode_targets(text, project_rel.as_posix()))
        memberships.extend(extract_xcode_resource_memberships(text))
        memberships.extend(
            extract_xcode_filesystem_synced_memberships(project_path, project_rel.as_posix(), text)
        )
    return targets, list(dict.fromkeys(memberships))


def extract_xcode_targets(text: str, project_file: str) -> dict[str, dict[str, str]]:
    targets: dict[str, dict[str, str]] = {}
    for target_id, name in re.findall(
        r"([A-F0-9]{8,}) /\* [^*]+ \*/ = \{\s*isa = PBXNativeTarget;.*?\bname = ([^;]+);",
        text,
        re.DOTALL,
    ):
        clean_name = name.strip().strip('"')
        if clean_name:
            targets[target_id] = {"name": clean_name, "project_file": project_file}
    return targets


def extract_xcode_resource_memberships(text: str) -> list[tuple[str, str]]:
    build_file_to_ref: dict[str, str] = {}
    file_ref_to_path: dict[str, str] = {}
    build_phase_to_files: dict[str, list[str]] = {}
    target_to_build_phases: dict[str, list[str]] = {}

    for build_file_id, file_ref_id in re.findall(
        r"([A-F0-9]{8,}) /\* [^*]+ \*/ = \{\s*isa = PBXBuildFile;\s*fileRef = ([A-F0-9]{8,})",
        text,
        re.DOTALL,
    ):
        build_file_to_ref[build_file_id] = file_ref_id

    for file_ref_id, path, source_tree in re.findall(
        r"([A-F0-9]{8,}) /\* [^*]+ \*/ = \{\s*isa = PBXFileReference;.*?\bpath = ([^;]+);.*?\bsourceTree = ([^;]+);",
        text,
        re.DOTALL,
    ):
        clean_path = path.strip().strip('"')
        clean_source = source_tree.strip().strip('"')
        if clean_path and clean_source != "BUILT_PRODUCTS_DIR":
            file_ref_to_path[file_ref_id] = clean_path

    for phase_id, files_blob in re.findall(
        r"([A-F0-9]{8,}) /\* Resources \*/ = \{\s*isa = PBXResourcesBuildPhase;.*?\bfiles = \((.*?)\);",
        text,
        re.DOTALL,
    ):
        build_phase_to_files[phase_id] = re.findall(r"([A-F0-9]{8,}) /\*", files_blob)

    for target_id, phases_blob in re.findall(
        r"([A-F0-9]{8,}) /\* [^*]+ \*/ = \{\s*isa = PBXNativeTarget;.*?\bbuildPhases = \((.*?)\);",
        text,
        re.DOTALL,
    ):
        target_to_build_phases[target_id] = re.findall(r"([A-F0-9]{8,}) /\*", phases_blob)

    memberships: list[tuple[str, str]] = []
    for target_id, phase_ids in target_to_build_phases.items():
        for phase_id in phase_ids:
            for build_file_id in build_phase_to_files.get(phase_id, []):
                file_ref_id = build_file_to_ref.get(build_file_id)
                file_path = file_ref_to_path.get(file_ref_id or "")
                if file_path:
                    memberships.append((target_id, file_path))
    return memberships


def extract_xcode_filesystem_synced_memberships(
    project_path: str,
    project_file: str,
    text: str,
) -> list[tuple[str, str]]:
    group_id_to_path: dict[str, str] = {}
    target_to_group_ids: dict[str, list[str]] = {}
    project_dir = PurePosixPath(project_file).parent

    for group_id, path in re.findall(
        r"([A-F0-9]{8,}) /\* [^*]+ \*/ = \{\s*isa = PBXFileSystemSynchronizedRootGroup;.*?\bpath = ([^;]+);",
        text,
        re.DOTALL,
    ):
        clean_path = path.strip().strip('"')
        if clean_path:
            group_id_to_path[group_id] = clean_path

    for target_id, groups_blob in re.findall(
        r"([A-F0-9]{8,}) /\* [^*]+ \*/ = \{\s*isa = PBXNativeTarget;.*?\bfileSystemSynchronizedGroups = \((.*?)\);",
        text,
        re.DOTALL,
    ):
        target_to_group_ids[target_id] = re.findall(r"([A-F0-9]{8,}) /\*", groups_blob)

    memberships: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for target_id, group_ids in target_to_group_ids.items():
        for group_id in group_ids:
            group_path = group_id_to_path.get(group_id)
            if not group_path:
                continue
            rel_root = (project_dir / group_path).as_posix().lstrip("./")
            abs_root = os.path.join(project_path, rel_root)
            if not os.path.isdir(abs_root):
                continue
            for current_root, dirnames, filenames in os.walk(abs_root):
                dirnames[:] = [dirname for dirname in dirnames if dirname not in {".git", ".build", "DerivedData", "node_modules"}]
                rel_current = os.path.relpath(current_root, project_path).replace("\\", "/").lstrip("./")
                for filename in filenames:
                    rel_path = f"{rel_current}/{filename}" if rel_current else filename
                    if not _is_indexable_apple_resource_path(rel_path):
                        continue
                    key = (target_id, rel_path)
                    if key in seen:
                        continue
                    seen.add(key)
                    memberships.append(key)
    return memberships


def _is_indexable_apple_resource_path(rel_path: str) -> bool:
    path = rel_path.lower()
    return path.endswith((".storyboard", ".xib", ".plist", ".xcworkspacedata", ".xcscheme", "project.pbxproj")) or "xcassets/" in path


def parse_xcode_workspaces(project_path: str, files: dict[str, str]) -> dict[str, set[str]]:
    workspaces: dict[str, set[str]] = {}
    workspace_files = [fp for fp in files if fp.endswith(".xcworkspace/contents.xcworkspacedata")]
    for workspace_path in workspace_files:
        text = _read_text(os.path.join(project_path, workspace_path))
        if not text:
            continue
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        workspace_dir = PurePosixPath(workspace_path).parent
        for file_ref in root.findall(".//FileRef"):
            location = file_ref.attrib.get("location", "")
            if location == "self:":
                project_file = f"{workspace_dir.parent.as_posix().rstrip('/')}/project.pbxproj".lstrip("./")
                file_id = files.get(project_file)
                if file_id:
                    workspaces.setdefault(workspace_path, set()).add(file_id)
                continue
            rel_ref = location.split(":", 1)[1] if ":" in location else location
            rel_ref = rel_ref.strip()
            if not rel_ref.endswith(".xcodeproj"):
                continue
            candidates = [
                rel_ref.lstrip("./"),
                str((workspace_dir / rel_ref).as_posix()).lstrip("./"),
                str((workspace_dir.parent / rel_ref).as_posix()).lstrip("./"),
            ]
            for candidate in candidates:
                project_file = f"{candidate.rstrip('/')}/project.pbxproj"
                file_id = files.get(project_file)
                if file_id:
                    workspaces.setdefault(workspace_path, set()).add(file_id)
                    break
    return workspaces


def parse_xcode_schemes(
    project_path: str,
    files: dict[str, str],
    xcode_targets: dict[str, dict[str, str]],
) -> list[dict[str, object]]:
    schemes: list[dict[str, object]] = []
    scheme_files = [fp for fp in files if fp.endswith(".xcscheme")]
    for scheme_path in scheme_files:
        text = _read_text(os.path.join(project_path, scheme_path))
        if not text:
            continue
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        target_ids: set[str] = set()
        container_path = ""
        for buildable in root.findall(".//BuildableReference"):
            blueprint_id = buildable.attrib.get("BlueprintIdentifier", "").strip()
            if blueprint_id in xcode_targets:
                target_ids.add(blueprint_id)
            container = buildable.attrib.get("ReferencedContainer", "").strip()
            if container and not container_path:
                container_path = normalize_scheme_container_path(scheme_path, container)
        schemes.append(
            {
                "scheme_path": scheme_path,
                "name": PurePosixPath(scheme_path).stem,
                "container_path": container_path,
                "target_ids": sorted(target_ids),
            }
        )
    return schemes


def normalize_scheme_container_path(scheme_path: str, container_ref: str) -> str:
    rel_ref = container_ref.split(":", 1)[1] if ":" in container_ref else container_ref
    rel_ref = rel_ref.strip().lstrip("./")
    if rel_ref.endswith(".xcodeproj"):
        return f"{rel_ref}/project.pbxproj"
    scheme_dir = PurePosixPath(scheme_path).parent
    candidate = (scheme_dir / rel_ref).as_posix().lstrip("./")
    if candidate.endswith(".xcodeproj"):
        return f"{candidate}/project.pbxproj"
    return candidate


async def write_resource_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    project_id: str,
    resource_edges: list[tuple[str, str, str, str | None, str]],
) -> None:
    if not resource_edges:
        return
    grouped: dict[str, list[tuple[str, str, str | None, str]]] = {}
    for src, rel, name, resource_path, resource_kind in resource_edges:
        grouped.setdefault(rel, []).append((src, name, resource_path, resource_kind))
    for rel_name, edges in grouped.items():
        query = f"""
        UNWIND $batch AS edge
        MATCH (a:File {{id: edge.src}})
        MERGE (res:Resource {{project_id: edge.project_id, name: edge.name, kind: edge.kind}})
        ON CREATE SET res.filepath = edge.name
        SET res.filepath = coalesce(edge.filepath, res.filepath)
        MERGE (a)-[:{rel_name}]->(res)
        """
        for i in range(0, len(edges), batch_size):
            batch = [
                {"src": s, "name": n, "filepath": p, "kind": k, "project_id": project_id}
                for s, n, p, k in edges[i : i + batch_size]
            ]
            async with write_semaphore:
                await execute_write(session, query, batch=batch, timeout=write_timeout_s)


async def write_resource_backing_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    project_id: str,
    resource_edges: list[tuple[str, str, str, str | None, str]],
) -> None:
    backing_rows = []
    seen: set[tuple[str, str, str]] = set()
    for _, _, name, resource_path, resource_kind in resource_edges:
        if not resource_path:
            continue
        key = (name, resource_kind, resource_path)
        if key in seen:
            continue
        seen.add(key)
        backing_rows.append({"name": name, "kind": resource_kind, "filepath": resource_path, "project_id": project_id})
    if not backing_rows:
        return
    query = """
    UNWIND $batch AS edge
    MATCH (res:Resource {project_id: edge.project_id, name: edge.name, kind: edge.kind})
    MATCH (f:File {project_id: edge.project_id, filepath: edge.filepath})
    MERGE (res)-[:BACKED_BY_FILE]->(f)
    """
    for i in range(0, len(backing_rows), batch_size):
        async with write_semaphore:
            await execute_write(session, query, batch=backing_rows[i : i + batch_size], timeout=write_timeout_s)


async def write_xcode_target_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    project_path: str,
    project_id: str,
    xcode_targets: dict[str, dict[str, str]],
    xcode_file_edges: list[tuple[str, str]],
    xcode_resource_edges: list[tuple[str, str, str]],
) -> None:
    if xcode_targets:
        query = """
        UNWIND $batch AS edge
        MERGE (t:XcodeTarget {project_id: edge.project_id, target_id: edge.target_id})
        SET t.name = edge.name, t.project_file = edge.project_file
        """
        rows = [
            {"project_id": project_id, "target_id": target_id, "name": meta["name"], "project_file": meta["project_file"]}
            for target_id, meta in xcode_targets.items()
        ]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)
    if xcode_file_edges:
        query = """
        UNWIND $batch AS edge
        MATCH (t:XcodeTarget {project_id: edge.project_id, target_id: edge.target_id})
        MATCH (f:File {id: edge.file_id})
        MERGE (t)-[:BUNDLES_FILE]->(f)
        """
        rows = [{"project_id": project_id, "target_id": t, "file_id": f} for t, f in xcode_file_edges]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)
    if xcode_resource_edges:
        query = """
        UNWIND $batch AS edge
        MERGE (r:Resource {project_id: edge.project_id, name: edge.name, kind: edge.kind})
        ON CREATE SET r.filepath = edge.filepath
        SET r.filepath = coalesce(edge.filepath, r.filepath)
        MATCH (t:XcodeTarget {project_id: edge.project_id, target_id: edge.target_id})
        OPTIONAL MATCH (f:File {project_id: edge.project_id, filepath: edge.filepath})
        FOREACH (_ IN CASE WHEN f IS NULL THEN [] ELSE [1] END | MERGE (r)-[:BACKED_BY_FILE]->(f))
        MERGE (r)-[:BUNDLED_IN_TARGET]->(t)
        """
        resource_catalog = discover_apple_resources(project_path)
        rows = [
            {
                "project_id": project_id,
                "target_id": t,
                "name": n,
                "kind": k,
                "filepath": resource_catalog.get((k, n)),
            }
            for t, n, k in xcode_resource_edges
        ]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)


async def write_xcode_workspace_scheme_edges(
    session,
    execute_write: ExecuteWrite,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    project_id: str,
    workspace_rows: list[dict[str, str]],
    workspace_project_edges: list[tuple[str, str]],
    scheme_rows: list[dict[str, str]],
    scheme_target_edges: list[tuple[str, str]],
    scheme_file_edges: list[tuple[str, str]],
) -> None:
    if workspace_rows:
        query = """
        UNWIND $batch AS edge
        MERGE (w:XcodeWorkspace {project_id: edge.project_id, filepath: edge.workspace_path})
        SET w.name = edge.name
        """
        rows = [{"project_id": project_id, **row} for row in workspace_rows]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)
    if workspace_project_edges:
        query = """
        UNWIND $batch AS edge
        MATCH (w:XcodeWorkspace {project_id: edge.project_id, filepath: edge.workspace_path})
        MATCH (f:File {id: edge.file_id})
        MERGE (w)-[:REFERENCES_PROJECT]->(f)
        """
        rows = [{"project_id": project_id, "workspace_path": w, "file_id": f} for w, f in workspace_project_edges]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)
    if scheme_rows:
        query = """
        UNWIND $batch AS edge
        MERGE (s:XcodeScheme {project_id: edge.project_id, filepath: edge.scheme_path})
        SET s.name = edge.name, s.container_path = edge.container_path
        """
        rows = [{"project_id": project_id, **row} for row in scheme_rows]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)
    if scheme_target_edges:
        query = """
        UNWIND $batch AS edge
        MATCH (s:XcodeScheme {project_id: edge.project_id, filepath: edge.scheme_path})
        MATCH (t:XcodeTarget {project_id: edge.project_id, target_id: edge.target_id})
        MERGE (s)-[:BUILDS_TARGET]->(t)
        """
        rows = [{"project_id": project_id, "scheme_path": s, "target_id": t} for s, t in scheme_target_edges]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)
    if scheme_file_edges:
        query = """
        UNWIND $batch AS edge
        MATCH (s:XcodeScheme {project_id: edge.project_id, filepath: edge.scheme_path})
        MATCH (f:File {id: edge.file_id})
        MERGE (s)-[:DEFINED_IN_FILE]->(f)
        """
        rows = [{"project_id": project_id, "scheme_path": s, "file_id": f} for s, f in scheme_file_edges]
        for i in range(0, len(rows), batch_size):
            async with write_semaphore:
                await execute_write(session, query, batch=rows[i : i + batch_size], timeout=write_timeout_s)
