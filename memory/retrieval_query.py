"""Query parsing primitives for retrieval ranking policy."""

from __future__ import annotations

import re

__all__ = [
    "implementation_query_definition_kind",
    "implementation_query_definition_subject_identifiers",
    "implementation_query_member_exprs",
    "implementation_query_path_hints",
    "implementation_query_symbols",
]


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
        "defined",
        "define",
        "definition",
        "enum",
        "struct",
        "class",
        "trait",
        "protocol",
        "interface",
        "type",
    }
    symbols = {
        token
        for token in re.findall(r"[a-z_][a-z0-9_]*", text)
        if len(token) >= 3 and token not in stopwords
    }
    return symbols


DECLARATION_KIND_TERMS = {
    "enum": "enum",
    "struct": "struct",
    "class": "class",
    "trait": "trait",
    "protocol": "protocol",
    "interface": "interface",
    "type": "type",
}


def implementation_query_definition_kind(query: str) -> str:
    tokens = [token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query or "")]
    for token in tokens:
        kind = DECLARATION_KIND_TERMS.get(token)
        if kind:
            return kind
    return ""


def _definition_subject_base(token: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_]+", "", (token or "").strip().lower())
    if len(cleaned) >= 5 and cleaned.endswith("ies"):
        return cleaned[:-3] + "y"
    if len(cleaned) >= 4 and cleaned.endswith("ses"):
        return cleaned[:-2]
    if len(cleaned) >= 4 and cleaned.endswith("s") and not cleaned.endswith("ss"):
        return cleaned[:-1]
    return cleaned


def implementation_query_definition_subject_identifiers(query: str) -> set[str]:
    kind = implementation_query_definition_kind(query)
    if not kind:
        return set()
    tokens = [token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query or "")]
    if not tokens:
        return set()
    stopwords = {
        "where",
        "what",
        "how",
        "is",
        "the",
        "a",
        "an",
        "defined",
        "define",
        "definition",
        "public",
        "api",
        "entrypoint",
        "implementation",
        "implemented",
        "in",
        "of",
        "for",
    }
    try:
        kind_index = tokens.index(kind)
    except ValueError:
        return set()
    for token in reversed(tokens[:kind_index]):
        if token in stopwords or len(token) < 3:
            continue
        base = _definition_subject_base(token)
        if len(base) < 3:
            continue
        variants = {base}
        if not base.endswith("s"):
            variants.add(f"{base}s")
        if "_" in base:
            variants.add("".join(part.capitalize() for part in base.split("_") if part))
        else:
            variants.add(base.capitalize())
        return {variant.lower() for variant in variants if variant}
    return set()


def _implementation_definition_declaration_patterns(query: str) -> list[str]:
    kind = implementation_query_definition_kind(query)
    identifiers = implementation_query_definition_subject_identifiers(query)
    if not kind or not identifiers:
        return []
    patterns_by_kind = {
        "enum": [
            r"\bpub\s+enum\s+{ident}\b",
            r"\benum\s+{ident}\b",
        ],
        "struct": [
            r"\bpub\s+struct\s+{ident}\b",
            r"\bstruct\s+{ident}\b",
        ],
        "class": [
            r"\bclass\s+{ident}\b",
            r"\bfinal\s+class\s+{ident}\b",
            r"\bpublic\s+class\s+{ident}\b",
        ],
        "trait": [
            r"\bpub\s+trait\s+{ident}\b",
            r"\btrait\s+{ident}\b",
        ],
        "protocol": [
            r"\bprotocol\s+{ident}\b",
            r"\bpublic\s+protocol\s+{ident}\b",
        ],
        "interface": [
            r"\binterface\s+{ident}\b",
            r"\bpublic\s+interface\s+{ident}\b",
        ],
        "type": [
            r"\btype\s+{ident}\b",
            r"\btypealias\s+{ident}\b",
            r"\bpub\s+type\s+{ident}\b",
        ],
    }
    templates = patterns_by_kind.get(kind, [])
    patterns: list[str] = []
    for identifier in identifiers:
        escaped = re.escape(identifier)
        for template in templates:
            patterns.append(template.format(ident=escaped))
    return patterns


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

