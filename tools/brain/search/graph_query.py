"""tools/search/graph_query.py — raw Neo4j queries and definitions lookup."""

import json
from mcp.server.fastmcp import FastMCP

from _helpers import get_project_id, get_workspace_path
from tools.brain.search import core as search_core


def register(mcp: FastMCP) -> None:

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
            WHERE (n:Class OR n:Function OR n:Struct OR n:Trait OR n:Enum) AND n.name = $name
            OPTIONAL MATCH (p:Project {id: n.project_id})
            RETURN n.project_id AS project_id, p.project_path AS project_path,
                   n.filepath AS file, n.start_line AS line, labels(n)[0] AS type
            """
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                output = [f"Found {symbol_name} in the following locations:"]
                records = await search_core._execute_read(
                    session, cypher, name=symbol_name, op="find_definitions"
                )
                for record in records:
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
