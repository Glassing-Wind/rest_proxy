"""Intent catalog for the MCP tool surface."""

from __future__ import annotations

from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP


@dataclass(frozen=True)
class ToolCatalogEntry:
    """Product-positioning metadata for one registered MCP tool."""

    tier: str
    workflow: str
    reach_for_when: str
    prefer_after: tuple[str, ...] = ()


TOOL_CATALOG: dict[str, ToolCatalogEntry] = {
    "add_memory": ToolCatalogEntry(
        "memory",
        "context",
        "Store a tagged and prioritized instruction or repo fact for future sessions.",
    ),
    "analyze_duplicate_results": ToolCatalogEntry(
        "support",
        "retrieval QA",
        "Diagnose duplicate pressure in caller-supplied results before changing ranking policy.",
    ),
    "author_and_index_documentation": ToolCatalogEntry(
        "docs",
        "documentation",
        "Create a repo-specific guide and immediately make it searchable.",
    ),
    "cancel_index_job": ToolCatalogEntry(
        "operational",
        "indexing",
        "Stop a runaway or obsolete background indexing job; use force only for an explicit admin override.",
    ),
    "cleanup_stale_shadow_graph": ToolCatalogEntry(
        "operational",
        "indexing",
        "Inspect or remove stale Neo4j shadow namespaces left by failed structural index runs.",
    ),
    "delete_documentation": ToolCatalogEntry(
        "admin",
        "documentation",
        "Remove stale or polluted indexed documentation chunks.",
    ),
    "describe_file": ToolCatalogEntry(
        "primary",
        "code investigation",
        "Understand one file's purpose, role metadata, and symbol surface without reading it manually.",
    ),
    "download_documentation": ToolCatalogEntry(
        "docs",
        "documentation",
        "Crawl and index known documentation URLs for later search.",
    ),
    "extract_class_interface": ToolCatalogEntry(
        "support",
        "code investigation",
        "Read signatures, member kinds, and decorators for one known class or struct.",
        ("get_symbol_context",),
    ),
    "extract_function_body": ToolCatalogEntry(
        "support",
        "code investigation",
        "Fetch the exact source body for a known function or class.",
        ("get_symbol_context",),
    ),
    "find_code_duplication": ToolCatalogEntry(
        "secondary",
        "code quality",
        "Find near-duplicate implementation chunks across a project or scoped directory.",
    ),
    "find_definitions": ToolCatalogEntry(
        "support",
        "symbol lookup",
        "Find exact-name definitions across projects when names are ambiguous.",
        ("list_symbol_matches", "get_symbol_context"),
    ),
    "find_references": ToolCatalogEntry(
        "primary",
        "code investigation",
        "Find cross-file callers, type usages, imports, and related references for a symbol.",
    ),
    "find_symbol_usages": ToolCatalogEntry(
        "support",
        "symbol lookup",
        "Inspect usages of a symbol inside one file with AST precision.",
        ("find_references",),
    ),
    "get_app_flow_summary": ToolCatalogEntry(
        "experimental",
        "architecture",
        "Check UI-to-API-to-service paths in a full-stack web repo, or diagnose which required graph edges are missing.",
    ),
    "get_apple_build_summary": ToolCatalogEntry(
        "secondary",
        "architecture",
        "Understand Apple source, resource, target, scheme, and workspace relationships.",
    ),
    "get_backend_flow_summary": ToolCatalogEntry(
        "secondary",
        "architecture",
        "Summarize API to service to database paths in backend-heavy repos.",
    ),
    "get_call_chain": ToolCatalogEntry(
        "primary",
        "code investigation",
        "Trace execution flow from or to a symbol across graph hops.",
    ),
    "get_changed_symbols": ToolCatalogEntry(
        "dev",
        "change review",
        "Review changed symbols and separate source-only changes from non-code/support files.",
    ),
    "get_code_communities": ToolCatalogEntry(
        "secondary",
        "architecture",
        "Compare major architectural clusters and choose which area to inspect first.",
        ("get_project_overview",),
    ),
    "get_code_importance": ToolCatalogEntry(
        "secondary",
        "architecture",
        "Find high-leverage or high-blast-radius files after the project overview.",
        ("get_project_overview",),
    ),
    "get_directory_snapshot": ToolCatalogEntry(
        "primary",
        "code investigation",
        "Onboard to a specific directory and choose the most relevant files.",
    ),
    "get_flow_summary": ToolCatalogEntry(
        "secondary",
        "architecture",
        "Ask for the best available application/backend flow summary without choosing a specific flow tool.",
    ),
    "get_index_status": ToolCatalogEntry(
        "operational",
        "indexing",
        "Inspect a background indexing job, or discover active job IDs when the original ID is stale.",
    ),
    "get_indexed_projects": ToolCatalogEntry(
        "operational",
        "indexing",
        "List graph project IDs and paths when workspace resolution is unclear.",
    ),
    "get_indexing_health": ToolCatalogEntry(
        "primary",
        "indexing",
        "Start any indexed-repo workflow by checking graph freshness, alignment, and runtime health.",
    ),
    "get_mcp_tool_catalog": ToolCatalogEntry(
        "primary", "tool choice", "Choose the right MCP tool for an indexed-repo task."
    ),
    "get_project_overview": ToolCatalogEntry(
        "primary",
        "code investigation",
        "Onboard to an unfamiliar indexed repo and identify first inspection targets.",
        ("get_indexing_health",),
    ),
    "get_related_files": ToolCatalogEntry(
        "secondary",
        "code investigation",
        "Find structurally adjacent files for a known target file.",
    ),
    "get_repo_dependency_summary": ToolCatalogEntry(
        "secondary",
        "architecture",
        "Understand editable/path dependency links across a repo or monorepo.",
    ),
    "get_symbol_context": ToolCatalogEntry(
        "primary",
        "code investigation",
        "Deep-dive one symbol with callers, callees, references, and bounded or full-span source.",
    ),
    "get_symbol_exports_summary": ToolCatalogEntry(
        "secondary",
        "architecture",
        "Inspect exported/public symbol surfaces for API boundary work.",
    ),
    "get_symbol_imports_overview": ToolCatalogEntry(
        "secondary",
        "architecture",
        "Inspect explicit and implicit symbol imports for dependency direction questions.",
    ),
    "get_test_coverage_for": ToolCatalogEntry(
        "dev", "change review", "Find tests likely to cover a changed source file."
    ),
    "git_summary": ToolCatalogEntry(
        "dev",
        "change review",
        "Summarize git state before editing, reviewing, or preparing a commit.",
    ),
    "grep_codebase": ToolCatalogEntry(
        "support",
        "code search",
        "Run exact text or regex search when semantic retrieval is too broad or token-specific.",
    ),
    "index_authored_documentation": ToolCatalogEntry(
        "docs",
        "documentation",
        "Index an existing local agent-authored guide for documentation search.",
    ),
    "index_local_documentation_file": ToolCatalogEntry(
        "docs",
        "documentation",
        "Index a local documentation file under a searchable topic.",
    ),
    "index_workspace": ToolCatalogEntry(
        "primary",
        "indexing",
        "Build or refresh the semantic and structural indexes for a workspace.",
    ),
    "lint_project_subset": ToolCatalogEntry(
        "dev",
        "change review",
        "Run or apply supported linter fixes against a focused file set.",
    ),
    "list_documentation_sources": ToolCatalogEntry(
        "operational",
        "documentation",
        "Audit indexed documentation topics, domains, and source coverage.",
    ),
    "list_memories": ToolCatalogEntry(
        "memory",
        "context",
        "Review or filter durable repo/session memories by tags, category, and priority.",
    ),
    "list_symbol_matches": ToolCatalogEntry(
        "support",
        "symbol lookup",
        "Disambiguate symbol names before a deeper symbol-context lookup.",
        ("get_symbol_context",),
    ),
    "query_graph": ToolCatalogEntry(
        "admin",
        "graph QA",
        "Run raw Cypher only when higher-level graph tools cannot answer a specific diagnostic question.",
    ),
    "rerank_retrieval_results": ToolCatalogEntry(
        "support",
        "retrieval QA",
        "Get a compact duplicate-aware order and suppression decision for external search results.",
    ),
    "research_and_index": ToolCatalogEntry(
        "docs",
        "documentation",
        "Search the web, select documentation sources, crawl them, and index the result.",
    ),
    "research_documentation": ToolCatalogEntry(
        "docs", "documentation", "Find candidate external docs before indexing them."
    ),
    "resolve_graph_project": ToolCatalogEntry(
        "primary",
        "indexing",
        "Normalize a workspace path/name to the graph project ID used by other tools.",
    ),
    "restart_brain_server": ToolCatalogEntry(
        "operational",
        "runtime",
        "Restart the shared HTTP brain daemon after stale runtime or tool drift is detected.",
    ),
    "search_codebase": ToolCatalogEntry(
        "primary",
        "code investigation",
        "Find implementation or usage sites for natural-language code questions.",
    ),
    "search_documentation": ToolCatalogEntry(
        "docs",
        "documentation",
        "Search indexed docs with hybrid vector and full-text retrieval.",
    ),
    "search_memory": ToolCatalogEntry(
        "memory",
        "context",
        "Retrieve durable memories relevant to the current workspace or task.",
    ),
    "suggest_indexignore": ToolCatalogEntry(
        "operational",
        "indexing",
        "Generate repo-specific ignore suggestions to keep indexing focused.",
    ),
    "swift_doc_lookup": ToolCatalogEntry(
        "support",
        "Apple/Swift",
        "Extract Swift documentation comments for a known symbol.",
    ),
    "trace_code_ranking": ToolCatalogEntry(
        "support",
        "retrieval QA",
        "Explain only the factors that changed an implementation-intent result ranking.",
    ),
    "trace_graph_provenance": ToolCatalogEntry(
        "primary",
        "graph QA",
        "Explain why graph nodes, file links, or parse facts exist.",
    ),
    "trace_symbol_cross_project": ToolCatalogEntry(
        "secondary",
        "cross-project",
        "Trace a symbol from a defining project to usages in another project.",
    ),
    "unwatch_project": ToolCatalogEntry(
        "operational",
        "indexing",
        "Remove a manually pinned background watch for a project.",
    ),
    "visualize_subgraph": ToolCatalogEntry(
        "secondary",
        "architecture",
        "Create a Mermaid view of a symbol neighborhood for communication or triage.",
    ),
    "watch_project": ToolCatalogEntry(
        "operational",
        "indexing",
        "Pin a project for background watching, or sync explicitly advertised MCP client roots when no path is supplied.",
    ),
}


_TIER_ORDER = {
    "primary": 0,
    "secondary": 1,
    "docs": 2,
    "support": 3,
    "dev": 4,
    "memory": 5,
    "operational": 6,
    "experimental": 7,
    "admin": 8,
}


def _matches_intent(entry: ToolCatalogEntry, intent: str) -> bool:
    if not intent:
        return True
    haystack = " ".join(
        [entry.tier, entry.workflow, entry.reach_for_when, *entry.prefer_after]
    ).lower()
    return all(token in haystack for token in intent.lower().split())


def render_tool_catalog(
    intent: str = "", include_admin: bool = False, limit: int = 25
) -> str:
    """Render catalog entries as compact MCP-facing guidance."""

    max_rows = max(1, min(int(limit or 25), 80))
    rows = []
    for name, entry in TOOL_CATALOG.items():
        if not include_admin and entry.tier == "admin":
            continue
        if _matches_intent(entry, intent):
            rows.append((name, entry))
    rows.sort(
        key=lambda item: (_TIER_ORDER.get(item[1].tier, 99), item[1].workflow, item[0])
    )

    if not rows:
        return f"No cataloged MCP tools matched intent `{intent}`."

    heading = "MCP tool catalog"
    if intent:
        heading += f" for `{intent}`"
    lines = [
        heading + ":",
        "Use `primary` tools first for normal indexed-repo investigation; use support/admin tools when their exact job matches.",
    ]
    for name, entry in rows[:max_rows]:
        after = (
            f" Prefer after: {', '.join(entry.prefer_after)}."
            if entry.prefer_after
            else ""
        )
        lines.append(
            f"- `{name}` [{entry.tier} / {entry.workflow}] - {entry.reach_for_when}{after}"
        )
    if len(rows) > max_rows:
        lines.append(
            f"... {len(rows) - max_rows} more cataloged tool(s) omitted by limit."
        )
    return "\n".join(lines)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def get_mcp_tool_catalog(
        intent: str = "",
        include_admin: bool = False,
        limit: int = 25,
    ) -> str:
        """
        Choose the right MCP tool for an indexed-repo task.

        Args:
            intent: Optional words such as "symbol", "indexing", "documentation",
                "architecture", "retrieval QA", or "change review".
            include_admin: Include destructive/admin-only tools when true.
            limit: Maximum number of catalog entries to return.
        """

        return render_tool_catalog(
            intent=intent, include_admin=include_admin, limit=limit
        )
