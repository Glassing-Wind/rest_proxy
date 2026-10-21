# Health and readiness acceptance — October 11, 2026

Both applications now expose `/live`, `/ready` and `/health/details` while preserving
legacy health responses. Readiness requires completed lifespan startup and all
explicitly required read-only probes. MCP defaults to transport only; the inference
proxy defaults to model-catalog connectivity; Compose declares Neo4j/PostgreSQL/Redis.
The container health check uses `/ready`.

[Health contract](../../../../docs/health.md) defines configuration, privacy, caching,
lazy embedded behavior and probe limits. [Validation receipt](validation.json)
identifies exact staged source bytes; [isolated gate log](isolated-ci.log) retains
full output, with trailing spaces normalized for Git. The complete gate passed
100 unittest suite executions / **893 test executions, including 9 skips**, plus
Ruff, graph pipeline and MCP STDIO/protocol/restart/roots checks. These are executions,
not distinct tests. Existing fixture warnings remain in the retained log.

Nineteen focused readiness checks ran with networking denied, and four actual ASGI
route/lifespan tests use injected probes. A separate native disposable-owner check
passed a real Ladybug read through the existing combined owner and verified monitor
shutdown did not close that owner. The fixture then closed its own native state.
Independent review found no actionable issue. Compose configuration passed using
dummy credentials without starting containers. No operational service, model,
index, .env or dependency environment was changed, and no hosted job was dispatched.

Passing connectivity does not prove generation, embeddings, schema/FTS/vector/source
integrity, all tool parity, backup recovery or authorization. The required embedded
probe never opens a lazy owner; it stays unready until an explicit operation does so.
Native work/cleanup still depends on engine cooperation, so the timeout is not a
strict whole-request wall-clock guarantee. Optional request-path failures remain
best-effort. Preexisting unrelated working-tree changes are excluded from this slice.
