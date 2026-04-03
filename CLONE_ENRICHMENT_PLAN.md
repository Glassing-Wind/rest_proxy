# Clone Enrichment Plan

Goal: treat clone detection as a GraphRAG enrichment layer that de-duplicates
retrieval, groups repeated implementations, and improves ranking/summarization.

## Scope

- Add clone-aware graph data without changing existing nodes/edges.
- Keep feature flags so indexing and retrieval remain opt-in and safe.
- Use current duplication pipeline (winnow + structural rerank) as Stage 1/2.

## Graph schema (additive only)

Nodes:
- `(:CloneGroup {id, size, method, score_min, score_max, score_avg, created_at})`

Edges:
- `(:Function)-[:MEMBER_OF_CLONE_GROUP]->(:CloneGroup)`
- `(:CloneGroup)-[:HAS_CANONICAL]->(:Function)`

Optional debug edge (disabled by default):
- `(:Function)-[:NEAR_CLONE_OF {score, method}]->(:Function)`

File-level clone groups (secondary, retrieval hygiene only):
- Derived from function clone-group overlap (source of truth)
- Chunk-only file similarity is legacy/debug and should not drive grouping
- Stored in Neo4j:
  - `(:FileCloneGroup {id, size, method, score_min, score_max, score_avg, created_at})`
  - `(:File)-[:MEMBER_OF_FILE_CLONE_GROUP]->(:FileCloneGroup)`
  - `(:FileCloneGroup)-[:HAS_CANONICAL]->(:File)`

## Feature flags

- `LM_PROXY_CLONE_DEDUP=1` enable retrieval-time dedup/collapse.
- `LM_PROXY_CLONE_NEAR_EDGE=1` optionally write pairwise edges for inspection.
- `LM_PROXY_CLONE_DEBUG=1` inject debug output into search results (dev only).

## Candidate generation (Stage 1)

Reuse `find_code_duplication` candidate logic with defaults:

- `min_similarity=0.92`
- `winnow_min_overlap=0.6`
- `winnow_token_sim_threshold=0.65`
- `winnow_kgram_sim_threshold=0.7`
- `winnow_small_token_threshold=50`

Structural rerank (Stage 2):

- `score = base_score * (0.5 + 0.5 * struct_score)`
- `struct_score = Jaccard(node_types)` from chunk metadata.

## Minimal grouping algorithm

1. Build candidate pairs from winnow + structural rerank.
2. Filter to function-level chunks (metadata file_symbols + start_line/end_line).
3. Apply threshold: `score >= 0.85` for grouping.
4. Union-find to cluster into groups.
5. Select canonical per group:
   - prefer function with most callers (CALLS + CALLS_INFERRED)
   - else longest LOC
   - else lexicographically first `(filepath, start_line)`

## Write path (indexing)

- Rust indexing writes clone groups during the same run (authoritative path)
- Reads candidate pairs from struct/winnow signals
- Clusters into groups (union-find)
- Writes CloneGroup nodes and edges
- Derives FileCloneGroup nodes/edges from function overlap
- Optionally writes NEAR_CLONE_OF edges

## Retrieval behavior (opt-in)

When `LM_PROXY_CLONE_DEDUP=1`:

- Deterministic, single-pass dedup:
  1. Fetch raw candidates.
  2. Force metadata hydration for all candidates.
  3. Batch-resolve file and function clone keys.
  4. Apply suppression in a single pass (file-level then function-level).
- Suppression keys:
  - file_key = file_clone_group_id || canonical file id || file path
  - func_key = function_clone_group_id || canonical function id || symbol id || (file path + start_line)
- Collapse results by clone group.
- File-level suppression uses function-derived file groups
- Keep canonical only unless:
  - query explicitly asks for variants or differences
  - canonical is filtered out by path/language
  - top-k diversity requires a variant

Debug instrumentation (dev only):
- When `LM_PROXY_CLONE_DEBUG=1`, emit a debug block with:
  - candidate count
  - resolved file-level group ids per candidate
  - resolved function-level group ids per candidate

## Safety / compatibility

- All changes are additive and opt-in.
- No existing edges or queries are modified.
- CloneGroup labels are distinct from Symbol/File nodes.

## Validation checklist

- Clone groups created for known duplicates (duplication_demo files).
- Canonical selection is deterministic.
- Retrieval with `LM_PROXY_CLONE_DEDUP=1` reduces redundant items.
- Graph size stays stable (no pairwise edges unless explicitly enabled).

## Rust migration plan (phased)

1. Emit per-symbol fingerprints in Rust (done)
2. Port grouping to Rust (done)
3. Retire Python clone grouping (next)
   - Keep retrieval dedup in Python (or move it to Rust once stable)

## Current state

- Rust is the authoritative writer for clone groups
- Python clone-enrich should be disabled and eventually removed
