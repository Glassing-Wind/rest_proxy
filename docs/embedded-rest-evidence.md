# Shared-owner REST evidence reads — October 6, 2026

The HTTP MCP daemon now offers opt-in `POST /evidence/read` through its existing
registered tools. MCP and REST clients share one process runtime and embedded
graph/vector owner. A separate inference proxy must call this daemon rather than
open the same state directory itself. No inference provider or request injection
behavior changes in this slice.

Set `LM_PROXY_EMBEDDED_REST_ENABLED=1` together with the existing embedded backend
and state configuration, then start `brain_server:app` on loopback as described in
[shared MCP ownership](embedded-mcp-owner.md). The route is absent when the flag is
off or embedded storage is not selected. Tool registration must also be configured;
an unavailable tool returns 503. This route inherits the daemon's deployment access
controls and does not add a separate authentication system.

Example request:

```sh
curl http://127.0.0.1:8001/evidence/read \
  -H 'Content-Type: application/json' \
  -d '{"tool":"describe_embedded_file","arguments":{"project_id":"my-project","file_path":"main.py","max_lines":40,"max_chars":4000}}'
```

Allowed tools are `list_embedded_projects`, `get_embedded_overview`,
`describe_embedded_file`, `get_embedded_file_facts`, `get_embedded_relationships` and
`search_embedded_repository`. Search defaults to **text** here and rejects vector or
hybrid modes, so this endpoint never requests an embedding encoder. Mutation and
model lifecycle tools are not dispatched. Existing tool argument validation and
publication/hash/scope checks remain in effect; unknown arguments are rejected.

Successful responses contain the tool's JSON value directly, including its existing
citations, status or null result. Request bodies are capped at 16,000 bytes and
serialized results at 48,000 bytes. Oversized requests/results return 413; malformed
JSON returns 400; unsupported tools/options or invalid schemas return 422. Unavailable
reads return a generic 503 without returning exception details. A lower-level tool
limit error wrapped by MCP may also return 503. Reduce tool limits for oversized
responses. Only POST is supported.

Validation: four offline contract tests cover route flag/backend gating, parity,
text-only search, invalid schemas, unknown arguments, write rejection, size limits
and sanitized outages. Full local CI passes. An isolated synthetic fixture verifies
original-source parity across real STDIO, Streamable HTTP and REST; the latter two
use the same running daemon. It also verifies REST mutation refusal. This establishes
transport/ownership behavior, not semantic quality or performance. See the
[acceptance receipt](../benchmarks/reports/2026-10-06/embedded-rest-evidence.json).

Priority 2 gains a REST read surface. Priority 4 still needs a shared context-bundle
contract, tokenizer-aware whole-request budgeting, freshness/omissions and duplicate
history handling. Calling an inference model with an assembled bundle and durable
FIRE checkpoint recovery remain separate acceptance gates.
