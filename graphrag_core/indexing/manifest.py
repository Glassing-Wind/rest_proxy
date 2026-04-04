"""Manifest builder for workspace indexing."""

from __future__ import annotations

from pathlib import Path


def build_manifest(project_path: str) -> list[dict[str, object]]:
    manifest: list[dict[str, object]] = []
    root = Path(project_path)
    skip_dirs = {
        ".git",
        "__pycache__",
        "node_modules",
        ".cache",
        ".ruff_cache",
        ".next",
        ".turbo",
        ".gemini",
        ".agents",
        ".agent",
        ".build",
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
    skip_exts = {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".mp4",
        ".mp3",
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
    }
    skip_filenames = {"parser.c", "grammar.json", "node-types.json", "parser.h"}
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

    for path in root.rglob("*"):
        if any(part in skip_dirs for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() in skip_exts:
            continue
        if path.name in skip_filenames:
            continue
        try:
            rel = str(path.relative_to(root))
            if _is_ignored(rel):
                continue
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

