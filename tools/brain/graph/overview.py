"""Overview helpers for project and directory graph summaries."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from _helpers import get_memory_modules, get_project_id, get_workspace_path
from graphrag_core.indexing import watcher as index_watcher
from tools.brain.graph_contract import node_label, rel_type
from tools.brain.graph import core as graph_core
from .core import _SYMBOL_FILTER_CYPHER


_SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
    ".runtime",
}

FILE_LABEL = node_label("file")
XCODE_TARGET_LABEL = node_label("xcode_target")
XCODE_SCHEME_LABEL = node_label("xcode_scheme")
XCODE_WORKSPACE_LABEL = node_label("xcode_workspace")
CARGO_CRATE_LABEL = node_label("cargo_crate")
CARGO_WORKSPACE_LABEL = node_label("cargo_workspace")
REL_BUNDLES_FILE = rel_type("bundles_file")
REL_BUILDS_TARGET = rel_type("builds_target")
REL_REFERENCES_PROJECT = rel_type("references_project")
REL_DEFINED_IN_FILE = rel_type("defined_in_file")
REL_HAS_PACKAGE = rel_type("has_package")
REL_DEPENDS_ON_PACKAGE = rel_type("depends_on_package")
REL_IMPORTS = rel_type("imports")
REL_CALLS = rel_type("calls")
REL_CALLS_INFERRED = rel_type("calls_inferred")
REL_CALLS_FILE = rel_type("calls_file")
REL_ASSET_LINKS = rel_type("asset_links")
REL_CALLS_API = rel_type("calls_api")
REL_CONTAINS = rel_type("contains")
REL_FILE_GRAPH_LINK = rel_type("file_graph_link")
_SWIFT_TYPE_MENTION_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]+\b")
_LOW_SIGNAL_SEMANTIC_PATH_RE = re.compile(
    r"(^|/)(session-ses_[^/]+\.md|agents\.md|readme(?:\.[^/]+)?|changelog(?:\.[^/]+)?|notes?)$",
    re.IGNORECASE,
)
_COMMON_SWIFT_TYPE_NAMES = {
    "App",
    "Scene",
    "View",
    "Text",
    "Image",
    "Color",
    "String",
    "Int",
    "Float",
    "Double",
    "Bool",
    "UUID",
    "URL",
    "Data",
    "Date",
    "Button",
}
_GENERATED_OVERVIEW_PATH_MARKERS = (
    "/generated/",
    "/pregeneratedspm/",
    ".pb.swift",
    ".grpc.swift",
    ".generated.swift",
    ".gen.swift",
    ".gen.ts",
    ".generated.ts",
    "_generated.swift",
)


def _semantic_file_roles_penalty(file_roles: set[str] | None) -> int:
    roles = {str(role).strip().lower() for role in (file_roles or set()) if str(role).strip()}
    penalty = 0
    if "generated_surface" in roles or "binding_surface" in roles:
        penalty = max(penalty, 40)
    if "example_surface" in roles:
        penalty = max(penalty, 60)
    if "benchmark_surface" in roles:
        penalty = max(penalty, 60)
    if "test_surface" in roles:
        penalty = max(penalty, 70)
    if "support_surface" in roles:
        penalty = max(penalty, 40)
    return penalty


def _has_generated_support_surface(file_roles: set[str] | None) -> bool:
    roles = {str(role).strip().lower() for role in (file_roles or set()) if str(role).strip()}
    return bool(
        {"generated_surface", "binding_surface", "example_surface", "benchmark_surface", "test_surface", "support_surface"}
        & roles
    )


async def _load_semantic_file_roles(conn, project_id: str, file_paths: list[str]) -> dict[str, set[str]]:
    paths = [str(path).strip() for path in (file_paths or []) if str(path).strip()]
    if not paths:
        return {}
    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT
              file_path,
              array_agg(DISTINCT role) FILTER (WHERE role IS NOT NULL) AS roles
            FROM codebase_embeddings
            LEFT JOIN LATERAL jsonb_array_elements_text(
              CASE
                WHEN jsonb_typeof(metadata->'file_roles') = 'array'
                THEN metadata->'file_roles'
                ELSE '[]'::jsonb
              END
            ) AS role ON TRUE
            WHERE project_id = %s
              AND file_path = ANY(%s)
            GROUP BY file_path
            """,
            (project_id, paths),
        )
        rows = await cur.fetchall()
    out: dict[str, set[str]] = {}
    for file_path, roles in rows:
        normalized_path = str(file_path or "").strip()
        if not normalized_path:
            continue
        out[normalized_path] = {
            str(role).strip().lower()
            for role in (roles or [])
            if str(role).strip()
        }
    return out


def _schema_cypher(text: str) -> str:
    replacements = {
        "__FILE__": FILE_LABEL,
        "__XCODE_TARGET__": XCODE_TARGET_LABEL,
        "__XCODE_SCHEME__": XCODE_SCHEME_LABEL,
        "__XCODE_WORKSPACE__": XCODE_WORKSPACE_LABEL,
        "__CARGO_CRATE__": CARGO_CRATE_LABEL,
        "__CARGO_WORKSPACE__": CARGO_WORKSPACE_LABEL,
        "__BUNDLES_FILE__": REL_BUNDLES_FILE,
        "__BUILDS_TARGET__": REL_BUILDS_TARGET,
        "__REFERENCES_PROJECT__": REL_REFERENCES_PROJECT,
        "__DEFINED_IN_FILE__": REL_DEFINED_IN_FILE,
        "__HAS_PACKAGE__": REL_HAS_PACKAGE,
        "__DEPENDS_ON_PACKAGE__": REL_DEPENDS_ON_PACKAGE,
        "__IMPORTS__": REL_IMPORTS,
        "__CALLS__": REL_CALLS,
        "__CALLS_INFERRED__": REL_CALLS_INFERRED,
        "__CALLS_FILE__": REL_CALLS_FILE,
        "__ASSET_LINKS__": REL_ASSET_LINKS,
        "__CALLS_API__": REL_CALLS_API,
        "__CONTAINS__": REL_CONTAINS,
        "__FILE_GRAPH_LINK__": REL_FILE_GRAPH_LINK,
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _file_path_expr(alias: str = "f") -> str:
    return f"coalesce({alias}.filepath, {alias}.file_path)"


def _is_low_signal_semantic_path(file_path: str | None) -> bool:
    norm = (file_path or "").replace("\\", "/").lower()
    if not norm:
        return True
    if _LOW_SIGNAL_SEMANTIC_PATH_RE.search(norm):
        return True
    if norm.endswith((".md", ".rst", ".txt")):
        return True
    return False


def _directory_snapshot_path_penalty(file_path: str | None, file_roles: set[str] | None = None) -> int:
    norm = (file_path or "").replace("\\", "/").lower()
    penalty = _semantic_file_roles_penalty(file_roles)
    if _is_low_signal_semantic_path(norm):
        penalty = max(penalty, 100)
    if any(
        marker in norm
        for marker in (
            "/generated/",
            "/pregeneratedspm/",
            ".pb.swift",
            ".grpc.swift",
            ".generated.swift",
            ".gen.swift",
        )
    ):
        penalty = max(penalty, 40)
    if any(
        marker in norm
        for marker in (
            "/tests/",
            "/test/",
            "tests/",
            "test/",
            "/stories/",
            "/fixtures/",
            "fixtures/",
            "/examples/",
            "examples/",
        )
    ):
        penalty = max(penalty, 60)
    if any(
        marker in norm
        for marker in (
            "/integrationtests/",
            "integrationtests/",
            "/integration-tests/",
            "integration-tests/",
            "/e2e/",
            "e2e/",
        )
    ):
        penalty = max(penalty, 70)
    return penalty


def _is_apple_support_path(file_path: str | None) -> bool:
    norm = (file_path or "").replace("\\", "/").lower()
    if not norm:
        return False
    if norm.endswith(
        (
            ".xcodeproj/project.pbxproj",
            ".xcworkspace/contents.xcworkspacedata",
            ".xcscheme",
            "contents.json",
            ".svg",
            ".plist",
            ".storyboard",
            ".xib",
        )
    ):
        return True
    return any(
        marker in norm
        for marker in (
            ".xcassets/",
            ".imageset/",
            ".colorset/",
            ".appiconset/",
            ".symbolset/",
            ".dataset/",
            ".brandassets/",
            ".stickerpack/",
        )
    )


def _directory_snapshot_signal_rank(signal: str | None) -> int:
    return {
        "symbol_call": 0,
        "file_graph": 1,
        "semantic": 2,
        "import": 3,
    }.get(str(signal or "").strip().lower(), 4)


def _is_code_directory_context(directory_path: str | None) -> bool:
    directory_norm = (directory_path or "").replace("\\", "/").lower().strip("/")
    if not directory_norm:
        return False
    return any(
        token in directory_norm
        for token in (
            "src/main/java",
            "src/test/java",
            "src/",
            "sources/",
            "pydantic_ai/",
            "okhttp/",
            "app/",
            "widgets/",
        )
    ) or directory_norm in {"src", "sources", "app", "widgets"}


def _rank_directory_snapshot_rows(
    rows: list[dict] | None,
    *,
    path_key: str,
    count_key: str,
    limit: int,
    directory_path: str | None = None,
    file_roles_by_path: dict[str, set[str]] | None = None,
) -> list[dict]:
    ranked: list[tuple[int, int, int, str, dict]] = []
    seen: set[str] = set()
    for row in rows or []:
        path = str(row.get(path_key) or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        ranked.append(
            (
                _directory_snapshot_path_penalty(path, (file_roles_by_path or {}).get(path))
                + _directory_snapshot_context_penalty(path, directory_path),
                _directory_snapshot_signal_rank(row.get("signal")),
                -int(row.get(count_key) or 0),
                path,
                row,
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return [item[4] for item in ranked[: max(1, limit)]]


def _directory_snapshot_display_rows(
    rows: list[dict] | None,
    *,
    path_key: str,
    directory_path: str | None = None,
    file_roles_by_path: dict[str, set[str]] | None = None,
) -> list[dict]:
    items = list(rows or [])
    if not items:
        return items
    if _is_code_directory_context(directory_path):
        preferred = [
            row
            for row in items
            if (
                _directory_snapshot_path_penalty(row.get(path_key))
                if file_roles_by_path is None
                else _directory_snapshot_path_penalty(
                    row.get(path_key),
                    file_roles_by_path.get(str(row.get(path_key) or "").strip()),
                )
                + _directory_snapshot_context_penalty(row.get(path_key), directory_path)
            )
            < 40
        ]
        if not preferred:
            context_safe = [
                row
                for row in items
                if _directory_snapshot_context_penalty(row.get(path_key), directory_path) < 50
                and (
                    _directory_snapshot_path_penalty(row.get(path_key))
                    if file_roles_by_path is None
                    else _directory_snapshot_path_penalty(
                        row.get(path_key),
                        file_roles_by_path.get(str(row.get(path_key) or "").strip()),
                    )
                )
                < 40
            ]
            return context_safe
    return items


def _directory_snapshot_display_files(
    rows: list[dict] | None,
    *,
    directory_path: str | None = None,
    file_roles_by_path: dict[str, set[str]] | None = None,
) -> list[dict]:
    items = list(rows or [])
    if not items:
        return items
    if not _is_code_directory_context(directory_path):
        return items
    preferred = [
        row
        for row in items
        if _directory_snapshot_path_penalty(
            row.get("fp"),
            (file_roles_by_path or {}).get(str(row.get("fp") or "").strip()),
        )
        < 40
    ]
    return preferred or items


def _directory_snapshot_context_penalty(path: str | None, directory_path: str | None) -> int:
    norm = (path or "").replace("\\", "/").lower()
    if not norm or not directory_path:
        return 0
    if not _is_code_directory_context(directory_path):
        return 0
    if any(
        token in norm
        for token in (
            "/resources/",
            "/static/",
            ".css",
            ".scss",
            ".sql",
            ".yml",
            ".yaml",
            ".properties",
            ".html",
        )
    ):
        return 50
    if _is_apple_support_path(norm):
        return 50
    if "/include/" in norm and norm.endswith((".h", ".hpp", ".hh")):
        return 45
    return 0


def _merge_directory_snapshot_rows(
    primary: list[dict] | None,
    extra: list[dict] | None,
    *,
    path_key: str,
    count_key: str,
) -> list[dict]:
    merged: dict[str, dict] = {}
    for row in (primary or []) + (extra or []):
        path = str(row.get(path_key) or "").strip()
        if not path:
            continue
        count = int(row.get(count_key) or 0)
        existing = merged.get(path)
        if existing is None:
            merged[path] = dict(row)
            continue
        existing_rank = _directory_snapshot_signal_rank(existing.get("signal"))
        incoming_rank = _directory_snapshot_signal_rank(row.get("signal"))
        if incoming_rank < existing_rank or (
            incoming_rank == existing_rank and count > int(existing.get(count_key) or 0)
        ):
            merged[path] = dict(row)
    return list(merged.values())


def _extract_swift_type_mentions(text: str, *, ignore: set[str] | None = None) -> list[str]:
    ignored = set(ignore or ())
    ignored.update(_COMMON_SWIFT_TYPE_NAMES)
    out: list[str] = []
    seen: set[str] = set()
    for match in _SWIFT_TYPE_MENTION_RE.findall(text or ""):
        if match in ignored or match in seen:
            continue
        seen.add(match)
        out.append(match)
        if len(out) >= 16:
            break
    return out


def _importance_penalty(filepath: str | None, file_roles: set[str] | None = None) -> float:
    norm = (filepath or "").replace("\\", "/").lower()
    basename = os.path.basename(norm)
    roles = {str(role).strip().lower() for role in (file_roles or set()) if str(role).strip()}
    if ("src/public/assets/" in norm or "/public/assets/" in norm) and norm.endswith((".js", ".ts", ".jsx", ".tsx")):
        return 0.08
    if "generated_surface" in roles or "binding_surface" in roles:
        return 0.08
    if "test_surface" in roles:
        return 0.005
    if "example_surface" in roles:
        return 0.005
    if "benchmark_surface" in roles:
        return 0.02
    if "support_surface" in roles:
        return 0.2
    if any(token in norm for token in ("/gen/",) + _GENERATED_OVERVIEW_PATH_MARKERS):
        return 0.08
    if basename.endswith("application.java") or basename.endswith("runtimehints.java"):
        return 0.35
    if "/e2e/" in norm or norm.endswith((".spec.ts", ".spec.tsx")):
        return 0.24
    if "/storybook/" in norm or ".stories." in norm:
        return 0.22
    if "/components/icons/" in norm:
        return 0.16
    if norm.startswith("vendors/") or "/vendors/" in norm:
        return 0.35
    return 1.0


def _backend_bridge_boost(filepath: str | None) -> float:
    norm = (filepath or "").replace("\\", "/").lower()
    basename = os.path.basename(norm)
    if any(
        token in norm
        for token in (
            "src/api/routes/",
            "src/api/",
            "src/services/",
            "src/db/",
            "prisma/schema.prisma",
        )
    ):
        return 1.8
    if (
        "/controller/" in norm
        or basename.endswith("controller.java")
        or basename.endswith("serviceimpl.java")
        or basename.endswith("endpoint.java")
    ):
        return 1.8
    if basename.endswith("repository.java"):
        return 1.25
    if any(token in norm for token in ("src/config/", "src/lib/", "src/models/")):
        return 1.12
    return 1.0


def _overview_directory_penalty(top_dir: str | None) -> int:
    norm = str(top_dir or "").replace("\\", "/").strip("/").lower()
    if not norm or norm == "(root)":
        return 0
    if norm in {"tests", "test"} or norm.startswith("tests/") or norm.startswith("test/"):
        return 40
    if norm in {"docs", "doc"} or norm.startswith("docs/") or norm.startswith("doc/"):
        return 25
    if norm.startswith("examples/") or norm == "examples":
        return 20
    if norm.startswith("samples/") or norm == "samples":
        return 18
    if norm.startswith("benchmark/") or norm == "benchmark":
        return 15
    return 0


def _overview_file_rank(filepath: str | None, symbol_count: int, file_roles: set[str] | None = None) -> float:
    base = float(symbol_count or 0) ** 0.85
    norm = (filepath or "").replace("\\", "/").lower()
    basename = os.path.basename(norm)
    score = base * _importance_penalty(filepath, file_roles) * _backend_bridge_boost(filepath)
    if any(marker in norm for marker in ("/tests/", "/test/", ".spec.", ".test.", "/fixtures/", "/examples/")) or norm.startswith(
        ("tests/", "test/", "fixtures/", "examples/")
    ):
        score *= 0.005
    if basename in {"mvnw", "mvnw.cmd", "gradlew", "gradlew.bat"}:
        score *= 0.005
    if norm.endswith((".cmd", ".bat")):
        score *= 0.02
    if norm.endswith(".sh"):
        score *= 0.2
    if _is_overview_low_signal_shell_helper(basename):
        score *= 0.2
    return score


def _is_overview_low_signal_shell_helper(basename: str | None) -> bool:
    value = str(basename or "").strip().lower()
    if not value.endswith(".sh"):
        return False
    return any(token in value for token in ("install", "build", "setup", "bootstrap"))


def _is_overview_low_signal_key_file(filepath: str | None, file_roles: set[str] | None = None) -> bool:
    norm = (filepath or "").replace("\\", "/").lower()
    basename = os.path.basename(norm)
    roles = {str(role).strip().lower() for role in (file_roles or set()) if str(role).strip()}
    if {"generated_surface", "binding_surface", "test_surface", "example_surface", "benchmark_surface", "support_surface"} & roles:
        return True
    if any(marker in norm for marker in ("/tests/", "/test/", ".spec.", ".test.", "/fixtures/", "/examples/")):
        return True
    if norm.startswith(("tests/", "test/", "fixtures/", "examples/")):
        return True
    if any(marker in norm for marker in _GENERATED_OVERVIEW_PATH_MARKERS):
        return True
    if basename in {"mvnw", "mvnw.cmd", "gradlew", "gradlew.bat"}:
        return True
    if _is_overview_low_signal_shell_helper(basename):
        return True
    return False


def _project_overview_priority_lines(
    *,
    project_path: str,
    dirs: list[dict[str, object]],
    key_files: list[str],
    apple_targets: list[dict[str, object]],
    cargo_crates: list[dict[str, object]],
    repo_linked_dependencies: list[str],
) -> list[str]:
    lines: list[str] = []
    if apple_targets:
        first_target = str(apple_targets[0].get("target") or "").strip()
        project_file = str(apple_targets[0].get("project_file") or "").strip()
        if first_target:
            detail = f"target `{first_target}`"
            if project_file:
                detail += f" via `{project_file}`"
            lines.append(f"  - inspect Apple build context first because {detail} anchors the app structure")
    if cargo_crates:
        first_crate = str(cargo_crates[0].get("crate") or "").strip()
        manifest = str(cargo_crates[0].get("manifest_path") or "").strip()
        if first_crate:
            detail = f"crate `{first_crate}`"
            if manifest:
                detail += f" via `{manifest}`"
            lines.append(f"  - inspect cargo workspace context next because {detail} anchors the Rust entry surface")
    if repo_linked_dependencies:
        lines.append("  - inspect repo-linked dependencies early because they define cross-repo breakage boundaries")
    if key_files:
        first_line = key_files[0].strip()
        if first_line.startswith("- "):
            first_line = first_line[2:]
        file_path = first_line.split("  (", 1)[0].strip()
        if file_path:
            lines.append(f"  - inspect `{file_path}` first because it is the highest-signal non-test implementation file")
    if dirs:
        top_dir = str(dirs[0].get("top_dir") or "").strip()
        if top_dir and top_dir != "(root)":
            lines.append(
                f"  - use get_directory_snapshot('{project_path}', '{top_dir}', limit=12) to orient inside the strongest implementation directory"
            )
    return lines[:4]


def _normalize_pkg_name(name: str) -> str:
    return (name or "").strip().replace("-", "_").lower()


def _repo_name_from_url(raw_url: str) -> str:
    path = urlparse(raw_url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else ""
    if name.endswith(".git"):
        name = name[:-4]
    return name


def _parse_repo_linked_dependencies(project_path: str) -> list[dict[str, str]]:
    deps: list[dict[str, str]] = []
    req_path = os.path.join(project_path, "requirements.txt")
    if not os.path.exists(req_path):
        return deps

    git_re = re.compile(
        r"git\+(?P<url>[^@#\s]+(?:\.git)?)(?:@(?P<rev>[^#\s]+))?"
        r"#egg=(?P<egg>[A-Za-z0-9_.-]+)(?:&subdirectory=(?P<subdir>[^\s]+))?"
    )

    try:
        with open(req_path, "r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                match = git_re.search(line)
                if not match:
                    continue
                url = match.group("url") or ""
                deps.append(
                    {
                        "package": match.group("egg") or "",
                        "repo_name": _repo_name_from_url(url),
                        "repo_url": url,
                        "rev": match.group("rev") or "",
                        "subdirectory": match.group("subdir") or "",
                    }
                )
    except Exception:
        return []

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for dep in deps:
        key = (_normalize_pkg_name(dep.get("package", "")), dep.get("repo_name", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(dep)
    return out


def _load_indexed_project_paths() -> dict[str, str]:
    data = index_watcher.load_indexed_projects()
    out: dict[str, str] = {}
    for entry in data.values():
        path = entry.get("project_path")
        if isinstance(path, str) and path:
            out[os.path.basename(path.rstrip(os.sep))] = path
    return out


def _find_repo_link_evidence(project_path: str, package_name: str, limit: int = 4) -> list[str]:
    pkg = _normalize_pkg_name(package_name)
    if not pkg or not os.path.isdir(project_path):
        return []
    patterns = (
        re.compile(rf"^\s*import\s+{re.escape(pkg)}(?:\s|$|,)", re.M),
        re.compile(rf"^\s*from\s+{re.escape(pkg)}(?:\.|\s+import\s+)", re.M),
    )
    hits: list[str] = []
    for root, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
        for filename in sorted(filenames):
            if not filename.endswith((".py", ".pyi")):
                continue
            abs_path = os.path.join(root, filename)
            try:
                if os.path.getsize(abs_path) > 512_000:
                    continue
                with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except Exception:
                continue
            if any(p.search(text) for p in patterns):
                hits.append(os.path.relpath(abs_path, project_path))
                if len(hits) >= limit:
                    return hits
    return hits


def _summarize_repo_linked_dependencies(project_path: str) -> list[str]:
    linked = _parse_repo_linked_dependencies(project_path)
    if not linked:
        return []

    indexed_paths = _load_indexed_project_paths()
    lines: list[str] = []
    for dep in linked[:5]:
        package = dep.get("package") or "(unknown)"
        repo_name = dep.get("repo_name") or package
        repo_path = indexed_paths.get(repo_name, "")
        evidence = _find_repo_link_evidence(project_path, package)
        base = f"  - `{package}` from `{repo_name}`"
        if repo_path:
            base += f" → local indexed repo `{repo_path}`"
        if dep.get("subdirectory"):
            base += f" (subdir `{dep['subdirectory']}`)"
        lines.append(base)
        if evidence:
            lines.append(f"    imported by {', '.join(f'`{item}`' for item in evidence)}")
    return lines


def _summarize_repo_linked_dependencies_for_directory(
    project_path: str,
    directory_path: str,
) -> list[str]:
    linked = _parse_repo_linked_dependencies(project_path)
    if not linked:
        return []

    dir_prefix = directory_path.strip("./")
    if dir_prefix:
        dir_prefix = dir_prefix.rstrip("/") + "/"
    indexed_paths = _load_indexed_project_paths()
    lines: list[str] = []
    for dep in linked[:5]:
        package = dep.get("package") or "(unknown)"
        evidence = _find_repo_link_evidence(project_path, package, limit=8)
        if dir_prefix:
            evidence = [item for item in evidence if item.startswith(dir_prefix)]
        if not evidence:
            continue
        repo_name = dep.get("repo_name") or package
        repo_path = indexed_paths.get(repo_name, "")
        base = f"- `{package}` from `{repo_name}`"
        if repo_path:
            base += f" → local indexed repo `{repo_path}`"
        if dep.get("subdirectory"):
            base += f" (subdir `{dep['subdirectory']}`)"
        lines.append(base)
        lines.append(f"- evidence in {', '.join(f'`{item}`' for item in evidence[:4])}")
    return lines


def _classify_apple_directory(directory_path: str | None) -> str | None:
    norm = (directory_path or "").replace("\\", "/").strip("./")
    if not norm:
        return None
    if ".xcworkspace" in norm:
        return "workspace"
    if ".xcodeproj" in norm:
        return "project"
    if ".xcassets" in norm or any(
        token in norm
        for token in (
            ".imageset",
            ".colorset",
            ".appiconset",
            ".brandassets",
            ".dataset",
            ".stickerpack",
        )
    ):
        return "resources"
    if norm.endswith((".storyboard", ".xib", ".plist")):
        return "resources"
    return None


def _directory_snapshot_priority_lines(
    directory_path: str,
    file_rows: list[dict],
    inbound_rows: list[dict],
    outbound_rows: list[dict],
    asset_rows: list[dict],
    has_apple_context: bool,
    has_cargo_context: bool,
    apple_workspace_rows: list[dict],
    repo_linked_dependencies: list[str],
    file_roles_by_path: dict[str, set[str]] | None = None,
) -> list[str]:
    priorities: list[str] = []
    directory_norm = (directory_path or "").replace("\\", "/").lower()
    code_context = _is_code_directory_context(directory_path)
    apple_dir_kind = _classify_apple_directory(directory_path) if has_apple_context else None
    if apple_dir_kind == "workspace":
        priorities.append(
            "- inspect workspace project references first because this directory defines the top-level Xcode workspace"
        )
    elif apple_dir_kind == "project":
        priorities.append(
            "- inspect scheme and target membership first because this directory defines an Xcode project"
        )
    elif apple_dir_kind == "resources":
        priorities.append(
            "- inspect bundled resource ownership first because this directory contains Apple app assets or build resources"
        )
    if file_rows and apple_dir_kind != "workspace":
        top = file_rows[0]
        priorities.append(
            f"- start with `{top['fp']}` because it has the densest local symbol surface ({top['sym_count']} symbols)"
        )
    if has_cargo_context:
        priorities.append("- inspect Cargo context next because this directory sits on a crate or workspace boundary")
    elif has_apple_context:
        if apple_dir_kind == "workspace" and apple_workspace_rows:
            workspace = apple_workspace_rows[0].get("workspace")
            if workspace:
                priorities.append(
                    f"- check `{workspace}` next because it resolves the owning Xcode projects for this workspace"
                )
        elif apple_dir_kind == "project":
            priorities.append(
                "- inspect Apple build context next because this project directory maps schemes to targets and bundled files"
            )
        elif apple_dir_kind == "resources":
            priorities.append(
                "- inspect Apple build context next because this resource directory is tied to bundled targets and schemes"
            )
        else:
            priorities.append("- inspect Apple build context next because this directory is tied to an Xcode target or scheme")
    if inbound_rows:
        top = _best_directory_snapshot_row(
            inbound_rows,
            path_key="caller",
            count_key="n_imports",
            directory_path=directory_path,
            max_penalty=40,
            file_roles_by_path=file_roles_by_path,
        )
        if top:
            priorities.append(
                f"- check inbound usage from `{top['caller']}` first because it is the strongest external consumer"
            )
        elif code_context and len(file_rows) > 1:
            sibling = next(
                (
                    row
                    for row in file_rows[1:]
                    if _directory_snapshot_path_penalty(
                        row.get("fp"),
                        (file_roles_by_path or {}).get(str(row.get("fp") or "").strip()),
                    ) < 40
                    and os.path.basename(str(row.get("fp") or "")).lower() != "package-info.java"
                ),
                None,
            )
            if sibling:
                priorities.append(
                    f"- inspect sibling implementation `{sibling['fp']}` next because external consumer signal here is mostly static/config noise"
                )
    if outbound_rows:
        top = _best_directory_snapshot_row(
            outbound_rows,
            path_key="dependency",
            count_key="n_usages",
            directory_path=directory_path,
            max_penalty=40,
            file_roles_by_path=file_roles_by_path,
        )
        if top:
            priorities.append(
                f"- check outbound dependency `{top['dependency']}` because files here rely on it most often"
            )
    if asset_rows:
        priorities.append("- review UI or API wiring because this directory has asset or endpoint linkages")
    if repo_linked_dependencies:
        priorities.append("- review repo-linked dependencies here because this directory crosses repo boundaries")
    return priorities[:4]


def _directory_snapshot_file_rank(
    filepath: str | None,
    symbol_count: int,
    file_roles: set[str] | None = None,
) -> tuple[float, int, str]:
    score = _overview_file_rank(filepath, symbol_count, file_roles)
    return (-score, _directory_snapshot_path_penalty(filepath, file_roles), str(filepath or ""))


def _best_directory_snapshot_row(
    rows: list[dict],
    *,
    path_key: str,
    count_key: str,
    directory_path: str,
    max_penalty: int,
    file_roles_by_path: dict[str, set[str]] | None = None,
) -> dict | None:
    candidates = [
        row
        for row in rows
        if (
            _directory_snapshot_path_penalty(
                row.get(path_key),
                (file_roles_by_path or {}).get(str(row.get(path_key) or "").strip()),
            )
            + _directory_snapshot_context_penalty(row.get(path_key), directory_path)
        )
        < max_penalty
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda row: (
            _directory_snapshot_path_penalty(
                row.get(path_key),
                (file_roles_by_path or {}).get(str(row.get(path_key) or "").strip()),
            )
            + _directory_snapshot_context_penalty(row.get(path_key), directory_path),
            _directory_snapshot_signal_rank(row.get("signal")),
            -int(row.get(count_key) or 0),
            str(row.get(path_key) or ""),
        ),
    )


def _repo_dependency_priority_lines(
    linked: list[dict[str, str]],
    indexed_paths: dict[str, str],
    project_path: str,
) -> list[str]:
    ranked: list[tuple[int, str]] = []
    for dep in linked:
        package = dep.get("package") or "(unknown)"
        repo_name = dep.get("repo_name") or package
        repo_path = indexed_paths.get(repo_name, "")
        evidence = _find_repo_link_evidence(project_path, package, limit=8)
        score = len(evidence) * 10
        reasons: list[str] = []
        if evidence:
            reasons.append(f"{len(evidence)} importer(s)")
        if repo_path:
            score += 5
            reasons.append("indexed sibling repo")
        if dep.get("subdirectory"):
            score += 1
            reasons.append("subdirectory binding")
        if dep.get("rev"):
            score += 1
            reasons.append("pinned revision")
        if not reasons:
            reasons.append("declared repo-linked package")
        ranked.append((score, f"- `{package}` from `{repo_name}`: {', '.join(reasons)}"))
    return [line for _, line in sorted(ranked, key=lambda item: (-item[0], item[1]))[:4]]


async def has_apple_build_context(session, project_id: str) -> bool:
    rows = await graph_core._execute_read(
        session,
        _schema_cypher(f"""
        MATCH (f:__FILE__ {{project_id:$p}})
        WHERE {_file_path_expr('f')} ENDS WITH '.xcodeproj/project.pbxproj'
           OR {_file_path_expr('f')} ENDS WITH '.xcworkspace/contents.xcworkspacedata'
           OR {_file_path_expr('f')} ENDS WITH '.xcscheme'
           OR {_file_path_expr('f')} ENDS WITH '.storyboard'
           OR {_file_path_expr('f')} ENDS WITH '.xib'
           OR {_file_path_expr('f')} CONTAINS '.xcassets/'
        RETURN count(f) AS n
        """),
        p=project_id,
        op="apple_context_presence",
    )
    return bool(rows and rows[0].get("n"))


async def has_cargo_build_context(session, project_id: str) -> bool:
    rows = await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (f:__FILE__ {project_id:$p})
        WHERE coalesce(f.filepath, f.file_path) ENDS WITH 'Cargo.toml'
        RETURN count(f) AS file_count
        """),
        p=project_id,
        op="cargo_context_presence",
    )
    file_count = int((rows or [{}])[0].get("file_count") or 0)
    if file_count > 0:
        return True
    crate_rows = await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (c:__CARGO_CRATE__ {project_id:$p})
        RETURN count(c) AS crate_count
        """),
        p=project_id,
        op="cargo_context_presence_crates",
    )
    return bool(crate_rows and crate_rows[0].get("crate_count"))


async def load_apple_build_context(session, project_id: str, dir_prefix: str = "", limit: int = 5):
    targets = await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (t:__XCODE_TARGET__ {project_id:$p})
        OPTIONAL MATCH (t)-[:__BUNDLES_FILE__]->(f:__FILE__ {project_id:$p})
        WHERE $dir = '' OR f.filepath STARTS WITH $dir
        RETURN t.name AS target, t.project_file AS project_file, count(DISTINCT f) AS bundled_files
        ORDER BY bundled_files DESC, target
        LIMIT $limit
        """),
        p=project_id,
        dir=dir_prefix,
        limit=limit,
        op="apple_context_targets",
    )
    schemes = await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (s:__XCODE_SCHEME__ {project_id:$p})-[:__BUILDS_TARGET__]->(t:__XCODE_TARGET__ {project_id:$p})
        RETURN s.name AS scheme, collect(DISTINCT t.name)[..10] AS targets
        ORDER BY scheme
        LIMIT $limit
        """),
        p=project_id,
        limit=limit,
        op="apple_context_schemes",
    )
    schema_labels = await graph_core._execute_read(
        session,
        """
        CALL db.labels() YIELD label
        RETURN collect(label) AS labels
        """,
        op="apple_context_schema_labels",
    )
    schema_rels = await graph_core._execute_read(
        session,
        """
        CALL db.relationshipTypes() YIELD relationshipType
        RETURN collect(relationshipType) AS rels
        """,
        op="apple_context_schema_relationship_types",
    )
    labels = set(schema_labels[0].get("labels") or []) if schema_labels else set()
    rels = set(schema_rels[0].get("rels") or []) if schema_rels else set()
    if XCODE_WORKSPACE_LABEL in labels and REL_REFERENCES_PROJECT in rels:
        workspaces = await graph_core._execute_read(
            session,
            _schema_cypher("""
            MATCH (w:__XCODE_WORKSPACE__ {project_id:$p})-[:__REFERENCES_PROJECT__]->(f:__FILE__ {project_id:$p})
            RETURN w.filepath AS workspace, collect(DISTINCT f.filepath)[..10] AS projects
            ORDER BY workspace
            LIMIT $limit
            """),
            p=project_id,
            limit=limit,
            op="apple_context_workspaces",
        )
    else:
        workspaces = []
    return targets, schemes, workspaces


async def load_cargo_build_context(session, project_id: str, dir_prefix: str = "", limit: int = 5):
    schema_labels = await graph_core._execute_read(
        session,
        """
        CALL db.labels() YIELD label
        RETURN collect(label) AS labels
        """,
        op="cargo_context_schema_labels",
    )
    schema_rels = await graph_core._execute_read(
        session,
        """
        CALL db.relationshipTypes() YIELD relationshipType
        RETURN collect(relationshipType) AS rels
        """,
        op="cargo_context_schema_relationship_types",
    )
    labels = set(schema_labels[0].get("labels") or []) if schema_labels else set()
    rels = set(schema_rels[0].get("rels") or []) if schema_rels else set()
    if CARGO_CRATE_LABEL not in labels:
        return [], [], []

    crates = await graph_core._execute_read(
        session,
        _schema_cypher(f"""
        MATCH (c:__CARGO_CRATE__ {{project_id:$p}})-[:__DEFINED_IN_FILE__]->(mf:__FILE__ {{project_id:$p}})
        WHERE $dir = ''
           OR {_file_path_expr('mf')} STARTS WITH $dir
           OR $dir STARTS WITH replace({_file_path_expr('mf')}, 'Cargo.toml', '')
        RETURN c.name AS crate,
               c.crate_name AS crate_name,
               {_file_path_expr('mf')} AS manifest_path,
               count(DISTINCT mf) AS manifest_files
        ORDER BY crate
        LIMIT $limit
        """),
        p=project_id,
        dir=dir_prefix,
        limit=limit,
        op="cargo_context_crates",
    )
    if not crates:
        crates = await graph_core._execute_read(
            session,
            _schema_cypher("""
            MATCH (c:__CARGO_CRATE__ {project_id:$p})
            RETURN c.name AS crate,
                   c.crate_name AS crate_name,
                   coalesce(c.manifest_path, '') AS manifest_path,
                   0 AS manifest_files
            ORDER BY crate
            LIMIT $limit
            """),
            p=project_id,
            limit=limit,
            op="cargo_context_crates_fallback",
        )
    if dir_prefix:
        local_crates = [
            rec
            for rec in crates
            if (rec.get("manifest_path") or "").strip()
            and (rec.get("manifest_files") or 0)
        ]
        if local_crates:
            crates = local_crates
        elif crates:
            local_by_manifest = await graph_core._execute_read(
                session,
                _schema_cypher("""
                MATCH (c:__CARGO_CRATE__ {project_id:$p})
                WHERE coalesce(c.manifest_path, '') STARTS WITH $dir
                RETURN c.name AS crate,
                       c.crate_name AS crate_name,
                       coalesce(c.manifest_path, '') AS manifest_path,
                       CASE WHEN coalesce(c.manifest_path, '') = '' THEN 0 ELSE 1 END AS manifest_files
                ORDER BY crate
                LIMIT $limit
                """),
                p=project_id,
                dir=dir_prefix,
                limit=limit,
                op="cargo_context_crates_manifest_fallback",
            )
            if local_by_manifest:
                crates = local_by_manifest

    if CARGO_WORKSPACE_LABEL in labels and REL_HAS_PACKAGE in rels:
        workspaces = await graph_core._execute_read(
            session,
            _schema_cypher("""
            MATCH (w:__CARGO_WORKSPACE__ {project_id:$p})-[:__HAS_PACKAGE__]->(c:__CARGO_CRATE__ {project_id:$p})
            RETURN w.filepath AS workspace, collect(DISTINCT c.name)[..10] AS crates
            ORDER BY workspace
            LIMIT $limit
            """),
            p=project_id,
            limit=limit,
            op="cargo_context_workspaces",
        )
    else:
        workspaces = []

    if REL_DEPENDS_ON_PACKAGE in rels:
        dependencies = await graph_core._execute_read(
            session,
            _schema_cypher(f"""
            MATCH (src:__CARGO_CRATE__ {{project_id:$p}})-[r:__DEPENDS_ON_PACKAGE__]->(tgt:__CARGO_CRATE__ {{project_id:$p}})
            MATCH (src)-[:__DEFINED_IN_FILE__]->(mf:__FILE__ {{project_id:$p}})
            WHERE $dir = ''
               OR {_file_path_expr('mf')} STARTS WITH $dir
               OR $dir STARTS WITH replace({_file_path_expr('mf')}, 'Cargo.toml', '')
            RETURN src.name AS crate, collect(DISTINCT tgt.name)[..10] AS deps
            ORDER BY crate
            LIMIT $limit
            """),
            p=project_id,
            dir=dir_prefix,
            limit=limit,
            op="cargo_context_dependencies",
        )
    else:
        dependencies = []

    return crates, workspaces, dependencies


async def load_cargo_directory_dependencies(session, project_id: str, dir_prefix: str = "", limit: int = 5):
    schema_labels = await graph_core._execute_read(
        session,
        """
        CALL db.labels() YIELD label
        RETURN collect(label) AS labels
        """,
        op="cargo_directory_schema_labels",
    )
    schema_rels = await graph_core._execute_read(
        session,
        """
        CALL db.relationshipTypes() YIELD relationshipType
        RETURN collect(relationshipType) AS rels
        """,
        op="cargo_directory_schema_relationship_types",
    )
    labels = set(schema_labels[0].get("labels") or []) if schema_labels else set()
    rels = set(schema_rels[0].get("rels") or []) if schema_rels else set()
    if CARGO_CRATE_LABEL not in labels or REL_DEPENDS_ON_PACKAGE not in rels:
        return [], []

    outbound = await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (src:__CARGO_CRATE__ {project_id:$p})-[:__DEFINED_IN_FILE__]->(mf:__FILE__ {project_id:$p})
        WHERE $dir <> ''
          AND (mf.filepath STARTS WITH $dir OR $dir STARTS WITH replace(mf.filepath, 'Cargo.toml', ''))
        MATCH (src)-[:__DEPENDS_ON_PACKAGE__]->(tgt:__CARGO_CRATE__ {project_id:$p})
        RETURN src.name AS crate, collect(DISTINCT tgt.name)[..10] AS deps
        ORDER BY crate
        LIMIT $limit
        """),
        p=project_id,
        dir=dir_prefix,
        limit=limit,
        op="cargo_directory_dependencies_outbound",
    )
    inbound = await graph_core._execute_read(
        session,
        _schema_cypher("""
        MATCH (tgt:__CARGO_CRATE__ {project_id:$p})-[:__DEFINED_IN_FILE__]->(mf:__FILE__ {project_id:$p})
        WHERE $dir <> ''
          AND (mf.filepath STARTS WITH $dir OR $dir STARTS WITH replace(mf.filepath, 'Cargo.toml', ''))
        MATCH (src:__CARGO_CRATE__ {project_id:$p})-[:__DEPENDS_ON_PACKAGE__]->(tgt)
        RETURN tgt.name AS crate, collect(DISTINCT src.name)[..10] AS dependents
        ORDER BY crate
        LIMIT $limit
        """),
        p=project_id,
        dir=dir_prefix,
        limit=limit,
        op="cargo_directory_dependencies_inbound",
    )
    return outbound, inbound


async def get_directory_snapshot_impl(*, driver, neo4j_db: str, workspace_id: str, directory_path: str, limit: int = 5) -> str:
    project_id = get_project_id(workspace_id)
    project_path = get_workspace_path(workspace_id)
    dir_prefix = directory_path.strip("./")
    if dir_prefix:
        dir_prefix += "/"
    fetch_limit = max(limit * 5, limit + 8)
    repo_linked_dependencies = _summarize_repo_linked_dependencies_for_directory(
        project_path,
        directory_path,
    )

    async with driver.session(database=neo4j_db) as session:
        r_files = await graph_core._execute_read(
            session,
            _schema_cypher("""
            MATCH (f:__FILE__ {{project_id: $p}})
            WHERE f.filepath STARTS WITH $dir
            OPTIONAL MATCH (f)-[:__CONTAINS__]->(s)
            WHERE {filters}
            WITH f.filepath AS fp, count(s) AS sym_count, collect(s.name)[..3] AS samples
            ORDER BY sym_count DESC
            RETURN fp, sym_count, samples
            LIMIT $limit
        """).format(filters=_SYMBOL_FILTER_CYPHER),
            p=project_id,
            dir=dir_prefix,
            limit=fetch_limit,
            op="get_directory_snapshot_files",
        )
        r_inbound = await graph_core._execute_read(
            session,
            _schema_cypher("""
            MATCH (ext:__FILE__ {project_id: $p})-[:__IMPORTS__]->(inner:__FILE__ {project_id: $p})
            WHERE inner.filepath STARTS WITH $dir
              AND NOT ext.filepath STARTS WITH $dir
            RETURN ext.filepath AS caller, count(DISTINCT inner) AS n_imports
            ORDER BY n_imports DESC
            LIMIT $limit
        """),
            p=project_id,
            dir=dir_prefix,
            limit=fetch_limit,
            op="get_directory_snapshot_inbound",
        )
        r_outbound = await graph_core._execute_read(
            session,
            _schema_cypher("""
            MATCH (inner:__FILE__ {project_id: $p})-[:__IMPORTS__]->(ext:__FILE__ {project_id: $p})
            WHERE inner.filepath STARTS WITH $dir
              AND NOT ext.filepath STARTS WITH $dir
            RETURN ext.filepath AS dependency, count(DISTINCT inner) AS n_usages
            ORDER BY n_usages DESC
            LIMIT $limit
        """),
            p=project_id,
            dir=dir_prefix,
            limit=fetch_limit,
            op="get_directory_snapshot_outbound",
        )
        r_inbound = [{**rec, "signal": "import"} for rec in (r_inbound or [])]
        r_outbound = [{**rec, "signal": "import"} for rec in (r_outbound or [])]
        if not r_inbound:
            r_inbound = await graph_core._execute_read(
                session,
                _schema_cypher("""
                MATCH (ext:__FILE__ {project_id: $p})-[r:__CALLS_FILE__|__FILE_GRAPH_LINK__]->(inner:__FILE__ {project_id: $p})
                WHERE inner.filepath STARTS WITH $dir
                  AND NOT ext.filepath STARTS WITH $dir
                RETURN ext.filepath AS caller, count(DISTINCT inner) AS n_imports
                ORDER BY n_imports DESC
                LIMIT $limit
            """),
                p=project_id,
                dir=dir_prefix,
                limit=fetch_limit,
                op="get_directory_snapshot_inbound_file_graph_fallback",
            )
            r_inbound = [{**rec, "signal": "file_graph"} for rec in (r_inbound or [])]
        swift_file_rows = [rec for rec in (r_files or []) if str(rec.get("fp") or "").endswith(".swift")]
        if swift_file_rows:
            swift_inbound = await graph_core._execute_read(
                session,
                _schema_cypher("""
                MATCH (ext:__FILE__ {project_id: $p})-[:__CONTAINS__]->(caller)
                MATCH (caller)-[:__CALLS__|__CALLS_INFERRED__]->(callee)
                MATCH (inner:__FILE__ {project_id: $p})-[:__CONTAINS__]->(callee)
                WHERE inner.filepath STARTS WITH $dir
                  AND NOT ext.filepath STARTS WITH $dir
                RETURN ext.filepath AS caller, count(DISTINCT inner) AS n_imports
                ORDER BY n_imports DESC
                LIMIT $limit
                """),
                p=project_id,
                dir=dir_prefix,
                limit=fetch_limit,
                op="get_directory_snapshot_inbound_symbol_call_fallback",
            )
            swift_inbound = [{**rec, "signal": "symbol_call"} for rec in (swift_inbound or [])]
            r_inbound = _merge_directory_snapshot_rows(
                r_inbound,
                swift_inbound,
                path_key="caller",
                count_key="n_imports",
            )
        if not r_outbound:
            r_outbound = await graph_core._execute_read(
                session,
                _schema_cypher("""
                MATCH (inner:__FILE__ {project_id: $p})-[r:__CALLS_FILE__|__FILE_GRAPH_LINK__]->(ext:__FILE__ {project_id: $p})
                WHERE inner.filepath STARTS WITH $dir
                  AND NOT ext.filepath STARTS WITH $dir
                RETURN ext.filepath AS dependency, count(DISTINCT inner) AS n_usages
                ORDER BY n_usages DESC
                LIMIT $limit
            """),
                p=project_id,
                dir=dir_prefix,
                limit=fetch_limit,
                op="get_directory_snapshot_outbound_file_graph_fallback",
            )
            r_outbound = [{**rec, "signal": "file_graph"} for rec in (r_outbound or [])]
        if swift_file_rows:
            swift_outbound = await graph_core._execute_read(
                session,
                _schema_cypher("""
                MATCH (inner:__FILE__ {project_id: $p})-[:__CONTAINS__]->(caller)
                MATCH (caller)-[:__CALLS__|__CALLS_INFERRED__]->(callee)
                MATCH (ext:__FILE__ {project_id: $p})-[:__CONTAINS__]->(callee)
                WHERE inner.filepath STARTS WITH $dir
                  AND NOT ext.filepath STARTS WITH $dir
                RETURN ext.filepath AS dependency, count(DISTINCT inner) AS n_usages
                ORDER BY n_usages DESC
                LIMIT $limit
                """),
                p=project_id,
                dir=dir_prefix,
                limit=fetch_limit,
                op="get_directory_snapshot_outbound_symbol_call_fallback",
            )
            swift_outbound = [{**rec, "signal": "symbol_call"} for rec in (swift_outbound or [])]
            r_outbound = _merge_directory_snapshot_rows(
                r_outbound,
                swift_outbound,
                path_key="dependency",
                count_key="n_usages",
            )
        r_assets = await graph_core._execute_read(
            session,
            _schema_cypher("""
            MATCH (f:__FILE__ {project_id: $p})
            WHERE f.filepath STARTS WITH $dir
            MATCH (f)-[r:__ASSET_LINKS__|__CALLS_API__]->(target:__FILE__ {project_id: $p})
            RETURN f.filepath AS source, type(r) AS rel, target.filepath AS target
            ORDER BY source, rel
            LIMIT $limit
        """),
            p=project_id,
            dir=dir_prefix,
            limit=fetch_limit * 3,
            op="get_directory_snapshot_assets",
        )
        if await has_apple_build_context(session, project_id):
            r_apple_targets, r_apple_schemes, r_apple_workspaces = await load_apple_build_context(
                session, project_id, dir_prefix=dir_prefix, limit=limit
            )
        else:
            r_apple_targets, r_apple_schemes, r_apple_workspaces = [], [], []
        if await has_cargo_build_context(session, project_id):
            r_cargo_crates, r_cargo_workspaces, r_cargo_dependencies = await load_cargo_build_context(
                session, project_id, dir_prefix=dir_prefix, limit=limit
            )
            r_cargo_dep_out, r_cargo_dep_in = await load_cargo_directory_dependencies(
                session, project_id, dir_prefix=dir_prefix, limit=limit
            )
        else:
            r_cargo_crates, r_cargo_workspaces, r_cargo_dependencies = [], [], []
            r_cargo_dep_out, r_cargo_dep_in = [], []

        local_symbol_rows = await graph_core._execute_read(
            session,
            _schema_cypher("""
            MATCH (f:__FILE__ {project_id: $p})-[:__CONTAINS__]->(s)
            WHERE f.filepath STARTS WITH $dir
              AND s.name IS NOT NULL
              AND __FILTERS__
            RETURN s.name AS name
            LIMIT 20
        """).replace("__FILTERS__", _SYMBOL_FILTER_CYPHER),
            p=project_id,
            dir=dir_prefix,
            op="get_directory_snapshot_local_symbols",
        )
        external_symbol_rows = await graph_core._execute_read(
            session,
            _schema_cypher("""
            MATCH (ext:__FILE__ {project_id: $p})-[:__CONTAINS__]->(s)
            WHERE NOT ext.filepath STARTS WITH $dir
              AND s.name IS NOT NULL
              AND __FILTERS__
            RETURN ext.filepath AS filepath, s.name AS symbol
            LIMIT 400
        """).replace("__FILTERS__", _SYMBOL_FILTER_CYPHER),
            p=project_id,
            dir=dir_prefix,
            op="get_directory_snapshot_external_symbols",
        )

    local_symbols = {
        str(rec.get("name") or "").strip()
        for rec in (local_symbol_rows or [])
        if str(rec.get("name") or "").strip()
    }

    if (not r_inbound or not r_outbound) and project_path:
        memory_store, _, _, _, _ = get_memory_modules()
        await memory_store.open_pool()
        async with memory_store._pg_pool.connection() as conn:
            async with conn.cursor() as cur:
                if not r_inbound and local_symbols:
                    ors = " OR ".join(["content ILIKE %s"] * len(local_symbols))
                    await cur.execute(
                        (
                            "SELECT file_path, count(*) AS hits "
                            "FROM codebase_embeddings "
                            "WHERE project_id = %s "
                            "  AND file_path NOT LIKE %s "
                            "  AND (" + ors + ") "
                            "GROUP BY file_path "
                            "ORDER BY hits DESC, file_path "
                            "LIMIT %s"
                        ),
                        [project_id, f"{dir_prefix}%"] + [f"%{symbol}%" for symbol in sorted(local_symbols)] + [limit],
                    )
                    rows = await cur.fetchall()
                    r_inbound = [
                        {"caller": fp, "n_imports": hits, "signal": "semantic"}
                        for fp, hits in rows
                        if not _is_low_signal_semantic_path(fp)
                    ]

                if not r_outbound and any((rec.get("fp") or "").endswith(".swift") for rec in (r_files or [])):
                    top_local_files = [rec.get("fp") for rec in (r_files or [])[: min(limit, 4)] if rec.get("fp")]
                    external_symbol_map: dict[str, set[str]] = {}
                    for rec in external_symbol_rows or []:
                        fp = str(rec.get("filepath") or "")
                        symbol = str(rec.get("symbol") or "").strip()
                        if not fp or not symbol or _is_low_signal_semantic_path(fp):
                            continue
                        external_symbol_map.setdefault(fp, set()).add(symbol)

                    scored: list[tuple[int, str]] = []
                    local_texts: list[str] = []
                    for fp in top_local_files:
                        await cur.execute(
                            """
                            SELECT content
                            FROM codebase_embeddings
                            WHERE project_id = %s AND file_path = %s
                            ORDER BY chunk_index
                            LIMIT 2
                            """,
                            (project_id, fp),
                        )
                        rows = await cur.fetchall()
                        local_texts.extend(str(row[0] or "") for row in rows)
                    combined_text = "\n".join(local_texts)
                    candidate_mentions = set(_extract_swift_type_mentions(combined_text, ignore=local_symbols))
                    for fp, symbols in external_symbol_map.items():
                        overlap = candidate_mentions & symbols
                        if overlap:
                            scored.append((len(overlap), fp))
                    scored.sort(key=lambda item: (-item[0], item[1]))
                    r_outbound = [
                        {"dependency": fp, "n_usages": score, "signal": "semantic"}
                        for score, fp in scored[:limit]
                    ]

    memory_store, _, _, _, _ = get_memory_modules()
    await memory_store.open_pool()
    file_role_paths = {
        str(rec.get("fp") or "").strip()
        for rec in (r_files or [])
        if str(rec.get("fp") or "").strip()
    }
    file_role_paths.update(
        str(rec.get("caller") or "").strip()
        for rec in (r_inbound or [])
        if str(rec.get("caller") or "").strip()
    )
    file_role_paths.update(
        str(rec.get("dependency") or "").strip()
        for rec in (r_outbound or [])
        if str(rec.get("dependency") or "").strip()
    )
    async with memory_store._pg_pool.connection() as conn:
        file_roles_by_path = await _load_semantic_file_roles(conn, project_id, sorted(file_role_paths))

    r_inbound = _rank_directory_snapshot_rows(
        r_inbound,
        path_key="caller",
        count_key="n_imports",
        limit=limit,
        directory_path=directory_path,
        file_roles_by_path=file_roles_by_path,
    )
    r_outbound = _rank_directory_snapshot_rows(
        r_outbound,
        path_key="dependency",
        count_key="n_usages",
        limit=limit,
        directory_path=directory_path,
        file_roles_by_path=file_roles_by_path,
    )
    r_files = sorted(
        r_files or [],
        key=lambda rec: _directory_snapshot_file_rank(
            rec.get("fp"),
            int(rec.get("sym_count") or 0),
            file_roles_by_path.get(str(rec.get("fp") or "").strip()),
        ),
    )

    r_files = _directory_snapshot_display_files(
        r_files,
        directory_path=directory_path,
        file_roles_by_path=file_roles_by_path,
    )[: max(1, limit)]

    lines = [f"# Directory Snapshot: `{directory_path or '.'}/`"]
    if not r_files:
        return f"No indexed files found in `{directory_path}`."
    lines.append("")
    lines.append("Use this to land in one directory and decide what to inspect first.")

    priority_lines = _directory_snapshot_priority_lines(
        directory_path,
        r_files,
        r_inbound,
        r_outbound,
        r_assets,
        bool(r_apple_targets or r_apple_schemes or r_apple_workspaces),
        bool(r_cargo_crates or r_cargo_workspaces or r_cargo_dependencies or r_cargo_dep_out or r_cargo_dep_in),
        r_apple_workspaces,
        repo_linked_dependencies,
        file_roles_by_path=file_roles_by_path,
    )
    if priority_lines:
        lines.append("")
        lines.append("### Recommended Inspection Order")
        lines.extend(priority_lines)

    r_inbound = _rank_directory_snapshot_rows(
        r_inbound,
        path_key="caller",
        count_key="n_imports",
        limit=limit,
        directory_path=directory_path,
        file_roles_by_path=file_roles_by_path,
    )
    r_inbound = _directory_snapshot_display_rows(
        r_inbound,
        path_key="caller",
        directory_path=directory_path,
        file_roles_by_path=file_roles_by_path,
    )
    r_outbound = _rank_directory_snapshot_rows(
        r_outbound,
        path_key="dependency",
        count_key="n_usages",
        limit=limit,
        directory_path=directory_path,
        file_roles_by_path=file_roles_by_path,
    )
    r_outbound = _directory_snapshot_display_rows(
        r_outbound,
        path_key="dependency",
        directory_path=directory_path,
        file_roles_by_path=file_roles_by_path,
    )

    lines.append("\n### 🏆 Top Files (by symbol density)")
    for rec in r_files:
        samples = ", ".join(rec["samples"])
        lines.append(f"- **{rec['fp']}** ({rec['sym_count']} symbols: {samples})")

    if r_assets:
        lines.append("\n### 🎨 UI & Assets (Linkages & Wiring)")
        for rec in r_assets:
            source = rec["source"]
            target = rec["target"]
            rel = rec["rel"]
            if rel == REL_ASSET_LINKS:
                lines.append(f"- `{source}` -> 📦 `{target}` (Asset/Style)")
            elif rel == REL_CALLS_API:
                lines.append(f"- `{source}` -> 🔌 `{target}` (API Endpoint)")

    if r_apple_targets or r_apple_schemes or r_apple_workspaces:
        lines.append("\n### 🍎 Apple Build Context")
        for rec in r_apple_targets:
            lines.append(
                f"- target `{rec['target']}` bundles {rec['bundled_files']} file(s)"
                + (f" via `{rec['project_file']}`" if rec.get("project_file") else "")
            )
        for rec in r_apple_schemes:
            targets = ", ".join(rec.get("targets") or [])
            lines.append(f"- scheme `{rec['scheme']}` builds {targets}")
        for rec in r_apple_workspaces:
            projects = ", ".join(rec.get("projects") or [])
            lines.append(f"- workspace `{rec['workspace']}` references {projects}")

    if r_cargo_crates or r_cargo_workspaces or r_cargo_dependencies:
        lines.append("\n### 🦀 Cargo Context")
        for rec in r_cargo_crates:
            manifest = rec.get("manifest_path") or "(external crate)"
            crate_name = rec.get("crate_name") or rec["crate"]
            lines.append(f"- crate `{rec['crate']}` ({crate_name}) via `{manifest}`")
        for rec in r_cargo_workspaces:
            crates = ", ".join(rec.get("crates") or [])
            lines.append(f"- workspace `{rec['workspace']}` includes {crates}")
        for rec in r_cargo_dependencies:
            deps = ", ".join(rec.get("deps") or [])
            lines.append(f"- crate `{rec['crate']}` depends on {deps}")
        for rec in r_cargo_dep_out:
            deps = ", ".join(rec.get("deps") or [])
            lines.append(f"- local crate `{rec['crate']}` depends on {deps}")
        for rec in r_cargo_dep_in:
            dependents = ", ".join(rec.get("dependents") or [])
            lines.append(f"- local crate `{rec['crate']}` is used by {dependents}")

    if repo_linked_dependencies:
        lines.append("\n### 🔗 Repo-Linked Dependencies")
        lines.extend(repo_linked_dependencies)

    if r_inbound:
        lines.append("\n### 📥 Consumers (External files importing from here)")
        for rec in r_inbound:
            caller_path = str(rec.get("caller") or "").strip()
            caller_roles = file_roles_by_path.get(caller_path)
            suffix = (
                " [generated/support]"
                if _has_generated_support_surface(caller_roles)
                or _directory_snapshot_path_penalty(rec.get("caller"), caller_roles) >= 40
                else ""
            )
            lines.append(f"- `{rec['caller']}` (imports {rec['n_imports']} files){suffix}")
    else:
        lines.append("\n### 📥 Consumers: None found.")

    if r_outbound:
        lines.append("\n### 📤 Dependencies (External files imported by here)")
        for rec in r_outbound:
            dependency_path = str(rec.get("dependency") or "").strip()
            dependency_roles = file_roles_by_path.get(dependency_path)
            suffix = (
                " [generated/support]"
                if _has_generated_support_surface(dependency_roles)
                or _directory_snapshot_path_penalty(rec.get("dependency"), dependency_roles) >= 40
                else ""
            )
            lines.append(f"- `{rec['dependency']}` (used by {rec['n_usages']} files){suffix}")
    else:
        lines.append("\n### 📤 Dependencies: None found.")

    return "\n".join(lines)


async def get_project_overview_impl(*, driver, neo4j_db: str, workspace_id: str) -> str:
    project_id = get_project_id(workspace_id)
    project_path = get_workspace_path(workspace_id)
    repo_linked_dependencies = _summarize_repo_linked_dependencies(project_path)

    async with driver.session(database=neo4j_db) as session:
        r = await graph_core._execute_read(
            session,
            _schema_cypher("MATCH (f:__FILE__ {project_id:$p}) RETURN count(f) AS files"),
            p=project_id,
            op="get_project_overview_file_count",
        )
        n_files = r[0]["files"] if r else 0

        r2 = await graph_core._execute_read(
            session,
            f"MATCH (s {{project_id:$p}}) WHERE {_SYMBOL_FILTER_CYPHER} RETURN count(s) AS syms",
            p=project_id,
            op="get_project_overview_symbol_count",
        )
        n_syms = r2[0]["syms"] if r2 else 0

        r3 = await graph_core._execute_read(
            session,
            _schema_cypher("""
            MATCH (f:__FILE__ {{project_id: $p}})
            WITH f, CASE WHEN f.filepath CONTAINS '/'
                 THEN split(f.filepath, '/')[0] ELSE '(root)' END AS top_dir
            OPTIONAL MATCH (f)-[:__CONTAINS__]->(s)
            WHERE {filters}
            WITH top_dir, count(DISTINCT s) AS syms, count(DISTINCT f) AS files
            ORDER BY syms DESC LIMIT 14
            RETURN top_dir, files, syms
        """).format(filters=_SYMBOL_FILTER_CYPHER),
            p=project_id,
            op="get_project_overview_dirs",
        )
        ranked_dir_rows = sorted(
            r3,
            key=lambda rec: (
                _overview_directory_penalty(rec.get("top_dir")),
                -(int(rec.get("syms") or 0)),
                -(int(rec.get("files") or 0)),
                str(rec.get("top_dir") or ""),
            ),
        )[:6]
        dirs = [
            f"  📂 {rec['top_dir']}/  ({rec['files']} files, {rec['syms']} symbols)"
            for rec in ranked_dir_rows
        ]

        r4 = await graph_core._execute_read(
            session,
            _schema_cypher("""
            MATCH (f:__FILE__ {{project_id: $p}})-[:__CONTAINS__]->(s)
            WHERE ({filters})
              AND NOT toLower(f.filepath) CONTAINS 'test'
              AND NOT toLower(f.filepath) CONTAINS 'spec'
            WITH f.filepath AS fp, count(s) AS n, collect(DISTINCT s.name)[..3] AS ex
            ORDER BY n DESC LIMIT 20
            RETURN fp, n, ex
        """).format(filters=_SYMBOL_FILTER_CYPHER),
            p=project_id,
            op="get_project_overview_key_files",
        )
        if await has_apple_build_context(session, project_id):
            apple_targets, apple_schemes, apple_workspaces = await load_apple_build_context(
                session, project_id, limit=5
            )
        else:
            apple_targets, apple_schemes, apple_workspaces = [], [], []
        if await has_cargo_build_context(session, project_id):
            cargo_crates, cargo_workspaces, cargo_dependencies = await load_cargo_build_context(
                session, project_id, limit=5
            )
        else:
            cargo_crates, cargo_workspaces, cargo_dependencies = [], [], []

        memory_store, _, _, _, _ = get_memory_modules()
        await memory_store.open_pool()
        async with memory_store._pg_pool.connection() as conn:
            key_file_role_paths = [
                str(rec.get("fp") or "").strip()
                for rec in (r4 or [])
                if str(rec.get("fp") or "").strip()
            ]
            key_file_roles_by_path = await _load_semantic_file_roles(conn, project_id, key_file_role_paths)
            key_files = []
            ranked_key_rows = sorted(
                r4,
                key=lambda rec: (
                    _overview_file_rank(
                        rec.get("fp"),
                        int(rec.get("n") or 0),
                        key_file_roles_by_path.get(str(rec.get("fp") or "").strip()),
                    ),
                    int(rec.get("n") or 0),
                ),
                reverse=True,
            )
            filtered_key_rows = [
                rec
                for rec in ranked_key_rows
                if not _is_overview_low_signal_key_file(
                    rec.get("fp"),
                    key_file_roles_by_path.get(str(rec.get("fp") or "").strip()),
                )
            ]
            if filtered_key_rows:
                ranked_key_rows = filtered_key_rows
            ranked_key_rows = ranked_key_rows[:5]
            for rec in ranked_key_rows:
                ex = ", ".join(e for e in rec["ex"] if e)
                key_files.append(f"  - {rec['fp']}  ({rec['n']} symbols: {ex})")
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT count(*) FROM codebase_embeddings WHERE project_id=%s",
                    (project_id,),
                )
                row = await cur.fetchone()
            n_chunks = row[0] if row else 0

    lines = (
        [
            f"# Project Overview: {project_path.split('/')[-1]}",
            "",
            "## Health",
            f"  Files indexed:   {n_files}",
            f"  Symbols:         {n_syms}",
            "  Symbol kinds:    Function, Class, Struct, Trait, Enum",
            f"  Semantic chunks: {n_chunks}",
            "",
            "## Architecture (top-level directories by symbol density)",
        ]
        + dirs
        + [
            "",
            "## Key Files (most symbol-dense, non-test)",
        ]
        + key_files
        + [
            "",
            "## Next steps",
            f"  - search_codebase('{project_path}', '<your question>') — semantic search",
            f"  - describe_file('{project_path}', '<file_path>') — deep-dive a specific file",
            f"  - get_code_importance('{project_path}') — full ranked file list",
        ]
    )
    if apple_targets or apple_schemes or apple_workspaces:
        lines.extend(["", "## Apple Build Context"])
        for rec in apple_targets:
            lines.append(
                f"  - target `{rec['target']}` bundles {rec['bundled_files']} file(s)"
                + (f" via `{rec['project_file']}`" if rec.get("project_file") else "")
            )
        for rec in apple_schemes:
            targets = ", ".join(rec.get("targets") or [])
            lines.append(f"  - scheme `{rec['scheme']}` builds {targets}")
        for rec in apple_workspaces:
            projects = ", ".join(rec.get("projects") or [])
            lines.append(f"  - workspace `{rec['workspace']}` references {projects}")
    if cargo_crates or cargo_workspaces or cargo_dependencies:
        lines.extend(["", "## Cargo Workspace Context"])
        for rec in cargo_crates:
            manifest = rec.get("manifest_path") or "(external crate)"
            crate_name = rec.get("crate_name") or rec["crate"]
            lines.append(f"  - crate `{rec['crate']}` ({crate_name}) via `{manifest}`")
        for rec in cargo_workspaces:
            crates = ", ".join(rec.get("crates") or [])
            lines.append(f"  - workspace `{rec['workspace']}` includes {crates}")
        for rec in cargo_dependencies:
            deps = ", ".join(rec.get("deps") or [])
            lines.append(f"  - crate `{rec['crate']}` depends on {deps}")
        if repo_linked_dependencies:
            lines.extend(["", "## Repo-Linked Dependencies"])
            lines.extend(repo_linked_dependencies)
    priority_lines = _project_overview_priority_lines(
        project_path=project_path,
        dirs=ranked_dir_rows,
        key_files=key_files,
        apple_targets=apple_targets,
        cargo_crates=cargo_crates,
        repo_linked_dependencies=repo_linked_dependencies,
    )
    if priority_lines:
        insert_at = lines.index("## Architecture (top-level directories by symbol density)")
        lines[insert_at:insert_at] = ["## Inspect First", *priority_lines, ""]
    return "\n".join(lines)


async def get_repo_dependency_summary_impl(*, workspace_id: str) -> str:
    project_path = get_workspace_path(workspace_id)
    linked = _parse_repo_linked_dependencies(project_path)
    if not linked:
        return f"No repo-linked editable/path dependencies found for `{project_path}`."

    indexed_paths = _load_indexed_project_paths()
    lines = [f"# Repo Dependency Summary: `{os.path.basename(project_path.rstrip(os.sep))}`"]
    lines.append("")
    lines.append("Use this to spot cross-repo integration boundaries and likely breakage points first.")
    priority_lines = _repo_dependency_priority_lines(linked, indexed_paths, project_path)
    if priority_lines:
        lines.append("")
        lines.append("## Inspect First")
        lines.extend(priority_lines)
    for dep in linked:
        package = dep.get("package") or "(unknown)"
        repo_name = dep.get("repo_name") or package
        repo_path = indexed_paths.get(repo_name, "")
        evidence = _find_repo_link_evidence(project_path, package, limit=8)
        lines.append("")
        line = f"- package `{package}` from repo `{repo_name}`"
        if repo_path:
            line += f" → local indexed repo `{repo_path}`"
        lines.append(line)
        if dep.get("subdirectory"):
            lines.append(f"- binding subdirectory: `{dep['subdirectory']}`")
        if dep.get("rev"):
            lines.append(f"- pinned revision: `{dep['rev']}`")
        if dep.get("repo_url"):
            lines.append(f"- source URL: `{dep['repo_url']}`")
        if evidence:
            lines.append(f"- imported by: {', '.join(f'`{item}`' for item in evidence)}")
        else:
            lines.append("- imported by: no direct Python import sites found")
    return "\n".join(lines)
