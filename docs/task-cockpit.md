# Local Task Cockpit

The first Cockpit slice is a standalone browser view of **one existing task's
status**, backed by the existing TaskRegistry. It adds a small REST adapter and
static interface, without a frontend build, model call or new storage owner.
It does not dispatch work, claim tasks, approve findings or connect private
investigation and lesson datasets.

## Start a synthetic example

From the repository root, choose a new private directory outside Git. Its parent
must already exist; the launcher creates the final directory with mode 700:

```sh
.venv/bin/python -m scripts.task_cockpit --demo \
  --runtime "$HOME/.local/share/rest_proxy/cockpit-example"
```

The launcher binds only `127.0.0.1`, choosing an unused port unless `--port` is
provided. It prints the base URL and the location of a private `launch.json` file.
Open the **URL inside that file** locally; do not paste it into chat, reports or
Git. It contains the per-launch access capability in a fragment. The interface
immediately removes that fragment and keeps the code in memory only. Refreshing
the browser requires reopening the private link or entering its access code.
**Refresh status** updates the selected task without reloading the page.

The demo creates a fresh synthetic registry and empty workspace under the runtime
directory. Stopping the process removes only its own launch file; synthetic data
remains available for inspection. Restarting `--demo` creates a new example.
An existing launch file is never overwritten. After a crash, confirm its PID has
stopped before removing the stale launch file yourself.

## Select an existing task

```sh
.venv/bin/python -m scripts.task_cockpit \
  --runtime /absolute/private/cockpit-launch \
  --state /absolute/private/task-registry \
  --project example-project --task existing-task-id
```

Runtime and registry must be separate directory trees. The selected registry must
already exist in a canonical owner-controlled private directory; `tasks.sqlite3`
must be a regular file, mode 600, with no symlinks or hardlinks. Startup checks
the selected task before announcing the listener. It does not scan for personal
registries or initialize missing state. This viewer supports the registry's
rollback-journal mode; WAL databases and sidecars are rejected rather than risking
sidecar writes or stale immutable reads.

## API and access boundary

| Route | Access and result |
| --- | --- |
| `GET /` and static assets | Public metadata-free shell on the exact loopback Host. |
| `GET /live` | Process liveness only; no claim of task readiness. |
| `GET /api/v1/task-status` | One launch capability, one configured project/task, bounded metadata. |

Every route requires a loopback peer and the exact configured Host. Browser Origin
headers, when present, must match that origin. The API requires a single
`Authorization: Bearer …` header. Queries, request bodies and mutation methods
are rejected; CORS is not enabled. Responses are no-store with a self-only CSP.
The frontend uses text nodes and has no third-party assets, cookies or browser
storage. Disconnect and pending-response checks prevent a late response from
repopulating a cleared view.

The schema-version-1 response contains `read_only: true`, project/task IDs,
revision, state, attempt/submission/review/recovery/assessment counts, claim and
checkpoint presence, bounded failure categories, and available review metadata.
It excludes task goals, source, workspace paths, claim tokens and review free text.
Older registry code with no review-policy projection reports that policy as
unavailable; a reviewer-type label is not verified human identity. An empty failure
history does not establish success. Missing task returns 404, malformed/incompatible
metadata 409, unavailable state 503, invalid request 400, unauthenticated API 401.

SQLite is opened with `mode=ro`, `query_only`, a bounded read transaction and no
schema initialization. The fixed reader reuses `TaskRegistry.inspect`; local file
permissions and per-launch capabilities are a local boundary, not team identity
or protection against another process already controlling the same OS account.
Remote exposure, shared-user authorization, worker execution and review mutations
need their own design. Do not proxy this endpoint onto a public interface.

## Focused verification

```sh
.venv/bin/python -W error::ResourceWarning -m unittest test_task_cockpit_http test_task_cockpit_launch
node scripts/check_task_cockpit_ui.cjs
.venv/bin/python test_distribution.py
```

These checks use synthetic fixtures and owned disposable listeners. They do not
restart the MCP daemon, index repositories or contact a model. This prototype
does not establish lower token usage, task correctness or commercial readiness.
