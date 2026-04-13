"""Manifest builder for workspace indexing."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

SKIP_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".npm",
    ".pnpm-store",
    ".yarn",
    ".yarn-cache",
    ".cache",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".gradle",
    ".idea",
    ".vscode",
    ".task",
    ".next",
    ".turbo",
    ".gemini",
    ".agents",
    ".agent",
    ".runtime",
    ".opencode",
    ".build",
    "coverage",
    ".nyc_output",
    "tmp",
    "temp",
    "target",
    "build",
    "dist",
    "Pods",
    "DerivedData",
    "venv",
    ".venv",
    "env",
    "vendor",
    "third_party",
    "vendored",
    "external",
    "testdata",
    "fixtures",
    "__fixtures__",
    "__mocks__",
    "mocks",
    "snapshots",
    "__snapshots__",
    "parsers",
}
SKIP_DIR_SUFFIXES = (
    ".xcuserdatad",
    ".dSYM",
    ".swiftpm",
    ".egg-info",
    ".dist-info",
)
SKIP_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".docx",
    ".doc",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".numbers",
    ".pages",
    ".key",
    ".zip",
    ".tar",
    ".gz",
    ".mp4",
    ".mp3",
    ".wav",
    ".flac",
    ".aac",
    ".bin",
    ".exe",
    ".dll",
    ".so",
    ".pyc",
    ".lock",
    ".dylib",
    ".a",
    ".o",
    ".dSYM",
    ".wasm",
    ".swiftmodule",
    ".swiftdeps",
    ".d",
    ".pb",
    ".dat",
    ".sqlite",
    ".db",
    ".ttf",
    ".otf",
    ".ttc",
    ".woff",
    ".woff2",
    ".eot",
    ".ico",
    ".icns",
    ".heic",
    ".psd",
    ".sketch",
    ".fig",
    ".dia",
    ".csproj",
    ".gemspec",
    ".rbs",
    ".podspec",
    ".xcbkptlist",
    ".resolved",
    ".typed",
    ".fbs",
    ".mm",
    ".model",
    ".spiece",
    ".crt",
    ".ls",
    ".bazel",
    ".bzl",
    ".pbtxt",
    ".jinja",
    ".sln",
    ".work",
    ".sum",
    ".pid",
    ".map",
    ".log",
    ".tmp",
    ".data",
    ".modulemap",
    ".mdx",
    ".bmp",
}
SKIP_FILENAMES = {
    ".DS_Store",
    ".coverage",
    "BUILD",
    "BUILD.bazel",
    "SYNCLIST",
    "WORKSPACE",
    "VERSION",
    "Gemfile",
    "Rakefile",
    "Steepfile",
    "LICENSE",
    "COPYING",
    "COPYRIGHT",
    "NOTICE",
    "PKG-INFO",
    "Dockerfile",
    "swift6Dockerfile",
    "cudaSwiftDockerfile",
    "pre-commit",
    "model-list",
    "dispatch",
    "parser.c",
    "grammar.json",
    "node-types.json",
    "parser.h",
    "_headers",
    "site.webmanifest",
    "git",
    "opencode",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "script.py.mako",
}
SKIP_RELATIVE_PATTERNS = {
    "migrations/README",
    "*/migrations/README",
}
MAX_FILE_SIZE = 1 * 1024 * 1024


def load_indexignore_patterns(project_path: str) -> list[str]:
    root = Path(project_path)
    indexignore_path = root / ".indexignore"
    if not indexignore_path.exists():
        return []
    patterns: list[str] = []
    for line in indexignore_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def matches_indexignore(rel: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    parts = rel.replace("\\", "/")
    for pattern in patterns:
        if fnmatch.fnmatch(parts, pattern):
            return True
        if fnmatch.fnmatch(parts.split("/")[-1], pattern):
            return True
    return False


def matches_global_skip_patterns(rel: str) -> bool:
    parts = rel.replace("\\", "/")
    for pattern in SKIP_RELATIVE_PATTERNS:
        if fnmatch.fnmatch(parts, pattern):
            return True
    return False


def suggest_indexignore_entries(project_path: str) -> list[dict[str, str]]:
    root = Path(project_path)
    existing_patterns = set(load_indexignore_patterns(project_path))
    suggestions: list[dict[str, str]] = []
    seen_patterns: set[str] = set()

    def _add(pattern: str, reason: str) -> None:
        if pattern in existing_patterns or pattern in seen_patterns:
            return
        seen_patterns.add(pattern)
        suggestions.append({"pattern": pattern, "reason": reason})

    for path in sorted(root.glob("*.egg-info")):
        if path.is_dir():
            _add(
                f"{path.name}/**",
                "Generated packaging metadata directory not covered by the built-in manifest skip set.",
            )
    for path in sorted(root.glob("*.dist-info")):
        if path.is_dir():
            _add(
                f"{path.name}/**",
                "Installed distribution metadata directory not covered by the built-in manifest skip set.",
            )

    return suggestions


def build_manifest(project_path: str) -> list[dict[str, object]]:
    manifest: list[dict[str, object]] = []
    root = Path(project_path)
    indexignore_patterns = load_indexignore_patterns(project_path)

    def _is_required_graph_file(rel: str) -> bool:
        rel_lower = rel.lower()
        return (
            rel_lower.endswith(".xcodeproj/project.pbxproj")
            or rel_lower.endswith(".xcworkspace/contents.xcworkspacedata")
            or rel_lower.endswith(".xcscheme")
            or rel_lower.endswith(".storyboard")
            or rel_lower.endswith(".xib")
            or rel_lower.endswith(".plist")
            or rel_lower.endswith(".xcassets/contents.json")
            or ".xcassets/" in rel_lower and rel_lower.endswith("/contents.json")
        )

    def _is_required_graph_dir(rel: str) -> bool:
        rel_lower = rel.lower()
        return rel_lower.endswith(".xcodeproj") or rel_lower.endswith(".xcworkspace") or ".xcassets" in rel_lower

    for current_root, dirnames, filenames in os.walk(root):
        current_path = Path(current_root)
        rel_dir_parts = current_path.relative_to(root).parts if current_path != root else ()
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in SKIP_DIRS
            and not any(dirname.endswith(suffix) for suffix in SKIP_DIR_SUFFIXES)
            and (
                _is_required_graph_dir(str(Path(*rel_dir_parts, dirname)).replace("\\", "/"))
                or not matches_indexignore(str(Path(*rel_dir_parts, dirname)).replace("\\", "/"), indexignore_patterns)
            )
        ]
        for filename in filenames:
            path = current_path / filename
            rel = str(path.relative_to(root)).replace("\\", "/")
            if matches_indexignore(rel, indexignore_patterns) and not _is_required_graph_file(rel):
                continue
            if matches_global_skip_patterns(rel):
                continue
            if path.suffix.lower() in SKIP_EXTS:
                continue
            if path.name in SKIP_FILENAMES:
                continue
            if path.name.startswith(".") and path.name not in {
                ".gitignore",
                ".indexignore",
                ".env.example",
                ".editorconfig",
            }:
                continue
            try:
                stats = path.stat()
                if stats.st_size > MAX_FILE_SIZE:
                    continue
                manifest.append(
                    {
                        "abs_path": str(path.absolute()),
                        "rel_path": rel,
                        "ext": path.suffix.lower().lstrip("."),
                        "size": stats.st_size,
                    }
                )
            except Exception:
                continue
    return manifest
