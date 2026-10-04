Question: Explain how the shared HTTP MCP server starts, registers tools, and exposes its health endpoints.

GraphRAG MCP was available: `get_indexing_health` reported 312 files in both indexes, healthy synchronization, and aligned runs.

The shared HTTP server works as follows:

1. **Launch.** The documented entry point is `scripts/start_brain_server_daemon.sh`. It takes a startup lock, checks for an existing process/listener, launches the startup wrapper through `nohup`, and records its PID. The wrapper loads environment settings, selects Python with Uvicorn available, sets `LM_PROXY_TRANSPORT=http`, and runs `brain_server:app` on loopback with one worker and port 8001 by default. ([README.md:60](/Users/michaelmarler/Projects/rest_proxy/README.md:60), [daemon script:17](/Users/michaelmarler/Projects/rest_proxy/scripts/start_brain_server_daemon.sh:17), [startup wrapper:11](/Users/michaelmarler/Projects/rest_proxy/scripts/start_brain_server.sh:11), [startup wrapper:51](/Users/michaelmarler/Projects/rest_proxy/scripts/start_brain_server.sh:51))

2. **Tool registration happens during import.** `brain_server.py` imports the shared `mcp` singleton from `_mcp.py`. That module loads environment configuration, creates `FastMCP("graphrag-brain", stateless_http=False)`, and calls `register_all(mcp)`. The same singleton module serves the STDIO entry point. Registration covers memory, catalog, search, code intelligence, development, documentation, indexing, graph, project tools, and the primary dispatcher. ([brain_server.py:51](/Users/michaelmarler/Projects/rest_proxy/brain_server.py:51), [_mcp.py:1](/Users/michaelmarler/Projects/rest_proxy/_mcp.py:1), [tools/__init__.py:10](/Users/michaelmarler/Projects/rest_proxy/tools/__init__.py:10))

   With `LM_PROXY_TOOL_PROFILE` set to `primary`, `compact`, or `agent`, discovery is filtered to the primary tool list. This changes `list_tools()` visibility; it is explicitly **not an authorization boundary**. ([tools/__init__.py:29](/Users/michaelmarler/Projects/rest_proxy/tools/__init__.py:29), [primary.py:48](/Users/michaelmarler/Projects/rest_proxy/tools/brain/primary.py:48))

3. **Startup and transport.** The outer application is Starlette. Its lifespan registers the running event loop, attempts to open the memory database pool with nonfatal error handling, loads watcher configuration, starts the watcher, and enters `mcp.session_manager.run()`. Shutdown stops the watcher and closes the pool. The MCP-generated application is mounted at `/`, preserving its internal `/mcp` route. Middleware scopes client sessions and adds transport diagnostics. ([brain_server.py:146](/Users/michaelmarler/Projects/rest_proxy/brain_server.py:146), [brain_server.py:194](/Users/michaelmarler/Projects/rest_proxy/brain_server.py:194), [brain_server.py:330](/Users/michaelmarler/Projects/rest_proxy/brain_server.py:330))

The diagnostic endpoints are registered before the catch-all mount:

| Endpoint | Response |
|---|---|
| `/` and `/health` | Server identity, protocol versions, `/mcp` transport path, visible tool count, boot ID, boot fingerprint, uptime, session details, and `"status": "online"`. |
| `/fingerprint` | Boot ID, fingerprint, visible tool count, uptime, and session details. |

Both handlers add `x-graphrag-*` headers for boot ID, fingerprint, session recognition, and active-session count. ([brain_server.py:279](/Users/michaelmarler/Projects/rest_proxy/brain_server.py:279), [brain_server.py:303](/Users/michaelmarler/Projects/rest_proxy/brain_server.py:303), [brain_server.py:130](/Users/michaelmarler/Projects/rest_proxy/brain_server.py:130), [brain_server.py:332](/Users/michaelmarler/Projects/rest_proxy/brain_server.py:332))

**Limitation:** these HTTP health handlers report process/session metadata; they do not probe database availability or index freshness. The fingerprint is computed at import time, not per request. I verified GraphRAG tool availability but did not probe the HTTP endpoints or execute application code, so their current runtime responses remain unverified. ([brain_server.py:60](/Users/michaelmarler/Projects/rest_proxy/brain_server.py:60), [brain_server.py:279](/Users/michaelmarler/Projects/rest_proxy/brain_server.py:279))