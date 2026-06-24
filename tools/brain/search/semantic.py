"""tools/search/semantic.py — semantic + keyword search tool."""

import json
import os
from mcp.server.fastmcp import FastMCP

from _helpers import get_memory_modules
from memory import retrieval_policy as sem_helpers
from proxy.logging import debug_log


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def rerank_retrieval_results(
        query: str,
        results: list[dict],
        mode: str = "code",
        experiments: dict | None = None,
        include_debug: bool = False,
    ) -> str:
        """
        Rerank a caller-supplied ranked result list using rest_proxy duplicate-aware retrieval policy.

        Args:
            query: Retrieval query that produced the ranked candidates.
            results: Ranked candidate list. Each item should include content plus file_path or source_url.
            mode: Retrieval corpus mode: "code" or "docs".
            experiments: Optional duplicate-policy overrides.
            include_debug: Return the full forensic contract instead of the compact decision summary.
        """
        try:
            mode_norm = (mode or "code").strip().lower()
            if mode_norm not in {"code", "docs"}:
                return json.dumps({"error": "Invalid mode. Use 'code' or 'docs'."}, indent=2)
            if not isinstance(results, list):
                return json.dumps({"error": "results must be a list of dict items."}, indent=2)
            contract = sem_helpers.rerank_retrieval_results_contract(
                results,
                query=query,
                mode=mode_norm,
                experiments=experiments,
                include_debug=include_debug,
            )
            payload = contract if include_debug else sem_helpers.compact_rerank_contract(contract, results)
            return json.dumps(payload, indent=2, sort_keys=True)
        except Exception as e:
            return json.dumps({"error": f"Error reranking retrieval results: {str(e)}"}, indent=2)

    @mcp.tool()
    async def analyze_duplicate_results(
        query: str,
        results: list[dict],
        mode: str = "code",
        include_debug: bool = False,
    ) -> str:
        """
        Analyze duplicate structure for a ranked result list without reranking it.

        Args:
            query: Retrieval query that produced the ranked candidates.
            results: Ranked candidate list. Each item should include content plus file_path or source_url.
            mode: Retrieval corpus mode: "code" or "docs".
            include_debug: Return full pair/group internals instead of the compact diagnosis.
        """
        try:
            mode_norm = (mode or "code").strip().lower()
            if mode_norm not in {"code", "docs"}:
                return json.dumps({"error": "Invalid mode. Use 'code' or 'docs'."}, indent=2)
            if not isinstance(results, list):
                return json.dumps({"error": "results must be a list of dict items."}, indent=2)
            contract = sem_helpers.analyze_duplicate_results_contract(
                results,
                query=query,
                mode=mode_norm,
            )
            payload = contract if include_debug else sem_helpers.compact_duplicate_analysis(contract, results)
            return json.dumps(payload, indent=2, sort_keys=True)
        except Exception as e:
            return json.dumps({"error": f"Error analyzing duplicate results: {str(e)}"}, indent=2)

    @mcp.tool()
    async def trace_code_ranking(
        query: str,
        results: list[dict],
        include_debug: bool = False,
    ) -> str:
        """
        Build a code-ranking trace for implementation-intent queries.

        Args:
            query: Retrieval query to classify and trace.
            results: Candidate rows with content, file_path, optional rrf/rank_score, and semantic metadata.
            include_debug: Return every ranking component instead of only non-zero contributions.
        """
        try:
            if not isinstance(results, list):
                return json.dumps({"error": "results must be a list of dict items."}, indent=2)
            trace = sem_helpers.build_implementation_ranking_trace(results, query)
            payload = trace if include_debug else sem_helpers.compact_implementation_ranking_trace(trace)
            return json.dumps(payload, indent=2, sort_keys=True)
        except Exception as e:
            return json.dumps({"error": f"Error tracing code ranking: {str(e)}"}, indent=2)

    @mcp.tool()
    async def search_codebase(
        workspace_ids: list | None = None,
        query: str | None = None,
        workspace_id: str | None = None,
        k: int = 5,
        include_metadata: bool = False,
        dedupe_files: bool = True,
        include_debug: bool = False,
        max_per_file: int = 0,
        max_per_dir: int = 2,
        meta_boost: float = 0.005,
        mode: str = "precise",
        fallback: str = "none",
        fallback_ratio: float = 0.4,
        fallback_max: int = 12,
        fallback_glob: str = "",
        exclude_tests: bool = True,
        languages: list|None = None,
        min_imports: int = 0,
        min_symbols: int = 0,
        require_diagnostics: bool = False,
        require_context: bool = False,
        crate_contains: str | None = None,
        include_paths: list|None = None,
        exclude_paths: list|None = None,
    ) -> str:
        """
        Perform a hybrid semantic search over one or more codebases simultaneously.
        Results are ranked by relevance using RRF (vector + full-text).

        Pass a single-element list for single-project search, or multiple IDs
        for cross-project search — results are merged and annotated with their
        source project in the multi-project case.

        Args:
            workspace_ids: List of logical workspace IDs or absolute paths to search across.
            workspace_id: Single logical workspace ID or absolute path for one-project search.
            query: Natural language or code snippet to search for.
            k: Total number of results to return (default 5).
            include_metadata: Show metadata lines in results (default False).
            dedupe_files: Collapse results to one chunk per file (default True).
            include_debug: Include clone-dedup debug entry in output (default False).
            max_per_file: Max results per file when dedupe is off (default 0 = disable).
            max_per_dir: Max results per top-level directory (default 2, 0=disable).
            meta_boost: Additive boost per metadata field present (default 0.005).
            mode: "precise" (default) or "broad" to expand coverage when query is exploratory.
            fallback: "none" (default) or "grep" to add exact-match paths when results are overly concentrated.
            fallback_ratio: Trigger fallback when unique files / results <= ratio (default 0.4).
            fallback_max: Maximum fallback file paths to show (default 12).
            fallback_glob: Optional glob filter for fallback grep (e.g., "*.ts").
            exclude_tests: Exclude test files from results (default True).
            languages: Optional allowlist of languages to include.
            min_imports: Require at least N file imports in metadata.
            min_symbols: Require at least N file symbols in metadata.
            require_diagnostics: Only return chunks with diagnostics.
            require_context: Only return chunks with a non-empty context_path.
            crate_contains: Optional Cargo crate filter for Rust workspace files.
            include_paths: Optional list of glob patterns to include (file_path).
            exclude_paths: Optional list of glob patterns to exclude (file_path).
        """
        try:
            from embedding_service import get_embedding_service

            memory_store, _, _, _, _ = get_memory_modules()

            if not isinstance(query, str) or not query.strip():
                return "Error: provide a non-empty query."

            normalized_workspace_ids: list = list(workspace_ids or [])
            if workspace_id is not None:
                normalized_workspace_ids.append(workspace_id)
            if not normalized_workspace_ids:
                return "Error: provide at least one workspace ID or path."
            deduped_workspace_ids: list = []
            for value in normalized_workspace_ids:
                if value in deduped_workspace_ids:
                    continue
                deduped_workspace_ids.append(value)
            workspace_ids = deduped_workspace_ids

            svc = get_embedding_service()
            vecs = await svc.embed_batch_async([query])
            query_vector = vecs[0]
            if not query_vector:
                return "Error: Could not generate embedding for query."

            res = await memory_store.search_codebase_core(
                workspace_ids=workspace_ids,
                query=query,
                query_vector=query_vector,
                k=k,
                include_metadata=include_metadata,
                dedupe_files=dedupe_files,
                include_debug=include_debug,
                max_per_file=max_per_file,
                max_per_dir=max_per_dir,
                meta_boost=meta_boost,
                mode=mode,
                fallback=fallback,
                fallback_ratio=fallback_ratio,
                fallback_max=fallback_max,
                fallback_glob=fallback_glob,
                exclude_tests=exclude_tests,
                languages=languages,
                min_imports=min_imports,
                min_symbols=min_symbols,
                require_diagnostics=require_diagnostics,
                require_context=require_context,
                crate_contains=crate_contains,
                include_paths=include_paths,
                exclude_paths=exclude_paths,
            )

            # Check if empty results were returned
            if not res.get("all_results"):
                projects = ", ".join(f"'{n}'" for n in res["pid_to_name"].values())
                return f"No matching code found in {projects}.\nEnsure projects are indexed with index_workspace()."

            lines = sem_helpers.render_results(
                res["all_results"],
                query=query,
                k=k,
                multi=res["multi"],
                pid_to_name=res["pid_to_name"],
                include_metadata=res["include_metadata"],
            )

            duplicate_trace = res["duplicate_trace"]
            if duplicate_trace and (include_debug or res["duplicate_trace_enabled"]):
                lines.extend(sem_helpers.summarize_trace_for_debug(duplicate_trace))
                lines.append("")
            if duplicate_trace and res["duplicate_telemetry_enabled"]:
                telemetry = duplicate_trace.get("telemetry") if isinstance(duplicate_trace, dict) else {}
                if isinstance(telemetry, dict):
                    sem_helpers.append_duplicate_telemetry_event(
                        duplicate_trace,
                        query=query,
                        tool="search_codebase",
                        mode="code",
                    )
                    debug_log(
                        "duplicate_rerank_telemetry",
                        query=query[:200],
                        mode=telemetry.get("mode"),
                        query_class=telemetry.get("query_class"),
                        exact_suppressions=telemetry.get("exact_suppressions"),
                        experimental_suppressions=telemetry.get("experimental_suppressions"),
                        relation_counts=telemetry.get("relation_counts"),
                        group_sizes=telemetry.get("group_sizes"),
                        topk_redundancy_before=telemetry.get("topk_redundancy_before"),
                        topk_redundancy_after=telemetry.get("topk_redundancy_after"),
                        kept_group_multi_member_count=telemetry.get("kept_group_multi_member_count"),
                        multi_representative_group_count=telemetry.get("multi_representative_group_count"),
                        query_distinct_multi_rep_count=telemetry.get("query_distinct_multi_rep_count"),
                        canonical_doc_preference_success=telemetry.get("canonical_doc_preference_success"),
                        version_sensitive_query=telemetry.get("version_sensitive_query"),
                        best_answer_loss_suspect=telemetry.get("best_answer_loss_suspect"),
                        regression_alerts=telemetry.get("regression_alerts"),
                        experiments=duplicate_trace.get("experiments"),
                    )

            fallback_lines = res["fallback_lines"]
            if fallback == "grep" and res["results"]:
                if include_debug:
                    import sys
                    token_text = (
                        ", ".join(res["fallback_tokens"]) if res["fallback_tokens"] else "(none)"
                    )
                    lines.append(f"Fallback grep tokens: {token_text}")
                    if res["fallback_glob"]:
                        lines.append(f"Fallback grep glob: {res['fallback_glob']}")
                    lines.append(f"Fallback grep rg path: {res['fallback_rg_hint']}")
                    lines.append(f"Debug sys.executable: {sys.executable}")
                    lines.append(f"Debug PATH: {os.getenv('PATH', '')}")
                if fallback_lines:
                    lines.append("Fallback (grep):")
                    for fp in fallback_lines:
                        if fp.startswith("- "):
                            lines.append(fp)
                        else:
                            lines.append(f"- {fp}")
                if include_debug or fallback_lines:
                    lines.append("")

            return "\n".join(lines)
        except Exception as e:
            return f"Error searching codebase: {str(e)}"
