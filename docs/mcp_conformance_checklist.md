# MCP Conformance Checklist

This checklist records the current `rest_proxy` MCP transport posture and the
gaps that still matter for enterprise hardening.

## Confirmed

- Streamable HTTP endpoint is exposed at `/mcp`.
- Stateful HTTP sessions are enabled via `FastMCP(..., stateless_http=False)`.
- `initialize` returns `Mcp-Session-Id` and negotiates the installed SDK's
  latest protocol version (`2025-11-25` at the current baseline).
- Health diagnostics expose both the latest and complete supported protocol
  version list; explicit `2025-06-18` clients remain regression-tested.
- Health/fingerprint diagnostics expose server boot identity and a runtime
  fingerprint covering registered tool implementations plus `brain_server.py`.
- `DELETE /mcp` with `Mcp-Session-Id` succeeds and terminates the transport
  session.
- Daemon restart rotates boot identity, rejects the previous session with 404,
  identifies it as unknown on the new boot, and accepts a newly initialized
  session afterward.
- Initialization without an `MCP-Protocol-Version` request header negotiates
  the requested latest SDK version, preserving the backward-compatible header path.
- Automated protocol lifecycle checks run in both the CI gate and the standard
  retrieval-quality trust gate.
- The STDIO lifecycle smoke holds a session past delayed startup work, lists
  tools, and executes a tool call; the server does not write JSON-RPC frames
  outside the SDK transport.
- Watcher activation is manual by default via `watch_project` /
  `unwatch_project`, which avoids incorrect workspace inference from transport
  sessions alone.
- `watch_project()` without an explicit path requests standards-based roots
  from a roots-capable client and pins only valid local `file://` directories;
  roots are never consumed without that explicit tool call.

## Partially Verified / Inferred

- Transport/session lifecycle details are mostly delegated to FastMCP and should
  be periodically checked against upstream MCP changes.

## Known Gaps

- The current suite is a focused transport contract rather than a complete
  third-party MCP compliance suite.

## Operational Guidance

- Use `scripts/check_mcp_protocol.py` against the local daemon after transport
  changes.
- Use `scripts/check_mcp_stdio_lifecycle.py` after STDIO startup or lifecycle
  changes.
- For shared HTTP clients, treat workspace watching as an explicit operator
  action: pass a path to `watch_project(path)` or call `watch_project()` to
  intentionally consume the client's advertised roots.
