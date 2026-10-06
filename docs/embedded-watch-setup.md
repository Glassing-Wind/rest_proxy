# Watch this project — October 5, 2026

The setup action now previews readiness and scope before enabling durable project
watch intent. It preserves the two choices: service permission to run the polling
worker, and project intent to participate.

## One setup command

```sh
# Inspect readiness, blockers and the published manifest scope.
.venv/bin/python scripts/watch_embedded_project.py PROJECT_ID \
  --mcp-url http://127.0.0.1:8001/mcp

# Enable this project's intent when all checks pass.
.venv/bin/python scripts/watch_embedded_project.py PROJECT_ID \
  --mcp-url http://127.0.0.1:8001/mcp --enable
```

The URL can also come from `LM_PROXY_EMBEDDED_OWNER_MCP_URL`. The CLI uses the
existing loopback HTTP owner, never a second database owner. Preview is the default;
`--enable` explicitly authorizes current-manifest watching. Blocked setup exits
nonzero with JSON explaining service/model/source blockers and changes no intent.
Conflicts are returned without a blind retry. The CLI has a 30-second deadline;
a timeout does not prove an enable write failed to commit.

The admin-classified MCP action is
`configure_embedded_project_watch(project_id, enable=False, expected_revision=None,
expected_run_id="")`. Enabling requires the preview revision and publication ID.
This prevents approving an old publication/scope after another index or metadata
update. The compact primary profile exposes its schema through the tool catalog;
admin classification is not authentication.

## Readiness and scope

The response reports:

- Service permission (`LM_PROXY_EMBEDDED_WATCH_ENABLED`) and a running owner task.
- Configured encoder identity matching the publication, plus a responding encoder
  checked with the non-private text `FIRE watch readiness probe`. No repository
  source is sent by this readiness probe. Connecting also validates dimension.
- A bounded source scan using the existing watcher limits and root/path rules.
- Current publication/revision, root, total watched files, first 20 paths, whether
  more paths exist, pending changes and missing-file count. New files are not enrolled.

An empty manifest is blocked: explicitly index the intended files first. Missing
configuration, incompatible/unavailable models, unavailable sources or a stopped
service block enabling. Readiness probes can use a small amount of inference on the
already-loaded local embedding model. They never load/unload a model, change the
inference proxy or certify resident weight/runtime attestation.

For a dedicated development installation, set `LM_PROXY_EMBEDDED_WATCH_ENABLED=1`
on the owning service and restart it. Its polling task may run at startup while
projects remain opted out. The setup action does not change global configuration or
start a worker when service permission is disabled. This implementation does not
enable any real project or install a daemon for the user.

Once enabled, current-manifest edits/deletions can trigger owned snapshot replacement.
Added/recreated omitted files still require explicit manifest indexing. Disable with
`set_embedded_watch_intent(..., requested=False)` using current activity preconditions.
[Dispatch and cancellation details](embedded-refresh-and-watching.md).

## Validation

Eighteen native repository tests pass, including preview without mutation,
service/model blockers, encoder probe, mismatched/stale setup refusal and ready
project activation. Ten runtime tests and full local CI pass. Real owner/CLI subprocess
acceptance checks that a blocked `--enable` prints scope, exits nonzero and leaves
watch intent false, while the existing registration/renewal/discovery checks continue
passing. Ready activation uses an explicit disposable fixture encoder in native
acceptance; it is not a production fallback.

[Acceptance receipt](../benchmarks/reports/2026-10-05/embedded-watch-setup.json).
Private logs: `.runtime/embedded-watch-setup-acceptance/` (mode 700).
