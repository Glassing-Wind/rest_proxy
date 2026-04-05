"""tools/graph/symbol_graph.py — symbol-level import/export graph build."""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Awaitable, Callable

import graph_bootstrap
from _helpers import get_memory_modules, get_project_id
from .core import _SYMBOL_FILTER_CYPHER


ExecuteRead = Callable[..., Awaitable[list[dict[str, object]]]]
ExecuteWrite = Callable[..., Awaitable[None]]
DebugLog = Callable[..., None]
RecordMetric = Callable[..., None]


async def build_symbol_graph(
    project_path: str,
    execute_read: ExecuteRead,
    execute_write: ExecuteWrite,
    debug_log: DebugLog,
    record_metric: RecordMetric,
    write_semaphore: asyncio.Semaphore,
    batch_size: int,
    write_timeout_s: float,
    symbol_graph_lock,
) -> str:
    """Build symbol-level IMPORTS/EXPORTS edges using Import nodes and chunk metadata."""
    try:
        debug_log("symbol_graph_start", project_path=project_path)
        start = time.perf_counter()

        project_id = get_project_id(project_path)
        driver = await graph_bootstrap.require_driver()

        memory_store, _, _, _, _ = get_memory_modules()

        await memory_store.open_pool()
        pg_available = memory_store._pg_pool_available()

        # Collect symbols by file
        symbols_by_file: dict[str, dict[str, str]] = {}
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            r = await execute_read(
                session,
                """
                MATCH (f:File {{project_id:$p}})-[:CONTAINS]->(s)
                WHERE {filters}
                RETURN f.filepath AS fp, s.name AS name, s.id AS sid
                """.format(filters=_SYMBOL_FILTER_CYPHER),
                p=project_id,
                op="build_symbol_graph_symbols",
            )
            for rec in r:
                fp = rec["fp"]
                name = rec["name"]
                sid = rec["sid"]
                if not fp or not name or not sid:
                    continue
                symbols_by_file.setdefault(fp, {})[name] = sid

        # Load file exports from Postgres metadata
        exports_by_file: dict[str, list[str]] = {}
        if pg_available:
            async with memory_store._pg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT file_path, metadata->'file_exports' AS exports
                        FROM codebase_embeddings
                        WHERE project_id = %s AND metadata ? 'file_exports'
                        """,
                        (project_id,),
                    )
                    async for row in cur:
                        fp = row[0]
                        exports = row[1] or []
                        names: list[str] = []
                        for item in exports or []:
                            if isinstance(item, dict) and item.get("name"):
                                names.append(item.get("name"))
                            elif isinstance(item, str):
                                names.append(item)
                        if names:
                            exports_by_file[fp] = list(dict.fromkeys(names))

        # Fallback: if no exports metadata, treat defined symbols as exports.
        if not exports_by_file:
            for fp, symbols in symbols_by_file.items():
                if symbols:
                    exports_by_file[fp] = list(symbols.keys())

        # Ensure tool registration functions are treated as exports.
        for fp, symbols in symbols_by_file.items():
            if not fp.startswith("tools/"):
                continue
            names = exports_by_file.get(fp, [])
            if "register" in symbols and "register" not in names:
                names.append("register")
            if fp.endswith("tools/__init__.py") and "register_all" in symbols:
                if "register_all" not in names:
                    names.append("register_all")
            if names:
                exports_by_file[fp] = names

        swift_module_map: dict[str, list[str]] = {}
        for fp in symbols_by_file:
            parts = fp.replace("\\", "/").split("/")
            if "Sources" in parts:
                src_idx = parts.index("Sources")
                if src_idx > 0:
                    mod_name = parts[src_idx - 1]
                    swift_module_map.setdefault(mod_name, []).append(fp)

        def _parse_imported_names(ext: str, src_text: str) -> list[str]:
            src_text = src_text.strip()
            names: list[str] = []

            def _clean(name: str) -> str:
                name = name.strip()
                if name.startswith("type "):
                    name = name[5:].strip()
                if name.startswith("typeof "):
                    name = name[7:].strip()
                return name

            if ext == "py":
                m = re.match(r"from\s+[\w.]+\s+import\s+(.+)", src_text)
                if m:
                    block = m.group(1).strip()
                    block = block.strip("()")
                    for part in block.split(","):
                        name = _clean(part)
                        if not name or name == "*":
                            continue
                        if " as " in name:
                            name = name.split(" as ", 1)[0].strip()
                        names.append(name)
            elif ext in ("js", "ts", "jsx", "tsx", "mjs", "cjs"):
                # import Default from 'x'
                m_default = re.match(r"import\s+([\w$]+)\s+from\s+['\"]", src_text)
                if m_default:
                    names.append(m_default.group(1))
                # import * as Name from 'x'
                m_star = re.match(
                    r"import\s+\*\s+as\s+([\w$]+)\s+from\s+['\"]", src_text
                )
                if m_star:
                    names.append(m_star.group(1))
                # import Default, {a as b, c} from 'x'
                m_both = re.match(r"import\s+([\w$]+)\s*,\s*\{([^}]+)\}", src_text)
                if m_both:
                    names.append(m_both.group(1))
                    block = m_both.group(2)
                    for part in block.split(","):
                        name = _clean(part)
                        if not name:
                            continue
                        if " as " in name:
                            name = name.split(" as ", 1)[0].strip()
                        names.append(name)
                # import {a as b, c} from 'x'
                m = re.search(r"import\s+\{([^}]+)\}", src_text)
                if m:
                    block = m.group(1)
                    for part in block.split(","):
                        name = _clean(part)
                        if not name:
                            continue
                        if " as " in name:
                            name = name.split(" as ", 1)[0].strip()
                        names.append(name)
            elif ext == "swift":
                m = re.match(r"^(?:@testable\s+)?import\s+(\w+)", src_text)
                if m:
                    names.append(m.group(1))
            elif ext == "rs":
                m = re.match(r"^(?:pub\s+)?use\s+(.+)$", src_text)
                if m:
                    body = m.group(1).strip().rstrip(";")

                    def _add_name(path: str) -> None:
                        path = path.strip()
                        if not path:
                            return
                        for prefix in ("crate::", "self::", "super::"):
                            if path.startswith(prefix):
                                path = path[len(prefix) :]
                        if path.endswith("::*"):
                            return
                        name = path.split("::")[-1].strip()
                        if " as " in name:
                            name = name.split(" as ", 1)[0].strip()
                        if not name or name in {"self", "super", "crate"}:
                            return
                        names.append(name)

                    if "{" in body and "}" in body:
                        prefix, rest = body.split("{", 1)
                        prefix = prefix.strip()
                        rest = rest.split("}", 1)[0]
                        for part in rest.split(","):
                            part = part.strip()
                            if not part or part in {"self", "super", "crate"}:
                                continue
                            full = part
                            if prefix:
                                if prefix.endswith("::"):
                                    full = f"{prefix}{part}"
                                else:
                                    full = f"{prefix}::{part}"
                            _add_name(full)
                    else:
                        _add_name(body)
            return list(dict.fromkeys(names))

        # Reuse Import nodes to resolve file edges and symbol edges
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            r2 = await execute_read(
                session,
                """
                MATCH (f:File {project_id:$p})-[:CONTAINS]->(imp:Import)
                RETURN f.id AS src_fid, f.filepath AS src_fp, imp.source AS src_text
                """,
                p=project_id,
                op="build_symbol_graph_imports",
            )
            imports = []
            for rec in r2:
                imports.append((rec["src_fid"], rec["src_fp"], rec["src_text"] or ""))

        # Resolve file imports using same logic as build_import_graph
        files: dict[str, str] = {}
        async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
            rfiles = await execute_read(
                session,
                session,
                "MATCH (f:File {project_id:$p}) RETURN f.filepath AS fp, f.id AS fid",
                p=project_id,
                op="build_symbol_graph_files",
            )
            for rec in rfiles:
                files[rec["fp"]] = rec["fid"]

        stems: dict[str, list[str]] = {}
        for fp in files:
            stem = os.path.splitext(os.path.basename(fp))[0]
            stems.setdefault(stem, []).append(fp)

        def resolve(src_fp: str, src_text: str) -> str | None:
            ext = os.path.splitext(src_fp)[1].lstrip(".")
            src_dir = os.path.dirname(src_fp)
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
                    if imp.startswith("./") or imp.startswith("../"):
                        import posixpath

                        base = posixpath.normpath(posixpath.join(src_dir, imp)).lstrip(
                            "/"
                        )
                    elif imp.startswith("@/") or imp.startswith("~/"):
                        base = imp[2:]
                    elif imp.startswith("src/"):
                        base = imp
                    else:
                        base = None
                    if base:
                        for suf in (
                            "",
                            ".js",
                            ".ts",
                            ".jsx",
                            ".tsx",
                            "/index.js",
                            "/index.ts",
                            "/index.jsx",
                            "/index.tsx",
                        ):
                            candidate = (base + suf).lstrip("/")
                            if candidate in files:
                                return candidate
            elif ext == "rs":
                m = re.match(r"(?:pub\s+)?(?:use|mod)\s+(?:crate::)?([\w:]+)", src_text)
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
            return None

        SWIFT_IMPORT_RE = re.compile(r"^(?:@testable\s+)?import\s+(\w+)", re.MULTILINE)
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

        def _build_symbol_edges() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
            import_edges_local: set[tuple[str, str]] = set()
            for src_fid, src_fp, src_text in imports:
                tgt_fp = resolve(src_fp, src_text)
                ext = os.path.splitext(src_fp)[1].lstrip(".")
                names = _parse_imported_names(ext, src_text)
                if not names:
                    continue
                if ext == "swift":
                    for mod in names:
                        for fp in swift_module_map.get(mod, []):
                            if fp == src_fp:
                                continue
                            target_symbols = symbols_by_file.get(fp, {})
                            for sid in target_symbols.values():
                                import_edges_local.add((src_fid, sid))
                    continue
                if not tgt_fp or tgt_fp == src_fp:
                    if not tgt_fp and names:
                        mod_stem = os.path.basename(src_text)
                        m_path = re.search(
                            r"from\s+[\x27\x22]([^\x27\x22]+)[\x27\x22]",
                            src_text,
                        )
                        if m_path:
                            mod_stem = os.path.basename(m_path.group(1))
                        if mod_stem in stems and len(stems[mod_stem]) == 1:
                            tgt_fp = stems[mod_stem][0]
                        elif mod_stem:
                            matches = stems.get(mod_stem, [])
                            if matches:
                                tgt_fp = matches[0]
                    if not tgt_fp or tgt_fp == src_fp:
                        continue
                target_symbols = symbols_by_file.get(tgt_fp, {})
                for name in names:
                    sid = target_symbols.get(name)
                    if sid:
                        import_edges_local.add((src_fid, sid))

            export_edges_local: set[tuple[str, str]] = set()
            for fp, names in exports_by_file.items():
                fid = files.get(fp)
                if not fid:
                    continue
                symbols = symbols_by_file.get(fp, {})
                for name in names:
                    sid = symbols.get(name)
                    if sid:
                        export_edges_local.add((fid, sid))

            return import_edges_local, export_edges_local

        import_edges, export_edges = await asyncio.to_thread(_build_symbol_edges)

        if not import_edges and not export_edges:
            return "No symbol import/export edges resolved."

        async def _write_edges() -> None:
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                if import_edges:
                    await execute_write(
                        session,
                        "MATCH (a:File {project_id:$p})-[r:IMPORTS_SYMBOL]->() DELETE r",
                        p=project_id,
                        timeout=write_timeout_s,
                    )
                    edges = list(import_edges)
                    for i in range(0, len(edges), batch_size):
                        batch = [
                            {"src": s, "tgt": t} for s, t in edges[i : i + batch_size]
                        ]
                        t0 = time.perf_counter()
                        async with write_semaphore:
                            await execute_write(
                                session,
                                """
                                UNWIND $batch AS edge
                                MATCH (a:File {id: edge.src})
                                MATCH (b {id: edge.tgt})
                                MERGE (a)-[:IMPORTS_SYMBOL]->(b)
                                """,
                                batch=batch,
                                timeout=write_timeout_s,
                            )
                        debug_log(
                            "symbol_import_batch",
                            project_id=project_id,
                            batch=i // batch_size,
                            batch_size=len(batch),
                            elapsed_ms=int((time.perf_counter() - t0) * 1000),
                        )
                        record_metric(
                            "symbol_import_batch",
                            project_id=project_id,
                            batch=i // batch_size,
                            batch_size=len(batch),
                            elapsed_ms=int((time.perf_counter() - t0) * 1000),
                        )
                        await asyncio.sleep(0)
                if export_edges:
                    await execute_write(
                        session,
                        "MATCH (a:File {project_id:$p})-[r:EXPORTS_SYMBOL]->() DELETE r",
                        p=project_id,
                        timeout=write_timeout_s,
                    )
                    edges = list(export_edges)
                    for i in range(0, len(edges), batch_size):
                        batch = [
                            {"src": s, "tgt": t} for s, t in edges[i : i + batch_size]
                        ]
                        t0 = time.perf_counter()
                        async with write_semaphore:
                            await execute_write(
                                session,
                                """
                                UNWIND $batch AS edge
                                MATCH (a:File {id: edge.src})
                                MATCH (b {id: edge.tgt})
                                MERGE (a)-[:EXPORTS_SYMBOL]->(b)
                                """,
                                batch=batch,
                                timeout=write_timeout_s,
                            )
                        debug_log(
                            "symbol_export_batch",
                            project_id=project_id,
                            batch=i // batch_size,
                            batch_size=len(batch),
                            elapsed_ms=int((time.perf_counter() - t0) * 1000),
                        )
                        record_metric(
                            "symbol_export_batch",
                            project_id=project_id,
                            batch=i // batch_size,
                            batch_size=len(batch),
                            elapsed_ms=int((time.perf_counter() - t0) * 1000),
                        )
                        await asyncio.sleep(0)

        # Serialize graph writes to avoid deadlocks with concurrent jobs.
        with symbol_graph_lock:
            for attempt in range(3):
                try:
                    await _write_edges()
                    break
                except Exception as e:
                    if "DeadlockDetected" in str(e) and attempt < 2:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    raise

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        debug_log(
            "symbol_graph_done",
            project_path=project_path,
            import_edges=len(import_edges),
            export_edges=len(export_edges),
            elapsed_ms=elapsed_ms,
        )
        record_metric(
            "symbol_graph_done",
            project_path=project_path,
            import_edges=len(import_edges),
            export_edges=len(export_edges),
            elapsed_ms=elapsed_ms,
        )
        return (
            "Symbol edges built:\n"
            f"  IMPORTS_SYMBOL: {len(import_edges)}\n"
            f"  EXPORTS_SYMBOL: {len(export_edges)}"
        )
    except Exception as e:
        return f"Error building symbol import/export graph: {str(e)}"
