"""Pure helpers for semantic search result processing."""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import re
from datetime import datetime, timezone
from pathlib import Path


def merge_duplicate_experiments(mode: str, experiments: dict | None) -> dict:
    merged = duplicate_experiment_flags_from_env(mode)
    if isinstance(experiments, dict):
        merged.update(experiments)
    return merged


def duplicate_experiment_flags_from_env(mode: str = "code") -> dict:
    mode_norm = (mode or "code").strip().lower()
    stage = (os.getenv("LM_PROXY_DUPLICATE_ROLLOUT_STAGE") or "stage2").strip().lower()
    raw = (os.getenv("LM_PROXY_DUPLICATE_EXPERIMENTS") or "").strip()
    flags = {
        "boilerplate_variant_suppression": False,
        "canonical_docs_mirror_suppression": False,
        "helper_clone_suppression": False,
        "threshold_struct": _float_env("LM_PROXY_DUPLICATE_THRESHOLD_STRUCT"),
        "threshold_lexical": _float_env("LM_PROXY_DUPLICATE_THRESHOLD_LEXICAL"),
        "threshold_role": _float_env("LM_PROXY_DUPLICATE_THRESHOLD_ROLE"),
        "min_length_ratio": _float_env("LM_PROXY_DUPLICATE_MIN_LENGTH_RATIO"),
        "max_length_ratio": _float_env("LM_PROXY_DUPLICATE_MAX_LENGTH_RATIO"),
        "threshold_query_distinction": _float_env("LM_PROXY_DUPLICATE_THRESHOLD_QUERY_DISTINCTION"),
        "allow_cross_role_suppression": False,
    }
    stage_map = {
        "off": {},
        "none": {},
        "exact_only": {},
        "stage1": {"boilerplate_variant_suppression": True},
        "stage2": {
            "boilerplate_variant_suppression": True,
            "canonical_docs_mirror_suppression": True,
        },
        "stage3": {
            "boilerplate_variant_suppression": True,
            "canonical_docs_mirror_suppression": True,
            "helper_clone_suppression": True,
        },
    }
    for key, value in stage_map.get(stage, {}).items():
        flags[key] = value
    if mode_norm == "docs":
        flags["boilerplate_variant_suppression"] = False
        flags["helper_clone_suppression"] = False
    if not raw:
        return flags
    enabled = {
        token.strip().lower()
        for token in raw.split(",")
        if token.strip()
    }
    if (
        mode_norm != "docs"
        and ("boilerplate" in enabled or "boilerplate_variant_suppression" in enabled)
    ):
        flags["boilerplate_variant_suppression"] = True
    if "canonical_docs_mirror" in enabled or "canonical_docs_mirror_suppression" in enabled:
        flags["canonical_docs_mirror_suppression"] = True
    if (
        mode_norm != "docs"
        and ("helper_clone" in enabled or "helper_clone_suppression" in enabled)
    ):
        flags["helper_clone_suppression"] = True
    return flags


def duplicate_experiment_flags_with_query_class(
    mode: str = "code",
    query_class: str | None = None,
) -> dict:
    flags = duplicate_experiment_flags_from_env(mode)
    if isinstance(query_class, str):
        trimmed = query_class.strip()
        if trimmed:
            flags["query_class_override"] = trimmed
    return flags


def duplicate_telemetry_enabled() -> bool:
    raw = os.getenv("LM_PROXY_DUPLICATE_TELEMETRY", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def append_duplicate_telemetry_event(
    trace: dict,
    *,
    query: str,
    tool: str,
    mode: str,
    topic: str = "",
) -> None:
    if not duplicate_telemetry_enabled() or not isinstance(trace, dict):
        return
    path = os.getenv("LM_PROXY_DUPLICATE_TELEMETRY_PATH", "").strip()
    if path:
        target = Path(os.path.expanduser(path))
    else:
        target = Path(__file__).resolve().parents[3] / ".runtime" / "duplicate_telemetry.ndjson"
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "mode": mode,
        "topic": topic,
        "query": (query or "")[:500],
        "selection": trace.get("selection", {}),
        "telemetry": trace.get("telemetry", {}),
        "suppression_policy": trace.get("suppression_policy", "exact_only"),
        "experiments": trace.get("experiments", {}),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
    except Exception:
        return


def _float_env(name: str) -> float | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def is_doc_like_path(file_path: str | None) -> bool:
    if not file_path:
        return False
    norm = (file_path or "").replace("\\", "/").lower()
    return (
        norm.endswith(".md")
        or norm.endswith(".markdown")
        or norm.endswith(".mdx")
        or "/docs/" in norm
        or norm.startswith("docs/")
        or norm.endswith("/readme")
        or norm.endswith("/readme.md")
        or norm.endswith("readme.md")
        or norm.endswith("changelog.md")
        or norm.endswith("quickstart.md")
    )


def implementation_query_intent(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    strong_terms = [
        "neo4j",
        "gds",
        "indexing",
        "finalization",
        "finalize",
        "retried",
        "retry",
        "resolved",
        "resolve",
        "import",
        "imports",
        "service",
        "route",
        "handler",
        "function",
        "method",
        "class",
        "parser",
        "symbol",
        "extraction",
        "repository analysis",
        "code intel",
        "call graph",
        "implementation",
        "code path",
        "where is",
        "where does",
        "find",
        "bug",
        "fix",
        "logic",
        "call site",
        "db model",
        "prisma",
        "quickbooks",
        "tenant credit",
        "accounting sync",
    ]
    if any(term in text for term in strong_terms):
        return True
    if re.search(r"\b[a-z_][a-z0-9_]*\s*\(", text):
        return True
    if "::" in text:
        return True
    token_hits = re.findall(r"[a-zA-Z_]{3,}", text)
    return any(tok in {"svc", "api", "db", "route", "model", "handler"} for tok in token_hits)


def implementation_query_class(query: str) -> str:
    text = (query or "").strip().lower()
    if not implementation_query_intent(text):
        return "general"
    path_hints = implementation_query_path_hints(text)
    if re.search(r"\b(?:where|how)\s+is\s+[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+\s+used\b", text):
        return "usage_lookup"
    if re.search(r"\bexamples?\s+of\s+[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+\b", text):
        return "usage_lookup"
    if (
        "call site" in text
        or "where is it called" in text
        or re.search(r"\bwhere\s+is\s+[a-z_][a-z0-9_]*\s+called\b", text)
        or "who calls" in text
        or "callers of" in text
        or "usage of" in text
        or "usage" in text
        or re.search(r"\b(?:where|how)\s+is\b.*\bused\b", text)
    ):
        return "usage_lookup"
    if (
        "where is" in text
        or "where does" in text
        or "defined" in text
        or "definition" in text
        or "entrypoint" in text
        or "public api" in text
        or "api entrypoint" in text
    ):
        if path_hints and ("entrypoint" in text or "main" in text):
            return "implementation_search"
        return "api_definition_lookup"
    if re.search(r"\bhow does\b", text) and re.search(r"\b[a-z_][a-z0-9_]*\s*\(", text):
        return "implementation_explanation"
    if re.search(r"\b[a-z_][a-z0-9_]*\s*\(", text):
        return "symbol_lookup"
    return "implementation_search"


def query_class_prefers_definitions(query_class: str) -> bool:
    return query_class in {"api_definition_lookup", "implementation_explanation", "symbol_lookup"}


def query_class_prefers_usage(query_class: str) -> bool:
    return query_class == "usage_lookup"


def usage_query_prefers_test_results(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    return any(token in text for token in (" test", " tests", "testing", "spec", "e2e", "unit test"))


def usage_query_prefers_example_results(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    return any(token in text for token in (" example", " examples", "sample", "smoke"))


def implementation_query_prefers_runtime_main_entrypoint(query: str) -> bool:
    text = (query or "").strip().lower()
    if not text:
        return False
    if not implementation_query_path_hints(text):
        return False
    return "main" in text and "entrypoint" in text


def implementation_expected_runtime_entrypoint_paths(query: str) -> list[str]:
    if not implementation_query_prefers_runtime_main_entrypoint(query):
        return []
    suffixes = (
        "src/main.rs",
        "src/main.py",
        "src/main.ts",
        "src/main.tsx",
        "src/main.js",
        "src/main.jsx",
    )
    paths: list[str] = []
    for hint in implementation_query_path_hints(query):
        prefix = hint.rstrip("/")
        for suffix in suffixes:
            paths.append(f"{prefix}/{suffix}")
    return paths


def implementation_query_relaxes_dir_cap(query: str) -> bool:
    if not query:
        return False
    if implementation_query_path_hints(query):
        return True
    return bool(implementation_expected_runtime_entrypoint_paths(query))


def is_low_signal_parser_data_path(file_path: str | None) -> bool:
    if not file_path:
        return False
    norm = (file_path or "").replace("\\", "/").lower()
    return (
        norm.startswith("node-types/")
        or "/node-types/" in norm
        or norm.endswith("-grammar.json")
        or norm.endswith("_grammar.json")
        or norm.endswith("/grammar.json")
        or "/grammars/" in norm
        or norm.startswith("grammars/")
        or norm.endswith("/_semantic_payload.py")
        or norm.endswith("/__init__.pyi")
        or norm.endswith("/index.d.ts")
        or norm.endswith("/importinfo.java")
        or norm.endswith("/processresult.java")
        or norm.endswith("/processconfig.php")
        or norm.endswith("/types.go")
    )


def is_low_signal_binding_surface_path(file_path: str | None) -> bool:
    if not file_path:
        return False
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    return (
        basename in {"models.cs", "types.go", "processresult.java", "processconfig.php"}
        or basename.endswith("registry.java")
        or "/src/main/java/" in norm
        or "/packages/csharp/" in norm
        or "/packages/go/" in norm
    )


def is_usage_heavy_path(file_path: str | None) -> bool:
    if not file_path:
        return False
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    return (
        basename == "main.rs"
        or "/tests/" in norm
        or "/test/" in norm
        or "/e2e/" in norm
        or "/examples/" in norm
        or "/spec/" in norm
        or norm.endswith("_test.go")
        or norm.endswith("_spec.rb")
    )


def is_low_signal_support_path(file_path: str | None) -> bool:
    if not file_path:
        return False
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    return (
        norm.startswith("scripts/")
        or "/scripts/" in norm
        or norm.startswith("tools/")
        or "/tools/" in norm
        or norm.startswith(".github/")
        or "/.github/" in norm
        or norm.startswith("nix/")
        or "/nix/" in norm
        or "/release/" in norm
        or "/vendor" in norm
        or basename in {"build.rs", "build.py"}
        or basename in {"justfile", "makefile"}
    )


def implementation_rank_tuple(
    result: dict,
) -> tuple[int, int, int, int, int, int, int, int, int, int, int, int, int, int, int, int, float, float]:
    """Rank implementation-intent results with code first, then docs/parser data last."""
    low_signal_parser_data = 1 if result.get("low_signal_parser_data") else 0
    low_signal_binding_surface = 1 if result.get("low_signal_binding_surface") else 0
    low_signal_support = 1 if result.get("low_signal_support_path") else 0
    doc_like = 1 if result.get("doc_like") else 0
    usage_heavy = 1 if result.get("implementation_usage_heavy_penalty") else 0
    member_usage_priority = int(result.get("implementation_member_usage_priority", 0) or 0)
    path_hint_priority = int(result.get("implementation_path_hint_hit", 0) or 0)
    runtime_main_priority = int(result.get("implementation_runtime_main_entrypoint_hit", 0) or 0)
    role_priority = int(result.get("implementation_role_priority", 0) or 0)
    node_type_priority = int(result.get("implementation_node_type_priority", 0) or 0)
    reexport_surface = int(result.get("implementation_reexport_surface_hit", 0) or 0)
    facade_surface = int(result.get("implementation_facade_surface_hit", 0) or 0)
    declared_symbol_hit = int(result.get("implementation_declared_symbol_hit", 0) or 0)
    definition_hit = int(result.get("implementation_definition_hit", 0) or 0)
    signature_hit = int(result.get("implementation_exact_signature_symbol_hit", 0) or 0)
    export_hit = int(result.get("implementation_export_hit", 0) or 0)
    api_entrypoint_hit = int(result.get("implementation_api_entrypoint_hit", 0) or 0)
    symbol_hit = int(result.get("implementation_symbol_hit", 0) or 0)
    rank_score = float(result.get("rank_score", result.get("rrf", 0.0)) or 0.0)
    meta_score = float(result.get("meta_score", 0.0) or 0.0)
    return (
        low_signal_parser_data,
        low_signal_binding_surface,
        low_signal_support,
        doc_like,
        usage_heavy,
        -member_usage_priority,
        -path_hint_priority,
        -runtime_main_priority,
        -role_priority,
        -node_type_priority,
        facade_surface,
        reexport_surface,
        -declared_symbol_hit,
        -signature_hit,
        -definition_hit,
        -export_hit,
        -api_entrypoint_hit,
        -symbol_hit,
        -rank_score,
        -meta_score,
    )


def implementation_query_symbols(query: str) -> set[str]:
    text = (query or "").strip().lower()
    if not text:
        return set()
    stopwords = {
        "how",
        "does",
        "work",
        "works",
        "what",
        "where",
        "when",
        "which",
        "into",
        "from",
        "with",
        "that",
        "this",
        "have",
        "uses",
        "using",
        "used",
        "build",
        "builds",
        "local",
        "files",
        "file",
        "code",
        "tree",
        "pack",
        "index",
        "ts",
    }
    symbols = {
        token
        for token in re.findall(r"[a-z_][a-z0-9_]*", text)
        if len(token) >= 3 and token not in stopwords
    }
    return symbols


def implementation_query_member_exprs(query: str) -> set[str]:
    text = (query or "").strip().lower()
    if not text:
        return set()
    return {
        expr
        for expr in re.findall(r"\b[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+\b", text)
        if len(expr.split(".")) >= 2
    }


def implementation_query_path_hints(query: str) -> list[str]:
    text = (query or "").strip().lower()
    if not text:
        return []
    hints = {
        hint.strip("./")
        for hint in re.findall(r"\b[a-z0-9._-]+(?:/[a-z0-9._-]+){1,}\b", text)
        if "/" in hint
    }
    return sorted(hint for hint in hints if hint)


def implementation_path_hint_hit(file_path: str | None, query: str | None = None, path_hints: list[str] | None = None) -> int:
    norm = (file_path or "").replace("\\", "/").lower()
    if not norm:
        return 0
    hints = path_hints if path_hints is not None else implementation_query_path_hints(query or "")
    if not hints:
        return 0
    return sum(1 for hint in hints if hint and hint in norm)


def implementation_symbol_hit(meta: dict, query: str) -> int:
    if not isinstance(meta, dict):
        return 0
    symbols = implementation_query_symbols(query)
    if not symbols:
        return 0
    file_symbols = meta.get("file_symbols")
    if not isinstance(file_symbols, list) or not file_symbols:
        return 0
    lowered = {
        str(symbol).strip().lower()
        for symbol in file_symbols
        if str(symbol).strip()
    }
    if not lowered:
        return 0
    return sum(1 for symbol in symbols if symbol in lowered)


def implementation_declared_symbol_hit(meta: dict, query: str) -> int:
    if not isinstance(meta, dict):
        return 0
    symbols = implementation_query_symbols(query)
    if not symbols:
        return 0
    declared_symbols = meta.get("declared_symbols")
    if not isinstance(declared_symbols, list) or not declared_symbols:
        return 0
    lowered = {
        str(symbol).strip().lower()
        for symbol in declared_symbols
        if str(symbol).strip()
    }
    if not lowered:
        return 0
    return sum(1 for symbol in symbols if symbol in lowered)


def implementation_member_usages(meta: dict) -> set[str]:
    if not isinstance(meta, dict):
        return set()
    member_usages = meta.get("member_usages")
    if not isinstance(member_usages, list):
        return set()
    return {
        str(expr).strip().lower()
        for expr in member_usages
        if str(expr).strip()
    }


def implementation_chunk_role(meta: dict, file_path: str | None = None) -> str:
    if not isinstance(meta, dict):
        meta = {}
    role = meta.get("chunk_role")
    if isinstance(role, str) and role.strip():
        return role.strip().lower()
    path = (file_path or "").replace("\\", "/").lower()
    if not path:
        return ""
    if path.startswith("examples/") or "/examples/" in path:
        return "example_usage"
    if any(segment in path for segment in ("/tests/", "/test/", "/e2e/", "/spec/")):
        return "test_usage"
    if path.startswith("scripts/") or "/scripts/" in path:
        return "script_support"
    return ""


def implementation_exact_member_usage_hit(content: str | None, query: str, meta: dict | None = None) -> int:
    exprs = implementation_query_member_exprs(query)
    if not exprs:
        return 0
    meta_hits = implementation_member_usages(meta or {})
    if meta_hits:
        return sum(1 for expr in exprs if expr in meta_hits)
    text = (content or "").lower()
    if not text:
        return 0
    return sum(1 for expr in exprs if expr in text)


def implementation_exact_member_usage_site_hit(result: dict) -> bool:
    if int(result.get("implementation_exact_member_usage_hit", 0) or 0) <= 0:
        return False
    role = str(result.get("implementation_role") or "").strip().lower()
    if role in {"usage_callsite", "test_example"}:
        return True
    chunk_role = implementation_chunk_role(coerce_meta(result), result.get("file_path"))
    return chunk_role in {"usage", "example_usage", "test_usage"}


def implementation_definition_hit(content: str | None, query: str) -> int:
    text = (content or "").strip()
    if not text:
        return 0
    symbols = implementation_query_symbols(query)
    if not symbols:
        return 0
    lowered = text.lower()
    hits = 0
    for symbol in symbols:
        patterns = [
            rf"\bpub\s+fn\s+{re.escape(symbol)}\s*\(",
            rf"\bfn\s+{re.escape(symbol)}\s*\(",
            rf"\bdef\s+{re.escape(symbol)}\s*\(",
            rf"\basync\s+def\s+{re.escape(symbol)}\s*\(",
            rf"\bfunction\s+{re.escape(symbol)}\s*\(",
            rf"\bexport\s+(?:async\s+)?function\s+{re.escape(symbol)}\s*\(",
            rf"\b{re.escape(symbol)}\s*:\s*function\b",
        ]
        if any(re.search(pattern, lowered) for pattern in patterns):
            hits += 1
    return hits


def implementation_api_entrypoint_hit(file_path: str | None, definition_hit: int) -> int:
    if definition_hit <= 0 or not file_path:
        return 0
    norm = (file_path or "").replace("\\", "/").lower()
    api_entrypoint_suffixes = (
        "/src/lib.rs",
        "/src/main.rs",
        "/src/main.py",
        "/src/main.ts",
        "/src/main.tsx",
        "/src/main.js",
        "/src/main.jsx",
        "/src/index.ts",
        "/src/index.tsx",
        "/src/index.js",
        "/src/index.jsx",
        "/__init__.py",
        "/lib.rs",
    )
    if norm.endswith(api_entrypoint_suffixes):
        return 1
    if norm.endswith("/main.go") and ("/cmd/" in norm or norm.startswith("cmd/")):
        return 1
    return 0


def implementation_runtime_main_entrypoint_hit(file_path: str | None, query: str) -> int:
    if not implementation_query_prefers_runtime_main_entrypoint(query) or not file_path:
        return 0
    norm = (file_path or "").replace("\\", "/").lower()
    runtime_main_suffixes = (
        "/src/main.rs",
        "/src/main.py",
        "/src/main.ts",
        "/src/main.tsx",
        "/src/main.js",
        "/src/main.jsx",
    )
    if norm.endswith(runtime_main_suffixes):
        return 1
    if norm.endswith("/main.go") and ("/cmd/" in norm or norm.startswith("cmd/")):
        return 1
    return 0


DECLARATION_NODE_TYPES = {
    "function_definition",
    "function_declaration",
    "function_item",
    "method_definition",
    "method_declaration",
    "method_item",
    "impl_item",
    "impl_block",
    "class_definition",
    "class_declaration",
    "class_specifier",
    "struct_item",
    "struct_specifier",
    "enum_item",
    "enum_specifier",
    "type_alias_declaration",
    "module",
    "mod_item",
    "source_file",
}

EXPORT_NODE_TYPES = {
    "export_statement",
    "export_clause",
    "export_specifier",
    "public_item",
}

CALLSITE_NODE_TYPES = {
    "call_expression",
    "call",
    "expression_statement",
    "match_expression",
    "await_expression",
    "argument_list",
}

TYPE_DEFINITION_NODE_TYPES = {
    "class_definition",
    "class_declaration",
    "class_specifier",
    "struct_item",
    "struct_specifier",
    "enum_item",
    "enum_specifier",
    "type_alias_declaration",
}

MODULE_NODE_TYPES = {"module", "mod_item", "source_file"}


def implementation_node_types(meta: dict) -> set[str]:
    if not isinstance(meta, dict):
        return set()
    node_types = meta.get("node_types")
    if not isinstance(node_types, list):
        return set()
    return {
        str(node_type).strip().lower()
        for node_type in node_types
        if str(node_type).strip()
    }


def implementation_exact_signature_symbol_hit(content: str | None, query: str) -> int:
    text = (content or "").strip()
    if not text:
        return 0
    header = "\n".join(text.splitlines()[:3]).lower()
    symbols = implementation_query_symbols(query)
    if not symbols:
        return 0
    hits = 0
    for symbol in symbols:
        patterns = [
            rf"\bpub\s+fn\s+{re.escape(symbol)}\s*\(",
            rf"\bfn\s+{re.escape(symbol)}\s*\(",
            rf"\bdef\s+{re.escape(symbol)}\s*\(",
            rf"\basync\s+def\s+{re.escape(symbol)}\s*\(",
            rf"\bfunction\s+{re.escape(symbol)}\s*\(",
            rf"\bexport\s+(?:async\s+)?function\s+{re.escape(symbol)}\s*\(",
            rf"\b{re.escape(symbol)}\s*:\s*function\b",
        ]
        if any(re.search(pattern, header) for pattern in patterns):
            hits += 1
    return hits


def implementation_export_hit(content: str | None, file_path: str | None, meta: dict) -> int:
    node_types = implementation_node_types(meta)
    path = (file_path or "").replace("\\", "/").lower()
    text = (content or "").lower()
    score = 0
    if node_types & EXPORT_NODE_TYPES:
        score += 1
    if "/src/lib.rs" in path or path.endswith("/lib.rs") or path.endswith("/__init__.py"):
        if "pub fn " in text or "pub use " in text or "__all__" in text or "export " in text:
            score += 1
    if re.search(r"\bpub\s+use\b", text) or re.search(r"\bexport\s+(?:\{|\*)", text):
        score += 1
    return score


def implementation_reexport_surface_hit(
    content: str | None,
    file_path: str | None,
    meta: dict,
    *,
    definition_hit: int,
    signature_hit: int,
) -> int:
    if definition_hit > 0 or signature_hit > 0:
        return 0
    path = (file_path or "").replace("\\", "/").lower()
    text = (content or "").lower()
    node_types = implementation_node_types(meta)
    reexport_like = any(
        (
            re.search(r"\bfrom\s+\.", text),
            re.search(r"\bpub\s+use\b", text),
            re.search(r"\b__all__\b", text),
            re.search(r"\bexport\s+(?:\{|\*)", text),
        )
    )
    if not reexport_like:
        return 0
    if path.endswith("/__init__.py") or path.endswith("/__init__.pyi") or path.endswith("/lib.rs"):
        return 1
    if node_types & EXPORT_NODE_TYPES:
        return 1
    return 0


def implementation_facade_surface_hit(
    file_path: str | None,
    meta: dict,
    *,
    symbol_hit: int,
    declared_symbol_hit: int,
    definition_hit: int,
    signature_hit: int,
    export_hit: int,
    api_context_hit: int,
) -> int:
    if symbol_hit <= 0:
        return 0
    if declared_symbol_hit > 0 or definition_hit > 0 or signature_hit > 0:
        return 0
    node_types = implementation_node_types(meta)
    declaration_like = bool(node_types & DECLARATION_NODE_TYPES)
    module_like = bool(node_types & MODULE_NODE_TYPES)
    chunk_role = implementation_chunk_role(meta, file_path)
    if export_hit > 0 or api_context_hit > 0:
        return 1
    if chunk_role == "definition" and (declaration_like or module_like):
        return 1
    return 0


def implementation_api_context_hit(meta: dict) -> int:
    if not isinstance(meta, dict):
        return 0
    context_path = meta.get("context_path")
    if not isinstance(context_path, list):
        return 0
    lowered = {str(part).strip().lower() for part in context_path if str(part).strip()}
    return 1 if {"api", "public", "exports"} & lowered else 0


def implementation_node_type_priority(meta: dict, query_class: str) -> int:
    node_types = implementation_node_types(meta)
    declaration_like = bool(node_types & DECLARATION_NODE_TYPES)
    callsite_like = bool(node_types & CALLSITE_NODE_TYPES)
    export_like = bool(node_types & EXPORT_NODE_TYPES)
    type_like = bool(node_types & TYPE_DEFINITION_NODE_TYPES)
    module_like = bool(node_types & MODULE_NODE_TYPES)
    if query_class_prefers_usage(query_class):
        if callsite_like:
            return 4
        if declaration_like:
            return 2
        return 0
    if query_class_prefers_definitions(query_class):
        score = 0
        if declaration_like:
            score += 4
        if export_like:
            score += 2
        if type_like or module_like:
            score += 1
        if callsite_like and not declaration_like:
            score -= 4
        return score
    score = 0
    if declaration_like:
        score += 2
    if callsite_like:
        score += 1
    return score


def implementation_result_role(
    file_path: str | None,
    meta: dict,
    *,
    definition_hit: int,
    export_hit: int,
    api_entrypoint_hit: int,
) -> str:
    path = (file_path or "").replace("\\", "/").lower()
    node_types = implementation_node_types(meta)
    declaration_like = bool(node_types & DECLARATION_NODE_TYPES)
    callsite_like = bool(node_types & CALLSITE_NODE_TYPES)
    api_context_hit = implementation_api_context_hit(meta)
    chunk_role = implementation_chunk_role(meta, file_path)
    if is_low_signal_binding_surface_path(file_path):
        return "generated_surface"
    if is_doc_like_path(file_path):
        return "docs"
    if chunk_role in {"example_usage", "test_usage"}:
        return "test_example"
    if chunk_role == "script_support":
        return "supporting_context"
    if chunk_role == "usage" and definition_hit <= 0 and export_hit <= 0:
        return "usage_callsite"
    if chunk_role == "definition" and (api_entrypoint_hit > 0 or api_context_hit > 0):
        return "public_api_definition"
    if chunk_role == "definition" and export_hit > 0:
        return "canonical_definition"
    if chunk_role == "definition" and definition_hit > 0:
        return "internal_implementation"
    if is_usage_heavy_path(file_path):
        if callsite_like and definition_hit <= 0 and export_hit <= 0:
            if any(segment in path for segment in ("/tests/", "/test/", "/e2e/", "/examples/", "/spec/")):
                return "test_example"
            return "usage_callsite"
    if declaration_like and (api_entrypoint_hit > 0 or api_context_hit > 0):
        return "public_api_definition"
    if declaration_like and export_hit > 0:
        return "canonical_definition"
    if declaration_like and definition_hit > 0:
        return "internal_implementation"
    if callsite_like:
        return "usage_callsite"
    if is_usage_heavy_path(file_path):
        return "test_example"
    return "internal_implementation" if declaration_like else "supporting_context"


def implementation_role_priority(role: str, query_class: str) -> int:
    if query_class_prefers_usage(query_class):
        order = {
            "test_example": 7,
            "usage_callsite": 6,
            "internal_implementation": 4,
            "canonical_definition": 3,
            "public_api_definition": 2,
            "supporting_context": 1,
            "generated_surface": 0,
            "docs": 0,
        }
        return order.get(role, 0)
    if query_class_prefers_definitions(query_class):
        order = {
            "public_api_definition": 7,
            "canonical_definition": 6,
            "internal_implementation": 5,
            "supporting_context": 3,
            "usage_callsite": 1,
            "test_example": 0,
            "generated_surface": 0,
            "docs": 0,
        }
        return order.get(role, 0)
    order = {
        "internal_implementation": 5,
        "canonical_definition": 4,
        "public_api_definition": 4,
        "supporting_context": 3,
        "usage_callsite": 2,
        "test_example": 1,
        "generated_surface": 0,
        "docs": 0,
    }
    return order.get(role, 0)


def implementation_role_score(role: str, query_class: str) -> float:
    if query_class_prefers_usage(query_class):
        weights = {
            "test_example": 0.1,
            "usage_callsite": 0.07,
            "internal_implementation": 0.02,
            "canonical_definition": 0.0,
            "public_api_definition": -0.01,
        }
        return weights.get(role, 0.0)
    if query_class == "implementation_explanation":
        weights = {
            "public_api_definition": 0.09,
            "canonical_definition": 0.06,
            "internal_implementation": 0.04,
            "supporting_context": 0.01,
            "usage_callsite": -0.06,
            "test_example": -0.05,
        }
        return weights.get(role, 0.0)
    if query_class_prefers_definitions(query_class):
        weights = {
            "public_api_definition": 0.1,
            "canonical_definition": 0.07,
            "internal_implementation": 0.03,
            "supporting_context": 0.0,
            "usage_callsite": -0.07,
            "test_example": -0.06,
        }
        return weights.get(role, 0.0)
    weights = {
        "internal_implementation": 0.04,
        "canonical_definition": 0.03,
        "public_api_definition": 0.02,
        "usage_callsite": 0.0,
        "test_example": -0.01,
    }
    return weights.get(role, 0.0)


def implementation_node_type_score(meta: dict, query_class: str) -> float:
    node_types = implementation_node_types(meta)
    declaration_like = bool(node_types & DECLARATION_NODE_TYPES)
    callsite_like = bool(node_types & CALLSITE_NODE_TYPES)
    export_like = bool(node_types & EXPORT_NODE_TYPES)
    type_like = bool(node_types & TYPE_DEFINITION_NODE_TYPES)
    module_like = bool(node_types & MODULE_NODE_TYPES)
    score = 0.0
    if query_class_prefers_usage(query_class):
        if callsite_like:
            score += 0.05
        if declaration_like:
            score -= 0.01
        return score
    if declaration_like:
        score += 0.05 if query_class_prefers_definitions(query_class) else 0.03
    if export_like:
        score += 0.03
    if type_like or module_like:
        score += 0.01
    if callsite_like and not declaration_like:
        score -= 0.06 if query_class_prefers_definitions(query_class) else 0.01
    return score


def enrich_implementation_result(
    result: dict,
    *,
    query: str,
    query_class: str,
    base_score: float,
    meta_boost: float = 0.0,
    base_bonus: float = 0.0,
) -> dict:
    meta = coerce_meta(result)
    result["_meta"] = meta
    result["doc_like"] = is_doc_like_path(result.get("file_path"))
    result["low_signal_parser_data"] = is_low_signal_parser_data_path(result.get("file_path"))
    result["low_signal_binding_surface"] = is_low_signal_binding_surface_path(result.get("file_path"))
    result["low_signal_support_path"] = is_low_signal_support_path(result.get("file_path"))
    result["implementation_symbol_hit"] = implementation_symbol_hit(meta, query)
    result["implementation_declared_symbol_hit"] = implementation_declared_symbol_hit(meta, query)
    result["implementation_path_hint_hit"] = implementation_path_hint_hit(
        result.get("file_path"),
        query=query,
    )
    chunk_role = implementation_chunk_role(meta)
    result["implementation_chunk_role"] = chunk_role
    result["implementation_exact_member_usage_hit"] = implementation_exact_member_usage_hit(
        result.get("content", ""),
        query,
        meta,
    )
    result["implementation_member_usage_priority"] = int(
        result.get("implementation_exact_member_usage_hit", 0) or 0
    )
    result["implementation_definition_hit"] = implementation_definition_hit(result.get("content", ""), query)
    result["implementation_exact_signature_symbol_hit"] = implementation_exact_signature_symbol_hit(
        result.get("content", ""),
        query,
    )
    result["implementation_export_hit"] = implementation_export_hit(
        result.get("content", ""),
        result.get("file_path"),
        meta,
    )
    result["implementation_reexport_surface_hit"] = implementation_reexport_surface_hit(
        result.get("content", ""),
        result.get("file_path"),
        meta,
        definition_hit=int(result.get("implementation_definition_hit", 0) or 0),
        signature_hit=int(result.get("implementation_exact_signature_symbol_hit", 0) or 0),
    )
    result["implementation_api_context_hit"] = implementation_api_context_hit(meta)
    result["implementation_facade_surface_hit"] = implementation_facade_surface_hit(
        result.get("file_path"),
        meta,
        symbol_hit=int(result.get("implementation_symbol_hit", 0) or 0),
        declared_symbol_hit=int(result.get("implementation_declared_symbol_hit", 0) or 0),
        definition_hit=int(result.get("implementation_definition_hit", 0) or 0),
        signature_hit=int(result.get("implementation_exact_signature_symbol_hit", 0) or 0),
        export_hit=int(result.get("implementation_export_hit", 0) or 0),
        api_context_hit=int(result.get("implementation_api_context_hit", 0) or 0),
    )
    result["implementation_api_entrypoint_hit"] = implementation_api_entrypoint_hit(
        result.get("file_path", ""),
        max(
            int(result.get("implementation_definition_hit", 0) or 0),
            int(result.get("implementation_declared_symbol_hit", 0) or 0),
        ),
    )
    result["implementation_runtime_main_entrypoint_hit"] = implementation_runtime_main_entrypoint_hit(
        result.get("file_path", ""),
        query,
    )
    result["implementation_usage_heavy_penalty"] = (
        query_class_prefers_definitions(query_class) and is_usage_heavy_path(result.get("file_path", ""))
    )
    result["implementation_node_type_priority"] = implementation_node_type_priority(meta, query_class)
    result["implementation_node_type_score"] = implementation_node_type_score(meta, query_class)
    role = implementation_result_role(
        result.get("file_path"),
        meta,
        definition_hit=int(result.get("implementation_definition_hit", 0) or 0),
        export_hit=int(result.get("implementation_export_hit", 0) or 0),
        api_entrypoint_hit=int(result.get("implementation_api_entrypoint_hit", 0) or 0),
    )
    if (
        query_class in {"api_definition_lookup", "symbol_lookup"}
        and int(result.get("implementation_reexport_surface_hit", 0) or 0) > 0
        and role in {"public_api_definition", "canonical_definition", "internal_implementation"}
    ):
        role = "supporting_context"
    if (
        query_class in {"api_definition_lookup", "symbol_lookup"}
        and int(result.get("implementation_facade_surface_hit", 0) or 0) > 0
        and role in {"public_api_definition", "canonical_definition", "internal_implementation"}
    ):
        role = "supporting_context"
    result["implementation_role"] = role
    result["implementation_role_priority"] = implementation_role_priority(role, query_class)
    result["implementation_role_score"] = implementation_role_score(role, query_class)

    doc_penalty = 0.05 if result["doc_like"] else 0.0
    parser_data_penalty = 0.08 if result["low_signal_parser_data"] else 0.0
    binding_surface_penalty = 0.06 if result["low_signal_binding_surface"] else 0.0
    if result["low_signal_support_path"]:
        support_path_penalty = 0.05 if query_class_prefers_definitions(query_class) else 0.03
    else:
        support_path_penalty = 0.0
    usage_penalty = 0.04 if result["implementation_usage_heavy_penalty"] else 0.0
    symbol_bonus = 0.015 * min(int(result.get("implementation_symbol_hit", 0) or 0), 2)
    declared_symbol_bonus = 0.0
    path_hint_bonus = 0.0
    runtime_main_bonus = 0.0
    member_usage_bonus = 0.0
    chunk_role_bonus = 0.0
    if query_class_prefers_usage(query_class):
        member_usage_bonus = 0.06 * min(int(result.get("implementation_exact_member_usage_hit", 0) or 0), 2)
        exact_member_hits = int(result.get("implementation_exact_member_usage_hit", 0) or 0)
        if exact_member_hits > 0:
            if chunk_role == "example_usage":
                chunk_role_bonus += 0.11
            elif chunk_role == "test_usage":
                chunk_role_bonus += 0.06
            elif chunk_role == "usage":
                chunk_role_bonus += 0.03
    elif query_class == "implementation_search":
        member_usage_bonus = 0.015 * min(int(result.get("implementation_exact_member_usage_hit", 0) or 0), 2)
    path_hint_hits = int(result.get("implementation_path_hint_hit", 0) or 0)
    if path_hint_hits > 0:
        if query_class == "implementation_search":
            path_hint_bonus = 0.12 * min(path_hint_hits, 1)
        elif query_class_prefers_definitions(query_class):
            path_hint_bonus = 0.08 * min(path_hint_hits, 1)
    runtime_main_hits = int(result.get("implementation_runtime_main_entrypoint_hit", 0) or 0)
    if runtime_main_hits > 0:
        runtime_main_bonus = 0.16 * min(runtime_main_hits, 1)
    library_entrypoint_penalty = 0.0
    if implementation_query_prefers_runtime_main_entrypoint(query):
        norm = (result.get("file_path") or "").replace("\\", "/").lower()
        if norm.endswith(("/src/lib.rs", "/__init__.py", "/lib.rs")):
            library_entrypoint_penalty = 0.05
    definition_bonus = 0.025 * min(int(result.get("implementation_definition_hit", 0) or 0), 2)
    signature_bonus = 0.04 * min(int(result.get("implementation_exact_signature_symbol_hit", 0) or 0), 2)
    if query_class_prefers_definitions(query_class):
        declared_symbol_bonus = 0.05 * min(int(result.get("implementation_declared_symbol_hit", 0) or 0), 2)
        export_bonus = 0.03 * min(int(result.get("implementation_export_hit", 0) or 0), 2)
        api_entrypoint_bonus = 0.03 * min(int(result.get("implementation_api_entrypoint_hit", 0) or 0), 1)
        api_context_bonus = 0.02 * min(int(result.get("implementation_api_context_hit", 0) or 0), 1)
        reexport_surface_penalty = 0.07 * min(int(result.get("implementation_reexport_surface_hit", 0) or 0), 1)
        facade_surface_penalty = 0.05 * min(int(result.get("implementation_facade_surface_hit", 0) or 0), 1)
    elif query_class == "implementation_search":
        declared_symbol_bonus = 0.02 * min(int(result.get("implementation_declared_symbol_hit", 0) or 0), 2)
        export_bonus = 0.01 * min(int(result.get("implementation_export_hit", 0) or 0), 2)
        api_entrypoint_bonus = 0.0
        api_context_bonus = 0.0
        reexport_surface_penalty = 0.0
        facade_surface_penalty = 0.0
    else:
        declared_symbol_bonus = 0.01 * min(int(result.get("implementation_declared_symbol_hit", 0) or 0), 2)
        export_bonus = 0.0
        api_entrypoint_bonus = 0.0
        api_context_bonus = 0.0
        reexport_surface_penalty = 0.0
        facade_surface_penalty = 0.0
    meta_component = (float(result.get("meta_score", 0.0) or 0.0) * meta_boost) if meta_boost > 0 else 0.0

    result["rank_score"] = (
        float(base_score)
        + float(base_bonus)
        + meta_component
        + float(result.get("implementation_node_type_score", 0.0) or 0.0)
        + float(result.get("implementation_role_score", 0.0) or 0.0)
        + symbol_bonus
        + declared_symbol_bonus
        + path_hint_bonus
        + runtime_main_bonus
        + member_usage_bonus
        + chunk_role_bonus
        + definition_bonus
        + signature_bonus
        + export_bonus
        + api_entrypoint_bonus
        + api_context_bonus
        - doc_penalty
        - parser_data_penalty
        - binding_surface_penalty
        - support_path_penalty
        - library_entrypoint_penalty
        - reexport_surface_penalty
        - facade_surface_penalty
        - usage_penalty
    )
    result["implementation_rank_components"] = {
        "base_relevance": float(base_score),
        "base_bonus": float(base_bonus),
        "meta_component": meta_component,
        "node_type_score": float(result.get("implementation_node_type_score", 0.0) or 0.0),
        "role_score": float(result.get("implementation_role_score", 0.0) or 0.0),
        "symbol_bonus": symbol_bonus,
        "declared_symbol_bonus": declared_symbol_bonus,
        "path_hint_bonus": path_hint_bonus,
        "runtime_main_bonus": runtime_main_bonus,
        "member_usage_bonus": member_usage_bonus,
        "chunk_role_bonus": chunk_role_bonus,
        "definition_bonus": definition_bonus,
        "signature_bonus": signature_bonus,
        "export_bonus": export_bonus,
        "api_entrypoint_bonus": api_entrypoint_bonus,
        "api_context_bonus": api_context_bonus,
        "reexport_surface_penalty": reexport_surface_penalty,
        "facade_surface_penalty": facade_surface_penalty,
        "doc_penalty": doc_penalty,
        "parser_data_penalty": parser_data_penalty,
        "binding_surface_penalty": binding_surface_penalty,
        "support_path_penalty": support_path_penalty,
        "library_entrypoint_penalty": library_entrypoint_penalty,
        "usage_penalty": usage_penalty,
        "role": role,
        "chunk_role": chunk_role,
        "node_types": sorted(implementation_node_types(meta)),
    }
    return result


def build_implementation_ranking_trace(results: list[dict], query: str) -> dict:
    query_class = implementation_query_class(query)
    enriched: list[dict] = []
    for result in results:
        row = dict(result)
        meta = coerce_meta(row)
        row["_meta"] = meta
        row["meta_score"] = meta_score(meta)
        try:
            base_score = float(row.get("rrf", row.get("rank_score", 0.0)) or 0.0)
        except (TypeError, ValueError):
            base_score = 0.0
        enrich_implementation_result(
            row,
            query=query,
            query_class=query_class,
            base_score=base_score,
            meta_boost=0.0,
        )
        enriched.append(row)
    enriched.sort(key=implementation_rank_tuple)
    return {
        "query_class": query_class,
        "rows": [
            {
                "file_path": row.get("file_path"),
                "base_relevance": row.get("implementation_rank_components", {}).get("base_relevance", 0.0),
                "rank_score": row.get("rank_score", 0.0),
                "role": row.get("implementation_role"),
                "role_priority": row.get("implementation_role_priority"),
                "node_types": row.get("implementation_rank_components", {}).get("node_types", []),
                "node_type_priority": row.get("implementation_node_type_priority"),
                "components": row.get("implementation_rank_components", {}),
            }
            for row in enriched
        ],
    }


def _context_payload(results: list[dict]) -> str:
    payload: list[dict] = []
    for result in results:
        file_path = result.get("file_path") or result.get("source_url") or ""
        payload.append(
            {
                "file_path": file_path,
                "metadata": coerce_meta(result),
            }
        )
    return json.dumps(payload)


def _load_ts_pack():
    try:
        import tree_sitter_language_pack as ts_pack
    except Exception:
        return None
    return ts_pack


def _ts_pack_runtime_call(
    function_name: str,
    *,
    texts: list[str],
    relevance_scores: list[float] | None = None,
    query: str = "",
    mode: str = "code",
    contexts_json: str = "[]",
    experiments: dict | None = None,
):
    try:
        from _runtime import resolve_python_runtime
    except Exception:
        return None
    runtime = resolve_python_runtime()
    python_cmd = list(runtime.get("cmd") or [])
    if not python_cmd:
        return None
    payload = {
        "function": function_name,
        "texts": texts,
        "relevance_scores": relevance_scores or [],
        "query": query or None,
        "mode": mode,
        "contexts_json": contexts_json,
        "experiments": experiments or {},
    }
    script = """
import json, sys
import tree_sitter_language_pack as ts_pack
payload = json.loads(sys.stdin.read())
fn = payload["function"]
if fn == "analyze_duplicate_texts":
    result = ts_pack.analyze_duplicate_texts(
        payload["texts"],
        payload.get("query"),
        payload.get("mode"),
        payload.get("contexts_json"),
    )
elif fn == "rerank_diverse_texts":
    result = ts_pack.rerank_diverse_texts(
        payload["texts"],
        payload.get("relevance_scores") or [],
        payload.get("query"),
        payload.get("mode"),
        payload.get("contexts_json"),
    )
elif fn == "trace_diverse_texts":
    result = ts_pack.trace_diverse_texts(
        payload["texts"],
        payload.get("relevance_scores") or [],
        payload.get("query"),
        payload.get("mode"),
        payload.get("contexts_json"),
        json.dumps(payload.get("experiments") or {}),
    )
else:
    raise SystemExit(f"unknown function: {fn}")
print(json.dumps(result))
"""
    try:
        completed = subprocess.run(
            python_cmd + ["-c", script],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=True,
        )
    except Exception:
        return None
    try:
        return json.loads(completed.stdout)
    except Exception:
        return None


def collapse_near_duplicate_results(results: list[dict], *, query: str = "", mode: str = "code") -> list[dict]:
    if len(results) < 2:
        return results
    contract = rerank_retrieval_results_contract(results, query=query, mode=mode)
    keep_indices = contract.get("keep_indices")
    if not isinstance(keep_indices, list):
        return results
    keep_set = {
        idx
        for idx in keep_indices
        if isinstance(idx, int) and 0 <= idx < len(results)
    }
    if not keep_set:
        return results
    return [results[idx] for idx in keep_indices if idx in keep_set]


def _coerce_result_score(result: dict) -> float:
    for key in ("rrf", "rank_score", "score", "relevance_score"):
        value = result.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def candidate_relevance_score(result: dict) -> float:
    for key in ("rank_score", "relevance_score", "score", "rrf"):
        value = result.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _normalize_result_item(result: dict, index: int) -> dict:
    normalized = dict(result) if isinstance(result, dict) else {"content": str(result)}
    normalized["original_index"] = index
    content = normalized.get("content")
    if not isinstance(content, str):
        normalized["content"] = "" if content is None else str(content)
    normalized["rrf"] = _coerce_result_score(normalized)
    meta = normalized.get("metadata")
    if meta is None and isinstance(normalized.get("_meta"), dict):
        normalized["metadata"] = dict(normalized["_meta"])
    elif isinstance(meta, dict):
        normalized["metadata"] = dict(meta)
    return normalized


def _normalize_result_items(results: list[dict]) -> list[dict]:
    return [_normalize_result_item(result, idx) for idx, result in enumerate(results)]


def _prepare_results_for_duplicate_rerank(
    results: list[dict],
    *,
    query: str = "",
    mode: str = "code",
) -> list[dict]:
    prepared = _normalize_result_items(results)
    if (mode or "code").strip().lower() != "code" or not implementation_query_intent(query):
        return prepared
    query_class = implementation_query_class(query)
    for row in prepared:
        if row.get("_implementation_rank_enriched"):
            continue
        meta = coerce_meta(row)
        row["_meta"] = meta
        row["meta_score"] = meta_score(meta)
        try:
            base_score = float(row.get("rank_score", row.get("rrf", 0.0)) or 0.0)
        except (TypeError, ValueError):
            base_score = 0.0
        enrich_implementation_result(
            row,
            query=query,
            query_class=query_class,
            base_score=base_score,
            meta_boost=0.0,
        )
        row["_implementation_rank_enriched"] = True
    return prepared


def _dedupe_int_list(values, *, limit: int) -> list[int]:
    output: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        if not isinstance(value, int) or value in seen or value < 0 or value >= limit:
            continue
        seen.add(value)
        output.append(value)
    return output


def _normalize_duplicate_pairs(pairs, *, limit: int) -> list[dict]:
    normalized: list[dict] = []
    for pair in pairs or []:
        if not isinstance(pair, dict):
            continue
        left = pair.get("left")
        right = pair.get("right")
        if not isinstance(left, int) or not isinstance(right, int):
            continue
        if left < 0 or right < 0 or left >= limit or right >= limit:
            continue
        normalized.append(dict(pair))
    return normalized


def _normalize_duplicate_groups(groups, *, limit: int) -> list[dict]:
    normalized: list[dict] = []
    for group in groups or []:
        if not isinstance(group, dict):
            continue
        members = _dedupe_int_list(group.get("members") or [], limit=limit)
        if not members:
            continue
        entry = dict(group)
        entry["members"] = members
        if "canonical_candidates" in entry:
            entry["canonical_candidates"] = _dedupe_int_list(
                entry.get("canonical_candidates") or [],
                limit=limit,
            )
        normalized.append(entry)
    return normalized


def _compact_candidate_trace(candidates) -> list[dict]:
    compact: list[dict] = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        compact.append(
            {
                "idx": candidate.get("idx"),
                "group_id": candidate.get("group_id"),
                "kept": candidate.get("kept"),
                "beaten_by": candidate.get("beaten_by"),
                "decision_reason": candidate.get("decision_reason"),
                "query_distinction_score": candidate.get("query_distinction_score"),
                "duplicate_relations": candidate.get("duplicate_relations") or [],
            }
        )
    return compact


def _effective_suppression_policy(
    trace: dict | None,
    telemetry: dict | None,
    experiments: dict,
) -> str:
    if isinstance(trace, dict):
        policy = str(trace.get("suppression_policy") or "exact_only")
    else:
        policy = "exact_only"
    if policy != "exact_only":
        return policy
    telemetry = telemetry if isinstance(telemetry, dict) else {}
    if int(telemetry.get("experimental_suppressions") or 0) > 0:
        return "experimental_non_exact"
    if any(bool(experiments.get(key)) for key in (
        "boilerplate_variant_suppression",
        "canonical_docs_mirror_suppression",
        "helper_clone_suppression",
    )):
        candidates = trace.get("candidates") if isinstance(trace, dict) else []
        for candidate in candidates or []:
            if not isinstance(candidate, dict) or candidate.get("kept") is not False:
                continue
            relations = {str(rel) for rel in (candidate.get("duplicate_relations") or [])}
            reason = str(candidate.get("decision_reason") or "")
            if reason not in {"exact_duplicate_suppressed", "exact_duplicate"} or (
                relations and relations != {"exact_duplicate"}
            ):
                return "experimental_non_exact"
    return policy


def trace_diverse_results(
    results: list[dict],
    *,
    query: str = "",
    mode: str = "code",
    experiments: dict | None = None,
) -> dict:
    results = _prepare_results_for_duplicate_rerank(results, query=query, mode=mode)
    if len(results) < 2:
        keep = list(range(len(results)))
        mode_name = "docs_retrieval" if mode == "docs" else "code_retrieval"
        return {
            "selection": {
                "mode": mode_name,
                "keep_indices": keep,
                "suppressed_indices": [],
                "exact_suppressed_indices": [],
                "group_order": keep,
                "representative_indices": keep,
                "mmr_lambda": 0.78,
                "aspect_lambda": 0.18,
                "selected_aspects": [],
            },
            "candidates": [],
            "telemetry": {
                "mode": mode_name,
                "query_class": "unknown",
                "exact_suppressions": 0,
                "experimental_suppressions": 0,
                "relation_counts": {},
                "group_sizes": [],
                "representative_selection_reasons": {},
                "topk_redundancy_before": 0.0,
                "topk_redundancy_after": 0.0,
                "kept_group_multi_member_count": 0,
                "canonical_doc_preference_success": None,
                "version_sensitive_query": False,
            },
            "suppression_policy": "exact_only",
            "experiments": experiments or {},
        }
    ts_pack = _load_ts_pack()

    trace = getattr(ts_pack, "trace_diverse_texts", None) if ts_pack is not None else None
    if not callable(trace):
        payload = experiments if isinstance(experiments, dict) else duplicate_experiment_flags_from_env(mode)
        fallback = _ts_pack_runtime_call(
            "trace_diverse_texts",
            texts=[
                result.get("content") if isinstance(result.get("content"), str) else ""
                for result in results
            ],
            relevance_scores=[candidate_relevance_score(result) for result in results],
            query=query,
            mode=mode,
            contexts_json=_context_payload(results),
            experiments=payload,
        )
        if isinstance(fallback, dict):
            return fallback
        selection = rerank_diverse_results(results, query=query, mode=mode)
        return {
            "selection": selection,
            "candidates": [],
            "telemetry": {
                "mode": selection.get("mode", "code_retrieval"),
                "query_class": "unknown",
                "exact_suppressions": len(selection.get("exact_suppressed_indices", [])),
                "experimental_suppressions": 0,
                "relation_counts": {},
                "group_sizes": [],
                "representative_selection_reasons": {},
                "topk_redundancy_before": 0.0,
                "topk_redundancy_after": 0.0,
                "kept_group_multi_member_count": 0,
                "canonical_doc_preference_success": None,
                "version_sensitive_query": False,
            },
            "suppression_policy": "exact_only",
            "experiments": payload,
        }

    texts: list[str] = []
    relevance_scores: list[float] = []
    for result in results:
        content = result.get("content")
        texts.append(content if isinstance(content, str) else "")
        relevance_scores.append(candidate_relevance_score(result))

    payload = experiments if isinstance(experiments, dict) else duplicate_experiment_flags_from_env(mode)
    try:
        traced = trace(
            texts,
            relevance_scores,
            query or None,
            mode,
            _context_payload(results),
            json.dumps(payload),
        )
    except Exception:
        selection = rerank_diverse_results(results, query=query, mode=mode)
        return {
            "selection": selection,
            "candidates": [],
            "telemetry": {
                "mode": selection.get("mode", "code_retrieval"),
                "query_class": "unknown",
                "exact_suppressions": len(selection.get("exact_suppressed_indices", [])),
                "experimental_suppressions": 0,
                "relation_counts": {},
                "group_sizes": [],
                "representative_selection_reasons": {},
                "topk_redundancy_before": 0.0,
                "topk_redundancy_after": 0.0,
                "kept_group_multi_member_count": 0,
                "canonical_doc_preference_success": None,
                "version_sensitive_query": False,
            },
            "suppression_policy": "exact_only",
            "experiments": payload,
        }
    if isinstance(traced, dict):
        return traced
    return trace_diverse_results(results[:1], query=query, mode=mode, experiments=payload)


def analyze_near_duplicate_results(results: list[dict], *, query: str = "", mode: str = "code") -> dict:
    results = _prepare_results_for_duplicate_rerank(results, query=query, mode=mode)
    if len(results) < 2:
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
            "pairs": [],
            "groups": [],
        }
    ts_pack = _load_ts_pack()

    analyze = getattr(ts_pack, "analyze_duplicate_texts", None) if ts_pack is not None else None
    if not callable(analyze):
        fallback = _ts_pack_runtime_call(
            "analyze_duplicate_texts",
            texts=[
                result.get("content") if isinstance(result.get("content"), str) else ""
                for result in results
            ],
            query=query,
            mode=mode,
            contexts_json=_context_payload(results),
        )
        if isinstance(fallback, dict):
            return fallback
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
            "pairs": [],
            "groups": [],
        }

    texts: list[str] = []
    for result in results:
        content = result.get("content")
        texts.append(content if isinstance(content, str) else "")

    try:
        analysis = analyze(texts, query or None, mode, _context_payload(results))
    except Exception:
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
            "pairs": [],
            "groups": [],
        }

    return analysis if isinstance(analysis, dict) else {
        "mode": "code_retrieval",
        "keep_indices": list(range(len(results))),
        "suppressed_indices": [],
        "pairs": [],
    }


def rerank_diverse_results(results: list[dict], *, query: str = "", mode: str = "code") -> dict:
    results = _prepare_results_for_duplicate_rerank(results, query=query, mode=mode)
    if len(results) < 2:
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
            "exact_suppressed_indices": [],
            "group_order": list(range(len(results))),
            "representative_indices": list(range(len(results))),
        }
    ts_pack = _load_ts_pack()
    if ts_pack is None:
        fallback = _ts_pack_runtime_call(
            "rerank_diverse_texts",
            texts=[
                result.get("content") if isinstance(result.get("content"), str) else ""
                for result in results
            ],
            relevance_scores=[candidate_relevance_score(result) for result in results],
            query=query,
            mode=mode,
            contexts_json=_context_payload(results),
        )
        if isinstance(fallback, dict):
            return fallback
        return {
            "mode": "code_retrieval",
            "keep_indices": list(range(len(results))),
            "suppressed_indices": [],
        }

    rerank = getattr(ts_pack, "rerank_diverse_texts", None)
    if not callable(rerank):
        analysis = analyze_near_duplicate_results(results, query=query, mode=mode)
        return {
            "mode": analysis.get("mode", "code_retrieval"),
            "keep_indices": analysis.get("keep_indices", list(range(len(results)))),
            "suppressed_indices": analysis.get("suppressed_indices", []),
            "exact_suppressed_indices": analysis.get("suppressed_indices", []),
            "group_order": [],
            "representative_indices": analysis.get("keep_indices", list(range(len(results)))),
        }

    texts: list[str] = []
    relevance_scores: list[float] = []
    for result in results:
        content = result.get("content")
        texts.append(content if isinstance(content, str) else "")
        relevance_scores.append(candidate_relevance_score(result))

    try:
        selection = rerank(texts, relevance_scores, query or None, mode, _context_payload(results))
    except Exception:
        analysis = analyze_near_duplicate_results(results, query=query, mode=mode)
        return {
            "mode": analysis.get("mode", "code_retrieval"),
            "keep_indices": analysis.get("keep_indices", list(range(len(results)))),
            "suppressed_indices": analysis.get("suppressed_indices", []),
            "exact_suppressed_indices": analysis.get("suppressed_indices", []),
            "group_order": [],
            "representative_indices": analysis.get("keep_indices", list(range(len(results)))),
        }

    return selection if isinstance(selection, dict) else {
        "mode": "code_retrieval",
        "keep_indices": list(range(len(results))),
        "suppressed_indices": [],
        "exact_suppressed_indices": [],
        "group_order": list(range(len(results))),
        "representative_indices": list(range(len(results))),
    }


def analyze_duplicate_results_contract(
    results: list[dict],
    *,
    query: str = "",
    mode: str = "code",
) -> dict:
    prepared = _prepare_results_for_duplicate_rerank(results, query=query, mode=mode)
    analysis = analyze_near_duplicate_results(prepared, query=query, mode=mode)
    keep_indices = _dedupe_int_list(
        analysis.get("keep_indices") or list(range(len(prepared))),
        limit=len(prepared),
    )
    suppressed_indices = _dedupe_int_list(
        analysis.get("suppressed_indices") or [],
        limit=len(prepared),
    )
    return {
        "keep_indices": keep_indices,
        "suppressed_indices": suppressed_indices,
        "pairs": _normalize_duplicate_pairs(analysis.get("pairs"), limit=len(prepared)),
        "groups": _normalize_duplicate_groups(analysis.get("groups"), limit=len(prepared)),
        "mode": analysis.get("mode", "docs_retrieval" if mode == "docs" else "code_retrieval"),
    }


def rerank_retrieval_results_contract(
    results: list[dict],
    *,
    query: str = "",
    mode: str = "code",
    experiments: dict | None = None,
    include_debug: bool = False,
) -> dict:
    prepared = _prepare_results_for_duplicate_rerank(results, query=query, mode=mode)
    payload = merge_duplicate_experiments(mode, experiments)
    trace = trace_diverse_results(prepared, query=query, mode=mode, experiments=payload)
    analysis = analyze_near_duplicate_results(prepared, query=query, mode=mode)

    selection = trace.get("selection") if isinstance(trace, dict) else {}
    telemetry = trace.get("telemetry") if isinstance(trace, dict) else {}
    keep_indices = _dedupe_int_list(
        selection.get("keep_indices") or analysis.get("keep_indices") or list(range(len(prepared))),
        limit=len(prepared),
    )
    suppressed_indices = _dedupe_int_list(
        selection.get("suppressed_indices") or analysis.get("suppressed_indices") or [],
        limit=len(prepared),
    )
    exact_suppressed = _dedupe_int_list(
        selection.get("exact_suppressed_indices") or [],
        limit=len(prepared),
    )
    representative_indices = _dedupe_int_list(
        selection.get("representative_indices") or keep_indices,
        limit=len(prepared),
    )
    group_order = _dedupe_int_list(
        selection.get("group_order") or representative_indices,
        limit=len(prepared),
    )

    contract = {
        "results": [dict(prepared[idx]) for idx in keep_indices],
        "keep_indices": keep_indices,
        "suppressed_indices": suppressed_indices,
        "groups": _normalize_duplicate_groups(analysis.get("groups"), limit=len(prepared)),
        "pairs": _normalize_duplicate_pairs(analysis.get("pairs"), limit=len(prepared)),
        "selection": {
            "mode": selection.get("mode", "docs_retrieval" if mode == "docs" else "code_retrieval"),
            "keep_indices": keep_indices,
            "suppressed_indices": suppressed_indices,
            "exact_suppressed_indices": exact_suppressed,
            "group_order": group_order,
            "representative_indices": representative_indices,
            "mmr_lambda": selection.get("mmr_lambda"),
            "aspect_lambda": selection.get("aspect_lambda"),
            "selected_aspects": selection.get("selected_aspects") or [],
        },
        "telemetry": telemetry if isinstance(telemetry, dict) else {},
        "suppression_policy": _effective_suppression_policy(
            trace if isinstance(trace, dict) else None,
            telemetry if isinstance(telemetry, dict) else None,
            payload,
        ),
        "experiments": trace.get("experiments", payload) if isinstance(trace, dict) else payload,
    }
    if include_debug:
        candidates = trace.get("candidates") if isinstance(trace, dict) else []
        contract["trace"] = _compact_candidate_trace(candidates)
        contract["candidates"] = candidates if isinstance(candidates, list) else []
    return contract


def summarize_trace_for_debug(trace: dict) -> list[str]:
    selection = trace.get("selection") if isinstance(trace, dict) else {}
    telemetry = trace.get("telemetry") if isinstance(trace, dict) else {}
    lines = [
        "duplicate trace:",
        f"- suppression_policy={trace.get('suppression_policy', 'exact_only')}",
        f"- keep={selection.get('keep_indices', [])}",
        f"- exact_suppressed={selection.get('exact_suppressed_indices', [])}",
        f"- experimental_suppressed={telemetry.get('experimental_suppressions', 0)}",
        f"- query_class={telemetry.get('query_class', 'unknown')}",
        f"- topk_redundancy_before={telemetry.get('topk_redundancy_before', 0.0):.3f}",
        f"- topk_redundancy_after={telemetry.get('topk_redundancy_after', 0.0):.3f}",
        f"- multi_rep_groups={telemetry.get('multi_representative_group_count', 0)}",
        f"- query_distinct_multi_rep={telemetry.get('query_distinct_multi_rep_count', 0)}",
        f"- alerts={','.join(telemetry.get('regression_alerts', []) or []) or '(none)'}",
    ]
    candidates = trace.get("candidates") if isinstance(trace, dict) else []
    if isinstance(candidates, list):
        for candidate in candidates[:6]:
            if not isinstance(candidate, dict):
                continue
            lines.append(
                "- idx={idx} group={group} kept={kept} reason={reason} beat_by={beat} qdist={qdist:.3f} rels={rels}".format(
                    idx=candidate.get("idx"),
                    group=candidate.get("group_id"),
                    kept=candidate.get("kept"),
                    reason=candidate.get("decision_reason"),
                    beat=candidate.get("beaten_by"),
                    qdist=float(candidate.get("query_distinction_score", 0.0)),
                    rels=",".join(candidate.get("duplicate_relations") or []),
                )
            )
    return lines


def format_meta(meta: dict) -> list[str]:
    if not isinstance(meta, dict):
        return []
    parts: list[str] = []
    language = meta.get("language")
    if language:
        parts.append(f"lang={language}")
    imports = meta.get("file_imports")
    if isinstance(imports, list) and imports:
        parts.append(f"imports={len(imports)}")
    symbols = meta.get("file_symbols")
    if isinstance(symbols, list) and symbols:
        parts.append(f"symbols={len(symbols)}")
    node_types = meta.get("node_types")
    if isinstance(node_types, list) and node_types:
        parts.append(f"node_types={len(node_types)}")
    diagnostics = meta.get("file_diagnostics") or {}
    if isinstance(diagnostics, dict):
        diag_count = diagnostics.get("count")
        if isinstance(diag_count, int) and diag_count > 0:
            parts.append(f"diagnostics={diag_count}")
    metrics = meta.get("file_metrics") or {}
    if isinstance(metrics, dict):
        total_lines = metrics.get("total_lines")
        if isinstance(total_lines, int):
            parts.append(f"lines={total_lines}")
    cargo_crate = meta.get("cargo_crate")
    if cargo_crate:
        parts.append(f"crate={cargo_crate}")
    ctx = meta.get("context_path")
    ctx_line = ""
    if isinstance(ctx, list) and ctx:
        ctx_line = "context=" + " > ".join(str(c) for c in ctx[:6])
    output = []
    if parts:
        output.append("meta: " + ", ".join(parts))
    if ctx_line:
        output.append(ctx_line)
    return output


def cargo_manifest_dir(manifest_path: str | None) -> str:
    if not manifest_path:
        return ""
    return manifest_path[:-len("Cargo.toml")] if manifest_path.endswith("Cargo.toml") else manifest_path


def match_cargo_crate(file_path: str | None, crate_rows) -> tuple[str | None, str | None]:
    if not file_path:
        return None, None
    for row in crate_rows:
        manifest_path = row.get("manifest_path")
        crate_root = cargo_manifest_dir(manifest_path)
        if crate_root and file_path.startswith(crate_root):
            return row.get("crate"), row.get("crate_name")
    return None, None


def attach_cargo_crate_meta(results: list[dict], crate_rows) -> list[dict]:
    if not crate_rows:
        return results
    for result in results:
        meta = coerce_meta(result)
        crate, crate_name = match_cargo_crate(result.get("file_path"), crate_rows)
        if crate:
            meta["cargo_crate"] = crate
        if crate_name:
            meta["cargo_crate_name"] = crate_name
    return results


def filter_by_cargo_crate(results: list[dict], crate_contains: str) -> list[dict]:
    needle = (crate_contains or "").strip().lower()
    if not needle:
        return results
    filtered: list[dict] = []
    for result in results:
        meta = coerce_meta(result)
        crate = meta.get("cargo_crate")
        crate_name = meta.get("cargo_crate_name")
        if (crate and needle in str(crate).lower()) or (
            crate_name and needle in str(crate_name).lower()
        ):
            filtered.append(result)
    return filtered


def meta_score(meta: dict) -> int:
    if not isinstance(meta, dict):
        return 0
    score = 0
    for key in (
        "file_imports",
        "file_symbols",
        "node_types",
        "file_metrics",
        "file_diagnostics",
        "context_path",
    ):
        value = meta.get(key)
        if isinstance(value, list) and value:
            score += 1
        elif isinstance(value, dict) and value:
            score += 1
    return score


def passes_filters(
    meta: dict,
    *,
    languages,
    min_imports: int,
    min_symbols: int,
    require_diagnostics: bool,
    require_context: bool,
) -> bool:
    if not isinstance(meta, dict):
        return False
    if languages:
        language = meta.get("language")
        if not language or language not in languages:
            return False
    if min_imports > 0:
        imports = meta.get("file_imports")
        if not isinstance(imports, list) or len(imports) < min_imports:
            return False
    if min_symbols > 0:
        symbols = meta.get("file_symbols")
        if not isinstance(symbols, list) or len(symbols) < min_symbols:
            return False
    if require_diagnostics:
        diagnostics = meta.get("file_diagnostics") or {}
        if not isinstance(diagnostics, dict) or diagnostics.get("count", 0) <= 0:
            return False
    if require_context:
        ctx = meta.get("context_path")
        if not isinstance(ctx, list) or not ctx:
            return False
    return True


def path_allowed(file_path: str, *, include_paths, exclude_paths) -> bool:
    if not file_path:
        return True
    if include_paths and not any(fnmatch.fnmatch(file_path, pat) for pat in include_paths):
        return False
    if exclude_paths:
        file_lower = file_path.lower()
        if any(
            fnmatch.fnmatch(file_path, pat) or fnmatch.fnmatch(file_lower, pat.lower())
            for pat in exclude_paths
        ):
            return False
    return True


def coerce_meta(result: dict) -> dict:
    meta = result.get("_meta")
    if isinstance(meta, dict):
        return meta
    raw = result.get("metadata")
    if isinstance(raw, str):
        try:
            meta = json.loads(raw)
        except Exception:
            meta = {}
    elif isinstance(raw, dict):
        meta = raw
    else:
        meta = {}
    result["_meta"] = meta
    return meta


def dedupe_files(results: list[dict]) -> list[dict]:
    seen_files: set[str] = set()
    deduped: list[dict] = []
    for result in results:
        file_path = result.get("file_path")
        if not file_path or file_path in seen_files:
            continue
        seen_files.add(file_path)
        deduped.append(result)
    return deduped


def cap_per_file(results: list[dict], max_per_file: int) -> list[dict]:
    if max_per_file <= 0:
        return results
    per_file_counts: dict[str, int] = {}
    capped: list[dict] = []
    for result in results:
        file_path = result.get("file_path") or ""
        if not file_path:
            continue
        count = per_file_counts.get(file_path, 0)
        if count >= max_per_file:
            continue
        per_file_counts[file_path] = count + 1
        capped.append(result)
    return capped


def cap_per_dir(results: list[dict], max_per_dir: int) -> list[dict]:
    if max_per_dir <= 0:
        return results
    dir_counts: dict[str, int] = {}
    diversified: list[dict] = []
    for result in results:
        file_path = result.get("file_path") or ""
        norm = file_path.replace("\\", "/")
        top = norm.split("/")[0] if "/" in norm else os.path.dirname(norm) or "."
        if dir_counts.get(top, 0) >= max_per_dir:
            continue
        dir_counts[top] = dir_counts.get(top, 0) + 1
        diversified.append(result)
    return diversified


def render_results(
    results: list[dict],
    *,
    query: str,
    k: int,
    multi: bool,
    pid_to_name: dict[str, str],
    include_metadata: bool,
) -> list[str]:
    top = results[:k]
    lines: list[str] = []
    if multi:
        lines.append(f"Cross-project search: '{query}'  ({len(pid_to_name)} projects)\n")
    for index, result in enumerate(top, 1):
        project = pid_to_name.get(result["project_id"], result["project_id"])
        if multi:
            lines.append(f"[{index}] [{project}] {result['file_path']}  (score: {result['rrf']:.4f})")
        else:
            lines.append(f"--- {result['file_path']} (Score: {result['rrf']:.4f}) ---")
        if include_metadata:
            lines.extend(format_meta(coerce_meta(result)))
        lines.append(result["content"].strip())
        lines.append("")
    return lines
