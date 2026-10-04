Question: Trace the major ranking and fallback stages used by code search.

The main `search_codebase` tool follows **hybrid retrieval → intent-aware ranking → targeted rescue → deduplication → diversity caps → optional grep suggestions**. Its fallbacks are mostly ways to improve retrieved candidates, rather than substitutes for unavailable services.

1. **Embed the query and establish search intent.**  
   The wrapper generates an embedding and calls `search_codebase_core`; an empty embedding returns an error. The core extracts identifiers, member expressions, path hints, and expected runtime entrypoints. Queries are classified as general, usage, definition, explanation, symbol lookup, or implementation search.  
   Evidence: [semantic.py:190](/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/semantic.py:190), [code_retrieval.py:93](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:93), [retrieval_intent_queries.py:97](/Users/michaelmarler/Projects/rest_proxy/memory/retrieval_intent_queries.py:97).

2. **Retrieve and fuse vector and keyword ranks.**  
   Per project, the candidate budget is `min(k * 10, 150)`. Vector results use cosine distance; keyword results use PostgreSQL `ts_rank` with `websearch_to_tsquery`. Fusion is:
   `2 / (60 + semantic_rank) + 1 / (60 + keyword_rank)`, with zero keyword contribution when unmatched.  
   Crucially, the SQL starts from semantic candidates and **left-joins** keyword matches: keyword-only candidates do not enter this initial pool. Implementation queries also exclude certain grammar/data paths before retrieval.  
   Evidence: [code_retrieval.py:105](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:105), [code_retrieval.py:137](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:137).

3. **Filter and apply policy-driven ranking.**  
   Tests are excluded by default. Metadata, language, path, and Cargo filters narrow the pool. General-query scoring adds metadata bonuses and subtracts documentation/parser/binding penalties. Implementation scoring additionally considers definitions, signatures, exact identifiers, roles, paths, callables, entrypoints, and usage signals.  
   The final implementation ordering is **lexicographic**, not simply descending numerical score: surface penalties and role/specialized priorities precede identifier hits and `rank_score`. Implementation queries also select ordinary code preferentially, falling back to parser, binding, then documentation candidates when stronger categories are absent.  
   Evidence: [code_retrieval.py:242](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:242), [code_retrieval.py:328](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:328), [retrieval_enrichment.py:367](/Users/michaelmarler/Projects/rest_proxy/memory/retrieval_enrichment.py:367), [retrieval_surfaces.py:245](/Users/michaelmarler/Projects/rest_proxy/memory/retrieval_surfaces.py:245), [code_retrieval.py:394](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:394).

4. **Rescue missing implementation evidence.**  
   There are several distinct branches:
   - An initially empty pool can be rescued through indexed runtime-entrypoint or path-hint lookups. Otherwise, search returns empty.
   - With implementation intent, path hints, and existing results, the top five are checked for path, entrypoint, and definition evidence. Missing evidence triggers indexed lookups and, for definition-oriented queries, definition-pattern grep.
   - Usage queries lacking an exact member usage site in the top five can trigger member-expression grep.

   Grep-discovered paths are used to reload **indexed chunks**, then enrich and rerank them; these branches do not directly turn arbitrary filesystem contents into ranked chunks. They are independent of the public `fallback="grep"` setting.  
   Evidence: [code_retrieval.py:196](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:196), [code_retrieval.py:427](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:427), [code_retrieval.py:522](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:522), [code_retrieval_loaders.py:101](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval_loaders.py:101).

5. **Suppress duplicates, then limit concentration.**  
   Optional Neo4j clone-group deduplication is controlled by `LM_PROXY_CLONE_DEDUP` and catches failures. With file deduplication enabled, duplicate-aware reranking can run through `ts_pack`, depending on tracing, telemetry, and experiment flags; the configured default rollout is `stage2`. Its compatibility paths can fall back through alternate runtime calls, duplicate analysis, or retaining candidates. Implementation ordering is reapplied before choosing one chunk per file. Finally, per-file and top-level-directory caps are applied, then results are truncated to `k`.  
   Evidence: [code_retrieval.py:618](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:618), [code_retrieval_clone.py:185](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval_clone.py:185), [code_retrieval_postprocess.py:39](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval_postprocess.py:39), [retrieval_telemetry.py:31](/Users/michaelmarler/Projects/rest_proxy/memory/retrieval_telemetry.py:31), [retrieval_duplicates.py:499](/Users/michaelmarler/Projects/rest_proxy/memory/retrieval_duplicates.py:499), [code_retrieval.py:640](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:640).

6. **Append optional grep suggestions.**  
   This final fallback runs only when there are already top results and either at most one unique file or `unique_files / results <= fallback_ratio`—default `0.4`. It returns separate path suggestions, without merging them into ranked results. Despite the “exact-match” description, the implementation uses a case-sensitive regex OR of extracted query tokens, without word boundaries. Missing `rg`, execution failures, or timeout produce empty suggestions plus diagnostic information.  
   Broad mode changes default directory capacity from two to four, removes the default metadata boost, enables grep internally, and sets the per-file cap to two. **A visible mismatch:** the wrapper renders grep suggestions only when its original `fallback` argument equals `"grep"`, so broad mode’s internally enabled suggestions can remain undisplayed.  
   Evidence: [code_retrieval.py:648](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:648), [retrieval_fallbacks.py:175](/Users/michaelmarler/Projects/rest_proxy/memory/retrieval_fallbacks.py:175), [code_retrieval.py:274](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:274), [semantic.py:271](/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/semantic.py:271).

**Service fallback differs between entrypoints.** The separate `memory/store_search.py::search_codebase` uses equal-weight vector/keyword RRF, or vector-only retrieval without query text. PostgreSQL unavailability/errors fall through to Neo4j vector search, then ultimately `[]`. The main tool instead calls the PostgreSQL-dependent core; its wrapper catches failures and returns an error string. The store-level Neo4j fallback therefore should not be assumed to protect the main tool.  
Evidence: [store_search.py:175](/Users/michaelmarler/Projects/rest_proxy/memory/store_search.py:175), [store_search.py:259](/Users/michaelmarler/Projects/rest_proxy/memory/store_search.py:259), [code_retrieval.py:108](/Users/michaelmarler/Projects/rest_proxy/memory/code_retrieval.py:108), [semantic.py:295](/Users/michaelmarler/Projects/rest_proxy/tools/brain/search/semantic.py:295).

This is a static source trace. Actual enabled flags, index contents, metadata quality, and external `ts_pack` behavior remain unverified; no services or application code were executed.