# Embedded IDE registration and discovery — October 5, 2026

The existing `scripts/register_session.py` CLI now supports explicit registration,
release and discovery through an already-running owning Streamable HTTP MCP
service. The registrar never opens Ladybug or LanceDB. It uses the current
publication and workspace-activity revision to refresh the existing durable lease.

## Usage

Start/configure the owning embedded HTTP service first. Provide a loopback HTTP
endpoint with an explicit port and `/mcp` path, plus a stable caller-selected session
identifier of at most 128 characters. No implicit owner startup or model loading
occurs. Example commands (replace the port, workspace and session identifier):

```sh
.venv/bin/python scripts/register_session.py /absolute/workspace \
  --embedded --mcp-url http://127.0.0.1:8001/mcp --session-id ide-window-1

.venv/bin/python scripts/register_session.py --embedded --discover \
  --mcp-url http://127.0.0.1:8001/mcp --session-id ide-window-1

.venv/bin/python scripts/register_session.py /absolute/workspace \
  --embedded --mcp-url http://127.0.0.1:8001/mcp --session-id ide-window-1 --lease-seconds 0
```

`LM_PROXY_EMBEDDED_OWNER_MCP_URL` and `LM_PROXY_EMBEDDED_SESSION_ID` supply CLI
option defaults. Explicit embedded storage selection also chooses the embedded
CLI branch. Embedded mode needs no `VSCODE_PID`; session identifiers are client
assertions, not PIDs or authentication. Successful commands print JSON, including
the canonical workspace path on discovery. Failures exit nonzero and never fall
back to writing legacy JSON registries. The original legacy CLI behavior remains
available when embedded mode is not selected.

Refresh lasts 900 seconds by default; the existing 60..3,600 second bounds apply.
The IDE/client must explicitly refresh before expiry or release when finished.
No heartbeat scheduler is installed. Registration first resolves the canonical
published workspace, verifies the activity root, reads revision/run preconditions,
and writes once. A conflict requires reviewing state before retrying. A 15-second
operation deadline bounds waiting for the owner; a timeout/disconnection does not
prove a write failed to commit, so inspect current activity before retrying.

## Discovery semantics

New primary tool `resolve_embedded_session(session_id)` reads root-bound activity
and current publication in one transaction. It resolves exactly one unexpired
lease, refuses ambiguity and excludes changed/deleted roots and expired leases.
It returns project/root/run/expiry and explicitly does not verify process liveness.
More than 32 matching candidate records returns a selection limit rather than
choosing a partial result; ambiguity choices are bounded to 20. Session identifiers
are serialized/parameterized, including quoted or non-ASCII names.

This is explicit owner-backed discovery for clients and the registrar CLI.
Synchronous legacy config/supervisor discovery still reads JSON. An IDE adapter
can consume the discovery JSON while connecting to the same HTTP owner; starting
another native owner on the same graph is not supported. No legacy registry import,
PID pruning or automatic supervisor rewiring is performed here.

## Validation

Three offline client tests cover loopback endpoint validation, exact revision/run
forwarding, conflict refusal without retry, missing/wrong-root registration and
ambiguous discovery. Fifteen native repository tests pass, including quoted session
IDs, unique/ambiguous discovery, expiry, deletion and root-change exclusion. Ten
runtime/MCP tests and full local CI pass.

A real disposable HTTP owner plus independent registrar subprocesses verifies
registration, discovery, release, missing/released session refusal and missing
workspace refusal. The owner stays usable across operations; sentinel legacy
session/active-session/watch files remain byte-for-byte unchanged. Disposable
indexing uses synthetic vectors and no model. The HTTP service is launched outside
the repository working directory to prevent its `.env` overriding fixture dimension.
No real IDE mapping or watch is registered by acceptance.

[Acceptance receipt](../benchmarks/reports/2026-10-05/embedded-ide-registration.json).
Private logs: `.runtime/embedded-registrar-acceptance/` (mode 700).

Next: integrate a chosen IDE adapter/supervisor transport and explicit lease refresh
policy; implement owned embedded watcher dispatch with manifest selection and
cancellation/recovery. Watch intent still does not activate a worker. Reference/import
query parity, REST ownership, historical jobs and FIRE checkpoints remain pending.
