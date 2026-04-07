"""Utility implementations for graph tools."""

from __future__ import annotations

from _helpers import get_project_id, get_workspace_path
from tools.brain.graph import core as graph_core


async def _load_cargo_crate_rows(session, project_id: str):
    schema_rows = await graph_core._execute_read(
        session,
        """
        CALL db.labels() YIELD label
        RETURN collect(label) AS labels
        """,
        op="utility_cargo_schema_labels",
    )
    labels = set(schema_rows[0].get("labels") or []) if schema_rows else set()
    if "CargoCrate" not in labels:
        return []
    return await graph_core._execute_read(
        session,
        """
        MATCH (c:CargoCrate {project_id:$p})-[:DEFINED_IN_FILE]->(mf:File {project_id:$p})
        RETURN c.name AS crate,
               c.crate_name AS crate_name,
               mf.filepath AS manifest_path
        ORDER BY size(mf.filepath) DESC, c.name
        """,
        p=project_id,
        op="utility_cargo_crates",
    )


def _cargo_manifest_dir(manifest_path: str | None) -> str:
    if not manifest_path:
        return ""
    return manifest_path[:-len("Cargo.toml")] if manifest_path.endswith("Cargo.toml") else manifest_path


def _match_cargo_crate(filepath: str | None, crate_rows) -> tuple[str | None, str | None]:
    if not filepath:
        return None, None
    for row in crate_rows:
        crate_root = _cargo_manifest_dir(row.get("manifest_path"))
        if crate_root and filepath.startswith(crate_root):
            return row.get("crate"), row.get("crate_name")
    return None, None


def _group_by_crate(rows: list[dict], *, item_key: str, line_builder) -> list[str]:
    groups: dict[str, list[str]] = {}
    for row in rows:
        crate = row.get(item_key) or "(unowned)"
        groups.setdefault(crate, [])
        groups[crate].append(line_builder(row))
    lines: list[str] = []
    for crate, items in groups.items():
        unique = list(dict.fromkeys(items))
        if not unique:
            continue
        lines.append(f"Crate: {crate}")
        for item in unique:
            lines.append(f"- {item}")
    return lines


def _is_test_like_path(filepath: str | None) -> bool:
    if not filepath:
        return False
    return (
        filepath.startswith("tests/")
        or "/tests/" in filepath
        or "__tests__" in filepath
        or ".test." in filepath
        or filepath.endswith("_test.py")
        or filepath.endswith("_spec.rb")
    )


async def _load_cargo_dependency_rows(session, project_id: str, limit: int):
    return await graph_core._execute_read(
        session,
        """
        MATCH (src:CargoCrate {project_id:$p})-[:DEFINED_IN_FILE]->(src_mf:File {project_id:$p})
        MATCH (src)-[:DEPENDS_ON_PACKAGE]->(dep:CargoCrate {project_id:$p})
        OPTIONAL MATCH (dep)-[:DEFINED_IN_FILE]->(dep_mf:File {project_id:$p})
        RETURN src.name AS src_crate,
               src_mf.filepath AS src_manifest,
               dep.name AS dep_crate,
               dep_mf.filepath AS dep_manifest
        ORDER BY
          CASE WHEN dep_mf.filepath IS NULL THEN 1 ELSE 0 END,
          src.name,
          dep.name
        LIMIT $limit
        """,
        p=project_id,
        limit=limit,
        op="utility_cargo_dependency_rows",
    )

async def get_heuristic_flow_summary_impl(
    *,
    driver,
    neo4j_db: str,
    workspace_id: str,
    limit: int = 20,
    as_table: bool = False,
) -> str:
    project_id = get_project_id(workspace_id)
    async with driver.session(database=neo4j_db) as session:
        result = await graph_core._execute_read(
            session,
            """
            MATCH (f1:File {project_id: $p})
            WHERE (f1.filepath CONTAINS 'ui' OR f1.filepath CONTAINS 'view' OR f1.filepath CONTAINS 'component' OR f1.filepath CONTAINS 'pages')
              AND NOT (f1.filepath CONTAINS 'test' OR f1.filepath CONTAINS 'spec')
            MATCH (f1)-[:IMPORTS]->(f2:File {project_id: $p})
            WHERE (f2.filepath CONTAINS 'api' OR f2.filepath CONTAINS 'client' OR f2.filepath CONTAINS 'controller' OR f2.filepath CONTAINS 'routes')
            OPTIONAL MATCH (f2)-[:IMPORTS]->(f3:File {project_id: $p})
            WHERE (f3.filepath CONTAINS 'service' OR f3.filepath CONTAINS 'domain' OR f3.filepath CONTAINS 'provider' OR f3.filepath CONTAINS 'usecase')
            OPTIONAL MATCH (f3)-[:IMPORTS]->(f4:File {project_id: $p})
            WHERE (f4.filepath CONTAINS 'model' OR f4.filepath CONTAINS 'db' OR f4.filepath CONTAINS 'entity' OR f4.filepath CONTAINS 'schema')
            RETURN f1.filepath AS ui, f2.filepath AS api, f3.filepath AS svc, f4.filepath AS model
            ORDER BY ui, api
            LIMIT $limit
            """,
            p=project_id,
            limit=limit * 2,
            op="get_heuristic_flow_summary",
        )
        cargo_rows = await _load_cargo_crate_rows(session, project_id)
    if not result:
        if cargo_rows:
            async with driver.session(database=neo4j_db) as session:
                dep_rows = await _load_cargo_dependency_rows(session, project_id, limit * 3)
            if dep_rows:
                rendered = []
                for row in dep_rows[:limit]:
                    src_crate = row.get("src_crate")
                    dep_crate = row.get("dep_crate")
                    if not src_crate or not dep_crate:
                        continue
                    relation = f"{src_crate} -> {dep_crate}"
                    src_manifest = row.get("src_manifest")
                    dep_manifest = row.get("dep_manifest")
                    detail_bits = []
                    if src_manifest:
                        detail_bits.append(src_manifest)
                    if dep_manifest:
                        detail_bits.append(dep_manifest)
                    detail = f" ({' -> '.join(detail_bits)})" if detail_bits else ""
                    rendered.append({"crate": src_crate, "line": relation + detail})
                if rendered:
                    return "\n".join(
                        ["### Heuristic Flow (Cargo crate dependencies)\n"]
                        + _group_by_crate(rendered, item_key="crate", line_builder=lambda row: row["line"])
                    )
        return "No heuristic paths found."

    rows = []
    for row in result:
        path = [value for value in [row.get("ui"), row.get("api"), row.get("svc"), row.get("model")] if value]
        if len(path) >= 2:
            api_crate, _ = _match_cargo_crate(row.get("api"), cargo_rows)
            svc_crate, _ = _match_cargo_crate(row.get("svc"), cargo_rows)
            rows.append({"path": path, "api_crate": api_crate, "svc_crate": svc_crate})

    if not rows:
        return "No heuristic paths found."

    if as_table:
        output = [
            "| API Crate | Service Crate | Origin | Endpoint | Secondary | Data |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows[:limit]:
            padded = row["path"] + [""] * (4 - len(row["path"]))
            output.append(
                f"| {row['api_crate'] or ''} | {row['svc_crate'] or ''} | {' | '.join(padded)} |"
            )
        return "\n".join(output)
    rendered = []
    for row in rows[:limit]:
        prefix_bits = []
        if row["api_crate"]:
            prefix_bits.append(f"api_crate={row['api_crate']}")
        if row["svc_crate"] and row["svc_crate"] != row["api_crate"]:
            prefix_bits.append(f"service_crate={row['svc_crate']}")
        if prefix_bits:
            rendered.append(
                {
                    "crate": row["api_crate"] or row["svc_crate"],
                    "line": f"[{', '.join(prefix_bits)}] " + " -> ".join(row["path"]),
                }
            )
        else:
            rendered.append(
                {
                    "crate": row["api_crate"] or row["svc_crate"],
                    "line": " -> ".join(row["path"]),
                }
            )
    if any(row.get("crate") for row in rendered):
        return "\n".join(
            _group_by_crate(rendered, item_key="crate", line_builder=lambda row: row["line"])
        )
    return "\n".join(row["line"] for row in rendered)


async def get_topology_summary_impl(
    *,
    driver,
    neo4j_db: str,
    workspace_id: str,
    limit: int = 10,
) -> str:
    project_id = get_project_id(workspace_id)
    async with driver.session(database=neo4j_db) as session:
        result = await graph_core._execute_read(
            session,
            """
            MATCH (f:File {project_id: $p})
            OPTIONAL MATCH (f)-[:IMPORTS]->(out:File {project_id: $p})
            OPTIONAL MATCH (in:File {project_id: $p})-[:IMPORTS]->(f)
            WITH f, count(DISTINCT out) AS outbound, count(DISTINCT in) AS inbound
            WHERE inbound + outbound > 0
            RETURN f.filepath AS fp, inbound, outbound
            ORDER BY inbound + outbound DESC
            LIMIT $limit
            """,
            p=project_id,
            limit=limit,
            op="get_topology_summary",
        )
        cargo_rows = await _load_cargo_crate_rows(session, project_id)
    if not result:
        return "No architectural topology found (index might be empty)."

    output = ["### Architectural Topology (Most Connected Files)\n"]
    rendered = []
    for rec in result:
        crate, _ = _match_cargo_crate(rec.get("fp"), cargo_rows)
        crate_part = f" [crate:{crate}]" if crate else ""
        rendered.append(
            {
                "crate": crate,
                "line": f"`{rec['fp']}`{crate_part}: {rec['inbound']} incoming, {rec['outbound']} outgoing imports",
            }
        )
    non_test = [row for row in rendered if not _is_test_like_path(row["line"])]
    test_like = [row for row in rendered if _is_test_like_path(row["line"])]
    rendered = non_test + test_like
    if any(row.get("crate") for row in rendered):
        output.extend(_group_by_crate(rendered, item_key="crate", line_builder=lambda row: row["line"]))
    else:
        output.extend(f"- {row['line']}" for row in rendered)
    return "\n".join(output)
