# Development appliance

This Compose stack is a local development deployment, not a production readiness
claim. The brain daemon listens on port 8000 inside the container, published only
on host loopback. Database services are available on the internal Compose network.

Set `LM_PROXY_APPLIANCE_PG_PASSWORD` and `LM_PROXY_APPLIANCE_NEO4J_PASSWORD` in your
shell. Use a URL-safe PostgreSQL password because it is interpolated into a DSN.
Set `LM_PROXY_APPLIANCE_WORKSPACE` to the absolute repository directory to mount
read-only at `/workspace`. These settings are separate from existing host services;
Compose does not load the host `.env` into the image. Never commit credentials.

```sh
docker compose config --quiet
docker compose up --build -d
curl --fail http://127.0.0.1:8000/health
```

Initialize an MCP client at `http://127.0.0.1:8000/mcp`. Index `/workspace` using
`index_workspace`; poll `get_index_status`, then query `get_indexing_health` and
`describe_file`. The primary catalog exposes 12 tools including the secondary read
analysis dispatcher. Write/indexing tools remain callable directly by a client
configured for admin work; hiding their schemas is not access control.

For lifecycle validation, use a distinct Compose project (`-p rest-proxy-validation`)
and its own named volumes. Record a successful index, query evidence, restart the
stack and repeat those queries. Stop and restart one database to verify clear
readiness/failure behavior and recovery. `down` preserves named data volumes;
`down --volumes` destroys them and is only appropriate for disposable validation.
Do not point validation at existing developer databases.

`LM_PROXY_STARTUP_TIMEOUT_SECONDS` defaults to 60. TCP reachability alone is not
schema readiness; bootstrap must succeed before Uvicorn starts. Watchers are
explicitly disabled in the appliance. This image does not supply TimescaleDB or
pg_cron and does not include or replace an embedding inference service. Semantic
indexing requires a reachable embedding provider; verify that configuration before
claiming one-command end-to-end indexing. Mutable image tags, root execution,
resource budgets, authentication and backups require further production work.
