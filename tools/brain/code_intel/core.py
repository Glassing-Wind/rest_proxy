"""tools/code_intel/core.py — code intelligence tools (symbol analysis, visualization, file description)."""

import time
import os
import re
from neo4j import unit_of_work
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules, get_project_id
from proxy.logging import debug_log
from ts_diagnostics import normalize_ts_pack_result
from tools.brain.code_intel import file_describe
from tools.brain.code_intel import references
from tools.brain.code_intel import symbol_graph

try:
    from tree_sitter_language_pack import GRAPH_REL_TYPES as _GRAPH_REL_TYPES
except ModuleNotFoundError:
    _GRAPH_REL_TYPES = {
        "bundles_file": "BUNDLES_FILE",
        "references_project": "REFERENCES_PROJECT",
        "builds_target": "BUILDS_TARGET",
        "defined_in_file": "DEFINED_IN_FILE",
    }


def _rel_type(name: str) -> str:
    return _GRAPH_REL_TYPES[name]


def register(mcp: FastMCP) -> None:

    _TX_TIMEOUT = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
    _TX_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
    _TX_METADATA_BASE = {"source": "lm_proxy", "tool": "code_intel"}
    _GENERIC_SWIFT_IMPORTS = {
        "SwiftUI",
        "Foundation",
        "AppKit",
        "UIKit",
        "Combine",
        "Observation",
        "SwiftData",
        "UniformTypeIdentifiers",
        "CoreGraphics",
        "AVFoundation",
    }
    _GENERIC_BRIDGE_IMPORTS = {
        "CNIOLinux",
        "CNIOOpenBSD",
        "CNIOWindows",
        "WinSDK",
        "ucrt",
        "Glibc",
        "Darwin",
        "FoundationNetworking",
        "NIOCore",
    }
    _SWIFT_TYPE_MENTION_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]+\b")
    _LOW_SIGNAL_RELATED_RE = re.compile(
        r"(^|/)(session-ses_[^/]+\.md|agents\.md|readme(?:\.[^/]+)?|changelog(?:\.[^/]+)?)$",
        re.IGNORECASE,
    )
    _REL_BUNDLES_FILE = _rel_type("bundles_file")
    _REL_REFERENCES_PROJECT = _rel_type("references_project")
    _REL_BUILDS_TARGET = _rel_type("builds_target")
    _REL_DEFINED_IN_FILE = _rel_type("defined_in_file")
    _FENCE_BY_EXT = {
        ".py": "python",
        ".pyi": "python",
        ".java": "java",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".swift": "swift",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".rs": "rust",
        ".go": "go",
        ".rb": "ruby",
        ".cs": "csharp",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".m": "objective-c",
        ".mm": "objective-cpp",
        ".scala": "scala",
        ".sql": "sql",
        ".sh": "bash",
        ".zsh": "bash",
        ".md": "markdown",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".xml": "xml",
        ".html": "html",
        ".css": "css",
    }

    def _source_preview_fence(file_path: str | None) -> str:
        _, ext = os.path.splitext(str(file_path or "").lower())
        return _FENCE_BY_EXT.get(ext, "")

    def _is_low_signal_support_path(file_path: str | None) -> bool:
        norm = (file_path or "").replace("\\", "/").lower()
        if not norm:
            return True
        if _LOW_SIGNAL_RELATED_RE.search(norm):
            return True
        if norm.endswith((".md", ".rst", ".txt")):
            return True
        return False

    def _is_test_like_path(file_path: str | None) -> bool:
        norm = (file_path or "").replace("\\", "/").lower()
        if not norm:
            return False
        return (
            "/test/" in norm
            or "/tests/" in norm
            or norm.startswith("test/")
            or norm.startswith("tests/")
            or "/e2e/" in norm
            or norm.startswith("e2e/")
            or "/fixtures/" in norm
            or norm.startswith("fixtures/")
            or ".spec." in norm
            or ".stories." in norm
        )

    def _is_low_signal_related_import_source(source: str | None) -> bool:
        value = str(source or "").strip()
        if not value:
            return True
        if value in _GENERIC_SWIFT_IMPORTS:
            return True
        if value in _GENERIC_BRIDGE_IMPORTS:
            return True
        if value.startswith("C_"):
            return True
        return False

    def _related_file_rank(target_file_path: str, candidate_path: str, useful_import_count: int, shared_imports: int) -> tuple[int, int, int, int, str]:
        target_norm = str(target_file_path or "").replace("\\", "/").strip("/")
        candidate_norm = str(candidate_path or "").replace("\\", "/").strip("/")
        target_dir = target_norm.rsplit("/", 1)[0] if "/" in target_norm else ""
        candidate_dir = candidate_norm.rsplit("/", 1)[0] if "/" in candidate_norm else ""
        target_parent = target_dir.rsplit("/", 1)[0] if "/" in target_dir else ""
        score = 0
        if candidate_dir and candidate_dir == target_dir:
            score += 90
        elif target_parent and candidate_dir.startswith(target_parent + "/"):
            score += 40
        elif target_dir and candidate_dir.split("/", 1)[0] == target_dir.split("/", 1)[0]:
            score += 20
        if _is_test_like_path(candidate_norm):
            score -= 80
        if _is_low_signal_support_path(candidate_norm):
            score -= 60
        return (-score, -useful_import_count, -shared_imports, len(candidate_norm), candidate_norm)

    def _structural_related_rank(
        target_file_path: str,
        candidate_path: str,
        call_hits: int,
        import_hits: int,
        symbol_count: int,
    ) -> tuple[int, int, int, int, int, str]:
        target_norm = str(target_file_path or "").replace("\\", "/").strip("/")
        candidate_norm = str(candidate_path or "").replace("\\", "/").strip("/")
        target_dir = target_norm.rsplit("/", 1)[0] if "/" in target_norm else ""
        candidate_dir = candidate_norm.rsplit("/", 1)[0] if "/" in candidate_norm else ""
        target_parent = target_dir.rsplit("/", 1)[0] if "/" in target_dir else ""
        score = 0
        if candidate_dir and candidate_dir == target_dir:
            score += 90
        elif target_parent and candidate_dir.startswith(target_parent + "/"):
            score += 45
        elif target_dir and candidate_dir.split("/", 1)[0] == target_dir.split("/", 1)[0]:
            score += 20
        if _is_test_like_path(candidate_norm):
            score -= 80
        if _is_low_signal_support_path(candidate_norm):
            score -= 60
        return (
            -score,
            -call_hits,
            -import_hits,
            -symbol_count,
            len(candidate_norm),
            candidate_norm,
        )

    def _extract_swift_type_mentions(text: str, local_symbols: list[str] | set[str] | None = None) -> list[str]:
        local = {str(item).strip() for item in (local_symbols or []) if str(item).strip()}
        ignored = {
            *local,
            "App",
            "Scene",
            "View",
            "Text",
            "Button",
            "Image",
            "Color",
            "UUID",
            "String",
            "Int",
            "Float",
            "Double",
            "Bool",
            "URL",
            "Data",
            "Date",
            "CommandConfiguration",
            "ParsableCommand",
        }
        out: list[str] = []
        seen: set[str] = set()
        for match in _SWIFT_TYPE_MENTION_RE.findall(text or ""):
            if match in ignored or match in seen:
                continue
            seen.add(match)
            out.append(match)
            if len(out) >= 12:
                break
        return out

    def _is_low_signal_symbol_name(name: str | None) -> bool:
        value = str(name or "").strip()
        if not value:
            return True
        if len(value) <= 2 and not any(ch.isalnum() for ch in value):
            return True
        if all(not ch.isalnum() and ch != "_" for ch in value):
            return True
        return False

    def _dedupe_symbol_names(symbols: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            value = str(symbol or "").strip()
            lowered = value.lower()
            if not value or lowered in seen or _is_low_signal_symbol_name(value):
                continue
            seen.add(lowered)
            out.append(value)
        return out

    def _best_symbol_snippet(content: str | None, symbol_name: str) -> str:
        symbol_call = f"{symbol_name}("
        symbol_token = symbol_name
        fallback = ""
        for raw_line in str(content or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("// File:"):
                continue
            if symbol_call in line:
                return line[:120]
            if symbol_token in line and not fallback:
                fallback = line[:120]
        return fallback

    def _importance_penalty(file_path: str | None) -> float:
        norm = (file_path or "").replace("\\", "/").lower()
        if ("src/public/assets/" in norm or "/public/assets/" in norm) and norm.endswith((".js", ".ts", ".jsx", ".tsx")):
            return 0.08
        if any(
            token in norm
            for token in (
                "/gen/",
                ".gen.ts",
                ".generated.ts",
                "_generated.swift",
                "pregeneratedspm/",
            )
        ):
            return 0.08
        if "/e2e/" in norm or norm.endswith((".spec.ts", ".spec.tsx")):
            return 0.24
        if "/storybook/" in norm or ".stories." in norm:
            return 0.22
        if "/components/icons/" in norm:
            return 0.16
        if norm.startswith("vendors/") or "/vendors/" in norm:
            return 0.35
        return 1.0

    def _backend_bridge_boost(file_path: str | None) -> float:
        norm = (file_path or "").replace("\\", "/").lower()
        if any(
            token in norm
            for token in (
                "src/api/routes/",
                "src/api/",
                "src/services/",
                "src/db/",
                "prisma/schema.prisma",
            )
        ):
            return 1.9
        if any(token in norm for token in ("src/config/", "src/lib/", "src/models/")):
            return 1.12
        return 1.0

    def _architecture_rank(record: dict) -> float:
        file_path = record.get("file")
        score = float(record.get("score") or 0.0)
        bridge = float(record.get("betweenness") or 0.0)
        adjusted = ((score ** 0.7) + (bridge * 0.6)) * _importance_penalty(file_path) * _backend_bridge_boost(file_path)
        return adjusted

    def _cluster_kind(top_files: list[str]) -> tuple[str, float]:
        files = [str(fp).replace("\\", "/").lower() for fp in (top_files or [])]
        if not files:
            return "misc", 1.0
        generated_count = sum(
            any(
                token in fp
                for token in ("/gen/", "gen/", ".gen.ts", ".generated.ts", "_generated.swift", "pregeneratedspm/")
            )
            for fp in files
        )
        public_count = sum("/public/" in fp or fp.startswith("src/public/") for fp in files)
        ui_count = sum(
            any(
                token in fp
                for token in (
                    "/packages/ui/",
                    "packages/ui/",
                    "/packages/app/",
                    "packages/app/",
                    "/src/components/",
                    "src/components/",
                    "/src/context/",
                    "src/context/",
                )
            )
            for fp in files
        )
        web_count = sum(
            any(token in fp for token in ("/packages/web/", "packages/web/", "/web/src/", "web/src/"))
            for fp in files
        )
        cli_count = sum(
            any(token in fp for token in ("/cli/", "cli/", "/packages/opencode/src/", "packages/opencode/src/"))
            for fp in files
        )
        sdk_count = sum(
            any(token in fp for token in ("/packages/sdk/", "packages/sdk/", "/sdk/js/", "sdk/js/"))
            for fp in files
        )
        api_count = sum("/src/api/" in fp or fp.startswith("src/api/") for fp in files)
        service_count = sum("/src/services/" in fp or fp.startswith("src/services/") for fp in files)
        db_count = sum(
            "/src/db/" in fp
            or fp.startswith("src/db/")
            or "prisma/schema.prisma" in fp
            or "/prisma/" in fp
            for fp in files
        )
        jobs_count = sum("/src/jobs/" in fp or fp.startswith("src/jobs/") for fp in files)
        total = max(len(files), 1)
        if generated_count / total >= 0.5 and sdk_count >= 1:
            return "sdk/generated", 0.22
        if public_count / total >= 0.6:
            return "ui/public", 0.3
        if sdk_count / total >= 0.6:
            return "sdk/runtime", 1.35
        if cli_count / total >= 0.5:
            return "cli/runtime", 1.5
        if web_count / total >= 0.5:
            return "web/site", 1.15
        if ui_count / total >= 0.5:
            return "ui/app", 1.1
        if api_count + service_count + db_count >= 2:
            return "backend/app", 2.0
        if db_count >= 1:
            return "data/schema", 1.4
        if jobs_count >= 1:
            return "jobs/runtime", 1.25
        return "mixed", 1.0

    def _importance_focus_reason(record: dict) -> str:
        parts: list[str] = []
        score = float(record.get("score") or 0.0)
        betweenness = float(record.get("betweenness") or 0.0)
        sym_count = int(record.get("sym_count") or 0)
        if score >= 5.0:
            parts.append("high centrality")
        if betweenness >= 3.0:
            parts.append("bridge file")
        if sym_count >= 20:
            parts.append("large symbol surface")
        if record.get("isolated"):
            parts.append("isolated hotspot")
        return ", ".join(parts) if parts else "architectural leverage"

    def _community_display_limit(records: list[dict]) -> int:
        if len(records) <= 12:
            return len(records)
        return 12

    def _is_small_community_tail(record: dict) -> bool:
        file_count = int(record.get("file_count") or 0)
        total_syms = int(record.get("total_syms") or 0)
        kind_label, _ = _cluster_kind(record.get("top_files") or [])
        if kind_label in {"backend/app", "cli/runtime", "sdk/runtime", "data/schema"}:
            return False
        return file_count <= 2 or total_syms <= 12

    async def _execute_read(
        session,
        cypher: str,
        timeout: float | None = None,
        op: str | None = None,
        **params,
    ):
        metadata = dict(_TX_METADATA_BASE)
        op_value = op or "read"
        if _TX_OP_PREFIX:
            op_value = f"{_TX_OP_PREFIX}.{op_value}"
        metadata["op"] = op_value

        @unit_of_work(timeout=timeout or _TX_TIMEOUT, metadata=metadata)
        async def _tx(tx):
            result = await tx.run(cypher, **params)
            return await result.data()

        if hasattr(session, "execute_read"):
            return await session.execute_read(_tx)
        return await _tx(session)

    def _cargo_manifest_dir(manifest_path: str | None) -> str:
        if not manifest_path:
            return ""
        return manifest_path[:-len("Cargo.toml")] if manifest_path.endswith("Cargo.toml") else manifest_path

    def _match_cargo_crate(file_path: str | None, cargo_rows: list[dict]) -> str | None:
        if not file_path:
            return None
        for row in cargo_rows:
            crate_root = _cargo_manifest_dir(row.get("manifest_path"))
            if crate_root and file_path.startswith(crate_root):
                return row.get("crate") or row.get("crate_name")
        return None

    async def _load_cargo_crate_rows(session, project_id: str):
        cargo_schema = await _execute_read(
            session,
            """
            CALL db.labels() YIELD label
            RETURN collect(label) AS labels
            """,
            op="cargo_schema_labels",
        )
        cargo_labels = set(cargo_schema[0].get("labels") or []) if cargo_schema else set()
        if "CargoCrate" not in cargo_labels:
            return []
        return await _execute_read(
            session,
            """
            MATCH (c:CargoCrate {project_id:$pid})-[:DEFINED_IN_FILE]->(mf:File {project_id:$pid})
            RETURN c.name AS crate,
                   c.crate_name AS crate_name,
                   mf.filepath AS manifest_path
            ORDER BY size(mf.filepath) DESC, c.name
            """,
            pid=project_id,
            op="cargo_crates",
        )

    @mcp.tool()
    async def get_symbol_context(
        workspace_id: str,
        symbol_name: str,
        include_source_preview: bool = True,
        file_path: str | None = None,
        signature: str | None = None,
    ) -> str:
        """
        Single-call deep dive into a symbol: definition location, signature,
        what it calls, what calls it, and the actual source code chunk.

        Replaces the common 4-step pattern of:
          get_code_summary → find_callers → find_definitions → search_codebase

        Args:
            workspace_id: Logical workspace name or absolute project path.
            symbol_name:  Name of the function, class, or struct to inspect.
            file_path:    Optional file path to disambiguate overloaded symbols.
            signature:    Optional signature substring to disambiguate overloaded symbols.
        """
        try:
            project_id = get_project_id(workspace_id)
            import graph_bootstrap

            _, _, _, _, proxy = get_memory_modules()

            driver = await graph_bootstrap.require_driver()
            normalized_file_path = symbol_graph.normalize_query_file_path(
                workspace_id, file_path
            )
            normalized_signature = (
                signature.strip() if isinstance(signature, str) else None
            )
            if not normalized_signature:
                normalized_signature = None

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await _execute_read(
                    session,
                    symbol_graph.SYMBOL_CONTEXT_CYPHER,
                    name=symbol_name,
                    pid=project_id,
                    file_path=normalized_file_path,
                    signature=normalized_signature,
                    op="get_symbol_context",
                )
                if symbol_graph.should_disambiguate_symbol_context(
                    records or [],
                    symbol_name=symbol_name,
                    normalized_file_path=normalized_file_path,
                    normalized_signature=normalized_signature,
                ):
                    return symbol_graph.format_symbol_context_ambiguity(
                        records or [],
                        symbol_name=symbol_name,
                        normalized_file_path=normalized_file_path,
                        normalized_signature=normalized_signature,
                    )
                rec = symbol_graph.pick_symbol_context_candidate(
                    records or [],
                    symbol_name=symbol_name,
                    normalized_file_path=normalized_file_path,
                    normalized_signature=normalized_signature,
                )

            if not rec:
                return f"Symbol '{symbol_name}' not found. Run index_workspace() first."

            out = symbol_graph.format_symbol_context(rec, symbol_name)

            if include_source_preview:
                # 1. Prefer the local filesystem when the workspace is available so the
                # preview starts from the exact symbol span instead of a semantic chunk.
                if os.path.exists(workspace_id):
                    try:
                        abs_path = os.path.join(workspace_id, rec["filepath"])
                        if os.path.exists(abs_path):
                            with open(
                                abs_path, "r", encoding="utf-8", errors="ignore"
                            ) as fh:
                                lines_list = fh.read().splitlines()
                            if lines_list:
                                start_line = max(1, int(rec["start_line"] or 1))
                                snippet = "\n".join(lines_list[start_line - 1 : start_line + 79]).strip()
                                if snippet:
                                    fence = _source_preview_fence(rec.get("filepath"))
                                    out += [f"\n**Source preview:**\n```{fence}\n{snippet[:900]}\n```"]
                                    return "\n".join(out)
                    except Exception:
                        pass

                # 2. Try Postgres (Brain/Central fallback) - This works remotely!
                try:
                    memory_store, _, _, _, _ = get_memory_modules()
                    await memory_store.open_pool()
                    async with memory_store._pg_pool.connection() as conn:
                        async with conn.cursor() as cur:
                            await cur.execute(
                                """
                                SELECT content FROM codebase_embeddings
                                WHERE  project_id = %s AND file_path = %s
                                ORDER  BY
                                       CASE
                                           WHEN EXISTS (
                                               SELECT 1
                                               FROM jsonb_array_elements_text(
                                                   CASE
                                                       WHEN jsonb_typeof(metadata->'declared_symbols') = 'array'
                                                       THEN metadata->'declared_symbols'
                                                       ELSE '[]'::jsonb
                                                   END
                                               ) AS sym(value)
                                               WHERE lower(sym.value) = lower(%s)
                                           ) THEN 0
                                           ELSE 1
                                       END,
                                       CASE
                                           WHEN metadata->>'chunk_role' = 'definition' THEN 0
                                           ELSE 1
                                       END,
                                       ABS(COALESCE((metadata->>'start_line')::int, 1) - %s),
                                       chunk_index
                                LIMIT 1
                            """,
                                (project_id, rec["filepath"], rec["name"], int(rec["start_line"] or 1)),
                            )
                            rows = await cur.fetchall()
                            if rows:
                                src = str(rows[0][0] or "")[:900]
                                out += [f"\n**Source preview:**\n```\n{src}\n```"]
                except Exception:
                    pass

            return "\n".join(out)
        except Exception as e:
            return f"Error getting symbol context: {str(e)}"

    @mcp.tool()
    async def get_call_chain(
        workspace_id: str,
        symbol_name: str,
        depth: int = 3,
        direction: str = "down",
        file_path: str | None = None,
        signature: str | None = None,
    ) -> str:
        """
        Trace a call chain N hops deep from a starting symbol.

        Unlike get_symbol_context (single hop), this recursively follows
        CALLS edges to build a full call tree — ideal for understanding
        execution paths and gRPC handler flows.

        Args:
            workspace_id: Logical workspace name or absolute project path.
            symbol_name:  Starting symbol name.
            depth:        How many hops to follow (default 3, max 5).
            direction:    'down' (what this calls) or 'up' (what calls this).
            file_path:    Optional file path to disambiguate symbols.
            signature:    Optional signature substring to disambiguate symbols.
        """
        try:
            project_id = get_project_id(workspace_id)
            depth = min(int(depth), 5)
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            normalized_file_path = symbol_graph.normalize_query_file_path(
                workspace_id, file_path
            )

            normalized_signature = (
                signature.strip() if isinstance(signature, str) else None
            )
            if not normalized_signature:
                normalized_signature = None

            resolved_name = symbol_name
            resolved_eid = None
            resolved_kind = None
            hop_label = "caller" if direction == "up" else "callee"

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                candidates = await _execute_read(
                    session,
                    symbol_graph.CALL_CHAIN_RESOLVE_CYPHER,
                    name=symbol_name,
                    pid=project_id,
                    file_path=normalized_file_path,
                    signature=normalized_signature,
                    op="get_call_chain_resolve",
                )
                if not candidates:
                    return (
                        f"`{symbol_name}` not found or no {hop_label}s within {depth} hops.\n"
                        "Try a fully qualified name like `Type.method` or include argument labels."
                    )

                picked = symbol_graph.pick_call_chain_candidate(
                    candidates,
                    normalized_file_path=normalized_file_path,
                    normalized_signature=normalized_signature,
                )

                resolved_eid = picked["eid"]
                resolved_name = (
                    picked.get("qualified_name") or picked.get("name") or symbol_name
                )
                resolved_kind = picked.get("kind") or ""
                resolved_filepath = picked.get("filepath") or ""
                hop_label, cypher = symbol_graph.build_call_chain_path_cypher(
                    direction,
                    depth,
                    is_backend_root=symbol_graph.is_backend_filepath(resolved_filepath),
                )
                rows = await _execute_read(
                    session, cypher, eid=resolved_eid, op="get_call_chain"
                )

                if (
                    not rows
                    and direction == "up"
                    and resolved_filepath.endswith(".swift")
                    and resolved_kind in {"Protocol", "Interface", "Trait"}
                ):
                    protocol_rows = await _execute_read(
                        session,
                        symbol_graph.build_swift_protocol_upward_fallback_cypher(depth),
                        eid=resolved_eid,
                        op="get_call_chain_swift_protocol_fallback",
                    )
                    if protocol_rows:
                        rows = protocol_rows

                if not rows:
                    if direction == "up" and resolved_filepath.endswith(".swift"):
                        memory_store, _, _, _, _ = get_memory_modules()
                        await memory_store.open_pool()
                        async with memory_store._pg_pool.connection() as conn:
                            async with conn.cursor() as cur:
                                await cur.execute(
                                    """
                                    SELECT file_path,
                                           metadata->>'start_line' AS start_line,
                                           content
                                    FROM codebase_embeddings
                                    WHERE project_id = %s
                                      AND file_path <> %s
                                      AND file_path LIKE '%%.swift'
                                      AND content ILIKE %s
                                    ORDER BY
                                      CASE
                                        WHEN content ILIKE %s THEN 0
                                        ELSE 1
                                      END,
                                      file_path
                                    LIMIT 80
                                    """,
                                    (
                                        project_id,
                                        resolved_filepath,
                                        f"%{symbol_name}%",
                                        f"%{symbol_name}(%",
                                    ),
                                )
                                semantic_rows = await cur.fetchall()
                        grouped_rows: dict[str, tuple[int, str, str]] = {}
                        for fp, sl, content in semantic_rows or []:
                            if _is_low_signal_support_path(fp):
                                continue
                            snippet = _best_symbol_snippet(content, symbol_name)
                            if not snippet:
                                continue
                            rank = 0 if f"{symbol_name}(" in snippet else 1
                            current = grouped_rows.get(fp)
                            candidate = (rank, str(sl or ""), snippet)
                            if current is None or candidate < current:
                                grouped_rows[fp] = candidate
                        if grouped_rows:
                            lines = [
                                f"`{resolved_name}` resolved but no graph callers within {depth} hops.",
                                "",
                                "Swift caller-like usages:",
                            ]
                            sorted_rows = sorted(
                                grouped_rows.items(),
                                key=lambda item: (item[1][0], item[0]),
                            )
                            for fp, (rank, sl, snippet) in sorted_rows[:5]:
                                line_part = f":{sl}" if sl else ""
                                lines.append(f"- `{fp}{line_part}`  >> {snippet}")
                            lines.append("")
                            lines.append(
                                "SwiftUI/component composition is not always represented as CALLS edges, so this fallback shows source usages."
                            )
                            return "\n".join(lines)
                    message = (
                        f"`{resolved_name}` resolved but no {hop_label}s within {depth} hops.\n"
                        "Make sure the project is indexed and Swift CALLS edges are available."
                    )
                    guidance = symbol_graph.exact_call_graph_guidance(
                        resolved_filepath,
                        has_callers=(direction == "up"),
                        has_callees=(direction == "down"),
                    )
                    if guidance:
                        message += f"\n\n{guidance}"
                    return message

            return symbol_graph.format_call_chain_rows(
                rows,
                resolved_name=resolved_name,
                symbol_name=symbol_name,
                direction=direction,
                depth=depth,
                resolved_filepath=resolved_filepath,
            )
        except Exception as e:
            return f"Error tracing call chain: {str(e)}"

    @mcp.tool()
    async def trace_graph_provenance(
        workspace_id: str,
        symbol_filter: str | None = None,
        file_filter: str | None = None,
    ) -> str:
        """
        Trace parse/resolve/finalize graph provenance samples for a project.

        Uses the ts-pack producer-side provenance report to explain where
        call and file-to-file graph links came from across parse, resolve,
        and finalize stages.

        Args:
            workspace_id: Logical workspace name or absolute project path.
            symbol_filter: Optional callee/symbol substring filter.
            file_filter: Optional file-path substring filter.
        """
        try:
            project_id = get_project_id(workspace_id)
            import graph_bootstrap
            import tree_sitter_language_pack as ts_pack

            symbol_filter_value = (
                symbol_filter.strip() if isinstance(symbol_filter, str) and symbol_filter.strip() else None
            )
            file_filter_value = (
                file_filter.strip() if isinstance(file_filter, str) and file_filter.strip() else None
            )
            report = ts_pack.trace_graph_provenance(
                project_id,
                graph_bootstrap._NEO4J_URI,
                graph_bootstrap._NEO4J_USER,
                graph_bootstrap._NEO4J_PASSWORD,
                graph_bootstrap._NEO4J_DB,
                symbol_filter=symbol_filter_value,
                file_filter=file_filter_value,
                project_path=workspace_id,
            )
            if not isinstance(report, dict):
                return f"Unexpected provenance report type: {type(report).__name__}"
            parse = report.get("parse") or {}
            resolve = report.get("resolve") or {}
            finalize = report.get("finalize") or {}
            parse_samples = parse.get("call_ref_samples") or []
            resolved_samples = resolve.get("resolved_internal_samples") or []
            external_samples = resolve.get("external_symbol_samples") or []
            resolve_note = resolve.get("note") or ""
            call_samples = finalize.get("calls_file_samples") or []
            link_samples = finalize.get("file_graph_link_samples") or []

            if not parse_samples and not resolved_samples and not external_samples and not call_samples and not link_samples:
                return (
                    f"No provenance samples matched for `{workspace_id}`."
                    + (f"\nSymbol filter: `{symbol_filter_value}`" if symbol_filter_value else "")
                    + (f"\nFile filter: `{file_filter_value}`" if file_filter_value else "")
                )

            lines = [f"# Graph Provenance: {os.path.basename(workspace_id.rstrip('/'))}", ""]
            lines.append(f"Project ID: `{report.get('project_id') or project_id}`")
            if symbol_filter_value:
                lines.append(f"Symbol filter: `{symbol_filter_value}`")
            if file_filter_value:
                lines.append(f"File filter: `{file_filter_value}`")
            lines.append("")

            inspect_first: list[str] = []
            if resolved_samples:
                sample = resolved_samples[0]
                src = sample.get("src") or "?"
                dst = sample.get("dst") or "?"
                caller = sample.get("caller") or "?"
                callee = sample.get("callee") or "?"
                inspect_first.append(
                    f"- inspect `{dst}` first because `{src}` resolves `{caller} -> {callee}` into it"
                )
            if call_samples:
                sample = call_samples[0]
                src = sample.get("src") or "?"
                dst = sample.get("dst") or "?"
                inspect_first.append(
                    f"- inspect the file edge `{src}` -> `{dst}` next because it survives finalize into `CALLS_FILE`"
                )
            if external_samples:
                sample = external_samples[0]
                qualified_name = sample.get("qualified_name") or sample.get("callee") or "?"
                src = sample.get("src") or "?"
                inspect_first.append(
                    f"- inspect external boundary `{qualified_name}` because `{src}` still depends on it after internal resolution"
                )
            if inspect_first:
                lines.append("## Inspect First")
                lines.extend(inspect_first[:3])
                lines.append("")

            if parse_samples:
                lines.append("## Parse Call Samples")
                for sample in parse_samples[:20]:
                    caller_filepath = sample.get("caller_filepath") or "?"
                    callee = sample.get("callee") or "?"
                    kind = sample.get("kind") or "plain"
                    receiver_hint = sample.get("receiver_hint") or ""
                    qualified_hint = sample.get("qualified_hint") or ""
                    suffix_parts = []
                    if receiver_hint:
                        suffix_parts.append(f"receiver={receiver_hint}")
                    if qualified_hint:
                        suffix_parts.append(f"qualified={qualified_hint}")
                    suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
                    lines.append(f"- `{caller_filepath}` -> `{callee}` [{kind}]{suffix}")
                lines.append("")

            if resolved_samples:
                lines.append("## Resolved Internal Samples")
                for sample in resolved_samples[:20]:
                    src = sample.get("src") or "?"
                    dst = sample.get("dst") or "?"
                    caller = sample.get("caller") or "?"
                    callee = sample.get("callee") or "?"
                    via = sample.get("via") or "CALLS"
                    lines.append(f"- `{src}` -> `{dst}` via `{caller} -> {callee}` [{via}]")
                lines.append("")

            if external_samples:
                lines.append("## External Symbol Samples")
                for sample in external_samples[:20]:
                    src = sample.get("src") or "?"
                    caller = sample.get("caller") or "?"
                    callee = sample.get("callee") or "?"
                    qualified_name = sample.get("qualified_name") or ""
                    language = sample.get("language") or ""
                    suffix_parts = []
                    if qualified_name:
                        suffix_parts.append(f"qualified={qualified_name}")
                    if language:
                        suffix_parts.append(f"language={language}")
                    suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
                    lines.append(f"- `{src}` external via `{caller} -> {callee}`{suffix}")
                lines.append("")

            if resolve_note:
                lines.append(f"Resolve note: {resolve_note}")
                lines.append("")

            if call_samples:
                lines.append("## CALLS_FILE Samples")
                for sample in call_samples[:20]:
                    src = sample.get("src") or "?"
                    dst = sample.get("dst") or "?"
                    caller = sample.get("caller") or "?"
                    callee = sample.get("callee") or "?"
                    via = sample.get("via") or "CALLS"
                    lines.append(f"- `{src}` -> `{dst}` via `{caller} -> {callee}` [{via}]")
                lines.append("")

            if link_samples:
                lines.append("## File Graph Link Samples")
                for sample in link_samples[:20]:
                    src = sample.get("src") or "?"
                    dst = sample.get("dst") or "?"
                    rel = sample.get("source_rel") or "UNKNOWN"
                    route = sample.get("route") or ""
                    method = sample.get("method") or ""
                    suffix = f" route={method} {route}".strip() if (route or method) else ""
                    lines.append(f"- `{src}` -> `{dst}` [{rel}]{(' ' + suffix) if suffix else ''}")

            return "\n".join(lines).rstrip()
        except Exception as e:
            return f"Error tracing graph provenance: {str(e)}"

    @mcp.tool()
    async def list_symbol_matches(
        project_path: str, query: str, limit: int = 30, kinds: list | None = None
    ) -> str:
        """
        List symbols whose name/signature contains a query string.

        Args:
            project_path: Absolute path to the project root.
            query:        Substring to match against symbol name/signature.
            limit:        Max results to return (default 30).
        kinds:        Optional list of kinds to include (Function, Class, Enum, EnumCase, Protocol, Extension, etc.).
        """
        try:
            project_id = get_project_id(project_path)
            q = (query or "").strip()
            if not q:
                return "Query is empty. Provide a symbol name substring to match."

            import graph_bootstrap

            _, _, _, _, proxy = get_memory_modules()

            driver = await graph_bootstrap.require_driver()

            kind_set = {
                "Function",
                "Method",
                "Class",
                "Struct",
                "Trait",
                "Enum",
                "EnumCase",
                "Protocol",
                "Extension",
                "TypeAlias",
                "AssociatedType",
            }
            kind_list = []
            if kinds:
                for k in kinds:
                    if isinstance(k, str) and k in kind_set:
                        kind_list.append(k)
            if not kind_list:
                kind_list = sorted(kind_set)

            type_kinds = [
                "Class",
                "Struct",
                "Enum",
                "EnumCase",
                "Protocol",
                "Extension",
                "TypeAlias",
                "AssociatedType",
                "Trait",
            ]
            callable_kinds = ["Function", "Method"]

            cypher = """
                MATCH (s:Node {project_id: $pid})
                WHERE any(k in $kinds WHERE k IN labels(s))
                AND (
                  s.name CONTAINS $q
                  OR (s.qualified_name IS NOT NULL AND s.qualified_name CONTAINS $q)
                  OR (s.signature IS NOT NULL AND s.signature CONTAINS $q)
                )
                AND (size(s.name) > 2 OR size($q) <= 2)
                RETURN labels(s) AS kinds, s.name AS name, s.qualified_name AS qualified_name,
                       s.signature AS signature, s.filepath AS filepath,
                       CASE WHEN s.name CONTAINS $q THEN 0 ELSE 1 END AS name_match,
                       CASE
                         WHEN any(k in $type_kinds WHERE k IN labels(s)) THEN 0
                         WHEN any(k in $callable_kinds WHERE k IN labels(s)) THEN 1
                         ELSE 2
                       END AS kind_rank
                ORDER BY name_match ASC, kind_rank ASC, size(s.name) ASC
                LIMIT $limit
            """

            start = time.perf_counter()
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                rows = await _execute_read(
                    session,
                    cypher,
                    pid=project_id,
                    q=q,
                    kinds=kind_list,
                    limit=min(int(limit), 200),
                    type_kinds=type_kinds,
                    callable_kinds=callable_kinds,
                    timeout=float(os.getenv("LM_PROXY_NEO4J_READ_TIMEOUT", "3.0")),
                    op="list_symbol_matches",
                )

            debug_log(
                "list_symbol_matches",
                query=q,
                project_id=project_id,
                rows=len(rows),
                elapsed_ms=int((time.perf_counter() - start) * 1000),
            )

            if not rows:
                return f"No symbol matches for '{q}'."

            lines = [f"Symbol matches for '{q}':", ""]
            for rec in rows:
                sig = rec.get("signature") or ""
                sig = sig.strip().replace("\n", " ")
                sig = f" — {sig}" if sig else ""
                qn = rec.get("qualified_name") or ""
                qn = f" ({qn})" if qn and qn != rec.get("name") else ""
                kinds = rec.get("kinds") or []
                kind = next(
                    (k for k in kinds if k in kind_set), kinds[0] if kinds else "Node"
                )
                lines.append(
                    f"- {rec.get('name')}{qn} ({kind})  {rec.get('filepath')}{sig}"
                )
            return "\n".join(lines)
        except Exception as e:
            debug_log("list_symbol_matches_error", error=str(e))
            return f"Error listing symbols: {str(e)}"

    @mcp.tool()
    async def get_code_importance(workspace_id: str) -> str:
        """
        Identify the most important files in a project using GDS PageRank on the
        CALLS graph. Files whose symbols are called by many important callers rank
        highest — this is architecturally meaningful, not just symbol density.

        Falls back to a heuristic (callers×3 + symbols) for projects that have not
        yet been indexed with the PageRank pass.

        Excludes test files automatically.

        Args:
            workspace_id: Logical workspace name or absolute project path.
        """
        try:
            project_id = get_project_id(workspace_id)
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            # Primary query: use GDS PageRank if available
            cypher_pr = """
            MATCH (f:File {project_id: $pid})
            WHERE f.pagerank IS NOT NULL
              AND NOT f.filepath CONTAINS 'test'
              AND NOT f.filepath CONTAINS 'Test'
              AND NOT f.filepath CONTAINS 'spec'
              AND NOT f.filepath CONTAINS 'vendor'
            OPTIONAL MATCH (f)-[:CONTAINS]->(s)
              WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum
            WITH f, count(s) AS sym_count, collect(DISTINCT s.name)[..4] AS sym_examples
            RETURN f.filepath AS file, sym_count, sym_examples,
                   f.pagerank      AS top_pagerank,
                   f.pagerank_sum  AS score,
                   f.betweenness   AS betweenness,
                   coalesce(f.isolated, false) AS isolated
            ORDER BY score DESC LIMIT 50
            """

            # Fallback query: heuristic for un-ranked projects
            cypher_fallback = """
            MATCH (f:File {project_id: $pid})
            WHERE NOT f.filepath CONTAINS 'test'
              AND NOT f.filepath CONTAINS 'Test'
              AND NOT f.filepath CONTAINS 'spec'
              AND NOT f.filepath CONTAINS 'vendor'
            OPTIONAL MATCH (f)-[:CONTAINS]->(s)
              WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum
            WITH f, count(s) AS sym_count, collect(DISTINCT s.name)[..4] AS sym_examples
            OPTIONAL MATCH (caller:File {project_id: $pid})-[:CALLS|CALLS_INFERRED]->(cs)<-[:CONTAINS]-(f)
              WHERE caller <> f
            WITH f, sym_count, sym_examples, count(DISTINCT caller) AS callers_in
            WITH f.filepath AS file, sym_count, sym_examples,
                 callers_in * 3 + sym_count AS score, NULL AS top_pagerank
            WHERE score > 0
            ORDER BY score DESC LIMIT 50
            RETURN file, sym_count, sym_examples, top_pagerank, score
            """

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await _execute_read(
                    session,
                    cypher_pr,
                    pid=project_id,
                    op="get_code_importance_pr",
                )
                using_pagerank = bool(records)

                if not using_pagerank:
                    records = await _execute_read(
                        session,
                        cypher_fallback,
                        pid=project_id,
                        op="get_code_importance_fallback",
                    )

                cargo_rows = await _load_cargo_crate_rows(session, project_id)

            scoring_method = (
                "GDS PageRank (CALLS graph)"
                if using_pagerank
                else "heuristic (callers×3 + symbols)"
            )

            records = sorted(
                records,
                key=lambda record: (
                    _architecture_rank(record),
                    float(record.get("score") or 0.0),
                    float(record.get("betweenness") or 0.0),
                ),
                reverse=True,
            )[:15]

            output = [
                f"Most important files [{scoring_method}, test/vendor excluded]:",
                "Use this to decide where architectural leverage or blast radius is highest.",
            ]
            rendered_rows = []
            for record in records:
                examples = (
                    ", ".join(record["sym_examples"]) if record["sym_examples"] else "—"
                )
                crate = _match_cargo_crate(record.get("file"), cargo_rows)
                pr_str = (
                    f"  pr:{record['top_pagerank']:.4f}"
                    if record["top_pagerank"]
                    else ""
                )
                rendered_rows.append(
                    {
                        "crate": crate,
                        "line": (
                            f"{record['file']}"
                            + (f"  [crate:{crate}]" if crate else "")
                            + f"  [score:{record['score'] or 0:.4f}{pr_str}  symbols:{record['sym_count'] or 0}"
                            + (
                                f"  bridge:{record['betweenness']:.1f}"
                                if record.get("betweenness")
                                else ""
                            )
                            + ("  ⚠️isolated" if record.get("isolated") else "")
                            + f"]  ({examples})"
                        ),
                    }
                )
            if any(row.get("crate") for row in rendered_rows):
                groups: dict[str, list[str]] = {}
                for row in rendered_rows:
                    groups.setdefault(row.get("crate") or "(unowned)", []).append(row["line"])
                top_focus = []
                for index, row in enumerate(rendered_rows[:3], start=1):
                    focus_label = row["line"].split("  [score:", 1)[0]
                    focus_reason = _importance_focus_reason(records[index - 1])
                    top_focus.append(f"- {focus_label} — {focus_reason}")
                if top_focus:
                    output.append("Recommended starting points:")
                    output.extend(top_focus)
                    output.append("")
                for crate, items in groups.items():
                    output.append(f"Crate: {crate}")
                    for item in items:
                        output.append(f"- {item}")
            else:
                top_focus = []
                for index, row in enumerate(rendered_rows[:3], start=1):
                    focus_label = row["line"].split("  [score:", 1)[0]
                    focus_reason = _importance_focus_reason(records[index - 1])
                    top_focus.append(f"- {focus_label} — {focus_reason}")
                if top_focus:
                    output.append("Recommended starting points:")
                    output.extend(top_focus)
                    output.append("")
                for row in rendered_rows:
                    output.append(f"- {row['line']}")
            if len(output) == 1:
                return "No importance metrics found (ensure project is indexed)."
            return "\n".join(output)
        except Exception as e:
            return f"Error calculating code importance: {str(e)}"

    @mcp.tool()
    async def get_code_communities(workspace_id: str) -> str:
        """
        Identify architectural clusters in the codebase by grouping files by their
        top-level directory. Shows the structure of the project at a glance.
        For each cluster, lists its most symbol-rich files.

        Run index_workspace() first to enable real PageRank and
        community detection via get_code_importance and
        get_code_communities.

        Args:
            workspace_id: Logical workspace name or absolute project path.
        """
        try:
            project_id = get_project_id(workspace_id)
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            # Primary: Louvain topology-based communities
            cypher_louvain = """
            MATCH (f:File {project_id: $pid})
            WHERE f.louvainCommunity IS NOT NULL
              AND NOT f.filepath CONTAINS 'test'
              AND NOT f.filepath CONTAINS 'vendor'
            OPTIONAL MATCH (f)-[:CONTAINS]->(s)
            WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum
            WITH f, f.louvainCommunity AS comm, count(s) AS sym_count
            WITH comm,
                 collect(f.filepath)[..5] AS top_files,
                 sum(sym_count)           AS total_syms,
                 count(f)                 AS file_count
            WHERE file_count > 0
            ORDER BY total_syms DESC
            RETURN comm, file_count, total_syms, top_files
            """

            # Fallback: directory grouping
            cypher_dir = """
            MATCH (f:File {project_id: $pid})
            WITH f, CASE WHEN f.filepath CONTAINS '/'
                         THEN split(f.filepath, '/')[0]
                         ELSE '(root)' END AS top_dir
            OPTIONAL MATCH (f)-[:CONTAINS]->(s)
            WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum
            WITH top_dir, f.filepath AS fp, count(s) AS sym_count
            ORDER BY top_dir, sym_count DESC
            WITH top_dir, collect(fp)[..5] AS top_files, sum(sym_count) AS total_syms,
                 count(fp) AS file_count
            ORDER BY total_syms DESC
            RETURN NULL AS comm, top_dir AS dominant_dir, file_count, total_syms, top_files
            """

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await _execute_read(
                    session,
                    cypher_louvain,
                    pid=project_id,
                    op="get_code_communities_louvain",
                )
                using_louvain = bool(records)

                if not using_louvain:
                    records = await _execute_read(
                        session,
                        cypher_dir,
                        pid=project_id,
                        op="get_code_communities_dir",
                    )
                cargo_rows = await _load_cargo_crate_rows(session, project_id)

            method = (
                "GDS Louvain (topology)" if using_louvain else "top-level directory"
            )
            output = [
                f"Architectural clusters [{method}]:",
                "Use this to decide which architectural area to inspect first and which areas are likely separate concerns.",
            ]
            filtered_records = []
            suppressed_small_records = 0
            for record in records:
                file_count = int(record.get("file_count") or 0)
                total_syms = int(record.get("total_syms") or 0)
                top_files = record.get("top_files") or []
                kind_label, _ = _cluster_kind(top_files)
                if file_count <= 1 and total_syms == 0:
                    suppressed_small_records += 1
                    continue
                if total_syms == 0 and all(
                    str(fp).endswith((".md", ".toml", ".yaml", ".yml", ".sql", ".example", ".json"))
                    for fp in top_files
                ):
                    suppressed_small_records += 1
                    continue
                if kind_label == "mixed" and (
                    (file_count <= 1 and total_syms <= 12)
                    or (file_count <= 2 and total_syms <= 4)
                ):
                    suppressed_small_records += 1
                    continue
                filtered_records.append(record)
            records = sorted(
                filtered_records,
                key=lambda record: (
                    (int(record.get("total_syms") or 0) ** 0.5) * _cluster_kind(record.get("top_files") or [])[1],
                    int(record.get("file_count") or 0),
                ),
                reverse=True,
            )
            display_limit = _community_display_limit(records)
            visible_records = records[:display_limit]
            hidden_records = records[display_limit:]
            hidden_small_tail = [record for record in hidden_records if _is_small_community_tail(record)]
            hidden_notable = [record for record in hidden_records if not _is_small_community_tail(record)]
            if hidden_notable:
                visible_records.extend(hidden_notable[: max(0, 15 - len(visible_records))])
                display_limit = len(visible_records)
            if records:
                output.append("Priority exploration order:")
                for record in records[:3]:
                    if using_louvain:
                        cluster_name = f"cluster #{record['comm']}"
                    else:
                        cluster_name = record.get("dominant_dir") or "(root)"
                    kind_label, _ = _cluster_kind(record.get("top_files") or [])
                    top_file = (record.get("top_files") or [None])[0]
                    crate = _match_cargo_crate(top_file, cargo_rows)
                    suffix = f" [crate:{crate}]" if crate else ""
                    reason = "broadest symbol-rich area"
                    if kind_label in {"backend/app", "cli/runtime", "sdk/runtime"}:
                        reason = "runtime-heavy cluster"
                    elif kind_label in {"sdk/generated", "ui/public"}:
                        reason = "likely lower-priority support cluster"
                    output.append(f"- {cluster_name} [{kind_label}]{suffix} — {reason}")
                output.append("")
            if cargo_rows and not using_louvain:
                crate_groups: dict[str, list[dict]] = {}
                for record in visible_records:
                    top_file = (record.get("top_files") or [None])[0]
                    crate = _match_cargo_crate(top_file, cargo_rows) or record.get("dominant_dir") or "(unowned)"
                    crate_groups.setdefault(crate, []).append(record)
                for crate, items in crate_groups.items():
                    total_files = sum(int(item.get("file_count") or 0) for item in items)
                    total_syms = sum(int(item.get("total_syms") or 0) for item in items)
                    top_files = []
                    seen = set()
                    for item in items:
                        for fp in item.get("top_files") or []:
                            if fp in seen:
                                continue
                            seen.add(fp)
                            top_files.append(fp)
                    output.append(
                        f"\n\U0001f4e6 crate `{crate}`"
                        f"  ({total_files} files, {total_syms} symbols)"
                        f"\n   Top files: {', '.join(top_files[:5])}"
                    )
            else:
                for record in visible_records:
                    if using_louvain:
                        comm_label = f"cluster #{record['comm']}"
                        kind_label, _ = _cluster_kind(record.get("top_files") or [])
                        if cargo_rows:
                            dominant_crates: dict[str, int] = {}
                            for fp in record.get("top_files") or []:
                                crate = _match_cargo_crate(fp, cargo_rows) or "(unowned)"
                                dominant_crates[crate] = dominant_crates.get(crate, 0) + 1
                            crate_text = ", ".join(
                                crate for crate, _ in sorted(dominant_crates.items(), key=lambda item: (-item[1], item[0]))[:3]
                            )
                        else:
                            crate_text = ""
                    else:
                        comm_label = record["dominant_dir"]
                        kind_label, _ = _cluster_kind(record.get("top_files") or [])
                        crate_text = ""
                    output.append(
                        f"\n\U0001f4e6 {comm_label}"
                        f"  [{kind_label}]"
                        f"  ({record['file_count']} files, {record['total_syms']} symbols)"
                        + (f"  crates: {crate_text}" if crate_text else "")
                        + f"\n   Top files: {', '.join(record['top_files'])}"
                    )
            total_suppressed_small = suppressed_small_records + len(hidden_small_tail)
            if total_suppressed_small:
                output.append("")
                output.append(
                    f"Suppressed {total_suppressed_small} small long-tail cluster(s) to keep the view decision-oriented."
                )
            if len(output) == 1:
                return "No communities found (ensure project is indexed)."
            return "\n".join(output)
        except Exception as e:
            return f"Error identifying code communities: {str(e)}"

    @mcp.tool()
    async def get_related_files(project_path: str, file_path: str) -> str:
        """
        Find files that are structurally related to the target file.

        Args:
            project_path: Absolute path to the project root.
            file_path: Relative path to the file in the project.
        """
        try:
            project_id = get_project_id(project_path)
            file_id = f"{project_id}:file:{file_path}"
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()
            cargo_related: list[str] = []
            apple_related: list[str] = []
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                cargo_rows = await _load_cargo_crate_rows(session, project_id)
                target_crate = _match_cargo_crate(file_path, cargo_rows)
                if target_crate:
                    same_crate = await _execute_read(
                        session,
                        """
                        MATCH (c:CargoCrate {project_id:$pid, name:$crate})-[:DEFINED_IN_FILE]->(mf:File {project_id:$pid})
                        WITH c, replace(mf.filepath, 'Cargo.toml', '') AS crate_root
                        MATCH (f:File {project_id:$pid})
                        WHERE f.filepath STARTS WITH crate_root
                          AND f.filepath <> $file_path
                        OPTIONAL MATCH (f)-[:CONTAINS]->(s)
                        WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum
                        WITH f, count(s) AS sym_count
                        RETURN f.filepath AS related_file, sym_count
                        ORDER BY sym_count DESC, related_file
                        LIMIT 5
                        """,
                        pid=project_id,
                        crate=target_crate,
                        file_path=file_path,
                        op="get_related_files_same_crate",
                    )
                    downstream = await _execute_read(
                        session,
                        """
                        MATCH (src:CargoCrate {project_id:$pid, name:$crate})-[:DEPENDS_ON_PACKAGE]->(tgt:CargoCrate {project_id:$pid})
                        MATCH (tgt)-[:DEFINED_IN_FILE]->(mf:File {project_id:$pid})
                        WITH tgt, replace(mf.filepath, 'Cargo.toml', '') AS crate_root
                        MATCH (f:File {project_id:$pid})
                        WHERE f.filepath STARTS WITH crate_root
                        OPTIONAL MATCH (f)-[:CONTAINS]->(s)
                        WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum
                        WITH tgt, f, count(s) AS sym_count
                        RETURN tgt.name AS crate, collect(f.filepath)[..3] AS files
                        ORDER BY crate
                        LIMIT 5
                        """,
                        pid=project_id,
                        crate=target_crate,
                        op="get_related_files_dependent_crates",
                    )
                    upstream = await _execute_read(
                        session,
                        """
                        MATCH (src:CargoCrate {project_id:$pid})-[:DEPENDS_ON_PACKAGE]->(tgt:CargoCrate {project_id:$pid, name:$crate})
                        MATCH (src)-[:DEFINED_IN_FILE]->(mf:File {project_id:$pid})
                        WITH src, replace(mf.filepath, 'Cargo.toml', '') AS crate_root
                        MATCH (f:File {project_id:$pid})
                        WHERE f.filepath STARTS WITH crate_root
                        OPTIONAL MATCH (f)-[:CONTAINS]->(s)
                        WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum
                        WITH src, f, count(s) AS sym_count
                        RETURN src.name AS crate, collect(f.filepath)[..3] AS files
                        ORDER BY crate
                        LIMIT 5
                        """,
                        pid=project_id,
                        crate=target_crate,
                        op="get_related_files_dependent_on_target",
                    )
                    if same_crate:
                        cargo_related.append(f"Crate: {target_crate}")
                        for record in same_crate:
                            cargo_related.append(
                                f"- {record['related_file']} (same crate, symbols: {record.get('sym_count') or 0})"
                            )
                    for record in downstream:
                        files = ", ".join(record.get("files") or [])
                        cargo_related.append(f"- depends on crate `{record['crate']}` via {files}")
                    for record in upstream:
                        files = ", ".join(record.get("files") or [])
                        cargo_related.append(f"- used by crate `{record['crate']}` via {files}")

                if file_path.endswith(".xcworkspace/contents.xcworkspacedata"):
                    workspace_refs = await _execute_read(
                        session,
                        f"""
                        MATCH (w:XcodeWorkspace {{project_id:$pid, filepath:$file_path}})
                        MATCH (w)-[:{_REL_REFERENCES_PROJECT}]->(project_file:File {{project_id:$pid}})
                        RETURN project_file.filepath AS related_file,
                               "referenced by workspace" AS relation
                        ORDER BY related_file
                        LIMIT 10
                        """,
                        pid=project_id,
                        file_path=file_path,
                        op="get_related_files_apple_workspace_refs",
                    )
                    for record in workspace_refs:
                        apple_related.append(
                            f"- {record['related_file']} ({record['relation']})"
                        )
                elif file_path.endswith(".xcodeproj/project.pbxproj"):
                    project_related = await _execute_read(
                        session,
                        f"""
                        MATCH (project_file:File {{project_id:$pid, filepath:$file_path}})
                        OPTIONAL MATCH (workspace:XcodeWorkspace {{project_id:$pid}})-[:{_REL_REFERENCES_PROJECT}]->(project_file)
                        OPTIONAL MATCH (target:XcodeTarget {{project_id:$pid}})
                        WHERE target.project_file = $file_path
                        OPTIONAL MATCH (scheme:XcodeScheme {{project_id:$pid}})-[:{_REL_BUILDS_TARGET}]->(target)
                        OPTIONAL MATCH (target)-[:{_REL_BUNDLES_FILE}]->(bundled_file:File {{project_id:$pid}})
                        WITH collect(DISTINCT CASE
                                 WHEN workspace IS NOT NULL THEN {{
                                     file: workspace.filepath,
                                     reason: "workspace references this project"
                                 }}
                               END) +
                             collect(DISTINCT CASE
                                 WHEN scheme IS NOT NULL THEN {{
                                     file: scheme.filepath,
                                     reason: "scheme builds a target from this project"
                                 }}
                               END) +
                             collect(DISTINCT CASE
                                 WHEN bundled_file IS NOT NULL THEN {{
                                     file: bundled_file.filepath,
                                     reason: "bundled by a target in this project"
                                 }}
                               END) AS related
                        UNWIND related AS row
                        WITH row
                        WHERE row IS NOT NULL AND row.file IS NOT NULL AND row.file <> $file_path
                        RETURN row.file AS related_file, row.reason AS relation
                        ORDER BY
                          CASE row.reason
                            WHEN "workspace references this project" THEN 0
                            WHEN "scheme builds a target from this project" THEN 1
                            ELSE 2
                          END,
                          related_file
                        LIMIT 12
                        """,
                        pid=project_id,
                        file_path=file_path,
                        op="get_related_files_apple_project_context",
                    )
                    for record in project_related:
                        apple_related.append(
                            f"- {record['related_file']} ({record['relation']})"
                        )
                elif "/xcshareddata/xcschemes/" in file_path and file_path.endswith(".xcscheme"):
                    scheme_related = await _execute_read(
                        session,
                        f"""
                        MATCH (scheme:XcodeScheme {{project_id:$pid, filepath:$file_path}})
                        MATCH (scheme)-[:{_REL_BUILDS_TARGET}]->(target:XcodeTarget {{project_id:$pid}})
                        RETURN target.project_file AS related_file,
                               "project owns a target built by this scheme" AS relation
                        ORDER BY related_file
                        LIMIT 10
                        """,
                        pid=project_id,
                        file_path=file_path,
                        op="get_related_files_apple_scheme_context",
                    )
                    for record in scheme_related:
                        apple_related.append(
                            f"- {record['related_file']} ({record['relation']})"
                        )
                elif file_path.endswith("Contents.json"):
                    resource_related = await _execute_read(
                        session,
                        f"""
                        MATCH (target:XcodeTarget {{project_id:$pid}})-[:{_REL_BUNDLES_FILE}]->(:File {{project_id:$pid, filepath:$file_path}})
                        OPTIONAL MATCH (scheme:XcodeScheme {{project_id:$pid}})-[:{_REL_BUILDS_TARGET}]->(target)
                        WITH target, scheme
                        OPTIONAL MATCH (project_file:File {{project_id:$pid, filepath: target.project_file}})
                        OPTIONAL MATCH (workspace:XcodeWorkspace {{project_id:$pid}})-[:{_REL_REFERENCES_PROJECT}]->(project_file)
                        WITH collect(DISTINCT CASE
                                 WHEN project_file IS NOT NULL THEN {{
                                     file: project_file.filepath,
                                     reason: "project bundles this resource"
                                 }}
                               END) +
                             collect(DISTINCT CASE
                                 WHEN scheme IS NOT NULL THEN {{
                                     file: scheme.filepath,
                                     reason: "scheme builds the owning target"
                                 }}
                               END) +
                             collect(DISTINCT CASE
                                 WHEN workspace IS NOT NULL THEN {{
                                     file: workspace.filepath,
                                     reason: "workspace references the owning project"
                                 }}
                               END) AS related
                        UNWIND related AS row
                        WITH row
                        WHERE row IS NOT NULL AND row.file IS NOT NULL
                        RETURN row.file AS related_file, row.reason AS relation
                        ORDER BY
                          CASE row.reason
                            WHEN "project bundles this resource" THEN 0
                            WHEN "scheme builds the owning target" THEN 1
                            ELSE 2
                          END,
                          related_file
                        LIMIT 10
                        """,
                        pid=project_id,
                        file_path=file_path,
                        op="get_related_files_apple_resource_context",
                    )
                    for record in resource_related:
                        apple_related.append(
                            f"- {record['related_file']} ({record['relation']})"
                        )

            cypher = """
            MATCH (f1:File {id: $fid})-[:CONTAINS]->(imp1:Import)
            WITH f1, collect(imp1.source) AS my_imports
            MATCH (f2:File {project_id: $pid})-[:CONTAINS]->(imp2:Import)
            WHERE f2 <> f1 AND imp2.source IN my_imports
            WITH f2.filepath AS related_file,
                 count(imp2) AS shared_imports,
                 collect(DISTINCT imp2.source)[..4] AS sample_imports
            ORDER BY shared_imports DESC LIMIT 10
            RETURN related_file, shared_imports, sample_imports
            """
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                structural_call_records = await _execute_read(
                    session,
                    """
                    MATCH (owner:File {project_id:$pid, filepath:$file_path})-[:CONTAINS]->(target:Node)<-[:CALLS]-(caller:Node)<-[:CONTAINS]-(caller_file:File {project_id:$pid})
                    WHERE target.name IS NOT NULL
                      AND trim(target.name) <> ''
                      AND caller_file <> owner
                    RETURN caller_file.filepath AS related_file,
                           target.name AS symbol
                    ORDER BY related_file, symbol
                    LIMIT 12
                    """,
                    pid=project_id,
                    file_path=file_path,
                    op="get_related_files_symbol_calls",
                )
                structural_inferred_call_records = await _execute_read(
                    session,
                    """
                    MATCH (owner:File {project_id:$pid, filepath:$file_path})-[:CONTAINS]->(target:Node)<-[:CALLS_INFERRED]-(caller:Node)<-[:CONTAINS]-(caller_file:File {project_id:$pid})
                    WHERE target.name IS NOT NULL
                      AND trim(target.name) <> ''
                      AND caller_file <> owner
                    RETURN caller_file.filepath AS related_file,
                           target.name AS symbol
                    ORDER BY related_file, symbol
                    LIMIT 12
                    """,
                    pid=project_id,
                    file_path=file_path,
                    op="get_related_files_symbol_calls_inferred",
                )
                structural_import_records = await _execute_read(
                    session,
                    """
                    MATCH (owner:File {project_id:$pid, filepath:$file_path})-[:CONTAINS]->(target:Node)<-[:IMPORTS_SYMBOL]-(importer:File {project_id:$pid})
                    WHERE target.name IS NOT NULL
                      AND trim(target.name) <> ''
                      AND importer <> owner
                    RETURN importer.filepath AS related_file,
                           target.name AS symbol
                    ORDER BY related_file, symbol
                    LIMIT 12
                    """,
                    pid=project_id,
                    file_path=file_path,
                    op="get_related_files_symbol_imports",
                )
                structural_implicit_import_records = await _execute_read(
                    session,
                    """
                    MATCH (owner:File {project_id:$pid, filepath:$file_path})-[:CONTAINS]->(target:Node)<-[:IMPLICIT_IMPORTS_SYMBOL]-(importer:File {project_id:$pid})
                    WHERE target.name IS NOT NULL
                      AND trim(target.name) <> ''
                      AND importer <> owner
                    RETURN importer.filepath AS related_file,
                           target.name AS symbol
                    ORDER BY related_file, symbol
                    LIMIT 12
                    """,
                    pid=project_id,
                    file_path=file_path,
                    op="get_related_files_symbol_imports",
                )
                related = []
                records = await _execute_read(
                    session,
                    cypher,
                    fid=file_id,
                    pid=project_id,
                    op="get_related_files",
                )
                related_records: list[dict] = []
                for record in records:
                    import_samples = [
                        item
                        for item in (record.get("sample_imports") or [])
                        if not _is_low_signal_related_import_source(item)
                    ]
                    if not import_samples and any(
                        _is_low_signal_related_import_source(item)
                        for item in (record.get("sample_imports") or [])
                    ):
                        continue
                    sample_text = ", ".join(import_samples[:3])
                    reason = f"shares {record['shared_imports']} import source(s)"
                    if sample_text:
                        reason += f": {sample_text}"
                    related_records.append(
                        {
                            "related_file": record["related_file"],
                            "shared_imports": int(record.get("shared_imports") or 0),
                            "useful_import_count": len(import_samples),
                            "reason": reason,
                        }
                    )
                related_records.sort(
                    key=lambda rec: _related_file_rank(
                        file_path,
                        str(rec.get("related_file") or ""),
                        int(rec.get("useful_import_count") or 0),
                        int(rec.get("shared_imports") or 0),
                    )
                )
                related = [f"- {rec['related_file']} ({rec['reason']})" for rec in related_records]
                structural_rollup: dict[str, dict] = {}
                for record in [*structural_call_records, *structural_inferred_call_records]:
                    related_file = str(record.get("related_file") or "").strip()
                    symbol = str(record.get("symbol") or "").strip()
                    if not related_file or not symbol:
                        continue
                    entry = structural_rollup.setdefault(
                        related_file,
                        {"call_hits": 0, "import_hits": 0, "symbols": []},
                    )
                    entry["call_hits"] += 1
                    entry["symbols"].append(symbol)
                for record in [*structural_import_records, *structural_implicit_import_records]:
                    related_file = str(record.get("related_file") or "").strip()
                    symbol = str(record.get("symbol") or "").strip()
                    if not related_file or not symbol:
                        continue
                    entry = structural_rollup.setdefault(
                        related_file,
                        {"call_hits": 0, "import_hits": 0, "symbols": []},
                    )
                    entry["import_hits"] += 1
                    entry["symbols"].append(symbol)
                structural_related_records: list[dict] = []
                for related_file, counts in structural_rollup.items():
                    if (
                        not related_file
                        or related_file == file_path
                        or _is_low_signal_support_path(related_file)
                    ):
                        continue
                    symbols = _dedupe_symbol_names(
                        [str(item) for item in (counts.get("symbols") or []) if str(item).strip()]
                    )
                    call_hits = int(counts.get("call_hits") or 0)
                    import_hits = int(counts.get("import_hits") or 0)
                    if call_hits <= 0 and import_hits <= 0:
                        continue
                    reason_bits: list[str] = []
                    if call_hits:
                        reason_bits.append(f"calls {call_hits} symbol(s)")
                    if import_hits:
                        reason_bits.append(f"imports {import_hits} symbol(s)")
                    if symbols:
                        reason_bits.append(f"symbols: {', '.join(symbols[:3])}")
                    structural_related_records.append(
                        {
                            "related_file": related_file,
                            "call_hits": call_hits,
                            "import_hits": import_hits,
                            "symbols": symbols,
                            "reason": "; ".join(reason_bits),
                        }
                    )
                structural_related_records.sort(
                    key=lambda rec: _structural_related_rank(
                        file_path,
                        str(rec.get("related_file") or ""),
                        int(rec.get("call_hits") or 0),
                        int(rec.get("import_hits") or 0),
                        len(rec.get("symbols") or []),
                    )
                )
                structural_related = [
                    f"- {rec['related_file']} ({rec['reason']})"
                    for rec in structural_related_records
                ]
            if cargo_related or apple_related or structural_related or related:
                output = [
                    "Related Files:",
                    "",
                    "Use this to find the fastest adjacent files to inspect before broadening search.",
                ]
                focus_lines: list[str] = []
                highlighted_entries: set[str] = set()
                if cargo_related:
                    first_same_crate = next((line for line in cargo_related if line.startswith("- ") and "(same crate" in line), None)
                    if first_same_crate:
                        focus_lines.append(f"- start with {first_same_crate[2:]}")
                        highlighted_entries.add(first_same_crate[2:])
                    first_boundary = next(
                        (line for line in cargo_related if "depends on crate" in line or "used by crate" in line),
                        None,
                    )
                    if first_boundary:
                        focus_lines.append(f"- then inspect {first_boundary[2:]}")
                        highlighted_entries.add(first_boundary[2:])
                if apple_related:
                    focus_lines.append(
                        f"{'- then inspect' if focus_lines else '- start with'} {apple_related[0][2:]}"
                    )
                    highlighted_entries.add(apple_related[0][2:])
                if structural_related:
                    prefix = "- then inspect" if focus_lines else "- start with"
                    focus_lines.append(f"{prefix} {structural_related[0][2:]}")
                    highlighted_entries.add(structural_related[0][2:])
                if related:
                    prefix = "- then inspect" if focus_lines else "- start with"
                    focus_lines.append(f"{prefix} {related[0][2:]}")
                    highlighted_entries.add(related[0][2:])
                if focus_lines:
                    output.extend(["", "Inspect First:", *focus_lines[:3]])
                output.extend(
                    line
                    for line in cargo_related
                    if not (line.startswith("- ") and line[2:] in highlighted_entries)
                )
                if apple_related:
                    output.append("Apple build graph:")
                    output.extend(
                        line
                        for line in apple_related
                        if not (line.startswith("- ") and line[2:] in highlighted_entries)
                    )
                if structural_related:
                    output.append("Symbol graph:")
                    output.extend(
                        line
                        for line in structural_related
                        if not (line.startswith("- ") and line[2:] in highlighted_entries)
                    )
                if related:
                    if cargo_related or apple_related or structural_related:
                        output.append("Import graph:")
                    output.extend(
                        line
                        for line in related
                        if not (line.startswith("- ") and line[2:] in highlighted_entries)
                    )
                return "\n".join(output)

            # Fallback: semantic co-mentions based on top symbols in the file
            symbol_query = """
                MATCH (f:File {id: $fid})-[:CONTAINS]->(s)
                WHERE s.name IS NOT NULL
                  AND (s:Function OR s:Method OR s:Class OR s:Struct OR s:Trait
                       OR s:Enum OR s:Protocol OR s:Extension OR s:TypeAlias OR s:AssociatedType)
                RETURN s.name AS name
                LIMIT 10
            """

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await _execute_read(
                    session,
                    symbol_query,
                    fid=file_id,
                    op="get_related_files_symbols",
                )
                symbols = [rec["name"] for rec in records if rec.get("name")]

            symbols = _dedupe_symbol_names(
                [s for s in symbols if isinstance(s, str) and s.strip()]
            )
            if not symbols:
                return "No structurally related files found."

            memory_store, _, _, _, _ = get_memory_modules()
            await memory_store.open_pool()
            async with memory_store._pg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    ors = " OR ".join(["content ILIKE %s"] * len(symbols))
                    sql = (
                        "SELECT file_path, count(*) AS hits "
                        "FROM codebase_embeddings "
                        "WHERE project_id = %s AND file_path <> %s AND (" + ors + ") "
                        "GROUP BY file_path "
                        "ORDER BY hits DESC "
                        "LIMIT 10"
                    )
                    params = [project_id, file_path] + [f"%{s}%" for s in symbols]
                    await cur.execute(sql, params)
                    rows = await cur.fetchall()

            if not rows:
                return "No structurally related files found."

            filtered_rows = [
                (fp, hits)
                for fp, hits in rows
                if not _is_low_signal_support_path(fp) and not _is_test_like_path(fp)
            ]
            if not filtered_rows:
                return "No structurally related files found."

            output = [
                "Related Files (semantic co-mentions):",
                "",
                "Use this when graph structure is thin and you need the nearest semantic neighbors first.",
                "",
                "Inspect First:",
                f"- start with `{filtered_rows[0][0]}` because it shares the strongest semantic co-mention surface",
            ]
            for fp, hits in filtered_rows[1:]:
                preview_symbols = ", ".join(symbols[:3])
                output.append(
                    f"- {fp} (semantic co-mentions: {hits}; symbols: {preview_symbols})"
                )
            return "\n".join(output)
        except Exception as e:
            return f"Error finding related files: {str(e)}"

    @mcp.tool()
    async def find_references(workspace_id: str | list[str], symbol_name: str) -> str:
        """
        Find all locations that reference a symbol — function calls, type usages,
        and any code chunk that mentions the name.

        Combines two sources:
        1. Neo4j [:CALLS|CALLS_INFERRED] edges (precise + inferred call graph hits)
        2. Postgres full-text search over codebase_embeddings (catches type references,
           field accesses, generic bounds, and string literals that the graph misses)

        Use this before renaming or deleting a symbol to find every location that
        must be updated.

        Args:
            workspace_id:  Logical workspace name or absolute project path (or list).
            symbol_name:  Exact name of the symbol to find references for.
        """
        return await references.find_references_impl(workspace_id, symbol_name)

    @mcp.tool()
    async def describe_file(project_path: str, file_path: str) -> str:
        """
        Generate a structural and semantic description of a single file.
        Returns its symbol list (functions, classes, structs) and a preview
        of its most representative semantic chunk. Much faster than reading the
        raw file for orientation.

        Replaces get_file_outline — works in two modes:
        1. Fast outline (no index required):
               describe_file("", "/abs/path/to/file.swift")
               describe_file("", "relative/path.swift")   ← relative to cwd
        2. Full description (ts-pack + Neo4j + Postgres semantic preview):
               describe_file("/project/root", "relative/path.swift")

        Args:
            project_path: Absolute path to project root, or "" for abs-path-only mode.
            file_path:    Relative path within project, or absolute path when project_path="".
        """
        return await file_describe.describe_file_impl(
            project_path=project_path,
            file_path=file_path,
            execute_read=_execute_read,
        )

    @mcp.tool()
    async def visualize_subgraph(workspace_id: str, symbol_name: str) -> str:
        """
        Generate a Mermaid diagram of a symbol's neighborhood in the structural graph.
        Shows the symbol's containing file, what it CALLS, what IMPORTS it, and
        sibling symbols in the same file — up to 2 hops.

        Args:
            workspace_id: Logical workspace name or absolute project path.
            symbol_name: Name of the symbol to visualize.
        """
        try:
            project_id = get_project_id(workspace_id)
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                focus_nodes = await _execute_read(
                    session,
                    symbol_graph.VISUALIZE_SUBGRAPH_FOCUS_CYPHER,
                    name=symbol_name,
                    pid=project_id,
                    op="visualize_subgraph_focus",
                )
                if not focus_nodes:
                    return f"No symbol named '{symbol_name}' found in this project."
                focus = symbol_graph.pick_visualize_candidate(
                    focus_nodes,
                    symbol_name=symbol_name,
                )
                if not focus:
                    return f"No symbol named '{symbol_name}' found in this project."
                focus_id = focus["id"]

                nbr_rows = await _execute_read(
                    session,
                    symbol_graph.VISUALIZE_SUBGRAPH_NEIGHBORS_CYPHER,
                    fid=focus_id,
                    op="visualize_subgraph_neighbors",
                )
                nbr = dict(nbr_rows[0]) if nbr_rows else {}
                nbr = symbol_graph.filter_visualize_neighbors(focus, nbr)
            summary = symbol_graph.format_subgraph_summary(focus, nbr)
            rendered = symbol_graph.render_subgraph_mermaid(focus, nbr)
            if not rendered:
                return f"No relationships found for '{symbol_name}'."
            return f"{summary}\n\n{rendered}"
        except Exception as e:
            return f"Error visualizing subgraph: {str(e)}"
