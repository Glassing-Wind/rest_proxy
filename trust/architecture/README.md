# Trust And Architecture Status

This directory is the stable pointer for the current enterprise-readiness plan.
The canonical status and backlog live in:

- [Tool trust status](../../docs/tool_trust_status.md)
- [Retrieval architecture](../../docs/retrieval_architecture.md)
- [MCP tool coverage tiers](../../docs/mcp_tool_coverage_tiers.md)

Current product contract:

- preferred investigation workflows start with health, overview, search, symbol
  context, call-chain/reference, and provenance tools
- every registered MCP tool must have an explicit tool-catalog entry describing
  when it is worth reaching for
- support, operational, and admin tools are intentionally positioned by exact
  use case instead of being presented as peer first-stop investigation tools

Inside MCP, use `get_mcp_tool_catalog` to choose the right tool for an
indexed-repo task.
