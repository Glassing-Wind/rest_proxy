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
- Watcher activation is manual by default via `watch_project` /
  `unwatch_project`, which avoids incorrect workspace inference from transport
  sessions alone.

## Partially Verified / Inferred

- Transport/session lifecycle details are mostly delegated to FastMCP and should
- be periodically checked against upstream MCP changes.
- The server accepts requests without an `MCP-Protocol-Version` header. This is
  compatible with the spec's backward-compatibility guidance, but the exact
  negotiated fallback behavior should be regression-tested if client support
  matters.

## Known Gaps

- No standards-based roots integration is currently implemented for shared HTTP
  clients. Workspace attribution is therefore explicit/manual rather than MCP
  roots-driven.
- No automated MCP compliance suite is wired into CI yet.
- No protocol regression currently checks stale-session behavior across daemon
  restart.

## Operational Guidance

- Use `scripts/check_mcp_protocol.py` against the local daemon after transport
  changes.
- For shared HTTP clients, treat workspace watching as an explicit operator
  action, not an inferred transport property.
