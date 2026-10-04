# Owned embedded outlines — October 4, 2026

Priority 2 now has a standalone native structural publisher and bounded original
source reader. This is an **outline-only implementation**, not full GraphRAG or
hybrid indexing. The MCP indexing guard remains in place.

## Ownership and publication

Ladybug drivers hold a nonblocking OS lock for the database lifetime. A second
process or driver using the same canonical database path is refused. The lock
inode is retained; deleting it would allow distinct processes to lock different
inodes. OS termination releases ownership. This protects local mutable files;
it does not implement HTTP/STDIO routing through a shared owner, network-file
locking guarantees, or distributed leases. Unix and Windows implementations are
present; execution evidence is from macOS ARM64 only.

An explicit manifest defines the complete replacement scope for one project.
Native ts-pack parses all selected files before project replacement starts.
Invalid paths, aliases/symlinks, binary files, duplicate paths, parsing errors and
resource-limit violations refuse publication. Limits are 5,000 files, 2 MiB per
file, 64 MiB total source, and 25,000 accepted symbols. Unsupported symbol kinds
are counted explicitly; no call/import/route relationships are inferred here.

One Ladybug transaction replaces project files/symbols, stores original source
with SHA256, native facts and run identity, and records an OutlinePublication
manifest. Exceptions before commit roll back replacements. Cancellation during
COMMIT can still leave a committed publication; inspect the recorded run ID
rather than assuming cancellation means rollback. Removed files disappear from
the published project, and other projects remain intact.

Source inspection reads original source and symbols in one transaction and checks
that evidence run IDs match the publication. It verifies the content hash, returns
complete numbered lines within explicit bounds, and limits symbol output to 40
with a total count. This establishes a provenance path for future FIRE evidence;
it does not implement task checkpoint/resume or whole-request token budgeting.
Original source is retained in the database: use a private storage directory.

## Executed acceptance

With OS network access denied, a manifest of **128 production Python files** in
this repository indexed **1,122 symbols**, with zero unsupported symbol kinds.
The snapshot contains 1,488,535 source bytes. Reopen verified all 128 stored source
hashes against the publication manifest; bounded inspection returned 12 source
lines and the matching run ID. A separate socket probe confirmed OS denial.
No Neo4j, Postgres, embedding service or Docker was used by these commands.
This is a selected production-source corpus, not every repository file.

Four native indexing tests passed: partial-write rollback, project scoping and
file deletion, parser-failure preservation, durable originals/reopen, bounded
source inspection, manifest guards, competing-process ownership, and killing an
uncommitted writer before reopening its preserved publication. The four adapter
transaction tests and full local CI also passed. Native tests are explicit
optional-engine acceptance and are not yet installed/run by the hosted CI matrix.

[Acceptance receipt](../benchmarks/reports/2026-10-04/owned-embedded-outlines.json).
Private logs, manifest and database remain under
`.runtime/embedded-outlines-acceptance/` (directory mode 700).

## Reproduce

Supply a JSON array of normalized paths relative to the repository root, such as
`["memory/types.py", "memory/embedded_ladybug.py"]`. Omitted files are removed
from this project's published outline.

```sh
mkdir -m 700 /tmp/private-outline-proof
.venv/bin/python scripts/index_embedded_outlines.py /absolute/repository \
  --project-id proof --database /tmp/private-outline-proof/graph \
  --manifest /absolute/manifest.json
.venv/bin/python scripts/inspect_embedded_outline.py \
  --database /tmp/private-outline-proof/graph --project-id proof \
  --file memory/types.py --start-line 1 --max-lines 40
.venv/bin/python test_embedded_outline_indexing.py
```

On macOS the acceptance commands were prefixed with
`sandbox-exec -p '(version 1) (allow default) (deny network*)'`. Parsers must be
cached for offline execution. Engine/parser packages must already be installed;
this is not a minimal clean-install recipe.

## Remaining Priority 2 gates

1. Extend the schema/writer to the actual call/import/route and query corpus.
2. Route HTTP, STDIO and indexing to one owner rather than competing processes.
3. Integrate LanceDB writes, prefiltered vector/text/hybrid search and deletion.
4. Stage vectors under the same run identity before publishing the graph receipt;
   filter retrieval by the published run and preserve readers during retention.
5. Route durable application metadata through the embedded backend. The outline
   manifest/originals are durable, but existing proxy memory still uses its server path.
6. Complete real-repository MCP investigation with hybrid retrieval and services
   disabled; verify restart, cancellation and cross-store recovery.
