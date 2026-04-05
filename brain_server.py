"""
brain_server.py — Dedicated entry point for the GraphRAG MCP Brain.

This module provides the central 'Brain' (reasoning and code intelligence tools) 
via the modern Streamable HTTP (2025-03-26 standard) transport.

It is distinct from the Stateful Chat Proxy to avoid mixed concerns.
"""

import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from _mcp import mcp
from _jobs import client_session_id

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages the lifecycle of the Brain server and its dependencies."""
    # Startup: Initialize external dependencies (Postgres/Neo4j)
    try:
        from proxy.config import _memory_store
        if _memory_store is not None:
            await _memory_store.open_pool()
            print("[lm-proxy] Brain Server: Database connection pool opened.", file=sys.stderr)
    except Exception as exc:
        print(f"[lm-proxy] Brain Server: Failed to open connection pool. error={exc}", file=sys.stderr)
    
    yield
    
    # Shutdown logic can go here if needed

# Initialize the main FastAPI application for the Brain
app = FastAPI(
    title="GraphRAG MCP Brain (Streamable HTTP)",
    lifespan=lifespan
)

@app.middleware("http")
async def session_middleware(request: Request, call_next):
    """Extracts session ID from headers and sets the ContextVar."""
    session_id = request.headers.get("X-Session-ID")
    token = client_session_id.set(session_id)
    try:
        response = await call_next(request)
        return response
    finally:
        client_session_id.reset(token)

# Generate the modern Streamable HTTP MCP application
# This unified endpoint handles both POST and streaming GET requests.
mcp_app = mcp.streamable_http_app()

# Mount the MCP application into the FastAPI app
app.mount("/mcp", mcp_app)

@app.get("/")
async def root():
    """Simple status check for the Brain server."""
    return {
        "server": "GraphRAG MCP Brain",
        "standard": "Streamable HTTP (2025-03-26)",
        "transport_path": "/mcp",
        "status": "online"
    }

if __name__ == "__main__":
    import uvicorn
    # Defaulting to port 8001 to keep it separate from the Chat Proxy on 8000
    uvicorn.run(app, host="0.0.0.0", port=8001)
