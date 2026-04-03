# AGENTS.md
# Guidance for agentic coding in this repository.

# ------------------------------------------------------------------------------
# Scope
# ------------------------------------------------------------------------------
# This repo is a FastAPI-based proxy plus GraphRAG tooling. Most code is Python
# 3.11+, with async I/O and optional integrations (Neo4j, Postgres/pgvector,
# Redis, LM Studio). The server is resilient: optional dependencies should not
# break request flow when missing.

# ------------------------------------------------------------------------------
# Build / Run / Lint / Test Commands
# ------------------------------------------------------------------------------
# Environment
# - Install deps:        pip install -r requirements.txt
# - Optional: create .env from .env.example if present and set credentials.
# - Python version:      3.11+ recommended (match production runtime).
# - Dev loop:            run one service at a time; both are standalone.
# - Quick sanity check:  python3 -m py_compile proxy.py
#
# Run the MCP server (tooling / indexing)
# - Start MCP server:    python3 mcp_server.py
# - Index a workspace:   python3 mcp_server.py index_workspace /abs/path/to/project
#
# Run the HTTP proxy
# - Uvicorn entry:       uvicorn proxy:app --host 0.0.0.0 --port 8000
# - Alt entry:           python3 proxy.py   (only if you add __main__ in future)
#
# Lint (optional; no enforced config in repo)
# - Ruff:                ruff check .
# - Pylint:              pylint proxy.py   (or a file list)
# - Lint subset (tool):  tools/dev.py exposes lint_project_subset for MCP use.
#
# Tests (script-style, not a formal test framework)
# - Run a single script: python3 test_neo4j.py
# - Run a single script: python3 test_tool_stream_client.py
# - Run a single script: python3 test_native_index.py
# - Run a single script: python3 verify_streaming.py
# - Pattern for one test: python3 test_<name>.py  (scripts live at repo root)
# - Single test example: python3 test_native_index.py
#
# Notes on tests
# - These test_*.py files are executable scripts; they are not wired to pytest.
# - Some scripts embed local paths or credentials; update before running.
# - Keep tests idempotent and avoid mutating production data.

# ------------------------------------------------------------------------------
# Agent Safety and Workflow
# ------------------------------------------------------------------------------
# - Prefer small, reversible changes; avoid large refactors unless asked.
# - Do not assume external services are available; guard integrations.
# - Use Read/Glob/Grep before edits to confirm context and style.
# - Avoid printing or logging secrets; redact tokens and credentials.

# ------------------------------------------------------------------------------
# Code Style Guidelines
# ------------------------------------------------------------------------------
# Formatting
# - 4-space indentation, no tabs.
# - Keep line length reasonable (~100-120); prioritize readability.
# - Use blank lines to separate logical blocks and sections.
# - Docstrings are used for module-level and public function descriptions.
# - Keep helper functions small; extract helpers for clarity.
#
# Imports
# - Order: standard library, third-party, local modules.
# - Use explicit imports (from typing import List, Dict, Optional).
# - Keep imports at module top unless lazy import is needed to avoid optional
#   dependencies or circular imports.
# - Lazy imports are acceptable inside functions for optional integrations.
# - Avoid wildcard imports; prefer explicit names for clarity.
#
# Typing
# - Prefer Python 3.11 typing (X | Y) when already used in file.
# - Use List/Dict/Optional for consistency with existing files.
# - Add type hints for public functions and core helpers.
# - Use dataclasses for structured records (see memory_types.py).
# - Favor Optional[...] over implicit None when signatures are public.
#
# Naming Conventions
# - snake_case for functions, variables, and modules.
# - UPPER_CASE for module constants and env-driven flags.
# - Class names in CapWords.
# - Private helpers may use a leading underscore.
# - Keep env var names consistent with existing LM_PROXY_* patterns.
#
# Error Handling
# - Optional integrations must fail open (do not crash the proxy).
# - Catch and log exceptions in background tasks and IO boundaries.
# - Never raise in the memory persistence path; use best-effort behavior.
# - Use debug_log() for structured logging when LM_PROXY_DEBUG is enabled.
# - Prefer returning safe defaults instead of throwing on request paths.
# - Neo4j sessions/transactions: use session.execute_read/execute_write with
#   @unit_of_work to apply timeouts/metadata and get automatic retry/commit.
#
# Async / I/O
# - Prefer async functions when interacting with network or storage.
# - Use httpx.AsyncClient for remote calls.
# - Use asyncio.create_task for background tasks that must not block responses.
# - Avoid long blocking operations on the request path.
# - Clean up clients with async context managers when possible.

# Data Handling
# - Use stable_json() for deterministic JSON serialization.
# - Truncate large payloads before logging or storing.
# - Tool outputs can be large; compact or truncate aggressively as needed.
# - Respect feature flags and limits from environment variables.
# - Preserve request/response schemas; avoid breaking compatibility.

# Environment & Configuration
# - Load .env at process start (see proxy.py and mcp_server.py).
# - Treat all integrations as optional; check flags before using.
# - Keep defaults safe; make enabling behavior explicit via env vars.
# - Avoid adding new required env vars; provide sensible fallbacks.
# - ts-pack usage: init/configure cache dir, auto-download missing parsers,
#   and use detect_language + process for chunking.
# - Launch edges: TS_PACK_LAUNCH_EDGES=1 emits LAUNCHES file edges; enable
#   TS_PACK_DEBUG_LAUNCH=1 for per-file launch resolution debug logs.

# Structure & File Layout
# - Core proxy:            proxy.py
# - MCP server entry:      mcp_server.py
# - Memory layer:          memory_store.py, memory_summary.py,
#                          memory_retrieval.py, memory_types.py
# - Indexing tools:        tools/indexing.py, scripts/index_workspace.py
# - Helper utilities:      _helpers.py, _jobs.py
# - Tool registry:         tools/__init__.py
# - Developer tooling:     tools/dev.py
#
# Specific Patterns to Follow
# - Use feature flags near module top with clear names and defaults.
# - Guard optional imports with try/except and fall back gracefully.
# - Use small helper functions to keep endpoints readable.
# - For log messages, prefer key-value payloads (debug_log).
# - Prefer stable ordering in lists/dicts returned by tools.

# Testing Patterns
# - Script tests should be runnable via python3 test_*.py.
# - Keep test scripts self-contained and explicit about env expectations.
# - If adding pytest later, keep scripts compatible or add separate tests/.
# - For new tests, document required env vars at top of file.

# Repository-Specific Notes
# - The proxy must not crash if Redis/Postgres/Neo4j are unavailable.
# - Memory persistence is best-effort and must never break request flow.
# - Tool output compaction is required to control prompt size.
# - The MCP server redirects stdout to stderr to protect JSON-RPC.
# - JSON-RPC handlers should remain strict about response shape.

# ------------------------------------------------------------------------------
# Cursor / Copilot Rules
# ------------------------------------------------------------------------------
# No Cursor rules (.cursor/rules/, .cursorrules) found in this repo.
# No Copilot instructions (.github/copilot-instructions.md) found in this repo.
