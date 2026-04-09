"""Helpers for file description and outline previews."""

from __future__ import annotations

import os

from _helpers import get_memory_modules
from ts_diagnostics import normalize_ts_pack_result


def resolve_describe_paths(project_path: str, file_path: str) -> tuple[str, str]:
    if not project_path:
        abs_path = os.path.abspath(file_path)
        return abs_path, abs_path
    abs_path = os.path.join(project_path, file_path) if not os.path.isabs(file_path) else file_path
    return abs_path, file_path


def format_ts_pack_symbols(result: dict) -> tuple[list[str], str]:
    ts_symbols: list[str] = []
    error_count = (result.get("metrics") or {}).get("error_count", 0)
    language = result.get("_language") or "?"
    language_label = f"  [{language}]"
    if error_count:
        language_label += f"  ⚠ {error_count} syntax error(s)"

    def _fmt(items: list, depth: int = 0) -> None:
        pad = "  " * depth
        for item in items:
            name = item.get("name") or "?"
            kind = item.get("kind") or ""
            sig = item.get("signature") or ""
            span = item.get("span") or {}
            sl = (span.get("start_line") or 0) + 1
            el = (span.get("end_line") or 0) + 1
            loc = f"  L{sl}–{el}" if sl else ""
            label = sig if sig else f"{kind} {name}"
            ts_symbols.append(f"{pad}  {label}{loc}")
            _fmt(item.get("children") or [], depth + 1)

    _fmt(result.get("structure") or [])
    return ts_symbols, language_label


async def describe_file_impl(
    *,
    project_path: str,
    file_path: str,
    execute_read,
) -> str:
    abs_path, display_path = resolve_describe_paths(project_path, file_path)
    lines = [f"=== {display_path} ==="]

    ts_symbols: list[str] = []
    try:
        import tree_sitter_language_pack as ts_pack

        if os.path.exists(abs_path):
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                code = fh.read()
            lang = ts_pack.detect_language(abs_path)
            if lang:
                cfg = ts_pack.ProcessConfig(lang)
                cfg.diagnostics = True
                result = normalize_ts_pack_result(code, lang, ts_pack.process(code, config=cfg))
                result["_language"] = lang
                ts_symbols, lang_label = format_ts_pack_symbols(result)
                lines.append(lang_label)
    except Exception:
        pass

    use_symbols = ts_symbols
    if project_path:
        try:
            from _helpers import get_project_id, normalize_neo4j_path
            import graph_bootstrap

            project_id = get_project_id(project_path)
            rel_path = os.path.relpath(abs_path, project_path)
            rel_path = normalize_neo4j_path(rel_path)
            file_id = f"{project_id}:file:{rel_path}"
            driver = await graph_bootstrap.require_driver()
            sym_cypher = """
            MATCH (f:File {id: $fid})-[:CONTAINS]->(s)
            WHERE s:Function OR s:Class OR s:Struct OR s:Trait OR s:Enum OR s:Module
               OR s:Method OR s:Protocol OR s:Interface OR s:Extension
               OR s:TypeAlias OR s:AssociatedType OR s:EnumCase
            RETURN head([label IN labels(s) WHERE label <> 'Node']) AS kind, s.name AS name,
                   s.start_line AS start, s.end_line AS end,
                   s.signature AS sig
            ORDER BY s.start_line
            """
            neo_symbols: list[str] = []
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await execute_read(
                    session,
                    sym_cypher,
                    fid=file_id,
                    op="describe_file_symbols",
                )
                for rec in records:
                    loc = f":{rec['start']}-{rec['end']}" if rec["start"] else ""
                    sig = f"  →  {rec['sig']}" if rec["sig"] else ""
                    neo_symbols.append(f"  [{rec['kind']}] {rec['name']}{loc}{sig}")
            use_symbols = neo_symbols or ts_symbols
        except Exception:
            use_symbols = ts_symbols

    if use_symbols:
        preview_count = min(10, len(use_symbols))
        lines.append(f"Top symbols ({preview_count} of {len(use_symbols)}):")
        lines.extend(use_symbols[:preview_count])
        if len(use_symbols) > preview_count:
            lines.append(f"More symbols available: {len(use_symbols) - preview_count}")
            lines.append("Symbols (up to 40):")
            lines.extend(use_symbols[:40])
    else:
        lines.append("No symbols found.")

    if project_path:
        try:
            from _helpers import get_project_id

            project_id = get_project_id(project_path)
            rel_path = os.path.relpath(abs_path, project_path)
            memory_store, _, _, _, _ = get_memory_modules()
            await memory_store.open_pool()
            async with memory_store._pg_pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT content FROM codebase_embeddings "
                        "WHERE project_id = %s AND file_path = %s "
                        "ORDER BY chunk_index LIMIT 1",
                        (project_id, rel_path),
                    )
                    row = await cur.fetchone()
                if row:
                    lines.append(f"\nFirst chunk preview:\n{row[0][:500].rstrip()}")
        except Exception:
            pass

    return "\n".join(lines)
