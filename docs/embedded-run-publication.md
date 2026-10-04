# Embedded graph/vector publication — October 4, 2026

Priority 2 now has an experimental **combined repository owner** that coordinates
Ladybug outlines/original source with LanceDB chunks and retrieval. This remains
a library prototype; the MCP indexing guard and existing proxy memory path have
not been replaced.

## Publication protocol

`EmbeddedRepositoryOwner` owns the graph and vector stores and serializes indexing,
retrieval, source inspection, cleanup and project deletion. Each store holds its
local OS owner lock. Consumers must use this owner; raw engine clients do not
participate in the protocol. HTTP/STDIO dispatch to the shared owner remains work.

Indexing captures/parses the explicit source manifest, chunks those snapshots with
native ts-pack, and calls an explicit async embedding callback in batches of 64.
The caller supplies the encoder identity; this code chooses no model or inference
provider. Vectors must match the configured dimension, contain finite values, and
have a nonzero finite norm. Cosine vectors are normalized before float32 storage.

Every run has a fresh ID. Chunks are upserted by project/run/ref identity without
an append fallback. Native FTS is built before graph publication, and the staged
row count must match the complete run. Failures propagate. The graph transaction
then replaces the outlines, source evidence and publication receipt, including
run ID, chunk count, dimension and encoder identity. **That graph receipt is the
visibility switch**, not a distributed transaction across the two engines.

Search reads the published receipt and prefilters LanceDB by project and run.
Vector and explicit native text search are implemented; hybrid retrieval uses
LanceDB's RRF reranker. The query encoder must match the published identity.
Returned rows omit vectors and retain source hashes, chunk metadata and run IDs;
source citations and chunk content hashes are checked against the publication.
No ANN index is created: vector search is exact on the tested corpus. FTS uses a
shared table, so its corpus statistics can change as runs are staged; no invariant
ranking or whole-request token-budget claim is made.

Failed staging, FTS creation or graph publication leaves the previous receipt
visible. Orphaned staged runs may remain physically present. Explicit cleanup
keeps the published run and removes other project runs while readers are excluded
by the owner lock. Project deletion first removes graph visibility, then removes
vector rows; cleanup failure can leave inaccessible physical rows requiring retry.
Cancellation during graph COMMIT can still produce a committed, fully staged run;
reconcile the receipt. This protocol does not establish power-loss durability,
remote leases, source-history retention or distributed concurrent-client support.

## Executed acceptance

Python 3.14.4 on macOS ARM64, Ladybug 0.21.2 / LanceDB 0.39.0 / PyArrow 25.0.1:

- Four native test methods passed: text/vector/hybrid retrieval; run/project
  prefilters including quoted IDs; idempotent upserts; schema/dimension/vector
  rejection; FTS/vector/graph failure preservation; reopen; project deletion and
  cleanup isolation; cancellation during embedding; kill after completed vector
  staging and before graph publication, followed by preserved published retrieval
  and guarded orphan cleanup.
- Under OS network denial, 130 production Python files from this repository
  produced 1,147 symbols and **1,622 chunks**. All three retrieval modes returned
  the published run. Reopening preserved hybrid retrieval; a bounded source bundle
  matched a retrieved citation's hash and run ID.
- The offline run and native tests use **synthetic 3D vectors**, explicitly labeled
  as storage fixtures. They prove storage/publication behavior, not semantic quality
  or the acceptance of an actual embedding model.
- Four earlier outline/crash tests, four transaction tests and full local CI passed.
  The optional native engine tests are still not hosted CI gates.

[Acceptance receipt](../benchmarks/reports/2026-10-04/embedded-run-publication.json).
Private runtime artifacts remain in `.runtime/embedded-hybrid-acceptance/` (mode 700).
The existing [owned outline acceptance](owned-embedded-outlines.md) supplies the
source/graph transaction foundation.

## API and remaining gates

Use `async with EmbeddedRepositoryOwner(private_path, dimension) as owner`, then
`owner.index(root, project_id, paths, embed=async_callback, encoder_id=identity)`.
The callback accepts a list of texts and returns one vector per text. Use the same
embedding model/configuration to encode queries passed to `owner.search`, with the
same identity. Search mode is `text`, `vector`, or `hybrid`; limit is 1–100. Source
inspection uses `owner.describe_file`; retention uses `cleanup_unpublished`, and
project deletion uses `delete_project`. Unpublished storage primitives are internal
and must not be exposed directly as MCP/admin writes.

Remaining Priority 2 work: connect the actual embedding provider and evaluate its
retrieval; implement call/import/route schema and query compatibility; route MCP,
REST and indexing through the owner; integrate durable application metadata; verify
real hybrid MCP investigation with external storage disabled. Native packaging,
restore drills, load/resource measurements and supported-platform CI remain release
gates. FIRE task checkpoints and whole-request context budgeting remain subsequent
priorities.
