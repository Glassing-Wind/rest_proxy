"""Pure helpers for duplication detection."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re


TOKEN_PATTERN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*|\d+|==|!=|<=|>=|->|[{}()\[\];,.:+\-*/%<>=]"
)
IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

DEFAULT_LOW_SIGNAL_DUPLICATION_PATTERNS = [
    "tests/**",
    "**/tests/**",
    "test_*.py",
    "**/test_*.py",
    "**/*.spec.*",
    "**/*.test.*",
    "docs/node_types/**",
    "**/docs/node_types/**",
    "node_modules/**",
    "**/node_modules/**",
    "dist/**",
    "**/dist/**",
    "build/**",
    "**/build/**",
    ".next/**",
    "**/.next/**",
    "coverage/**",
    "**/coverage/**",
    "site-packages/**",
    "**/site-packages/**",
    "__pycache__/**",
    "**/__pycache__/**",
    "**/*.min.js",
    "**/*.bundle.js",
    "**/*.generated.*",
    "generated/**",
    "**/generated/**",
]

DEFAULT_DUPLICATE_SYMBOL_NAME_BLOCKLIST = {
    "__init__",
    "__aenter__",
    "__aexit__",
    "setUp",
    "session",
    "decorator",
    "tool",
    "register",
    "_tx",
}

PREVIEW_IDENTIFIER_BLOCKLIST = {
    "def",
    "async",
    "fn",
    "pub",
    "class",
    "struct",
    "trait",
    "enum",
    "protocol",
    "extension",
    "return",
    "import",
    "from",
    "as",
    "let",
    "var",
    "const",
    "true",
    "false",
    "none",
    "some",
    "self",
    "super",
    "crate",
    "mod",
    "where",
    "await",
    "list",
    "dict",
    "str",
    "int",
    "bool",
    "any",
    "option",
    "result",
    "session",
    "project_id",
    "workspace_id",
    "limit",
    "driver",
    "neo4j_db",
    "query",
    "record",
    "records",
    "row",
    "rows",
    "file",
    "filepath",
    "path",
    "text",
    "content",
    "data",
    "name",
    "value",
    "main",
    "optional",
    "py",
    "tool",
    "tools",
    "docs",
    "documentation",
    "search",
    "graph",
    "helpers",
    "helper",
    "overview",
    "summary",
    "flow",
    "pipeline",
    "crawl",
    "research",
    "raw",
    "lookup",
    "indexing",
}


def glob_to_like(pattern: str) -> str:
    pattern = pattern.replace("%", r"\%")
    pattern = pattern.replace("_", r"\_")
    pattern = pattern.replace("*", "%")
    pattern = pattern.replace("?", "_")
    return pattern


def path_allowed(
    file_path: str,
    *,
    include_patterns: list[str],
    exclude_patterns: list[str],
) -> bool:
    if not file_path:
        return False
    if include_patterns and not any(
        fnmatch.fnmatch(file_path, pattern) for pattern in include_patterns
    ):
        return False
    if exclude_patterns and any(
        fnmatch.fnmatch(file_path, pattern) for pattern in exclude_patterns
    ):
        return False
    return True


def default_duplication_exclude_patterns(include_patterns: list[str]) -> list[str]:
    if include_patterns:
        return []
    return list(DEFAULT_LOW_SIGNAL_DUPLICATION_PATTERNS)


def keep_default_winnow_pair(
    pair: tuple[dict, dict, float, float],
    *,
    include_patterns: list[str],
) -> bool:
    row_a, row_b, score, struct_score = pair
    if score <= 0.50 and struct_score <= 0.0:
        return False
    preview_a = preview_line(row_a.get("content") or "")
    preview_b = preview_line(row_b.get("content") or "")
    if _is_import_only_preview(preview_a) and _is_import_only_preview(preview_b):
        return False
    identifiers_a = preview_identifiers(row_a.get("content") or "")
    identifiers_b = preview_identifiers(row_b.get("content") or "")
    path_overlap = path_token_overlap(row_a.get("file_path") or "", row_b.get("file_path") or "")
    if (
        score >= 0.90
        and struct_score >= 0.90
        and not (identifiers_a & identifiers_b)
        and not path_overlap
    ):
        return False
    return True


def filter_duplicate_symbol_name_records(
    records: list[dict],
    *,
    include_patterns: list[str],
) -> list[dict]:
    if include_patterns:
        return records
    filtered: list[dict] = []
    for record in records:
        name = record.get("name")
        if not isinstance(name, str):
            continue
        if name in DEFAULT_DUPLICATE_SYMBOL_NAME_BLOCKLIST:
            continue
        if name.startswith("__") and name.endswith("__"):
            continue
        if name.startswith("_"):
            continue
        filtered.append(record)
    return filtered


def tokenize(text: str) -> list[str]:
    if not text:
        return []
    return TOKEN_PATTERN.findall(text)


def normalize_tokens(tokens: list[str]) -> list[str]:
    normalized: list[str] = []
    for token in tokens:
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", token):
            normalized.append("<id>")
        elif re.match(r"^\d+$", token):
            normalized.append("<num>")
        else:
            normalized.append(token)
    return normalized


def same_file_allowed(
    file_path: str,
    content: str,
    counts: dict[str, int],
    *,
    max_same_file_pairs_per_file: int,
    same_file_min_tokens: int,
) -> bool:
    if counts.get(file_path, 0) >= max_same_file_pairs_per_file:
        return False
    if same_file_min_tokens > 0 and len(tokenize(content)) < same_file_min_tokens:
        return False
    return True


def pick_pair(group: list[dict], *, cross_file: bool) -> tuple[dict, dict] | None:
    if len(group) < 2:
        return None
    if cross_file:
        for index in range(len(group)):
            for offset in range(index + 1, len(group)):
                if group[index]["file_path"] != group[offset]["file_path"]:
                    return group[index], group[offset]
    return group[0], group[1]


def node_type_jaccard(meta_a: dict, meta_b: dict) -> float:
    types_a = meta_a.get("node_types") or []
    types_b = meta_b.get("node_types") or []
    if not types_a or not types_b:
        return 0.0
    set_a = set(types_a)
    set_b = set(types_b)
    denom = len(set_a | set_b)
    if denom == 0:
        return 0.0
    return len(set_a & set_b) / denom


def winnow_fingerprints(tokens: list[str], k: int, window: int) -> set[int]:
    if len(tokens) < k:
        return set()
    hashes: list[int] = []
    for index in range(len(tokens) - k + 1):
        gram = " ".join(tokens[index : index + k])
        hashes.append(int(hashlib.md5(gram.encode()).hexdigest()[:16], 16))
    if not hashes:
        return set()
    if len(hashes) <= window:
        return {min(hashes)}
    fingerprints: set[int] = set()
    min_hash = None
    min_pos = -1
    for index in range(len(hashes) - window + 1):
        window_hashes = hashes[index : index + window]
        current_min = min(window_hashes)
        if min_hash != current_min or min_pos < index:
            min_hash = current_min
            min_pos = index + window_hashes.index(current_min)
            fingerprints.add(min_hash)
    return fingerprints


def kgrams(tokens: list[str], k: int) -> set[tuple[str, ...]]:
    if len(tokens) < k:
        return set()
    return {tuple(tokens[index : index + k]) for index in range(len(tokens) - k + 1)}


def is_code_file(file_path: str, metadata: dict | None) -> bool:
    if not file_path:
        return False
    if metadata and isinstance(metadata, dict):
        language = metadata.get("language")
        if isinstance(language, str) and language.lower() in {
            "json",
            "markdown",
            "md",
            "yaml",
            "toml",
            "ini",
            "text",
        }:
            return False
    _, ext = os.path.splitext(file_path.lower())
    return ext not in {
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".txt",
        ".csv",
        ".lock",
        ".env",
    }


def preview_line(text: str, limit: int = 200) -> str:
    if not text:
        return ""
    for line in (text or "").strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("// File: ") or stripped.startswith("# File: "):
            continue
        return stripped[:limit]
    lines = (text or "").strip().splitlines()
    return lines[0][:limit] if lines else ""


def is_low_signal_preview(text: str) -> bool:
    preview = preview_line(text).strip()
    if not preview:
        return True
    if preview.startswith("#!/"):
        return True
    if preview.startswith('"""') or preview.startswith("'''"):
        return True
    return False


def preview_identifiers(text: str) -> set[str]:
    preview = preview_line(text)
    if not preview:
        return set()
    return {
        token.lower()
        for token in IDENTIFIER_PATTERN.findall(preview)
        if len(token) > 1 and token.lower() not in PREVIEW_IDENTIFIER_BLOCKLIST
    }


def path_token_overlap(path_a: str, path_b: str) -> set[str]:
    path_stopwords = {
        "test",
        "spec",
        "index",
        "main",
        "lib",
        "flow",
        "summary",
        "helper",
        "helpers",
        "core",
        "graph",
        "search",
        "code",
        "intel",
        "apple",
        "report",
        "tools",
        "brain",
        "memory",
        "store",
        "util",
        "utility",
    }

    def _tokens(path: str) -> set[str]:
        stem = os.path.splitext(os.path.basename(path or ""))[0]
        return {
            token.lower()
            for token in re.split(r"[_\-.]+", stem)
            if len(token) > 2 and token.lower() not in path_stopwords
        }

    return _tokens(path_a) & _tokens(path_b)


def _is_import_only_preview(preview: str) -> bool:
    stripped = (preview or "").strip()
    return stripped.startswith("import ") or stripped.startswith("from ")


def has_actionable_duplicate_signal(row_a: dict, row_b: dict) -> bool:
    if preview_identifiers(row_a.get("content") or "") & preview_identifiers(row_b.get("content") or ""):
        return True
    if path_token_overlap(row_a.get("file_path") or "", row_b.get("file_path") or ""):
        return True
    return False


def duplicate_candidate_details(
    row_a: dict,
    row_b: dict,
    *,
    score: float,
    struct_score: float,
) -> dict:
    identifiers = preview_identifiers(row_a.get("content") or "") & preview_identifiers(
        row_b.get("content") or ""
    )
    path_overlap = path_token_overlap(row_a.get("file_path") or "", row_b.get("file_path") or "")
    same_dir = os.path.dirname(row_a.get("file_path") or "") == os.path.dirname(
        row_b.get("file_path") or ""
    )
    same_ext = os.path.splitext(row_a.get("file_path") or "")[1].lower() == os.path.splitext(
        row_b.get("file_path") or ""
    )[1].lower()
    preview_a = preview_line(row_a.get("content") or "")
    preview_b = preview_line(row_b.get("content") or "")
    low_signal_a = is_low_signal_preview(row_a.get("content") or "")
    low_signal_b = is_low_signal_preview(row_b.get("content") or "")
    preview_equal = bool(
        preview_a and preview_b and preview_a == preview_b and not (low_signal_a and low_signal_b)
    )

    candidate_score = 0.0
    candidate_score += min(score, 1.0) * 0.45
    candidate_score += min(struct_score, 1.0) * 0.10
    if preview_equal:
        candidate_score += 0.20
    if identifiers and not (low_signal_a and low_signal_b):
        candidate_score += min(0.25, 0.07 * len(identifiers))
    if path_overlap and not (low_signal_a and low_signal_b):
        candidate_score += min(0.12, 0.04 * len(path_overlap))
    if same_dir:
        candidate_score += 0.06
    if same_ext:
        candidate_score += 0.04

    reasons: list[str] = []
    if preview_equal:
        reasons.append("same lead statement")
    if identifiers and not (low_signal_a and low_signal_b):
        shared = ", ".join(sorted(identifiers)[:3])
        reasons.append(f"shared identifiers ({shared})")
    if path_overlap and not (low_signal_a and low_signal_b):
        shared_path = ", ".join(sorted(path_overlap)[:3])
        reasons.append(f"path overlap ({shared_path})")
    if same_dir:
        reasons.append("same directory")
    if same_ext:
        reasons.append("same file type")
    if score >= 0.95:
        reasons.append("very high duplicate score")
    elif score >= 0.85:
        reasons.append("high duplicate score")

    path_overlap_actionable = bool(path_overlap) and score >= 0.95

    return {
        "candidate_score": candidate_score,
        "reasons": reasons,
        "shared_identifiers": identifiers,
        "path_overlap": path_overlap,
        "same_dir": same_dir,
        "same_ext": same_ext,
        "preview_equal": preview_equal,
        "actionable": bool(
            preview_equal
            or (identifiers and not (low_signal_a and low_signal_b))
            or (path_overlap_actionable and not (low_signal_a and low_signal_b))
        ),
    }
