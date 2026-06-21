# MCP Conformance Checklist

This checklist records the current `rest_proxy` MCP transport posture and the
gaps that still matter for enterprise hardening.

## Confirmed

- Streamable HTTP endpoint is exposed at `/mcp`.
- Stateful HTTP sessions are enabled via `FastMCP(..., stateless_http=False)`.
- `initialize` returns `Mcp-Session-Id` and negotiates protocol version
  `2025-06-18`.
- Health/fingerprint diagnostics expose server boot identity and tool
  fingerprinting for restart validation.
- `DELETE /mcp` with `Mcp-Session-Id` succeeds and terminates the transport
  session.
- Daemon restart rotates boot identity, rejects the previous session with 404,
  identifies it as unknown on the new boot, and accepts a newly initialized
  session afterward.
- Initialization without an `MCP-Protocol-Version` request header negotiates
  `2025-06-18`, preserving the protocol's backward-compatible client path.
- Automated protocol lifecycle checks run in both the CI gate and the standard
  retrieval-quality trust gate.
- Watcher activation is manual by default via `watch_project` /
  `unwatch_project`, which avoids incorrect workspace inference from transport
  sessions alone.

## Partially Verified / Inferred

- Transport/session lifecycle details are mostly delegated to FastMCP and should
  be periodically checked against upstream MCP changes.

## Known Gaps

- No standards-based roots integration is currently implemented for shared HTTP
  clients. Workspace attribution is therefore explicit/manual rather than MCP
  roots-driven.
- The current suite is a focused transport contract rather than a complete
  third-party MCP compliance suite.

## Operational Guidance

- Use `scripts/check_mcp_protocol.py` against the local daemon after transport
  changes.
- For shared HTTP clients, treat workspace watching as an explicit operator
  action, not an inferred transport property.
