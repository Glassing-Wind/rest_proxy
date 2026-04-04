"""tools/graph/asset_graph.py — asset linkage graph build."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from pathlib import PurePosixPath
from typing import Awaitable, Callable

import graph_bootstrap


ExecuteRead = Callable[..., Awaitable[list[dict[str, object]]]]
ExecuteWrite = Callable[..., Awaitable[None]]
DebugLog = Callable[..., None]


async def build_asset_graph(
    project_path: str,
    execute_read: ExecuteRead,
    execute_write: ExecuteWrite,
    debug_log: DebugLog,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
) -> str:
    """Build asset linkage edges (HTML → assets, JS/TS → API spec/routes)."""
    try:
        debug_log("asset_graph_start", project_path=project_path)
        start = time.perf_counter()
        import posixpath

        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        driver = await graph_bootstrap.require_driver()

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            r = await execute_read(
                session,
                session,
                "MATCH (f:File {project_id:$p}) RETURN f.filepath AS fp, f.id AS fid",
                p=project_id,
                op="build_asset_graph_files",
            )
            files: dict[str, str] = {}
            for rec in r:
                files[rec["fp"]] = rec["fid"]

        if not files:
            return "No files found for asset graph."

        html_files = [
            (fp, fid) for fp, fid in files.items() if fp.endswith((".html", ".astro"))
        ]
        script_files = [
            (fp, fid)
            for fp, fid in files.items()
            if fp.endswith((".js", ".ts", ".tsx", ".astro"))
        ]

        api_target_paths: list[str] = []
        if "src/api/openapi.yaml" in files:
            api_target_paths.append("src/api/openapi.yaml")
        if "src/api/routes.ts" in files:
            api_target_paths.append("src/api/routes.ts")

        for fp in files:
            if fp.startswith("src/pages/api/") or fp.startswith("pages/api/"):
                if fp.endswith((".ts", ".js", ".tsx", ".jsx")):
                    api_target_paths.append(fp)
            if fp.startswith("src/app/api/") or fp.startswith("app/api/"):
                if fp.endswith(("route.ts", "route.js", "route.tsx", "route.jsx")):
                    api_target_paths.append(fp)
            if fp.startswith("src/app/") or fp.startswith("app/"):
                if fp.endswith(("route.ts", "route.js", "route.tsx", "route.jsx")):
                    api_target_paths.append(fp)

        for fp in files:
            if fp.startswith("src/api/") and fp.endswith((".ts", ".js", ".tsx")):
                api_target_paths.append(fp)

        api_targets = [files[p] for p in api_target_paths if p in files]
        api_targets = list(dict.fromkeys(api_targets))

        def _route_path_from_file(fp: str) -> str | None:
            path = PurePosixPath(fp)
            parts = path.parts
            if len(parts) < 2:
                return None
            if parts[0] == "src":
                parts = parts[1:]
            if not parts:
                return None
            if parts[0] == "app" and path.name.startswith("route."):
                route_parts = parts[1:-1]
                if not route_parts:
                    return "/"
                return "/" + "/".join(route_parts)
            if parts[0] == "pages" and len(parts) > 1 and parts[1] == "api":
                rel = parts[2:]
                if not rel:
                    return "/api"
                file_stem = PurePosixPath(*rel).stem
                if file_stem == "index":
                    rel = rel[:-1]
                else:
                    rel = rel[:-1] + (file_stem,)
                if not rel:
                    return "/api"
                return "/api/" + "/".join(rel)
            if parts[0] == "api":
                rel = parts[1:]
                if not rel:
                    return "/api"
                file_stem = PurePosixPath(*rel).stem
                if file_stem == "index":
                    rel = rel[:-1]
                else:
                    rel = rel[:-1] + (file_stem,)
                if not rel:
                    return "/api"
                return "/api/" + "/".join(rel)
            return None

        route_targets: dict[str, str] = {}
        for fp, fid in files.items():
            route_path = _route_path_from_file(fp)
            if route_path:
                route_targets.setdefault(route_path, fid)

        def _read_text(abs_path: str) -> str:
            try:
                if os.path.getsize(abs_path) > 1_000_000:
                    return ""
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                    return fh.read()
            except OSError:
                return ""

        def _resolve_href(src_fp: str, raw: str) -> str | None:
            raw = raw.split("#", 1)[0].split("?", 1)[0].strip()
            if not raw:
                return None
            if raw.startswith(("http://", "https://", "//", "data:", "mailto:")):
                return None
            if raw.startswith("/"):
                candidate = raw.lstrip("/")
                if candidate.startswith("assets/"):
                    candidate = posixpath.join("src/public", candidate)
                else:
                    public_candidate = posixpath.join("src/public", candidate)
                    if public_candidate in files:
                        candidate = public_candidate
            else:
                src_dir = str(PurePosixPath(src_fp).parent)
                candidate = posixpath.normpath(posixpath.join(src_dir, raw)).lstrip("/")
                if candidate.startswith("../"):
                    return None
            return candidate if candidate in files else None

        html_edges: list[tuple[str, str]] = []
        for fp, fid in html_files:
            abs_path = os.path.join(project_path, fp)
            content = _read_text(abs_path)
            if not content:
                continue
            for m in re.finditer(r"<script[^>]+src=[\"']([^\"']+)[\"']", content, re.I):
                target = _resolve_href(fp, m.group(1))
                if target and files[target] != fid:
                    html_edges.append((fid, files[target]))
            for m in re.finditer(r"<link[^>]+href=[\"']([^\"']+)[\"']", content, re.I):
                href = m.group(1)
                if not href.lower().endswith(".css"):
                    continue
                target = _resolve_href(fp, href)
                if target and files[target] != fid:
                    html_edges.append((fid, files[target]))

        api_edges: list[tuple[str, str]] = []
        if api_targets or route_targets:
            api_re = re.compile(r"[\"'](/api/[^\"']+)[\"']")
            route_re = re.compile(r"[\"'](/[^\"']+)[\"']")
            client_re = re.compile(r"\b(fetch|axios|ky|ofetch)\b")
            for fp, fid in script_files:
                abs_path = os.path.join(project_path, fp)
                content = _read_text(abs_path)
                if not content:
                    continue
                if not api_re.search(content) and not client_re.search(content):
                    continue
                matched_targets: set[str] = set()
                for literal in route_re.findall(content):
                    cleaned = literal.split("?", 1)[0].split("#", 1)[0]
                    if cleaned in route_targets:
                        matched_targets.add(route_targets[cleaned])
                    elif cleaned.startswith("/api/") and api_targets:
                        matched_targets.update(api_targets)
                if not matched_targets and api_re.search(content):
                    matched_targets.update(api_targets)
                for tgt in matched_targets:
                    if tgt != fid:
                        api_edges.append((fid, tgt))

        service_edges: list[tuple[str, str]] = []
        service_files = {
            os.path.splitext(os.path.basename(fp))[0]: fid
            for fp, fid in files.items()
            if fp.startswith("src/services/") and fp.endswith((".ts", ".js"))
        }
        if service_files:
            backend_files = [
                (fp, fid)
                for fp, fid in files.items()
                if (
                    fp.startswith("src/api/")
                    or fp.startswith("src/webhooks/")
                    or fp.startswith("src/jobs/")
                    or fp.startswith("src/pages/api/")
                    or fp.startswith("pages/api/")
                    or fp.startswith("src/app/api/")
                    or fp.startswith("app/api/")
                )
                and fp.endswith((".ts", ".js"))
            ]
            for fp, fid in backend_files:
                abs_path = os.path.join(project_path, fp)
                content = _read_text(abs_path)
                if not content:
                    continue
                for name, svc_fid in service_files.items():
                    if name in {"index", "types"}:
                        continue
                    if re.search(rf"\b{name}\b", content):
                        service_edges.append((fid, svc_fid))

        db_edges: list[tuple[str, str]] = []
        schema_fp = "prisma/schema.prisma"
        schema_fid = files.get(schema_fp)
        if schema_fid:
            schema_path = os.path.join(project_path, schema_fp)
            schema_text = _read_text(schema_path)
            models = set(re.findall(r"\bmodel\s+(\w+)\s+\{", schema_text))
            if models:
                delegates = set(models)
                for name in list(models):
                    if name:
                        delegates.add(name[0].lower() + name[1:])
                prisma_re = re.compile(
                    r"\b(?:this\.)?prisma\.([A-Za-z_][A-Za-z0-9_]*)\b"
                )
                scan_files = [
                    (fp, fid)
                    for fp, fid in files.items()
                    if fp.startswith("src/") and fp.endswith((".ts", ".js"))
                ]
                for fp, fid in scan_files:
                    abs_path = os.path.join(project_path, fp)
                    content = _read_text(abs_path)
                    if not content:
                        continue
                    for match in prisma_re.findall(content):
                        if match in delegates:
                            db_edges.append((fid, schema_fid))
                            break

        html_edges = list(set(html_edges))
        api_edges = list(set(api_edges))
        service_edges = list(set(service_edges))
        db_edges = list(set(db_edges))
        if not html_edges and not api_edges and not service_edges and not db_edges:
            return "No asset edges resolved."

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:ASSET_LINKS]->() DELETE r",
                p=project_id,
                timeout=write_timeout_s,
            )
            await execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:CALLS_API]->() DELETE r",
                p=project_id,
                timeout=write_timeout_s,
            )
            await execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:CALLS_SERVICE]->() DELETE r",
                p=project_id,
                timeout=write_timeout_s,
            )
            await execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:CALLS_DB]->() DELETE r",
                p=project_id,
                timeout=write_timeout_s,
            )
            if html_edges:
                for i in range(0, len(html_edges), batch_size):
                    batch = [
                        {"src": s, "tgt": t} for s, t in html_edges[i : i + batch_size]
                    ]
                    async with write_semaphore:
                        await execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (a:File {id: edge.src})
                            MATCH (b:File {id: edge.tgt})
                            MERGE (a)-[:ASSET_LINKS]->(b)
                            """,
                            batch=batch,
                            timeout=write_timeout_s,
                        )
            if api_edges:
                for i in range(0, len(api_edges), batch_size):
                    batch = [
                        {"src": s, "tgt": t} for s, t in api_edges[i : i + batch_size]
                    ]
                    async with write_semaphore:
                        await execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (a:File {id: edge.src})
                            MATCH (b:File {id: edge.tgt})
                            MERGE (a)-[:CALLS_API]->(b)
                            """,
                            batch=batch,
                            timeout=write_timeout_s,
                        )

            if service_edges:
                for i in range(0, len(service_edges), batch_size):
                    batch = [
                        {"src": s, "tgt": t}
                        for s, t in service_edges[i : i + batch_size]
                    ]
                    async with write_semaphore:
                        await execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (a:File {id: edge.src})
                            MATCH (b:File {id: edge.tgt})
                            MERGE (a)-[:CALLS_SERVICE]->(b)
                            """,
                            batch=batch,
                            timeout=write_timeout_s,
                        )

            if db_edges:
                for i in range(0, len(db_edges), batch_size):
                    batch = [
                        {"src": s, "tgt": t} for s, t in db_edges[i : i + batch_size]
                    ]
                    async with write_semaphore:
                        await execute_write(
                            session,
                            """
                            UNWIND $batch AS edge
                            MATCH (a:File {id: edge.src})
                            MATCH (b:File {id: edge.tgt})
                            MERGE (a)-[:CALLS_DB]->(b)
                            """,
                            batch=batch,
                            timeout=write_timeout_s,
                        )

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        debug_log(
            "asset_graph_done",
            project_path=project_path,
            elapsed_ms=elapsed_ms,
            asset_links=len(html_edges),
            api_links=len(api_edges),
            service_links=len(service_edges),
            db_links=len(db_edges),
        )
        return (
            f"ASSET graph built for {project_path.split('/')[-1]}:\n"
            f"  {len(html_edges)} ASSET_LINKS edges\n"
            f"  {len(api_edges)} CALLS_API edges\n"
            f"  {len(service_edges)} CALLS_SERVICE edges\n"
            f"  {len(db_edges)} CALLS_DB edges\n"
            f"  elapsed={elapsed_ms}ms"
        )
    except Exception as exc:
        debug_log("asset_graph_error", project_path=project_path, error=str(exc))
        return f"Error building asset graph: {str(exc)}"
