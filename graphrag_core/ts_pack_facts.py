"""Typed file-fact extraction using tree-sitter-language-pack extraction queries."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
NON_HTTP_CLIENTS = {"router", "app", "server"}


def route_path_from_file(file_path: str) -> str | None:
    """Infer a route path from common route file layouts."""
    path = PurePosixPath(file_path)
    parts = path.parts
    if len(parts) < 2:
        return None

    idx = 0
    while idx < len(parts) and parts[idx] in {"packages", "apps", "src"}:
        if parts[idx] in {"packages", "apps"} and idx + 1 < len(parts):
            idx += 2
        else:
            idx += 1
    relevant = parts[idx:]
    if not relevant:
        return None

    if relevant[0] == "app" and path.name.startswith("route."):
        route_parts = relevant[1:-1]
        return "/" if not route_parts else "/" + "/".join(route_parts)

    if len(relevant) > 1 and relevant[0] == "pages" and relevant[1] == "api":
        rel = list(relevant[2:])
        if not rel:
            return "/api"
        stem = PurePosixPath(*rel).stem
        rel = rel[:-1] if stem in {"index", "route"} else rel[:-1] + [stem]
        return "/api" if not rel else "/api/" + "/".join(rel)

    if relevant[0] == "api":
        rel = list(relevant[1:])
        if not rel:
            return "/api"
        stem = PurePosixPath(*rel).stem
        rel = rel[:-1] if stem in {"index", "route"} else rel[:-1] + [stem]
        return "/api" if not rel else "/api/" + "/".join(rel)

    return None


def _normalize_method(value: str | None) -> str | None:
    method = (value or "").strip().upper()
    return method if method in HTTP_METHODS else None


def _capture_texts(match: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for cap in match.get("captures") or []:
        name = cap.get("name")
        text = cap.get("text")
        if not name or text is None:
            continue
        out.setdefault(str(name), []).append(str(text))
    return out


def _pattern_matches(result: dict[str, Any], name: str) -> list[dict[str, Any]]:
    patterns = result.get("results") or result
    entry = patterns.get(name) or {}
    matches = entry.get("matches") or []
    return matches if isinstance(matches, list) else []


def _typescript_patterns() -> dict[str, Any]:
    return {
        "language": "typescript",
        "patterns": {
            "express_routes": {
                "query": (
                    "(call_expression "
                    "  function: (member_expression "
                    "    object: (identifier) @router "
                    "    property: (property_identifier) @method) "
                    "  arguments: (arguments (string (string_fragment) @path))) @route_call"
                ),
                "capture_output": "Text",
                "max_results": 200,
            },
            "http_member_calls": {
                "query": (
                    "[(call_expression "
                    "   function: (member_expression object: (identifier) @client property: (property_identifier) @method) "
                    "   arguments: (arguments (string (string_fragment) @path))) "
                    " (call_expression "
                    "   function: (member_expression object: (call_expression function: (identifier) @client) property: (property_identifier) @method) "
                    "   arguments: (arguments (string (string_fragment) @path)))] @http_call"
                ),
                "capture_output": "Text",
                "max_results": 200,
            },
            "http_fetch_calls": {
                "query": (
                    "(call_expression "
                    "  function: (identifier) @client "
                    "  arguments: (arguments (string (string_fragment) @path))) @http_call "
                    "(#eq? @client \"fetch\")"
                ),
                "capture_output": "Text",
                "max_results": 200,
            },
            "http_method_props": {
                "query": (
                    "(pair "
                    "  key: (property_identifier) @key "
                    "  value: (string (string_fragment) @method)) @method_pair "
                    "(#eq? @key \"method\")"
                ),
                "capture_output": "Text",
                "max_results": 200,
            },
            "route_methods": {
                "query": (
                    "[(function_declaration name: (identifier) @method) "
                    " (lexical_declaration (variable_declarator name: (identifier) @method))]"
                ),
                "capture_output": "Text",
                "max_results": 50,
            },
        },
    }


def _swift_patterns() -> dict[str, Any]:
    return {
        "language": "swift",
        "patterns": {
            "resource_calls": {
                "query": (
                    "[(call_expression "
                    "   called_expression: (simple_identifier) @callee "
                    "   arguments: (call_suffix (value_arguments (value_argument (string_literal (string_literal_content) @name))))) "
                    " (call_expression "
                    "   called_expression: (member_access_expr name: (simple_identifier) @callee) "
                    "   arguments: (call_suffix (value_arguments (value_argument (string_literal (string_literal_content) @name)))))] @resource_call"
                ),
                "capture_output": "Text",
                "max_results": 200,
            }
        },
    }


def _config_for_language(language: str) -> dict[str, Any] | None:
    lang = (language or "").lower()
    if lang in {"typescript", "tsx", "javascript"}:
        return _typescript_patterns() | {"language": lang}
    if lang == "swift":
        return _swift_patterns()
    return None


def extract_file_facts(ts_pack: Any, source: str, language: str, file_path: str) -> dict[str, Any]:
    """Extract compact, typed file facts from ts-pack extraction results."""
    if hasattr(ts_pack, "extract_file_facts"):
        try:
            raw = ts_pack.extract_file_facts(source, language, file_path)
        except Exception:
            raw = None
        if isinstance(raw, dict):
            facts: dict[str, Any] = {}
            for key in ("route_defs", "http_calls", "resource_refs"):
                value = raw.get(key)
                if isinstance(value, list) and value:
                    facts[key] = value
            if facts:
                return facts

    if not hasattr(ts_pack, "extract"):
        return {}
    config = _config_for_language(language)
    if not config:
        return {}

    try:
        raw = ts_pack.extract(source, config)
    except Exception:
        return {}

    facts: dict[str, Any] = {}

    route_defs: list[dict[str, str]] = []
    for match in _pattern_matches(raw, "express_routes"):
        caps = _capture_texts(match)
        method = _normalize_method((caps.get("method") or [None])[0])
        path = (caps.get("path") or [None])[0]
        if method and path and str(path).startswith("/"):
            route_defs.append(
                {"framework": "express", "method": method, "path": str(path)}
            )

    inferred_path = route_path_from_file(file_path)
    if inferred_path:
        for match in _pattern_matches(raw, "route_methods"):
            caps = _capture_texts(match)
            method = _normalize_method((caps.get("method") or [None])[0])
            if method:
                route_defs.append(
                    {"framework": "file_route", "method": method, "path": inferred_path}
                )

    http_calls: list[dict[str, str]] = []
    pending_methods = [
        _normalize_method(((_capture_texts(m).get("method") or [None])[0]))
        for m in _pattern_matches(raw, "http_method_props")
    ]
    for match in _pattern_matches(raw, "http_member_calls"):
        caps = _capture_texts(match)
        client = (caps.get("client") or [None])[0]
        method = _normalize_method((caps.get("method") or [None])[0])
        path = (caps.get("path") or [None])[0]
        if client and path and str(path).startswith("/") and str(client) not in NON_HTTP_CLIENTS:
            http_calls.append(
                {
                    "client": str(client),
                    "method": method or "ANY",
                    "path": str(path),
                }
            )
    for idx, match in enumerate(_pattern_matches(raw, "http_fetch_calls")):
        caps = _capture_texts(match)
        client = (caps.get("client") or [None])[0]
        path = (caps.get("path") or [None])[0]
        method = pending_methods[idx] if idx < len(pending_methods) else None
        if client and path and str(path).startswith("/"):
            http_calls.append(
                {
                    "client": str(client),
                    "method": method or "ANY",
                    "path": str(path),
                }
            )

    resource_refs: list[dict[str, str]] = []
    for match in _pattern_matches(raw, "resource_calls"):
        caps = _capture_texts(match)
        callee = (caps.get("callee") or [None])[0]
        name = (caps.get("name") or [None])[0]
        if not callee or not name:
            continue
        kind = None
        if callee in {"Image", "UIImage", "NSImage"}:
            kind = "image"
        elif callee == "Color":
            kind = "color"
        elif callee in {"UINib", "NSNib"}:
            kind = "nib"
        if kind:
            resource_refs.append({"kind": kind, "name": str(name), "callee": str(callee)})

    if route_defs:
        facts["route_defs"] = sorted(
            {tuple(sorted(item.items())) for item in route_defs}
        )
        facts["route_defs"] = [dict(items) for items in facts["route_defs"]]
    if http_calls:
        facts["http_calls"] = sorted(
            {tuple(sorted(item.items())) for item in http_calls}
        )
        facts["http_calls"] = [dict(items) for items in facts["http_calls"]]
    if resource_refs:
        facts["resource_refs"] = sorted(
            {tuple(sorted(item.items())) for item in resource_refs}
        )
        facts["resource_refs"] = [dict(items) for items in facts["resource_refs"]]

    return facts
