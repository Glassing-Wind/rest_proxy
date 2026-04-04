"""tools/graph/import_graph.py — file-level IMPORTS graph build."""

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
RecordMetric = Callable[..., None]


async def build_import_graph(
    project_path: str,
    execute_read: ExecuteRead,
    execute_write: ExecuteWrite,
    debug_log: DebugLog,
    record_metric: RecordMetric,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
) -> str:
    """Build file-level IMPORTS graph edges in Neo4j."""
    try:
        debug_log("import_graph_start", project_path=project_path)
        start = time.perf_counter()

        project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
        driver = await graph_bootstrap.require_driver()

        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            r = await execute_read(
                session,
                session,
                "MATCH (f:File {project_id:$p}) RETURN f.filepath AS fp, f.id AS fid",
                p=project_id,
                op="build_import_graph_files",
            )
            files: dict[str, str] = {}
            for rec in r:
                files[rec["fp"]] = rec["fid"]

            stems: dict[str, list[str]] = {}
            for fp in files:
                stem = PurePosixPath(fp).stem
                stems.setdefault(stem, []).append(fp)

            r2 = await execute_read(
                session,
                session,
                "MATCH (f:File {project_id:$p})-[:CONTAINS]->(imp:Import) "
                "RETURN f.id AS src_fid, f.filepath AS src_fp, imp.source AS src_text",
                p=project_id,
                op="build_import_graph_imports",
            )
            imports = []
            for rec in r2:
                imports.append((rec["src_fid"], rec["src_fp"], rec["src_text"] or ""))

        # Build Swift SPM module → file mapping
        swift_module_map: dict[str, list[str]] = {}
        for fp in files:
            parts = fp.replace("\\", "/").split("/")
            if "Sources" in parts:
                src_idx = parts.index("Sources")
                if src_idx > 0:
                    mod_name = parts[src_idx - 1]
                    swift_module_map.setdefault(mod_name, []).append(fp)

        def resolve(src_fp: str, src_text: str) -> str | None:
            ext = PurePosixPath(src_fp).suffix.lstrip(".")
            src_dir = str(PurePosixPath(src_fp).parent)
            if ext in ("py", ""):
                m = re.match(r"from\s+([\w.]+)\s+import", src_text) or re.match(
                    r"import\s+([\w.]+)", src_text
                )
                if m:
                    mod = m.group(1).split(".")[0]
                    for fp in [
                        f"{src_dir}/{mod}.py",
                        f"{mod}.py",
                        f"{src_dir}/{mod}/__init__.py",
                        f"{mod}/__init__.py",
                    ]:
                        fp = fp.lstrip("./")
                        if fp in files:
                            return fp
                    if mod in stems and len(stems[mod]) == 1:
                        return stems[mod][0]
            elif ext in ("js", "ts", "jsx", "tsx", "mjs", "cjs"):
                m = re.search(r"from\s+[\x27\x22]([^\x27\x22]+)[\x27\x22]", src_text)
                if m:
                    imp = m.group(1)
                    if imp.startswith("."):
                        import posixpath

                        base = posixpath.normpath(posixpath.join(src_dir, imp)).lstrip(
                            "/"
                        )
                        for suf in (
                            "",
                            ".js",
                            ".ts",
                            ".jsx",
                            ".tsx",
                            "/index.js",
                            "/index.ts",
                        ):
                            candidate = (base + suf).lstrip("/")
                            if candidate in files:
                                return candidate
            elif ext == "rs":
                m = re.match(r"(?:use|mod)\s+(?:crate::)?([\w:]+)", src_text)
                if m:
                    mod = m.group(1).split("::")[0]
                    for fp in [
                        f"{src_dir}/{mod}.rs",
                        f"{src_dir}/{mod}/mod.rs",
                        f"src/{mod}.rs",
                        f"src/{mod}/mod.rs",
                    ]:
                        fp = fp.lstrip("./")
                        if fp in files:
                            return fp
            elif ext == "swift":
                m = re.match(r"import\s+(\w+)", src_text)
                if m:
                    mod = m.group(1)
                    candidates = swift_module_map.get(mod, [])
                    if candidates:
                        return candidates[0]
            return None

        edges: list[tuple[str, str]] = []
        for src_fid, src_fp, src_text in imports:
            tgt_fp = resolve(src_fp, src_text)
            if tgt_fp and tgt_fp != src_fp:
                edges.append((src_fid, files[tgt_fp]))

        # Swift on-disk supplement
        SWIFT_IMPORT_RE = re.compile(
            r"""^(?:@testable\s+)?import\s+(\w+)""", re.MULTILINE
        )
        SYSTEM_MODS = frozenset(
            {
                "Foundation",
                "Swift",
                "Dispatch",
                "Darwin",
                "Combine",
                "UIKit",
                "AppKit",
                "SwiftUI",
                "XCTest",
                "os",
                "simd",
                "CoreFoundation",
                "ObjectiveC",
                "CoreGraphics",
                "QuartzCore",
                "Metal",
                "MetalKit",
                "MetalPerformanceShaders",
                "Accelerate",
                "CoreML",
                "CreateML",
                "Vision",
                "NaturalLanguage",
                "AVFoundation",
                "CoreVideo",
                "CoreImage",
                "CoreData",
                "SystemConfiguration",
                "Network",
                "Logging",
                "NIO",
                "NIOHTTP1",
                "NIOHTTP2",
                "NIOSSL",
                "GRPC",
                "SwiftProtobuf",
                "Atomics",
                "ConcurrencyExtras",
                "ArgumentParser",
            }
        )
        swift_files = {fp: fid for fp, fid in files.items() if fp.endswith(".swift")}

        def _scan_swift_import_edges() -> list[tuple[str, str]]:
            swift_edges: list[tuple[str, str]] = []
            for src_fp, src_fid in swift_files.items():
                abs_src = (
                    os.path.join(project_path, src_fp)
                    if not os.path.isabs(src_fp)
                    else src_fp
                )
                try:
                    content = open(
                        abs_src, "r", encoding="utf-8", errors="ignore"
                    ).read()
                except OSError:
                    continue
                for m in SWIFT_IMPORT_RE.finditer(content):
                    mod = m.group(1)
                    if mod in SYSTEM_MODS:
                        continue
                    candidates = swift_module_map.get(mod, [])
                    for tgt_fp in candidates:
                        if tgt_fp != src_fp and tgt_fp in files:
                            swift_edges.append((src_fid, files[tgt_fp]))
            return swift_edges

        if swift_files:
            edges.extend(await asyncio.to_thread(_scan_swift_import_edges))

        if not edges:
            return "No resolvable imports found — IMPORTS graph not built."

        edges = list(set(edges))
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            await execute_write(
                session,
                "MATCH (a:File {project_id:$p})-[r:IMPORTS]->() DELETE r",
                p=project_id,
                timeout=write_timeout_s,
            )
            for i in range(0, len(edges), batch_size):
                batch = [{"src": s, "tgt": t} for s, t in edges[i : i + batch_size]]
                t0 = time.perf_counter()
                async with write_semaphore:
                    await execute_write(
                        session,
                        """
                        UNWIND $batch AS edge
                        MATCH (a:File {id: edge.src})
                        MATCH (b:File {id: edge.tgt})
                        MERGE (a)-[:IMPORTS]->(b)
                    """,
                        batch=batch,
                        timeout=write_timeout_s,
                    )
                debug_log(
                    "import_graph_batch",
                    project_id=project_id,
                    batch=i // batch_size,
                    batch_size=len(batch),
                    elapsed_ms=int((time.perf_counter() - t0) * 1000),
                )
                record_metric(
                    "import_graph_batch",
                    project_id=project_id,
                    batch=i // batch_size,
                    batch_size=len(batch),
                    elapsed_ms=int((time.perf_counter() - t0) * 1000),
                )
                await asyncio.sleep(0)

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        debug_log(
            "import_graph_done",
            project_path=project_path,
            imports=len(imports),
            edges=len(edges),
            elapsed_ms=elapsed_ms,
        )
        record_metric(
            "import_graph_done",
            project_path=project_path,
            imports=len(imports),
            edges=len(edges),
            elapsed_ms=elapsed_ms,
        )
        return (
            f"IMPORTS graph built for {project_path.split('/')[-1]}:\n"
            f"  {len(imports)} import statements scanned\n"
            f"  {len(edges)} resolved IMPORTS edges written to Neo4j\n"
            f"\nPageRank and community detection are now meaningful.\n"
            f"Re-run get_code_importance() and get_code_communities() for upgraded results."
        )
    except Exception as e:
        return f"Error building import graph: {str(e)}"
