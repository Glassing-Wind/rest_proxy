"""tools/search/graph_query.py — raw Neo4j queries and definitions lookup."""

import json
from mcp.server.fastmcp import FastMCP

from tools.brain.search import core as search_core


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def query_graph(cypher_query: str) -> str:
        """
        Execute a raw Cypher query on the Neo4j structural graph.
        Useful for complex relationship analysis.

        Args:
            cypher_query: The Cypher query string.
        """
        try:
            import graph_bootstrap

            driver = await graph_bootstrap.require_driver()
            if not driver:
                return "Error: Could not connect to Neo4j."
            async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                data = await search_core._execute_read(
                    session, cypher_query, op="query_graph"
                )
            if not data:
                return "No results found."
            return json.dumps(data, indent=2)
        except Exception as e:
            return f"Error querying graph: {str(e)}"

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
            WHERE (n:Class OR n:Function) AND n.name = $name
            RETURN n.project_id AS project, n.filepath AS file,
                   n.start_line AS line, labels(n)[0] AS type
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
                    output.append(
                        f"- [{record['type']}] Project: {record['project']}, File: {loc_str}"
                    )
            if len(output) == 1:
                return f"Symbol '{symbol_name}' not found in any indexed project."
            return "\n".join(output)
        except Exception as e:
            return f"Error finding definition: {str(e)}"
