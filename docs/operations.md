# MCP and Proxy Operations

## Shared HTTP MCP daemon

The recommended mode is one shared Streamable HTTP daemon:

```bash
./scripts/start_brain_server_daemon.sh
./scripts/brain_server_status.sh
./scripts/restart_brain_server.sh
./scripts/stop_brain_server.sh
```

Clients connect to `http://127.0.0.1:8001/mcp`.

After a restart, reconnect the client so it receives the new process and tool
list. Check freshness with:

```bash
curl -sS -D - http://127.0.0.1:8001/fingerprint
curl -sS -D - http://127.0.0.1:8001/health
```

`x-graphrag-boot-id` changes on restart. `x-graphrag-tool-fingerprint` changes
when registered tools, delegated implementations, shared helpers, transport
runtime, or pinned dependencies change.

Run protocol checks with:

```bash
python scripts/check_mcp_protocol.py
./scripts/run_mcp_tool_parity_smoke.sh
./scripts/check_mcp_stale_session_restart.sh
```

## Client configuration

For clients that accept URL-based MCP configuration:

```toml
[mcp_servers.graphrag-brain]
url = "http://127.0.0.1:8001/mcp"
```

Workspace watching is manual by default. Use `watch_project(path)` and
`unwatch_project(path)`. Roots-capable clients may opt into root synchronization;
transport sessions are not treated as reliable repository identity by default.

## STDIO fallback

For clients without Streamable HTTP support:

```bash
python mcp_server.py
```

The supervisor wrapper adds restart handling:

```bash
python scripts/graphrag_mcp_supervisor.py
./scripts/restart_graphrag_mcp.sh
```

## Inference proxy

Run the proxy independently of the MCP daemon:

```bash
uvicorn proxy:app --host 127.0.0.1 --port 8000
```

Useful endpoints:

- `GET /health`
- `GET /v1/models`
- `GET /api/v1/models`
- `GET /v1/local-models`
- `POST /v1/chat/completions`

`GET /debug/state` is available only when `LM_PROXY_DEBUG=true`.

## Release

Update `VERSION`, `project.version` in `pyproject.toml`, and `CHANGELOG.md`, then
push the corresponding `vX.Y.Z` tag or run the release workflow manually. CI
validates that the three version references agree.
