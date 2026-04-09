"""tools/search/graph_query.py — raw Neo4j queries and definitions lookup."""

import json
import os
from mcp.server.fastmcp import FastMCP

from _helpers import get_project_id, get_workspace_path
from tools.brain.search import core as search_core


def register(mcp: FastMCP) -> None:

    def _project_display_allowed(project_id: str | None, project_path: str | None) -> bool:
        pid = (project_id or "").strip()
        path = (project_path or "").strip()
        if path:
            return True
        if not pid:
            return False
        noisy_prefixes = (
            "profilequery",
            "exportcheck",
            "routecontext",
            "rental-benchmark",
        )
        if pid.startswith(noisy_prefixes):
            return False
        if pid.isdigit():
            return False
        return True

    def _definition_path_penalty(file_path: str | None) -> int:
        norm = (file_path or "").replace("\\", "/").lower()
        if not norm:
            return 5
        if any(
            token in norm
            for token in (
                "/pregeneratedspm/",
                "/vendors/",
                "vendors/",
                "/generated/",
                "/gen/",
                ".gen.ts",
                ".generated.ts",
                ".generated.js",
                "_generated.swift",
            )
        ):
            return 4
        if any(
            token in norm
            for token in (
                "/e2e/",
                "/tests/",
                "/test/",
                ".spec.",
                ".stories.",
                "/storybook/",
                "/fixtures/",
                "/examples/",
            )
        ):
            return 3
        if any(token in norm for token in ("/src/", "src/", "/packages/", "packages/")):
            return 0
        return 2

    def _definition_kind_rank(kind: str | None) -> int:
        return {
            "Function": 0,
            "Method": 0,
            "Class": 1,
            "Struct": 1,
            "Enum": 2,
            "Protocol": 3,
            "Interface": 3,
            "Trait": 3,
            "TypeAlias": 4,
            "AssociatedType": 4,
            "Extension": 5,
            "EnumCase": 6,
        }.get(kind or "", 7)

    @mcp.tool()
    async def query_graph(
        cypher_query: str,
        workspace_id: str = "",
        project_id: str = "",
    ) -> str:
        """
        Execute a raw Cypher query on the Neo4j structural graph.
        Useful for complex relationship analysis.

        Args:
            cypher_query: The Cypher query string.
            workspace_id: Optional logical workspace ID or local project path.
                When provided, the tool also binds `project_id`, `pid`,
                `workspace_id`, `workspace_path`, and `project_path` params.
            project_id: Optional explicit graph project ID. Overrides the
                derived ID when both are provided.
        """
        try:
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()
            if not driver:
                return "Error: Could not connect to Neo4j."

            resolved_workspace_path = ""
            if workspace_id:
                resolved_workspace_path = get_workspace_path(workspace_id)
            resolved_project_id = project_id or (get_project_id(workspace_id) if workspace_id else "")
            params: dict[str, str] = {}
            if workspace_id:
                params["workspace_id"] = workspace_id
            if resolved_workspace_path:
                params["workspace_path"] = resolved_workspace_path
                params["project_path"] = resolved_workspace_path
            if resolved_project_id:
                params["project_id"] = resolved_project_id
                params["pid"] = resolved_project_id

            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                data = await search_core._execute_read(
                    session, cypher_query, op="query_graph", **params
                )
            if not data:
                return "No results found."
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error querying graph: {str(e)}"

    @mcp.tool()
    async def resolve_graph_project(workspace_id: str) -> str:
        """
        Resolve a workspace identifier or local project path to the graph project ID.

        Args:
            workspace_id: Logical workspace ID or absolute project path.
        """
        project_id = get_project_id(workspace_id)
        workspace_path = get_workspace_path(workspace_id)
        return json.dumps(
            {
                "workspace_id": workspace_id,
                "workspace_path": workspace_path,
                "project_id": project_id,
            },
            indent=2,
        )

    @mcp.tool()
    async def find_definitions(symbol_name: str) -> str:
        """
        Search for the definition of a class, function, or struct across ALL indexed projects.
        Ideal for cross-project dependency discovery.

        Args:
            symbol_name: Name of the symbol to find.
        """
        try:
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()
            cypher = """
            MATCH (n)
            WHERE (
                n:Class OR n:Function OR n:Struct OR n:Trait OR n:Enum
                OR n:Method OR n:Protocol OR n:Interface OR n:Extension
                OR n:TypeAlias OR n:AssociatedType OR n:EnumCase
            ) AND n.name = $name
            OPTIONAL MATCH (p:Project {id: n.project_id})
            RETURN n.project_id AS project_id, p.project_path AS project_path,
                   n.filepath AS file, n.start_line AS line,
                   head([label IN labels(n) WHERE label <> 'Node']) AS type
            """
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                records = await search_core._execute_read(
                    session, cypher, name=symbol_name, op="find_definitions"
                )
                filtered = [
                    record
                    for record in records
                    if _project_display_allowed(
                        record.get("project_id"),
                        record.get("project_path"),
                    )
                ]
                filtered.sort(
                    key=lambda record: (
                        _definition_path_penalty(record.get("file")),
                        _definition_kind_rank(record.get("type")),
                        len(record.get("project_path") or record.get("project_id") or ""),
                        len(record.get("file") or ""),
                        record.get("line") or 0,
                    )
                )
                output = [f"Found {symbol_name} in the following locations:"]
                for record in filtered:
                    loc = record["file"] or "unknown"
                    line = record["line"]
                    loc_str = f"{loc}:{line}" if line is not None else loc
                    project_display = record["project_path"] or record["project_id"]
                    output.append(
                        f"- [{record['type']}] Project: {project_display}, File: {loc_str}"
                    )
            if len(output) == 1:
                return f"Symbol '{symbol_name}' not found in any indexed project."
            return "\n".join(output)
        except Exception as e:
            return f"Error finding definition: {str(e)}"
