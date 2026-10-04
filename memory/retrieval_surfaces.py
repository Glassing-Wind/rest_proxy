"""Surface and path classification policy for retrieval ranking."""

from __future__ import annotations

__all__ = [
    "implementation_rank_tuple",
    "is_doc_like_path",
    "is_generated_implementation_surface_path",
    "is_low_signal_binding_surface_path",
    "is_low_signal_parser_data_path",
    "is_low_signal_support_path",
    "is_usage_heavy_path",
]


def is_doc_like_path(file_path: str | None, file_roles: set[str] | None = None) -> bool:
    roles = {
        str(role).strip().lower()
        for role in (file_roles or set())
        if str(role).strip()
    }
    if roles:
        return "docs_surface" in roles
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


def is_low_signal_parser_data_path(
    file_path: str | None,
    file_roles: set[str] | None = None,
) -> bool:
    if not file_path:
        return False
    roles = file_roles or set()
    if "implementation_surface" in roles:
        return False
    if roles:
        return bool({"binding_surface", "config_surface"} & roles)
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


def is_low_signal_binding_surface_path(
    file_path: str | None,
    file_roles: set[str] | None = None,
) -> bool:
    if not file_path:
        return False
    roles = file_roles or set()
    if roles:
        return "binding_surface" in roles
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    return (
        basename in {"models.cs", "types.go", "processresult.java", "processconfig.php"}
        or basename.endswith(".proto")
        or basename.endswith("registry.java")
        or "/packages/csharp/" in norm
    )


def is_generated_implementation_surface_path(
    file_path: str | None,
    file_roles: set[str] | None = None,
) -> bool:
    if not file_path:
        return False
    roles = file_roles or set()
    if roles:
        return "generated_surface" in roles
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    return (
        basename.endswith(".pb.swift")
        or basename.endswith(".grpc.swift")
        or basename.endswith("_generated.swift")
        or basename.endswith("_generated.h")
        or basename.endswith("_generated.c")
        or "/pregeneratedspm/" in norm
        or "/generatedc/" in norm
        or "/generated/" in norm
    )


def is_usage_heavy_path(
    file_path: str | None,
    file_roles: set[str] | None = None,
) -> bool:
    if not file_path:
        return False
    roles = file_roles or set()
    if roles:
        return bool({"test_surface", "example_surface"} & roles)
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    production_language_roots = (
        "/src/main/java/",
        "src/main/java/",
        "/src/main/kotlin/",
        "src/main/kotlin/",
        "/src/main/scala/",
        "src/main/scala/",
    )
    if norm.startswith(("samples/", "examples/")):
        return True
    if any(segment in norm for segment in ("/samples/", "/examples/")) and not any(
        segment in norm for segment in production_language_roots
    ):
        return True
    if any(
        segment in norm
        for segment in (
            *production_language_roots,
            "/src/test/java/",
            "src/test/java/",
            "/src/test/kotlin/",
            "src/test/kotlin/",
            "/src/test/scala/",
            "src/test/scala/",
        )
    ):
        return (
            basename == "main.rs"
            or "/tests/" in norm
            or "/test/" in norm
            or "/e2e/" in norm
            or "/spec/" in norm
            or norm.endswith("_test.go")
            or norm.endswith("_spec.rb")
        )
    return (
        basename == "main.rs"
        or "/tests/" in norm
        or "/test/" in norm
        or "/e2e/" in norm
        or norm.startswith("samples/")
        or "/examples/" in norm
        or "/samples/" in norm
        or "/spec/" in norm
        or norm.endswith("_test.go")
        or norm.endswith("_spec.rb")
    )


def is_low_signal_support_path(
    file_path: str | None,
    file_roles: set[str] | None = None,
) -> bool:
    if not file_path:
        return False
    roles = file_roles or set()
    if "implementation_surface" in roles:
        return False
    if roles:
        return "support_surface" in roles
    norm = (file_path or "").replace("\\", "/").lower()
    basename = norm.rsplit("/", 1)[-1]
    return (
        "/templates/" in norm
        or "/resources/static/" in norm
        or "/static/" in norm
        or basename.endswith(".html")
        or basename.endswith(".css")
        or basename.endswith(".scss")
        or basename.endswith(".less")
        or basename.endswith(".sass")
        or norm.startswith("scripts/")
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
) -> tuple[int | float, ...]:
    """Rank implementation-intent results with code first, then docs/parser data last."""
    low_signal_parser_data = 1 if result.get("low_signal_parser_data") else 0
    low_signal_binding_surface = 1 if result.get("low_signal_binding_surface") else 0
    generated_surface = 1 if result.get("generated_implementation_surface") else 0
    low_signal_support = 1 if result.get("low_signal_support_path") else 0
    doc_like = 1 if result.get("doc_like") else 0
    usage_heavy = 1 if result.get("implementation_usage_heavy_penalty") else 0
    callable_priority = int(result.get("implementation_callable_priority", 0) or 0)
    member_usage_priority = int(result.get("implementation_member_usage_priority", 0) or 0)
    provider_wiring_priority = int(result.get("implementation_provider_wiring_priority", 0) or 0)
    dispatcher_priority = int(result.get("implementation_dispatcher_priority", 0) or 0)
    dispatcher_contract_priority = int(result.get("implementation_dispatcher_contract_priority", 0) or 0)
    command_definition_priority = int(result.get("implementation_command_definition_priority", 0) or 0)
    routing_priority = int(result.get("implementation_routing_priority", 0) or 0)
    handler_priority = int(result.get("implementation_request_handler_priority", 0) or 0)
    path_hint_priority = int(result.get("implementation_path_hint_hit", 0) or 0)
    basename_token_priority = int(result.get("implementation_basename_token_hit", 0) or 0)
    view_body_priority = int(result.get("implementation_view_body_priority", 0) or 0)
    runtime_main_priority = int(result.get("implementation_runtime_main_entrypoint_hit", 0) or 0)
    role_priority = int(result.get("implementation_role_priority", 0) or 0)
    node_type_priority = int(result.get("implementation_node_type_priority", 0) or 0)
    reexport_surface = int(result.get("implementation_reexport_surface_hit", 0) or 0)
    facade_surface = int(result.get("implementation_facade_surface_hit", 0) or 0)
    declared_symbol_hit = int(result.get("implementation_declared_symbol_hit", 0) or 0)
    exact_identifier_hit = int(result.get("implementation_exact_identifier_hit", 0) or 0)
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
        generated_surface,
        low_signal_support,
        doc_like,
        usage_heavy,
        -dispatcher_contract_priority,
        -role_priority,
        -provider_wiring_priority,
        -callable_priority,
        -member_usage_priority,
        -dispatcher_priority,
        -command_definition_priority,
        -handler_priority,
        -routing_priority,
        -path_hint_priority,
        -basename_token_priority,
        -view_body_priority,
        -runtime_main_priority,
        -node_type_priority,
        facade_surface,
        reexport_surface,
        -exact_identifier_hit,
        -declared_symbol_hit,
        -signature_hit,
        -definition_hit,
        -export_hit,
        -api_entrypoint_hit,
        -symbol_hit,
        -rank_score,
        -meta_score,
    )

