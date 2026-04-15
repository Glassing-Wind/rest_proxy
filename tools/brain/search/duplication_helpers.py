"""Pure helpers for duplication detection."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re


TOKEN_PATTERN = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*|\d+|==|!=|<=|>=|->|[{}()\[\];,.:+\-*/%<>=]"
)

DEFAULT_LOW_SIGNAL_DUPLICATION_PATTERNS = [
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
    _row_a, _row_b, score, struct_score = pair
    if score <= 0.50 and struct_score <= 0.0:
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
    if preview.startswith('"""') or preview.startswith("'''"):
        return True
    return False
