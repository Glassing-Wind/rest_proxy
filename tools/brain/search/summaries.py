"""Summary implementations for graph-backed search tools."""

from __future__ import annotations

import fnmatch
import os
import re

from _helpers import get_project_id
from tools.brain.graph import core as graph_tools


def _is_test_like_path(file_path: str) -> bool:
    normalized = (file_path or "").lower()
    return (
        normalized.startswith("tests/")
        or "/tests/" in normalized
        or "__tests__" in normalized
        or "/spec/" in normalized
        or normalized.startswith("spec/")
        or ".test." in normalized
        or normalized.endswith("_test.py")
        or normalized.endswith("_test.rs")
        or normalized.endswith("_test.go")
        or normalized.endswith("_spec.rb")
    )


def _is_stub_like_path(file_path: str) -> bool:
    normalized = (file_path or "").lower()
    return normalized.endswith(".pyi") or normalized.endswith(".d.ts")


LOW_SIGNAL_IMPORT_SYMBOLS = {
    "Error",
    "Result",
    "Language",
    "Snippet",
    "SnippetStatus",
    "ValidationLevel",
    "Fixture",
    "Generator",
}


_EXPORT_ALIAS_RE = re.compile(
    r"export\s*\{(?P<body>[^}]*)\}",
    re.MULTILINE | re.DOTALL,
)
_EXPORT_ALIAS_ITEM_RE = re.compile(
    r"(?:type\s+)?(?P<target>[A-Za-z_][A-Za-z0-9_]*)\s+as\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*)"
)


def _extract_explicit_export_aliases(file_path: str, *, symbol_prefix: str = "") -> list[tuple[str, str]]:
    try:
        text = open(file_path, "r", encoding="utf-8", errors="replace").read()
    except Exception:
        return []
    aliases: list[tuple[str, str]] = []
    for match in _EXPORT_ALIAS_RE.finditer(text):
        body = match.group("body") or ""
        for item in _EXPORT_ALIAS_ITEM_RE.finditer(body):
            alias = item.group("alias")
            target = item.group("target")
            if symbol_prefix and not alias.startswith(symbol_prefix):
                continue
            aliases.append((alias, target))
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for pair in aliases:
        if pair in seen:
            continue
        seen.add(pair)
        out.append(pair)
    return out


def _import_focus_lines(exp_files: list[tuple[str, int, list[str]]], imp_files: list[tuple[str, int, list[str]]]) -> list[str]:
    lines: list[str] = []
    if exp_files:
        file, count, symbols = exp_files[0]
        sample = ", ".join(_useful_symbol_sample(symbols))
        lines.append(f"- start with `{file}` because it pulls the widest explicit symbol surface ({count}: {sample})")
    if imp_files:
        file, count, symbols = imp_files[0]
        sample = ", ".join(_useful_symbol_sample(symbols))
        lines.append(f"- inspect `{file}` next because it relies on the strongest implicit import surface ({count}: {sample})")
    return lines[:3]


def _export_focus_lines(
    top_symbols: list[tuple],
    top_files: list[tuple[str, int, list[str]]],
    export_mode: str,
) -> list[str]:
    lines: list[str] = []
    if top_symbols:
        item = top_symbols[0]
        if len(item) == 5:
            name, target_name, alias_edges, _exporters, importers = item
            rendered = f"{name} -> {target_name}" if alias_edges and name != target_name else str(name)
            lines.append(
                f"- start with `{rendered}` because it has the strongest downstream import surface ({importers} importer(s))"
            )
        else:
            name, count = item
            lines.append(f"- start with `{name}` because it is the strongest public export signal ({count})")
    if top_files:
        file, count, symbols = top_files[0]
        sample = ", ".join(symbols[:3])
        detail = "public-surface" if export_mode == "heuristic" else "export"
        lines.append(f"- inspect `{file}` next because it concentrates the widest {detail} surface ({count}: {sample})")
    return lines[:3]


def _is_low_signal_import_symbol(name: str) -> bool:
    return name in LOW_SIGNAL_IMPORT_SYMBOLS


def _useful_symbol_sample(symbols: list[str], limit: int = 3) -> list[str]:
    filtered = [symbol for symbol in symbols if not _is_low_signal_import_symbol(symbol)]
    if filtered:
        return filtered[:limit]
    return symbols[:limit]


async def get_symbol_imports_overview_impl(
    *,
    driver,
    neo4j_db: str,
    project_path: str,
    limit: int = 20,
    include_implicit: bool = False,
) -> str:
    project_id = get_project_id(project_path)
    limit = max(1, min(int(limit), 100))

    async with driver.session(database=neo4j_db) as session:
        r_exp_count = await graph_tools._execute_read(
            session,
            "MATCH (:File {project_id:$p})-[r:IMPORTS_SYMBOL]->() RETURN count(r) AS n",
            p=project_id,
            op="get_symbol_imports_overview_exp_count",
        )
        exp_count = r_exp_count[0]["n"] if r_exp_count else 0

        imp_count = 0
        if include_implicit:
            r_imp_count = await graph_tools._execute_read(
                session,
                "MATCH (:File {project_id:$p})-[r:IMPLICIT_IMPORTS_SYMBOL]->() RETURN count(r) AS n",
                p=project_id,
                op="get_symbol_imports_overview_imp_count",
            )
            imp_count = r_imp_count[0]["n"] if r_imp_count else 0

        r_exp_symbols = await graph_tools._execute_read(
            session,
            """
            MATCH (f:File {project_id:$p})-[:IMPORTS_SYMBOL]->(s)
            RETURN s.name AS symbol, count(*) AS n
            ORDER BY n DESC
            LIMIT $limit
            """,
            p=project_id,
            limit=limit,
            op="get_symbol_imports_overview_exp_symbols",
        )
        exp_symbols = [
            (rec["symbol"], rec["n"])
            for rec in r_exp_symbols
            if not _is_low_signal_import_symbol(rec["symbol"])
        ]
        if not exp_symbols:
            exp_symbols = [(rec["symbol"], rec["n"]) for rec in r_exp_symbols]

        r_exp_files = await graph_tools._execute_read(
            session,
            """
            MATCH (f:File {project_id:$p})-[:IMPORTS_SYMBOL]->(s)
            WITH f.filepath AS file, count(*) AS n, collect(DISTINCT s.name) AS symbols
            ORDER BY n DESC
            LIMIT $limit
            RETURN file, n, symbols
            """,
            p=project_id,
            limit=limit,
            op="get_symbol_imports_overview_exp_files",
        )
        exp_files = [(rec["file"], rec["n"], rec["symbols"]) for rec in r_exp_files]

        imp_symbols = []
        imp_files = []
        if include_implicit:
            r_imp_symbols = await graph_tools._execute_read(
                session,
                """
                MATCH (f:File {project_id:$p})-[:IMPLICIT_IMPORTS_SYMBOL]->(s)
                WHERE f.filepath ENDS WITH '.swift'
                RETURN s.name AS symbol, count(*) AS n
                ORDER BY n DESC
                LIMIT $limit
                """,
                p=project_id,
                limit=limit,
                op="get_symbol_imports_overview_imp_symbols",
            )
            imp_symbols = [
                (rec["symbol"], rec["n"])
                for rec in r_imp_symbols
                if not _is_low_signal_import_symbol(rec["symbol"])
            ]
            if not imp_symbols:
                imp_symbols = [(rec["symbol"], rec["n"]) for rec in r_imp_symbols]

            r_imp_files = await graph_tools._execute_read(
                session,
                """
                MATCH (f:File {project_id:$p})-[:IMPLICIT_IMPORTS_SYMBOL]->(s)
                WHERE f.filepath ENDS WITH '.swift'
                WITH f.filepath AS file, count(*) AS n, collect(DISTINCT s.name) AS symbols
                ORDER BY n DESC
                LIMIT $limit
                RETURN file, n, symbols
                """,
                p=project_id,
                limit=limit,
                op="get_symbol_imports_overview_imp_files",
            )
            imp_files = [(rec["file"], rec["n"], rec["symbols"]) for rec in r_imp_files]

    if not exp_symbols and not exp_files and not imp_symbols and not imp_files:
        return "No symbol import edges found."

    lines = [f"# Symbol import overview: {project_path.split('/')[-1]}", ""]
    lines.append("Use this to decide which files pull the widest symbol surface and where import coupling is concentrated.")
    focus_lines = _import_focus_lines(exp_files, imp_files)
    if focus_lines:
        lines.append("")
        lines.append("## Inspect First")
        lines.extend(focus_lines)
    lines.append("")
    lines.append("## Counts")
    lines.append(f"IMPORTS_SYMBOL: {exp_count}")
    if include_implicit:
        lines.append(f"IMPLICIT_IMPORTS_SYMBOL: {imp_count}")

    if exp_symbols:
        lines.append("")
        lines.append("## Top explicit imported symbols")
        for name, count in exp_symbols:
            lines.append(f"- {name}  ({count})")
    if exp_files:
        lines.append("")
        lines.append("## Files with most explicit symbol imports")
        for file, count, symbols in exp_files[:limit]:
            sample = ", ".join(_useful_symbol_sample(symbols, limit=6))
            lines.append(f"- {file}  ({count})  [{sample}]")

    if include_implicit and imp_symbols:
        lines.append("")
        lines.append("## Top implicit imported symbols")
        for name, count in imp_symbols:
            lines.append(f"- {name}  ({count})")
    if include_implicit and imp_files:
        lines.append("")
        lines.append("## Files with most implicit symbol imports")
        for file, count, symbols in imp_files[:limit]:
            sample = ", ".join(_useful_symbol_sample(symbols, limit=6))
            lines.append(f"- {file}  ({count})  [{sample}]")

    return "\n".join(lines)


async def get_symbol_exports_summary_impl(
    *,
    driver,
    neo4j_db: str,
    project_path: str,
    limit: int = 20,
    include_paths: list | None = None,
    exclude_paths: list | None = None,
    symbol_prefix: str = "",
) -> str:
    project_id = get_project_id(project_path)
    limit = max(1, min(int(limit), 100))

    def _path_allowed(file_path: str) -> bool:
        if include_paths and not any(fnmatch.fnmatch(file_path, pat) for pat in include_paths):
            return False
        if exclude_paths and any(fnmatch.fnmatch(file_path, pat) for pat in exclude_paths):
            return False
        if not include_paths and _is_test_like_path(file_path):
            return False
        return True

    def _symbol_allowed(name: str) -> bool:
        if not isinstance(name, str) or not name:
            return False
        if symbol_prefix and not name.startswith(symbol_prefix):
            return False
        return True

    def _heuristic_visibility_ok(file_path: str, visibility: str | None, name: str) -> bool:
        visibility_norm = (visibility or "").strip().lower()
        if visibility_norm in {"public", "open", "pub"} or visibility_norm.startswith("pub("):
            return True

        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".py":
            return not name.startswith("_")
        return False

    export_mode = "graph"
    export_edges = 0

    async with driver.session(database=neo4j_db) as session:
        count_rows = await graph_tools._execute_read(
            session,
            "MATCH (:File {project_id:$p})-[r:EXPORTS_SYMBOL]->() RETURN count(r) AS n",
            p=project_id,
            op="get_symbol_exports_summary_count",
        )
        export_edges = count_rows[0]["n"] if count_rows else 0

        if include_paths or exclude_paths or symbol_prefix:
            filtered_rows = await graph_tools._execute_read(
                session,
                """
                MATCH (f:File {project_id:$p})-[:EXPORTS_SYMBOL]->(s)
                OPTIONAL MATCH (f)-[alias:EXPORTS_SYMBOL_AS]->(s)
                OPTIONAL MATCH (importer:File {project_id:$p})-[:IMPORTS_SYMBOL]->(s)
                RETURN f.filepath AS file,
                       coalesce(alias.name, s.name) AS symbol,
                       s.name AS target_symbol,
                       count(DISTINCT alias) AS alias_edges,
                       count(DISTINCT importer) AS importers
                ORDER BY file, symbol
                """,
                p=project_id,
                op="get_symbol_exports_summary_filtered_rows",
            )

            symbol_totals: dict[tuple[str, str], dict[str, object]] = {}
            file_symbols: dict[str, list[str]] = {}

            for rec in filtered_rows:
                file = rec.get("file")
                name = rec.get("symbol")
                target_name = rec.get("target_symbol") or name
                if not isinstance(file, str) or not _path_allowed(file) or not _symbol_allowed(name):
                    continue

                rendered = (
                    f"{name} -> {target_name}"
                    if (rec.get("alias_edges") or 0) and name != target_name
                    else name
                )
                file_symbols.setdefault(file, [])
                if rendered not in file_symbols[file]:
                    file_symbols[file].append(rendered)

                key = (name, target_name)
                entry = symbol_totals.setdefault(
                    key,
                    {
                        "alias_edges": 0,
                        "exporter_files": set(),
                        "importers": 0,
                    },
                )
                entry["alias_edges"] = max(int(entry["alias_edges"]), int(rec.get("alias_edges") or 0))
                entry["importers"] = max(int(entry["importers"]), int(rec.get("importers") or 0))
                cast_files = entry["exporter_files"]
                assert isinstance(cast_files, set)
                cast_files.add(file)

            top_symbols = sorted(
                (
                    (
                        name,
                        target_name,
                        int(data["alias_edges"]),
                        len(data["exporter_files"]),
                        int(data["importers"]),
                    )
                    for (name, target_name), data in symbol_totals.items()
                ),
                key=lambda item: (-item[4], -item[3], item[0], item[1]),
            )[:limit]
            top_files = sorted(
                (
                    (file, len(symbols), symbols)
                    for file, symbols in file_symbols.items()
                    if symbols
                ),
                key=lambda item: (_is_stub_like_path(item[0]), -item[1], item[0]),
            )[:limit]
        else:
            r1 = await graph_tools._execute_read(
                session,
                """
                MATCH (f:File {project_id:$p})-[:EXPORTS_SYMBOL]->(s)
                OPTIONAL MATCH (f)-[alias:EXPORTS_SYMBOL_AS]->(s)
                OPTIONAL MATCH (importer:File {project_id:$p})-[:IMPORTS_SYMBOL]->(s)
                RETURN coalesce(alias.name, s.name) AS symbol,
                       s.name AS target_symbol,
                       count(DISTINCT alias) AS alias_edges,
                       count(DISTINCT f) AS exporters,
                       count(DISTINCT importer) AS importers
                ORDER BY importers DESC, exporters DESC, symbol
                LIMIT $limit
                """,
                p=project_id,
                limit=limit,
                op="get_symbol_exports_summary_symbols",
            )
            top_symbols = []
            for rec in r1:
                name = rec["symbol"]
                if not _symbol_allowed(name):
                    continue
                top_symbols.append(
                    (
                        name,
                        rec.get("target_symbol") or name,
                        rec.get("alias_edges") or 0,
                        rec.get("exporters") or 0,
                        rec.get("importers") or 0,
                    )
                )
            top_symbols = sorted(
                top_symbols,
                key=lambda item: (-item[4], -item[3], item[0], item[1]),
            )[:limit]

            r2 = await graph_tools._execute_read(
                session,
                """
                MATCH (f:File {project_id:$p})-[:EXPORTS_SYMBOL]->(s)
                OPTIONAL MATCH (f)-[alias:EXPORTS_SYMBOL_AS]->(s)
                WITH f.filepath AS file,
                     count(*) AS n,
                     collect(DISTINCT CASE
                       WHEN alias.name IS NOT NULL AND alias.name <> s.name THEN alias.name + ' -> ' + s.name
                       ELSE coalesce(alias.name, s.name)
                     END) AS symbols
                ORDER BY n DESC
                LIMIT $limit
                RETURN file, n, symbols
                """,
                p=project_id,
                limit=limit,
                op="get_symbol_exports_summary_files",
            )
            top_files = []
            for rec in r2:
                file = rec["file"]
                if not _path_allowed(file):
                    continue
                top_files.append((file, rec["n"], rec["symbols"]))
            top_files = sorted(
                top_files,
                key=lambda item: (_is_stub_like_path(item[0]), -item[1], item[0]),
            )[:limit]

        if not top_symbols and not top_files:
            export_mode = "heuristic"
            heuristic_limit = min(limit * 10, 400)
            r3 = await graph_tools._execute_read(
                session,
                """
                MATCH (f:File {project_id:$p})-[:CONTAINS]->(s)
                WHERE s.name IS NOT NULL
                  AND (
                    s:Function OR s:Method OR s:Class OR s:Struct OR s:Trait
                    OR s:Enum OR s:Protocol OR s:Extension OR s:TypeAlias OR s:AssociatedType
                  )
                RETURN f.filepath AS file, s.name AS symbol, coalesce(s.visibility, '') AS visibility
                ORDER BY f.filepath, s.name
                LIMIT $limit
                """,
                p=project_id,
                limit=heuristic_limit,
                op="get_symbol_exports_summary_heuristic",
            )

            file_symbols: dict[str, list[str]] = {}
            symbol_counts: dict[str, int] = {}
            for rec in r3:
                file = rec["file"]
                name = rec["symbol"]
                visibility = rec.get("visibility")
                if not file or not _path_allowed(file) or not _symbol_allowed(name):
                    continue
                if not _heuristic_visibility_ok(file, visibility, name):
                    continue
                file_symbols.setdefault(file, [])
                if name not in file_symbols[file]:
                    file_symbols[file].append(name)
                symbol_counts[name] = symbol_counts.get(name, 0) + 1

            top_symbols = sorted(
                symbol_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:limit]
            top_files = sorted(
                (
                    (file, len(symbols), symbols)
                    for file, symbols in file_symbols.items()
                    if symbols
                ),
                key=lambda item: (-item[1], item[0]),
            )[:limit]
            if include_paths:
                explicit_alias_symbols: dict[str, int] = {}
                explicit_alias_files: list[tuple[str, int, list[str]]] = []
                for rel_path in include_paths:
                    abs_path = os.path.join(project_path, rel_path)
                    alias_pairs = _extract_explicit_export_aliases(
                        abs_path,
                        symbol_prefix=symbol_prefix,
                    )
                    if not alias_pairs:
                        continue
                    rendered = [f"{alias} -> {target}" for alias, target in alias_pairs]
                    explicit_alias_files.append((rel_path, len(rendered), rendered))
                    for alias, target in alias_pairs:
                        explicit_alias_symbols[f"{alias} -> {target}"] = (
                            explicit_alias_symbols.get(f"{alias} -> {target}", 0) + 1
                        )
                if explicit_alias_symbols:
                    top_symbols = sorted(
                        explicit_alias_symbols.items(),
                        key=lambda item: (-item[1], item[0]),
                    )[:limit]
                if explicit_alias_files:
                    top_files = sorted(
                        explicit_alias_files,
                        key=lambda item: (_is_stub_like_path(item[0]), -item[1], item[0]),
                    )[:limit]

    if not top_symbols and not top_files:
        return (
            "No symbol exports found.\n"
            "Checked EXPORTS_SYMBOL edges, then visibility/name-based public-surface heuristics."
        )

    lines = [f"# Symbol export summary: {project_path.split('/')[-1]}", ""]
    lines.append("Use this to find the main public surfaces and decide which exported symbols or files matter first.")
    if export_mode == "graph":
        lines.append(f"Source: EXPORTS_SYMBOL edges ({export_edges})")
    else:
        lines.append(
            "Source: heuristic public-surface inference "
            "(visibility metadata, or Python non-underscore naming when visibility is absent)"
        )
    lines.append("")
    focus_lines = _export_focus_lines(top_symbols, top_files, export_mode)
    if focus_lines:
        lines.append("## Inspect First")
        lines.extend(focus_lines)
        lines.append("")
    if top_symbols:
        lines.append("## Top exported symbols")
        for item in top_symbols:
            if len(item) == 5:
                name, target_name, alias_edges, exporters, importers = item
                if alias_edges and name != target_name:
                    lines.append(
                        f"- {name} -> {target_name}  (imported by {importers} file(s); exported from {exporters} file(s))"
                    )
                else:
                    lines.append(
                        f"- {name}  (imported by {importers} file(s); exported from {exporters} file(s))"
                    )
            else:
                name, count = item
                lines.append(f"- {name}  ({count})")
        lines.append("")
    if top_files:
        lines.append("## Files with most symbol exports")
        for file, count, symbols in top_files:
            sample = ", ".join(_useful_symbol_sample(symbols, limit=6))
            lines.append(f"- {file}  ({count})  [{sample}]")
    return "\n".join(lines)
