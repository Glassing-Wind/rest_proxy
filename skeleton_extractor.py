"""skeleton_extractor — structural skeleton using ts_pack.

Previously used SourceKitten (subprocess), Python's ast module, and a regex
fallback. Now uses ts_pack.process() for all 156 supported languages with a
single code path. Falls back to the regex heuristic for unknown file types.
"""
import logging

logger = logging.getLogger("lm_proxy.skeleton")

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_skeleton(code: str, file_path: str) -> str:
    """Extract a structural skeleton (classes, functions, etc.) from *code*.

    Uses ts_pack.detect_language() for automatic language detection across
    156 languages.  Returns a human-readable outline of top-level and nested
    declarations.  Returns empty string if the file type is not supported or
    the file is empty.
    """
    try:
        import tree_sitter_language_pack as ts_pack

        lang = ts_pack.detect_language(file_path)
        if not lang:
            return _parse_regex_fallback(code)

        config = ts_pack.ProcessConfig(lang)
        result = ts_pack.process(code, config)
        structure = result.get("structure", [])
        if not structure:
            return ""

        return _format_structure(structure)

    except Exception as e:
        logger.warning(f"ts_pack skeleton extraction failed for {file_path}: {e}")
        return _parse_regex_fallback(code)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _format_structure(items: list, indent: int = 0) -> str:
    """Recursively format structure items as a readable skeleton."""
    lines = []
    prefix = "    " * indent

    for item in items:
        kind     = item.get("kind", "")
        name     = item.get("name", "?")
        sig      = item.get("signature") or ""
        children = item.get("children") or []

        # Build declaration line
        if sig:
            decl = sig
        else:
            # Synthesize a minimal declaration from kind + name
            kind_kw = _KIND_KEYWORD.get(kind, kind.lower())
            decl = f"{kind_kw} {name}"

        if children:
            lines.append(f"{prefix}{decl}:")
            lines.append(_format_structure(children, indent + 1))
        else:
            lines.append(f"{prefix}{decl}: ...")

    return "\n".join(l for l in lines if l)


# Map ts_pack kind strings → more idiomatic keyword
_KIND_KEYWORD: dict[str, str] = {
    "Function": "def",
    "Method":   "def",
    "Class":    "class",
    "Struct":   "struct",
    "Enum":     "enum",
    "Trait":    "trait",
    "Interface":"interface",
    "Module":   "mod",
    "Const":    "const",
    "Variable": "var",
    "Type":     "type",
}


# ---------------------------------------------------------------------------
# Regex fallback (kept for truly unknown file types)
# ---------------------------------------------------------------------------

def _parse_regex_fallback(code: str) -> str:
    """Heuristic fallback for languages ts_pack cannot detect."""
    import re
    pattern = re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
        r"(?:class\s+\w+|function\s+\w+\s*\(.*?\)|const\s+\w+\s*=|let\s+\w+\s*=)",
        re.MULTILINE,
    )
    lines = []
    for match in pattern.finditer(code):
        decl = match.group(0).strip()
        if decl.endswith("="):
            decl += " ..."
        lines.append(decl)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Legacy Swift doc lookup (kept for backward compat, now falls back to ts_pack)
# ---------------------------------------------------------------------------

def get_swift_docs(code: str, symbol_name: str) -> str:
    """Find documentation for a Swift symbol.

    Tries ts_pack structure first; previously used SourceKitten subprocess.
    """
    try:
        import tree_sitter_language_pack as ts_pack
        config = ts_pack.ProcessConfig("swift")
        result = ts_pack.process(code, config)

        def find_doc(items):
            for item in items:
                if item.get("name") == symbol_name:
                    doc = item.get("doc_comment") or ""
                    if doc:
                        return doc
                sub = find_doc(item.get("children") or [])
                if sub:
                    return sub
            return None

        return find_doc(result.get("structure", [])) or ""
    except Exception as e:
        logger.warning(f"Error extracting Swift docs: {e}")
        return ""
