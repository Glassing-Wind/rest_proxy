# Embedded project discovery — October 4, 2026

Priority 2 now exposes durable project identity through the embedded owner.
Project IDs, canonical source roots and publication run IDs already commit together
in Ladybug's `OutlinePublication` receipt. Discovery reads that receipt directly;
it requires neither a model nor a separate local/Neo4j registry write. Existing
publications work without a schema migration or reindex.

## Usable tools

With [embedded MCP configuration](embedded-mcp-owner.md), use:

- `list_embedded_projects(limit=25, after="")` to discover committed IDs, workspace
  paths and run IDs. Limits are 1..100; follow `next_cursor` for another page.
- Existing `resolve_graph_project(workspace_id)` to resolve an explicit project ID,
  canonical absolute path or unique directory basename. It retains its JSON-string
  response contract. Missing workspaces return `project_id: null`, `workspace_path:
  null`, and `status: "not_published"`.
- Existing `get_project_overview(workspace_id)` to read published file/symbol counts,
  manifest/run identity, retrieval metadata and supported capabilities. In embedded
  mode it returns a JSON-string summary of the available slice; no dependency,
  community or call-graph claims are synthesized.

Project ID matches take precedence. Absolute paths are normalized and never fall
back to a basename. Multiple projects sharing a root, or roots sharing a basename,
require an explicit project ID. Relative paths containing directory separators do
not resolve as names. Missing projects do not receive an invented path hash.
Canonical paths still resolve after deleting the original source directory;
published source reads remain available separately.

List pages contain bounded metadata rather than full manifests or source. Each
page uses a read transaction; the cursor is an ordered project ID, not a multi-page
snapshot token. Publications can change between pages. Runtime workspace overview
serializes resolution and overview against runtime indexing/deletion operations.
The publication receipt remains the sole authority: rollback preserves the old
root/run, and deleting the project removes it from discovery immediately.

## Validation

Six gated offline runtime/MCP tests pass, including standard-tool routing without
legacy workspace hashing. Six optional native combined-owner methods pass. The new
native case covers pagination, quoted IDs, canonical paths, ambiguous basenames,
missing workspaces, genuine graph rollback retaining root/run, reopen after source
deletion, SDK dispatch through both standard tools and scoped project deletion.
Full local CI passes.

The extended [transport check](../scripts/check_embedded_mcp.py) verifies listing,
standard path resolution, standard overview, published source and text search on
the existing 131-file / 1,629-chunk real-model publication. Sequential STDIO and HTTP
MCP outputs match with external-storage networking denied. No embedding connection
or new indexing run is required. This is a functional compatibility check, not a
coding-outcome or latency/token improvement measurement.

[Acceptance receipt](../benchmarks/reports/2026-10-04/embedded-project-discovery.json).
Private logs: `.runtime/embedded-project-acceptance/` (mode 700).

Remaining Priority 2 work includes call/import/route query compatibility, other
legacy investigation tools, session/workspace/watch/job metadata, REST bundle
integration and production encoder identity. The synchronous legacy workspace
registry is unchanged; this slice routes only the two standard tools named above.
Existing standard index workers remain guarded. Packaging, restore/resource drills,
FIRE continuity and whole-request budgeting retain their roadmap gates.
