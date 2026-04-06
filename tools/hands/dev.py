"""tools/dev.py — developer workflow tools (git, grep, test discovery, linting)."""

import os
import sys
from typing import List
from neo4j import unit_of_work
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules, get_project_id, normalize_neo4j_path, get_workspace_path


def register(mcp: FastMCP) -> None:

    _TX_TIMEOUT = int(os.getenv("LM_PROXY_NEO4J_TX_TIMEOUT", "30"))
    _TX_OP_PREFIX = os.getenv("LM_PROXY_NEO4J_OP_PREFIX", "").strip()
    _TX_METADATA_BASE = {"source": "lm_proxy", "tool": "dev"}

    def _which(name: str) -> str | None:
        """Robust binary discovery across common paths and environments."""
        import shutil
        hit = shutil.which(name)
        if hit:
            return hit
        # Try alongside the current Python interpreter (conda/venv)
        py_bin = os.path.dirname(sys.executable)
        candidate = os.path.join(py_bin, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        # Common brew/system paths for Mac/Linux
        for p in ["/opt/homebrew/bin/" + name, "/usr/local/bin/" + name, "/usr/bin/" + name]:
            if os.path.exists(p):
                return p
        return None

    async def _execute_read(session, cypher: str, op: str | None = None, **params):
        metadata = dict(_TX_METADATA_BASE)
        op_value = op or "read"
        if _TX_OP_PREFIX:
            op_value = f"{_TX_OP_PREFIX}.{op_value}"
        metadata["op"] = op_value

        @unit_of_work(timeout=_TX_TIMEOUT, metadata=metadata)
        async def _tx(tx):
            result = await tx.run(cypher, **params)
            return await result.data()

        if hasattr(session, "execute_read"):
            return await session.execute_read(_tx)
        return await _tx(session)

    @mcp.tool()
    async def list_dir(DirectoryPath: str) -> str:
        """
        List the contents of a directory.
        """
        path = get_workspace_path(DirectoryPath)
        try:
            return "\n".join(os.listdir(path))
        except Exception as e:
            return f"Error listing directory: {str(e)}"

    @mcp.tool()
    async def git_summary(workspace_id: str) -> str:
        """
        Show the current git state of a project: recent commits, working-tree
        status, and a diff stat of any uncommitted changes.

        Use this at the start of a session to understand what has changed recently,
        or before making edits to confirm the branch and working-tree state.

        Args:
            workspace_id: The logical workspace ID or absolute path to the project root.
        """
        project_path = get_workspace_path(workspace_id)
        try:
            import subprocess

            def git(args: list[str]) -> str:
                r = subprocess.run(
                    ["git"] + args,
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                return r.stdout.strip()

            branch = git(["rev-parse", "--abbrev-ref", "HEAD"])
            status = git(["status", "--short"])
            log = git(["log", "--oneline", "-12"])
            diff_stat = git(["diff", "--stat", "HEAD"])

            parts = [f"### Git Summary: `{project_path}`", f"**Branch:** `{branch}`\n"]
            if status:
                parts += ["**Working-tree status:**\n```", status, "```\n"]
            else:
                parts.append("**Working-tree status:** clean ✅\n")
            if diff_stat:
                parts += ["**Uncommitted diff (--stat):**\n```", diff_stat, "```\n"]
            if log:
                parts += ["**Recent commits:**\n```", log, "```"]
            return "\n".join(parts)
        except Exception as e:
            return f"Error running git: {str(e)}"

    @mcp.tool()
    async def grep_codebase(
        workspace_id: str, pattern: str, file_glob: str = ""
    ) -> str:
        """
        Search for a literal string or regex pattern across the entire codebase
        using ripgrep (rg). Faster and more precise than semantic search for
        exact tokens: error messages, config keys, SQL fragments, symbol names.

        Use when you need exact-text matches rather than semantic similarity.
        Complements search_codebase (semantic) and find_references (graph+pg).

        Args:
            workspace_id: The logical workspace ID or absolute path to the project root.
            pattern:      Literal string or regex to search for.
            file_glob:    Optional glob to restrict files, e.g. '*.py' or '*.rs'.
                          Leave empty to search all non-ignored files.
        """
        import subprocess
        from collections import defaultdict
        project_path = get_workspace_path(workspace_id)

        try:
            rg = _which("rg") or "rg"
            cmd = [
                rg,
                "--line-number",
                "--no-heading",
                "--color=never",
                "--max-count=3",
                "--max-filesize=500K",
                "-e",
                pattern,
            ]
            if file_glob:
                cmd += ["--glob", file_glob]
            cmd.append(".")

            r = subprocess.run(
                cmd, cwd=project_path, capture_output=True, text=True, timeout=15
            )
            lines = r.stdout.strip().splitlines()
            if not lines:
                extra = f" in `{file_glob}`" if file_glob else ""
                return f"No matches for `{pattern}`{extra}."

            by_file: dict = defaultdict(list)
            for line in lines[:80]:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    by_file[parts[0]].append(f"  L{parts[1]}: {parts[2].strip()}")

            out = [f"## `{pattern}` — {len(by_file)} file(s)\n"]
            for fp, hits in sorted(by_file.items()):
                out.append(f"**{fp}**")
                out.extend(hits)
            return "\n".join(out)
        except Exception as e:
            return f"Error running grep: {str(e)}"

    @mcp.tool()
    async def get_test_coverage_for(workspace_id: str, file_path: str) -> str:
        """
        Find test files that cover a given source file.

        Uses three strategies:
        1. Name convention  — e.g. 'foo.py' → 'test_foo.py', 'foo_test.py'
        2. Import graph     — files in the Neo4j graph that IMPORT this file
        3. Directory scan   — ripgrep for the source basename inside test files

        Run this before modifying a file to know exactly what to test afterwards.

        Args:
            workspace_id: The logical workspace ID or absolute path to the project root.
            file_path:    Relative path to the source file within the project.
        """
        project_path = get_workspace_path(workspace_id)
        try:
            import hashlib, subprocess

            project_id = get_project_id(workspace_id)
            basename = os.path.splitext(os.path.basename(file_path))[0]
            results: dict[str, str] = {}

            candidates = [
                f"test_{basename}.py",
                f"{basename}_test.py",
                f"test_{basename}.ts",
                f"{basename}.test.ts",
                f"test_{basename}.rs",
                f"{basename}_test.rs",
                f"test_{basename}.go",
                f"{basename}_test.go",
            ]
            find_args = ["-type", "f", "("]
            for i, c in enumerate(candidates):
                if i > 0:
                    find_args.append("-o")
                find_args += ["-name", c]
            find_args.append(")")
            r = subprocess.run(
                ["find", project_path] + find_args,
                capture_output=True,
                text=True,
                timeout=10,
            )
            for p in r.stdout.strip().splitlines():
                rel = os.path.relpath(p, project_path)
                results[rel] = "name convention"

            try:
                import graph_bootstrap

                driver = await graph_bootstrap.require_driver()
                async with driver.session(
                    database=graph_bootstrap._NEO4J_DB
                ) as session:
                    res = await _execute_read(
                        """
                        MATCH (src:File {project_id: $pid})
                        WHERE src.filepath ENDS WITH $fp
                        OPTIONAL MATCH (tester:File)-[:IMPORTS]->(src)
                        OPTIONAL MATCH (src)-[:CONTAINS]->(sym:Node)
                        OPTIONAL MATCH (tester2:File)-[:IMPORTS_SYMBOL]->(sym)
                        RETURN tester.filepath AS tf, tester2.filepath AS tf2
                        LIMIT 50
                        """,
                        pid=project_id,
                        fp=normalize_neo4j_path(file_path),
                        op="get_test_coverage_for",
                    )
                    for rec in res:
                        tf = rec.get("tf")
                        tf2 = rec.get("tf2")
                        for path, reason in (
                            (tf, "imports this file"),
                            (tf2, "imports symbol"),
                        ):
                            if path:
                                rel = (
                                    os.path.relpath(path, project_path)
                                    if os.path.isabs(path)
                                    else path
                                )
                                results.setdefault(rel, reason)
            except Exception:
                pass

            try:
                rg_r = subprocess.run(
                    [
                        "rg",
                        "--files-with-matches",
                        "--glob",
                        "*test*",
                        "-e",
                        basename,
                        ".",
                    ],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                for p in rg_r.stdout.strip().splitlines():
                    rel = os.path.relpath(os.path.join(project_path, p), project_path)
                    results.setdefault(rel, "mentions basename")
            except Exception:
                pass

            if not results:
                return (
                    f"No test files found for `{file_path}`.\n"
                    "Either no tests exist yet or the project is not indexed."
                )
            out = [f"## Tests covering `{file_path}`\n"]
            for rel, reason in sorted(results.items()):
                out.append(f"- `{rel}`  ← {reason}")
            return "\n".join(out)
        except Exception as e:
            return f"Error finding tests: {str(e)}"

    @mcp.tool()
    async def get_changed_symbols(workspace_id: str, since: str = "HEAD~1") -> str:
        """
        List which functions and classes changed between the current working tree
        and a commit reference — not just which files, but which *symbols*.

        Runs `git diff <since>` and extracts function/class definition lines
        from modified hunks. Supports Python, Rust, TypeScript, Go, Swift, Ruby.

        Args:
            workspace_id: The logical workspace ID or absolute path to the project root.
            since:        Git ref to diff against (default 'HEAD~1' = last commit).
                          Examples: 'HEAD', 'main', 'abc1234', 'HEAD~3'.
        """
        project_path = get_workspace_path(workspace_id)
        try:
            import subprocess, re

            r = subprocess.run(
                ["git", "diff", "--unified=4", since],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=15,
            )
            diff = r.stdout
            if not diff.strip():
                return f"No changes vs `{since}`. Working tree is clean."

            file_re = re.compile(r"^diff --git a/.+ b/(.+)$")
            def_re = re.compile(
                r"^\+[ \t]*(?:pub (?:async )?)?(?:"
                r"def |async def |fn |class |struct |impl |trait |enum |"
                r"func |function |"
                r"(?:public|private|internal|open|final) (?:class|struct|func|enum|protocol)|"
                r"(?:export )?(?:default )?(?:async )?function "
                r")"
                r"([A-Za-z_][A-Za-z0-9_<>]*)"
            )

            current_file = ""
            changed: dict[str, set] = {}
            all_files: set[str] = set()

            for line in diff.splitlines():
                m = file_re.match(line)
                if m:
                    current_file = m.group(1)
                    all_files.add(current_file)
                    continue
                dm = def_re.match(line)
                if dm and current_file:
                    changed.setdefault(current_file, set()).add(dm.group(1))

            unnamed = all_files - set(changed)
            out = [f"## Changed symbols vs `{since}`\n"]
            if changed:
                for fp in sorted(changed):
                    syms = ", ".join(f"`{s}`" for s in sorted(changed[fp]))
                    out.append(f"**{fp}** — {syms}")
            if unnamed:
                out.append("\n**Files changed (no symbol-level detection):**")
                for fp in sorted(unnamed):
                    out.append(f"  - {fp}")
            return "\n".join(out)
        except Exception as e:
            return f"Error diffing symbols: {str(e)}"

    @mcp.tool()
    async def lint_project_subset(workspace_id: str, relative_paths: List[str]) -> str:
        """
        Run best-available linter on a set of files within a workspace.
        Supports Swift (swiftlint) and Python (pylint/ruff).

        Args:
            workspace_id:   The logical workspace ID or absolute path to the project root.
            relative_paths: List of relative paths to files to lint.
        """
        import subprocess
        import shutil
        import sys

        project_path = get_workspace_path(workspace_id)
        results = []

        ruff = _which("ruff")
        pylint = _which("pylint")
        swiftlint = _which("swiftlint")
        for rel_f in relative_paths:
            f = os.path.join(project_path, rel_f)
            if not os.path.exists(f):
                results.append(f"File not found: {rel_f}")
                continue

            if f.endswith(".swift"):
                if swiftlint:
                    res = subprocess.run(
                        [swiftlint, "lint", f], capture_output=True, text=True
                    )
                    results.append(
                        f"--- SwiftLint: {rel_f} ---\n{res.stdout or 'No issues found.'}"
                    )
                else:
                    results.append(f"SwiftLint not found. Skipping {rel_f}.")
            elif f.endswith(".py"):
                linter = ruff or pylint
                if linter:
                    cmd = (
                        [linter, "check", f]
                        if "ruff" in linter
                        else [linter, "--errors-only", f]
                    )
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    results.append(
                        f"--- Python Linter ({os.path.basename(linter)}): {rel_f} ---\n{res.stdout or 'No issues found.'}"
                    )
                else:
                    results.append(
                        f"Python linter (ruff/pylint) not found. Skipping {rel_f}."
                    )
        return (
            "\n\n".join(results)
            if results
            else "No supported files provided or no linters found."
        )

    @mcp.tool()
    async def swift_doc_lookup(workspace_id: str, file_path: str, symbol_name: str) -> str:
        """
        Extract documentation comments for a Swift symbol using SourceKitten.

        Args:
            workspace_id: The logical workspace ID or absolute path to the project root.
            file_path:    Relative path to the Swift file within the project.
            symbol_name:  Name of the symbol to lookup.
        """
        import subprocess
        import json
        import shutil
        import sys

        project_path = get_workspace_path(workspace_id)
        if not os.path.isabs(file_path):
            file_path = os.path.join(project_path, file_path)

        if not os.path.exists(file_path):
            return f"File not found: {file_path}"

        # Prefer SourceKitten — gives full AST including doc_comment fields
        sk = shutil.which("sourcekitten") or os.path.join(
            "/opt/homebrew/bin", "sourcekitten"
        )
        if sk and os.path.isfile(sk):
            try:
                res = subprocess.run(
                    [sk, "structure", "--file", file_path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if res.returncode == 0:
                    data = json.loads(res.stdout)

                    def _find(items: list, name: str) -> dict | None:
                        for item in items:
                            item_name = item.get("key.name") or ""
                            # Match exact or prefix — SourceKitten suffixes methods
                            # with their argument labels: 'foo(bar:baz:)'
                            if item_name == name or item_name.startswith(name + "("):
                                return item
                            sub = _find(item.get("key.substructure") or [], name)
                            if sub:
                                return sub
                        return None

                    node = _find(data.get("key.substructure") or [], symbol_name)
                    if node:
                        # Compute line numbers from SourceKitten byte offsets
                        try:
                            with open(file_path, "rb") as fh:
                                raw = fh.read()
                            offset = node.get("key.offset", 0) or 0
                            end_offset = offset + (node.get("key.length", 0) or 0)
                            start_line = raw[:offset].count(b"\n") + 1
                            end_line = raw[:end_offset].count(b"\n") + 1
                        except Exception:
                            start_line, end_line = "?", "?"
                        lines = [
                            f"**{symbol_name}** ({node.get('key.kind', '?').split('.')[-1]})",
                            f"File: `{file_path}`  L{start_line}–{end_line}",
                        ]
                        doc = node.get("key.doc.comment") or node.get(
                            "key.annotated_decl", ""
                        )
                        if doc:
                            lines += ["", doc.strip()]
                        return "\n".join(lines)
                    return f"Symbol `{symbol_name}` not found in `{os.path.basename(file_path)}`."
            except Exception as e:
                pass  # fall through to ts_pack

        # Fallback: ts_pack structural extraction
        try:
            import memory.skeleton_extractor as skeleton_extractor

            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()
            doc = skeleton_extractor.get_swift_docs(code, symbol_name)
            return doc if doc else f"No documentation found for '{symbol_name}'."
        except Exception as e:
            return f"Error looking up docs: {str(e)}"

    @mcp.tool()
    async def extract_function_body(workspace_id: str, file_path: str, symbol_name: str) -> str:
        """
        Extract the exact source code of a function, method, or class using
        tree-sitter AST. No Neo4j required — works on any file.

        More precise than reading line ranges manually.

        Args:
            workspace_id: The logical workspace ID or absolute path to the project root.
            file_path:    Relative path to the source file within the project.
            symbol_name:  Name of the function, method, or class to extract.
        """
        try:
            import tree_sitter_language_pack as ts_pack

            project_path = get_workspace_path(workspace_id)
            orig_file_path = file_path
            if not os.path.isabs(file_path):
                file_path = os.path.join(project_path, file_path)

            if not os.path.exists(file_path):
                return f"File not found: {file_path}"
            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                code = fh.read()
            lines_list = code.splitlines()
            lang = ts_pack.detect_language(file_path)
            if not lang:
                return f"Language not supported for `{os.path.basename(file_path)}`."
            cfg = ts_pack.ProcessConfig.all(lang)
            result = ts_pack.process(code, config=cfg)

            def _find(items: list, name: str) -> dict | None:
                for item in items:
                    n = item.get("name") or ""
                    if n == name or n.startswith(name + "("):
                        return item
                    found = _find(item.get("children") or [], name)
                    if found:
                        return found
                return None

            node = _find(result.get("structure") or [], symbol_name)
            if not node:
                try:
                    import graph_bootstrap

                    project_root = None
                    cur = os.path.abspath(os.path.dirname(file_path))
                    while cur and cur != os.path.dirname(cur):
                        if os.path.isdir(os.path.join(cur, ".git")):
                            project_root = cur
                            break
                        cur = os.path.dirname(cur)

                    if project_path:
                        rel_path = os.path.relpath(file_path, project_path)
                        project_id = get_project_id(workspace_id)
                        driver = await graph_bootstrap.require_driver()
                        async with driver.session(
                            database=graph_bootstrap._NEO4J_DB
                        ) as session:
                            records = await _execute_read(
                                """
                                MATCH (f:File {project_id:$pid, filepath:$fp})-[:CONTAINS]->(s)
                                WHERE s.name = $name
                                RETURN s.start_line AS sl, s.end_line AS el, labels(s) AS labels
                                LIMIT 1
                                """,
                                pid=project_id,
                                fp=normalize_neo4j_path(rel_path),
                                name=symbol_name,
                                op="extract_function_body_fallback",
                            )
                            rec = records[0] if records else None
                            if rec:
                                sl = rec.get("sl")
                                el = rec.get("el")
                                if sl is not None and el is not None:
                                    sl1, el1 = sl, el
                                    body = "\n".join(lines_list[sl1 - 1 : el1])
                                    label = (rec.get("labels") or [""])[0]
                                    return (
                                        f"## `{symbol_name}` ({label})  —  L{sl1}–{el1}\n"
                                        f"```{lang}\n{body}\n```"
                                    )
                except Exception:
                    pass
                return f"`{symbol_name}` not found in `{os.path.basename(file_path)}`."

            # ts-pack stores line info in span dict, 0-indexed
            span = node.get("span") or {}
            sl = span.get("start_line")
            el = span.get("end_line")
            if sl is None or el is None:
                return f"Found `{symbol_name}` but line range not available."

            # Convert to 1-indexed for display and slicing
            sl1, el1 = sl + 1, el + 1
            body = "\n".join(lines_list[sl:el1])
            kind = node.get("kind", "")
            return (
                f"## `{symbol_name}` ({kind})  —  L{sl1}–{el1}\n```{lang}\n{body}\n```"
            )
        except Exception as e:
            return f"Error extracting body: {str(e)}"

    @mcp.tool()
    async def extract_class_interface(workspace_id: str, file_path: str, class_name: str) -> str:
        """
        Extract the public API surface of a class or struct — method signatures
        only, no bodies. Useful for understanding what a class exposes without
        reading thousands of lines.

        Args:
            workspace_id:  The logical workspace ID or absolute path to the project root.
            file_path:     Relative path to the source file within the project.
            class_name:    Name of the class or struct.
        """
        try:
            import tree_sitter_language_pack as ts_pack

            project_path = get_workspace_path(workspace_id)
            if not os.path.isabs(file_path):
                file_path = os.path.join(project_path, file_path)

            if not os.path.exists(file_path):
                return f"File not found: {file_path}"
            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                code = fh.read()
            lang = ts_pack.detect_language(file_path)
            if not lang:
                return f"Language not supported for `{os.path.basename(file_path)}`."
            cfg = ts_pack.ProcessConfig(lang)
            result = ts_pack.process(code, config=cfg)

            def _find_class(items: list, name: str) -> dict | None:
                for item in items:
                    n = item.get("name") or ""
                    if n == name:
                        return item
                    found = _find_class(item.get("children") or [], name)
                    if found:
                        return found
                return None

            cls_node = _find_class(result.get("structure") or [], class_name)
            if not cls_node:
                return f"Class/struct `{class_name}` not found in `{os.path.basename(file_path)}`."

            kind = cls_node.get("kind", "")
            cls_span = cls_node.get("span") or {}
            sl = (cls_span.get("start_line") or 0) + 1
            el = (cls_span.get("end_line") or 0) + 1
            out = [f"## `{class_name}` ({kind})  L{sl}–{el}\n"]

            MAX_MEMBERS = 100
            children = cls_node.get("children") or []
            
            for child in children[:MAX_MEMBERS]:
                child_kind = child.get("kind") or ""
                if child_kind in (
                    "Method",
                    "Function",
                    "Property",
                    "Variable",
                    "Const",
                    "Enum",
                    "Struct",
                    "Class",
                    "EnumCase",
                ):
                    name = child.get("name") or "?"
                    cspan = child.get("span") or {}
                    csl = (cspan.get("start_line") or 0) + 1
                    out.append(f"  {name}  (L{csl})")

            if len(children) > MAX_MEMBERS:
                out.append(f"  ... (and {len(children) - MAX_MEMBERS} more members truncated)")
            elif not children:
                out.append("  (no public members found)")

            return "\n".join(out)
        except Exception as e:
            return f"Error extracting interface: {str(e)}"

    @mcp.tool()
    async def find_symbol_usages(workspace_id: str, file_path: str, symbol_name: str) -> str:
        """
        Find all usages of a symbol within a single file using tree-sitter AST.

        Complements find_references (which is cross-file) — this focuses on
        intra-file call sites, assignments, and type annotations.

        Args:
            workspace_id: The logical workspace ID or absolute path to the project root.
            file_path:    Relative path to the source file within the project.
            symbol_name:  Identifier to search for.
        """
        try:
            import re

            project_path = get_workspace_path(workspace_id)
            if not os.path.isabs(file_path):
                file_path = os.path.join(project_path, file_path)

            if not os.path.exists(file_path):
                return f"File not found: {file_path}"
            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                lines_list = fh.readlines()

            # Use word-boundary regex for precision — fast and accurate
            pattern = re.compile(rf"\b{re.escape(symbol_name)}\b")
            hits: list[str] = []
            for i, line in enumerate(lines_list, start=1):
                if pattern.search(line):
                    hits.append(f"  L{i:4d}: {line.rstrip()}")
                if len(hits) >= 60:
                    hits.append(f"  … (truncated at 60 matches)")
                    break

            if not hits:
                return f"`{symbol_name}` not found in `{os.path.basename(file_path)}`."
            return (
                f"## `{symbol_name}` in `{os.path.basename(file_path)}`"
                f"  ({len(hits)} hit{'s' if len(hits) != 1 else ''})\n"
                + "\n".join(hits)
            )
        except Exception as e:
            return f"Error finding usages: {str(e)}"

    @mcp.tool()
    async def list_available_models() -> str:
        """
        List models currently available in LM Studio via the proxy.
        """
        try:
            _, _, _, _, proxy_models = get_memory_modules()
            models_data = await proxy_models.fetch_lmstudio_models()
            keys = proxy_models.extract_model_keys(models_data)
            if keys:
                return "\n".join(keys)
            return "No models found."
        except Exception as e:
            return f"Error listing models: {str(e)}"
