# Reliable indexing acceptance — October 4, 2026

## Golden discrepancy resolution

The June `rental_app_flow_summary` golden expected 81 route links. Both old/new
upgrade wheels produced 80, so the mismatch predates the parser upgrade. The
workspace is `/Users/michaelmarler/Projects/rental`, not the separately cleaned
`rentallaw` project. June's complete source/edge snapshot is unavailable; the
exact historical 81st edge cannot be reconstructed from the aggregate expectation.

Source inspection exposed an actual extraction defect: fetch calls and independent
method properties were paired by list position. A default GET request for
`/api/leases` inherited POST from another call. Agreement between graph and
extracted facts alone had therefore failed to establish source correctness.

The fork fix associates method properties with their originating AST call, uses GET
for literal default fetch requests, and retains ANY for dynamic/spread options.
Quoted method keys are supported. Public fork APIs remain intact.

A frozen 175-file snapshot was indexed into two unique disposable projects:

| Arm | Pin | Route links |
| --- | --- | --- |
| Baseline | `e1c99f71478dd1d2f974cb02e4038424d21a12ce` | 80 |
| Fixed candidate | `6fcead43fc13b0049481ea5b5c491e02eab4ac68` | 78 |

Source manifest hash: `1caa6478dcd61e982a36d5ef9e31482faea6922cd9d15c573789b30cda69c408`.
Four correct links were added and six incorrect links removed; these are not
changes made solely to recover the old aggregate count. Full inventories, source
hashes and indexing logs are retained privately under `.runtime/reliable-indexing`.
Both disposable graphs were removed. The live rental workspace was then indexed
successfully with the corrected parser; the golden now expects 78 while retaining
its named UI/route/handler checks, including `GET /api/leases`.

Added:

- landlord-dashboard.js: GET `/api/leases`, `/api/properties`, `/api/tenants`.
- tenant-payment.js: GET `/api/payments/config`.

Removed incorrect associations:

- landlord-dashboard.js: GET `/api/charges`, `/api/expenses`, `/api/payments`;
  POST `/api/leases`, `/api/properties`.
- tenant-payment.js: GET `/api/tenant/portal/messages`.

The golden remains a current-workspace check; future source changes require source
alignment before changing its count. Synthetic binding regressions independently
check method scoping across JavaScript, TypeScript and TSX.

## Interruption, cancellation and restart recovery

- SIGTERM and SIGKILL during staging preserved published fixture nodes.
- SIGKILL during uncommitted canonical deletion rolled back the transaction and
  preserved published fixture nodes. Ownership remained protected until explicit
  local adjudication; no operational repository was killed for these checks.
- Local owned-writer recovery preserves all data, saves a private preview and
  refuses changed staging, active publication and another running writer.
- Remote/live/reused/unknown PIDs and uncertain process inspection remain protected.
- Persisted cancellation requires host and process creation identity; an owned
  stopped child is not signalled again through its old PID.
- Restart reconciliation no longer reports native parsing completion as publication
  success. Explicit errors override success markers.

See [operator procedure](../../../docs/indexing-lifecycle-safety.md) and the
[machine-readable receipt](reliable-indexing.json).

## Validation and limits

Python 3.14 native wheel and binding regressions passed; core Rust library tests
passed (314, with four ignored). Broad Cargo integration tests exposed
API-mismatch compilation errors outside the changed extraction code; only the library suite is claimed here. Required
rest_proxy CI and direct/MCP parity are recorded in the receipt. A separate stale
parity assertion pinned the branch to `codex/enterprise-hardening`; it now verifies
the actual native Git branch rather than assuming a particular development branch.

[Fork PR #2](https://github.com/Zmaroo/tree-sitter-language-pack/pull/2) is a draft.
Hosted validation and all three native Python 3.14 wheel jobs passed. The initial
PR title validation failure was corrected. Initial macOS wheel setup failed
because it selected `1.95` while the installed toolchain was `1.95.0`; the acceptance
workflow now explicitly selects the installed version and runs the binding tests.
The application manifests pin the published candidate SHA; this is a development
cutover, not a merged fork release or general enterprise-readiness claim.

Remaining: multi-host/uncertain-writer recovery, coordinated graph/vector publication,
large-repository promotion costs, lifecycle-record retention and broader restore
drills. The local structural interruption gate is distinct from those milestones.
