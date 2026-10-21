# AGENTS.md
# Guidance for agentic coding in this repository.

# ------------------------------------------------------------------------------
# Scope
# ------------------------------------------------------------------------------
# This repo is a FastAPI-based proxy plus GraphRAG tooling. Most code is Python
# 3.11+, with async I/O and optional integrations (Neo4j, Postgres/pgvector,
# Redis, LM Studio). The server is resilient: optional dependencies should not
# break request flow when missing.
#
# Product goal: supply coding agents (including Codex and open-source models)
# with trustworthy repository evidence and measurably better investigation tools.
# The LM Studio inference proxy is a separate, optional compatibility layer.
# Do not interpret improving the agent/tooling stack as a request to migrate the
# proxy's inference provider. Read docs/next-session-roadmap.md and the latest
# benchmarks/reports evidence before choosing enterprise-readiness work.
#
# Tool choice: for unfamiliar indexed-repo investigations, check indexing health
# and project overview, then use semantic search, symbol context, callers, and
# provenance as the question requires. For a known file, prefer bounded source
# evidence (describe_file with include_source=True) or a direct file read; do not
# force semantic discovery for exact lookups. Count native fallbacks in evaluations.
# Passing tool contracts is not evidence of lower end-to-end latency, token usage,
# or better coding outcomes; those claims require controlled paired measurements.
#
# ChatGPT/Codex workflow: read docs/chatgpt-codex-workflow.md for architecture
# handoffs, acceptance criteria, validation and implementation feedback. ChatGPT
# supplies reasoning; Codex verifies current source and carries authorized work
# through implementation, tests and Git review. Chats do not synchronize themselves.

# ------------------------------------------------------------------------------
# Build / Run / Lint / Test Commands
# ------------------------------------------------------------------------------
# Environment
# - Existing runtime:   use .venv/bin/python (avoid reinstalling working deps).
# - New environment:    ./scripts/setup_project_python.sh
# - Optional: create .env from .env.example if present and set credentials.
# - Python version:      3.14 recommended; 3.11 is the compatibility floor.
# - Dev loop:            run one service at a time; both are standalone.
# - Quick sanity check:  python -m py_compile proxy/app.py mcp_server.py brain_server.py
#
# Run the MCP server (tooling / indexing)
# - Start MCP server:    python mcp_server.py
# - Index a workspace:   python mcp_server.py index_workspace /abs/path/to/project
#   The CLI waits for completion and exits nonzero on failure; the MCP tool
#   returns a job ID immediately for asynchronous status polling.
#
# Run the HTTP proxy
# - Uvicorn entry:       uvicorn proxy:app --host 0.0.0.0 --port 8000
# - Package entry:       proxy/__init__.py exposes proxy.app:app
#
# Lint (Ruff configuration in pyproject.toml; production checks enforced by CI)
# - Changed Python:      .venv/bin/python -m ruff check <changed-paths>
# - Production scope:    see scripts/run_ci_checks.sh for the gated file list.
# - Lint subset (tool):  tools/hands/dev.py exposes lint_project_subset for MCP use.
#
# Tests (executable scripts, commonly using unittest; not wired to pytest)
# - Offline citations:  .venv/bin/python test_file_source_excerpt.py
# - Offline retrieval:  .venv/bin/python test_search_codebase_tool.py
# - Offline handoff:    .venv/bin/python test_task_handoff.py
# - Graph regressions:  ./scripts/check_graph_pipeline.sh
# - Live MCP protocol:  .venv/bin/python scripts/check_mcp_protocol.py
# - Full gate:          ./scripts/run_ci_checks.sh
#
# Notes on tests
# - Select meaningful checks for the change; inspect live-test prerequisites.
# - Full CI includes daemon start/stop and stale-session restart checks. Explain
#   these operational changes before running; use offline checks for setup work.
# - The live protocol check manages only its own MCP client sessions.
# - Keep tests idempotent and avoid mutating production data.

# ------------------------------------------------------------------------------
# Agent Safety and Workflow
# ------------------------------------------------------------------------------
# - Prefer small, reversible changes; avoid large refactors unless asked.
# - Do not assume external services are available; guard integrations.
# - Use Read/Glob/Grep before edits to confirm context and style.
# - Avoid printing or logging secrets; redact tokens and credentials.
# - Complete authorized reversible work without repeated permission questions.
# - Inspect Git state first and preserve unrelated user changes. Explain proposed
#   configuration, indexing and service changes before making them.
# - Follow direct user authorization for commits, pushes and pull requests; stage
#   only intended files, inspect the diff and use normal non-force pushes.

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
# - Use dataclasses for structured records (see memory/types.py).
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
# - Load .env at process start (see proxy/config.py and mcp_server.py).
# - Treat all integrations as optional; check flags before using.
# - Keep defaults safe; make enabling behavior explicit via env vars.
# - Avoid adding new required env vars; provide sensible fallbacks.
# - ts-pack usage: init/configure cache dir, auto-download missing parsers,
#   and use detect_language + process for chunking.
# - Launch edges: TS_PACK_LAUNCH_EDGES=1 emits LAUNCHES file edges; enable
#   TS_PACK_DEBUG_LAUNCH=1 for per-file launch resolution debug logs.

# Structure & File Layout
# - Core proxy:            proxy/ package, exposed as proxy:app
# - HTTP MCP daemon:       brain_server.py
# - STDIO MCP entry:       mcp_server.py
# - Memory/retrieval:      memory/ package
# - GraphRAG core:         graphrag_core/ package
# - Brain MCP tools:       tools/brain/ package
# - Hands/admin tools:     tools/hands/ package
# - Indexing pipeline:     scripts/index_workspace.py
# - Helper utilities:      _helpers.py, _jobs.py, _mcp.py
#
# Specific Patterns to Follow
# - Use feature flags near module top with clear names and defaults.
# - Guard optional imports with try/except and fall back gracefully.
# - Use small helper functions to keep endpoints readable.
# - For log messages, prefer key-value payloads (debug_log).
# - Prefer stable ordering in lists/dicts returned by tools.

# Testing Patterns
# - Script tests should be runnable via python test_*.py.
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


## AWS Agent Toolkit (AWS work only)

<!-- BEGIN AWS Agent Toolkit rules -->
# AWS Guidance

- Where these AWS rules conflict with the project's own instructions, the
  project's instructions take precedence.
- Prefer the AWS MCP Server for AWS interactions — it provides sandboxed
  execution, observability, and audit logging. If unavailable, use the
  AWS CLI directly.
- Before starting a task, check whether a relevant AWS skill is available.
  Load the skill with `retrieve_skill` and prefer its guidance over
  general knowledge.
- When uncertain about specific AWS details (API parameters, permissions,
  limits, error codes), verify against documentation rather than guessing.
  State uncertainty explicitly if you cannot confirm.
- When creating infrastructure, prefer infrastructure-as-code (AWS CDK or
  CloudFormation) over direct CLI commands.
- When working with infrastructure, follow AWS Well-Architected Framework
  principles.
- Do not use em dashes in AWS resource names or descriptions. Use
  hyphens instead.

## Secret Safety

- MUST load the `aws-secrets-manager` skill first for any secret,
  credential, API key, token, or password task. MUST NOT call
  `secretsmanager get-secret-value` or `batch-get-secret-value`, and MUST
  NOT hit the Secrets Manager Agent daemon directly. MUST use
  `{{resolve:secretsmanager:secret-id:SecretString:json-key}}` with
  `asm-exec` so the secret resolves at runtime without entering context.
<!-- END AWS Agent Toolkit rules -->
