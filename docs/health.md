# Liveness, readiness and health

The MCP brain and optional inference proxy expose additive health endpoints.
The existing `/health` responses retain their previous payloads and HTTP behavior;
the brain's `/` and `/fingerprint` responses also remain unchanged. Existing health
responses describe process/configuration/transport state and are not dependency
readiness guarantees.

| Endpoint | HTTP behavior | Work performed |
| --- | --- | --- |
| `/live` | 200 with `live: true` when the application responds. | No database, provider, model or embedded-owner calls. |
| `/ready` | 200 when application startup completed and every declared required dependency passes; otherwise 503. | Only the declared read-only dependency probes. |
| `/health/details` | 200 with the same readiness fields, including failures, plus optional telemetry diagnostics. | Shares the readiness probes/cache; the HTTP status itself does not indicate readiness. |

The monitor activates after lifespan startup completes and becomes inactive before
shutdown cleanup. A request made without completed lifespan startup receives an
unready snapshot, even with no required dependencies. Readiness remains separate
from normal request behavior: optional integration failures can still degrade
individual features without breaking the inference request path.

`/health/details` additionally reports the private telemetry layer's safe configuration
and capped export acknowledgements. `/ready` keeps its readiness-only payload;
telemetry is not a required dependency and collector failure does not change its
status. See [telemetry setup and diagnostic limits](telemetry.md).

## Declare the required capability

`LM_PROXY_READY_REQUIRED` is a comma-separated list of dependency names:
`neo4j`, `postgres`, `redis`, `embedded`, `inference`. Names are case-insensitive,
surrounding whitespace is ignored, and repeated names are collapsed. Unknown
names or empty items inside a nonempty list produce a sanitized configuration
error and HTTP 503 from `/ready`.

| Deployment | Default requirement when the variable is unset | Meaning |
| --- | --- | --- |
| MCP brain | Empty list. | Transport startup only. It does not promise indexed-tool/storage availability. |
| Inference proxy | `inference`. | Startup plus connectivity to the selected provider's model catalog. |
| Compose MCP appliance | `neo4j,postgres,redis`, explicitly set in Compose. | Startup plus connectivity to all three declared services. |

An explicitly empty value overrides either application's default and selects
transport-only readiness:

```dotenv
# Local MCP transport without required external storage:
LM_PROXY_READY_REQUIRED=

# Example service-backed contract; enable only the dependencies this deployment requires:
# LM_PROXY_READY_REQUIRED=neo4j,postgres,redis
```

The declaration is independent of feature flags: listing a dependency requests its
check even if a particular feature is disabled. For example, enabling an optional
memory flag does not automatically make that store a readiness requirement. When
running both applications, choose each process's required list explicitly; a shared
environment override applies to both.

## What each probe establishes

| Dependency | Probe | What a passing result means |
| --- | --- | --- |
| `neo4j` | Temporary one-connection driver; read transaction `RETURN 1` in the configured `LM_PROXY_NEO4J_DB`. | The configured credentials/database accept a bounded read. The default database is `neo4j`; explicit existing names remain honored. |
| `postgres` | Temporary connection; `SELECT 1`. | The configured PostgreSQL DSN accepts a read. An absent DSN reports `not_configured`. |
| `redis` | Temporary client; `PING`. | The configured Redis endpoint responds. |
| `inference` | Temporary HTTP client; bounded `GET /models` at the selected LM Studio/OpenAI-compatible base. | The catalog endpoint returns HTTP 200 and a JSON object with a `data` list. An empty list can pass; availability of a requested generation model is not established. |
| `embedded` | Existing process runtime/owner only; check open graph/vector handles and execute `RETURN 1` through a new read-only connection to that owner's graph. | The initialized owner can perform this graph read. No independent database owner is opened. |

Network probes own and close temporary resources; they do not create application
pools, bootstrap schemas or inspect repository content. OpenAI requires a configured
key and sends it only in its probe request. The LM Studio probe sends no bearer
header, matching the current legacy proxy routes. HTTP redirects are disabled and provider
catalog content is bounded to 64 KiB. These probes do not generate text, compute
embeddings or load/unload/download models.

Embedded storage is lazy. If the existing process runtime or combined owner has
not been opened, a required `embedded` check returns `not_initialized` and `/ready`
returns 503. An explicit embedded tool operation can initialize that owner; the
readiness request never performs initialization. This strict requirement can keep
a fresh deployment unready until that explicit operation occurs. Leave the default
transport-only contract when that behavior is appropriate. An already initialized
owner uses its existing locks; the probe cannot bypass an indexing operation.

Readiness is separate from repository indexing and freshness tool contracts.
Service-backed indexing health compares source/index timestamps; embedded publication
evidence has its own explicit contracts and acceptance limits. The readiness checks
do not audit publication/source/vector integrity, parser fidelity, full-text
indexes, semantic quality, query parity, schema compatibility, backup recovery,
model generation, embeddings or request authorization. Temporary service connectivity
also does not attest the state of a separate application connection pool.

## Deadlines, shared work and response privacy

`LM_PROXY_READY_TIMEOUT_SECONDS` sets each probe's deadline, default **2 seconds**.
It must be a finite number from **0.1 through 5**. Invalid values return a
configuration error instead of running probes. Required probes run concurrently;
completed results are cached for **one second**, and simultaneous requests share
one in-flight check. Cancelling a requesting client does not cancel that shared
work. `/ready` and `/health/details` send `Cache-Control: no-store`; their shared
internal cache still applies.

Temporary-resource cleanup receives its own shielded, bounded budget and can extend
the request beyond the configured probe deadline. Native embedded reads also depend
on engine cooperation: native work is drained before its connection can be closed.
The response marks this limit when `embedded` is required. Do not treat the probe
setting as a strict whole-request wall-clock guarantee.

Readiness snapshots contain fixed status/reason/check codes, the required names,
transport state and coverage. They omit URLs, DSNs, filesystem paths, keys,
credentials, provider content and raw exception messages. Reasons such as
`timeout`, `missing_package`, `not_configured`, `not_initialized`, `owner_closed`,
`http_error` and `cleanup_failed` identify the check outcome without exposing
private connection details. Connection settings and requirements are captured for
the lifespan; a configuration change takes effect with a new application lifespan.

For example, a started MCP brain using the default transport-only contract reports:

```json
{
  "ready": true,
  "scope": "transport",
  "coverage": "transport_lifespan",
  "transport": {"status": "active"},
  "required": [],
  "dependencies": {}
}
```

Source: [shared readiness monitor](../graphrag_core/readiness.py),
[MCP brain endpoints/lifespan](../brain_server.py),
[proxy endpoints/lifespan](../proxy/app.py) and
[Compose requirements](../docker-compose.yml). Use the
[current architecture](current-architecture.md) for storage and local trust boundaries.
