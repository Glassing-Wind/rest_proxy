"""Manifest builder for workspace indexing."""

from __future__ import annotations

import os
from pathlib import Path


def build_manifest(project_path: str) -> list[dict[str, object]]:
    manifest: list[dict[str, object]] = []
    root = Path(project_path)
    skip_dirs = {
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
    skip_dir_suffixes = (
        ".xcuserdatad",
        ".dSYM",
        ".swiftpm",
    )
    skip_exts = {
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
    skip_filenames = {
        ".DS_Store",
        ".coverage",
        "BUILD",
        "BUILD.bazel",
        "SYNCLIST",
        "WORKSPACE",
        "Gemfile",
        "Rakefile",
        "Steepfile",
        "LICENSE",
        "COPYING",
        "COPYRIGHT",
        "NOTICE",
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
    }
    max_file_size = 1 * 1024 * 1024

    indexignore_patterns: list[str] = []
    indexignore_path = root / ".indexignore"
    if indexignore_path.exists():
        import fnmatch

        for line in indexignore_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                indexignore_patterns.append(line)

    def _is_ignored(rel: str) -> bool:
        if not indexignore_patterns:
            return False
        import fnmatch

        parts = rel.replace("\\", "/")
        for pat in indexignore_patterns:
            if fnmatch.fnmatch(parts, pat):
                return True
            if fnmatch.fnmatch(parts.split("/")[-1], pat):
                return True
        return False

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
            if dirname not in skip_dirs
            and not any(dirname.endswith(suffix) for suffix in skip_dir_suffixes)
            and (
                _is_required_graph_dir(str(Path(*rel_dir_parts, dirname)).replace("\\", "/"))
                or not _is_ignored(str(Path(*rel_dir_parts, dirname)).replace("\\", "/"))
            )
        ]
        for filename in filenames:
            path = current_path / filename
            rel = str(path.relative_to(root)).replace("\\", "/")
            if _is_ignored(rel) and not _is_required_graph_file(rel):
                continue
            if path.suffix.lower() in skip_exts:
                continue
            if path.name in skip_filenames:
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
                if stats.st_size > max_file_size:
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
