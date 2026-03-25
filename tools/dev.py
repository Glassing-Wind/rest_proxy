"""tools/dev.py — developer workflow tools (git, grep, test discovery, linting)."""
import os
import sys
from typing import List
from mcp.server.fastmcp import FastMCP
from _helpers import get_memory_modules


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    async def git_summary(project_path: str) -> str:
        """
        Show the current git state of a project: recent commits, working-tree
        status, and a diff stat of any uncommitted changes.

        Use this at the start of a session to understand what has changed recently,
        or before making edits to confirm the branch and working-tree state.

        Args:
            project_path: Absolute path to the project root (must be a git repo).
        """
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

            branch    = git(["rev-parse", "--abbrev-ref", "HEAD"])
            status    = git(["status", "--short"])
            log       = git(["log", "--oneline", "-12"])
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
    async def grep_codebase(project_path: str, pattern: str, file_glob: str = "") -> str:
        """
        Search for a literal string or regex pattern across the entire codebase
        using ripgrep (rg). Faster and more precise than semantic search for
        exact tokens: error messages, config keys, SQL fragments, symbol names.

        Use when you need exact-text matches rather than semantic similarity.
        Complements search_codebase (semantic) and find_references (graph+pg).

        Args:
            project_path: Absolute path to the project root.
            pattern:      Literal string or regex to search for.
            file_glob:    Optional glob to restrict files, e.g. '*.py' or '*.rs'.
                          Leave empty to search all non-ignored files.
        """
        try:
            import subprocess, shutil
            from collections import defaultdict

            rg = shutil.which("rg") or "rg"
            cmd = [rg, "--line-number", "--no-heading", "--color=never",
                   "--max-count=3", "--max-filesize=500K", "-e", pattern]
            if file_glob:
                cmd += ["--glob", file_glob]
            cmd.append(".")

            r = subprocess.run(cmd, cwd=project_path, capture_output=True, text=True, timeout=15)
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
    async def get_test_coverage_for(project_path: str, file_path: str) -> str:
        """
        Find test files that cover a given source file.

        Uses three strategies:
        1. Name convention  — e.g. 'foo.py' → 'test_foo.py', 'foo_test.py'
        2. Import graph     — files in the Neo4j graph that IMPORT this file
        3. Directory scan   — ripgrep for the source basename inside test files

        Run this before modifying a file to know exactly what to test afterwards.

        Args:
            project_path: Absolute path to the project root.
            file_path:    Relative path to the source file within the project.
        """
        try:
            import hashlib, subprocess
            project_id = hashlib.md5(project_path.encode()).hexdigest()[:12]
            basename   = os.path.splitext(os.path.basename(file_path))[0]
            results: dict[str, str] = {}

            candidates = [
                f"test_{basename}.py", f"{basename}_test.py",
                f"test_{basename}.ts", f"{basename}.test.ts",
                f"test_{basename}.rs", f"{basename}_test.rs",
                f"test_{basename}.go", f"{basename}_test.go",
            ]
            find_args = ["-type", "f", "("]
            for i, c in enumerate(candidates):
                if i > 0:
                    find_args.append("-o")
                find_args += ["-name", c]
            find_args.append(")")
            r = subprocess.run(["find", project_path] + find_args,
                               capture_output=True, text=True, timeout=10)
            for p in r.stdout.strip().splitlines():
                rel = os.path.relpath(p, project_path)
                results[rel] = "name convention"

            try:
                import graph_bootstrap
                await graph_bootstrap.init_graph_db()
                driver = graph_bootstrap.get_driver()
                async with driver.session(database=graph_bootstrap._NEO4J_DB) as session:
                    res = await session.run("""
                        MATCH (src:File {project_id: $pid})
                        WHERE src.filepath ENDS WITH $fp
                        MATCH (tester:File)-[:IMPORTS]->(src)
                        RETURN tester.filepath AS tf LIMIT 20
                    """, pid=project_id, fp=file_path)
                    async for rec in res:
                        tf = rec["tf"]
                        if tf:
                            rel = os.path.relpath(tf, project_path) if os.path.isabs(tf) else tf
                            results.setdefault(rel, "imports this file")
            except Exception:
                pass

            try:
                rg_r = subprocess.run(
                    ["rg", "--files-with-matches", "--glob", "*test*", "-e", basename, "."],
                    cwd=project_path, capture_output=True, text=True, timeout=10
                )
                for p in rg_r.stdout.strip().splitlines():
                    rel = os.path.relpath(os.path.join(project_path, p), project_path)
                    results.setdefault(rel, "mentions basename")
            except Exception:
                pass

            if not results:
                return (f"No test files found for `{file_path}`.\n"
                        "Either no tests exist yet or the project is not indexed.")
            out = [f"## Tests covering `{file_path}`\n"]
            for rel, reason in sorted(results.items()):
                out.append(f"- `{rel}`  ← {reason}")
            return "\n".join(out)
        except Exception as e:
            return f"Error finding tests: {str(e)}"

    @mcp.tool()
    async def get_changed_symbols(project_path: str, since: str = "HEAD~1") -> str:
        """
        List which functions and classes changed between the current working tree
        and a commit reference — not just which files, but which *symbols*.

        Runs `git diff <since>` and extracts function/class definition lines
        from modified hunks. Supports Python, Rust, TypeScript, Go, Swift, Ruby.

        Args:
            project_path: Absolute path to the project root (must be a git repo).
            since:        Git ref to diff against (default 'HEAD~1' = last commit).
                          Examples: 'HEAD', 'main', 'abc1234', 'HEAD~3'.
        """
        try:
            import subprocess, re
            r = subprocess.run(
                ["git", "diff", "--unified=4", since],
                cwd=project_path, capture_output=True, text=True, timeout=15
            )
            diff = r.stdout
            if not diff.strip():
                return f"No changes vs `{since}`. Working tree is clean."

            file_re = re.compile(r"^diff --git a/.+ b/(.+)$")
            def_re  = re.compile(
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
    async def lint_project_subset(files: List[str]) -> str:
        """
        Run best-available linter on a set of files.
        Supports Swift (swiftlint) and Python (pylint/ruff).

        Args:
            files: List of absolute paths to files to lint.
        """
        import subprocess
        import shutil
        import sys
        results = []

        # Extend PATH with common conda/venv bin dirs so linters installed
        # inside the MCP process's environment are always discoverable.
        def _which(name: str) -> str | None:
            hit = shutil.which(name)
            if hit:
                return hit
            # Try alongside the current Python interpreter
            py_bin = os.path.dirname(sys.executable)
            candidate = os.path.join(py_bin, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
            return None

        ruff      = _which("ruff")
        pylint    = _which("pylint")
        swiftlint = _which("swiftlint")
        for f in files:
            if f.endswith(".swift"):
                if swiftlint:
                    res = subprocess.run([swiftlint, "lint", f], capture_output=True, text=True)
                    results.append(f"--- SwiftLint: {os.path.basename(f)} ---\n{res.stdout or 'No issues found.'}")
                else:
                    results.append(f"SwiftLint not found. Skipping {f}.")
            elif f.endswith(".py"):
                linter = ruff or pylint
                if linter:
                    cmd = [linter, "check", f] if "ruff" in linter else [linter, "--errors-only", f]
                    res = subprocess.run(cmd, capture_output=True, text=True)
                    results.append(f"--- Python Linter ({os.path.basename(linter)}): {os.path.basename(f)} ---\n{res.stdout or 'No issues found.'}")
                else:
                    results.append(f"Python linter (ruff/pylint) not found. Skipping {f}.")
        return "\n\n".join(results) if results else "No supported files provided or no linters found."

    @mcp.tool()
    async def swift_doc_lookup(file_path: str, symbol_name: str) -> str:
        """
        Extract documentation comments for a Swift symbol using SourceKitten.

        Args:
            file_path: Absolute path to the Swift file.
            symbol_name: Name of the symbol to lookup.
        """
        import subprocess
        import json
        import shutil
        import sys

        if not os.path.exists(file_path):
            return "File not found."

        # Prefer SourceKitten — gives full AST including doc_comment fields
        sk = shutil.which("sourcekitten") or os.path.join(
            "/opt/homebrew/bin", "sourcekitten"
        )
        if sk and os.path.isfile(sk):
            try:
                res = subprocess.run(
                    [sk, "structure", "--file", file_path],
                    capture_output=True, text=True, timeout=30
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
                            offset     = node.get("key.offset", 0) or 0
                            end_offset = offset + (node.get("key.length", 0) or 0)
                            start_line = raw[:offset].count(b"\n") + 1
                            end_line   = raw[:end_offset].count(b"\n") + 1
                        except Exception:
                            start_line, end_line = "?", "?"
                        lines = [
                            f"**{symbol_name}** ({node.get('key.kind', '?').split('.')[-1]})",
                            f"File: `{file_path}`  L{start_line}–{end_line}",
                        ]
                        doc = node.get("key.doc.comment") or node.get("key.annotated_decl", "")
                        if doc:
                            lines += ["", doc.strip()]
                        return "\n".join(lines)
                    return f"Symbol `{symbol_name}` not found in `{os.path.basename(file_path)}`."
            except Exception as e:
                pass  # fall through to ts_pack

        # Fallback: ts_pack structural extraction
        try:
            import skeleton_extractor
            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()
            doc = skeleton_extractor.get_swift_docs(code, symbol_name)
            return doc if doc else f"No documentation found for '{symbol_name}'."
        except Exception as e:
            return f"Error looking up docs: {str(e)}"

    # ── ts-pack AST tools (no Neo4j required) ────────────────────────────────

    @mcp.tool()
    async def get_file_outline(file_path: str) -> str:
        """
        Generate a structural outline of any source file using tree-sitter AST.

        Faster than describe_file — works on unindexed files, no Neo4j required.
        Returns a hierarchical list of classes, functions, structs, and methods
        with their line ranges.

        Args:
            file_path: Absolute path to any source file.
        """
        try:
            import tree_sitter_language_pack as ts_pack
            if not os.path.exists(file_path):
                return "File not found."
            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                code = fh.read()
            lang = ts_pack.detect_language(file_path)
            if not lang:
                return f"Language not supported for `{os.path.basename(file_path)}`."
            cfg    = ts_pack.ProcessConfig(lang)
            result = ts_pack.process(code, config=cfg)

            def _fmt(items: list, indent: int = 0) -> list[str]:
                out = []
                pad = "  " * indent
                for item in items:
                    name = item.get("name") or "?"
                    kind = item.get("kind") or ""
                    sig  = item.get("signature") or ""
                    sl   = item.get("start_line", "")
                    el   = item.get("end_line", "")
                    loc  = f"  L{sl}–{el}" if sl else ""
                    label = sig if sig else f"{kind} {name}"
                    out.append(f"{pad}{label}{loc}")
                    out.extend(_fmt(item.get("children") or [], indent + 1))
                return out

            lines = _fmt(result.get("structure") or [])
            if not lines:
                return f"No symbols found in `{os.path.basename(file_path)}`."
            header = f"## {os.path.basename(file_path)}  ({lang})\n"
            return header + "\n".join(lines)
        except Exception as e:
            return f"Error generating outline: {str(e)}"

    @mcp.tool()
    async def extract_function_body(file_path: str, symbol_name: str) -> str:
        """
        Extract the exact source code of a function, method, or class using
        tree-sitter AST. No Neo4j required — works on any file.

        More precise than reading line ranges manually.

        Args:
            file_path:   Absolute path to the source file.
            symbol_name: Name of the function, method, or class to extract.
        """
        try:
            import tree_sitter_language_pack as ts_pack
            if not os.path.exists(file_path):
                return "File not found."
            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                code = fh.read()
            lines_list = code.splitlines()
            lang = ts_pack.detect_language(file_path)
            if not lang:
                return f"Language not supported for `{os.path.basename(file_path)}`."
            cfg    = ts_pack.ProcessConfig(lang)
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
                return f"`{symbol_name}` not found in `{os.path.basename(file_path)}`."

            # ts-pack stores line info in span dict, 0-indexed
            span = node.get("span") or {}
            sl = span.get("start_line")
            el = span.get("end_line")
            if sl is None or el is None:
                return f"Found `{symbol_name}` but line range not available."

            # Convert to 1-indexed for display and slicing
            sl1, el1 = sl + 1, el + 1
            body = "\n".join(lines_list[sl : el1])
            kind = node.get("kind", "")
            return (
                f"## `{symbol_name}` ({kind})  —  L{sl1}–{el1}\n"
                f"```{lang}\n{body}\n```"
            )
        except Exception as e:
            return f"Error extracting body: {str(e)}"

    @mcp.tool()
    async def extract_class_interface(file_path: str, class_name: str) -> str:
        """
        Extract the public API surface of a class or struct — method signatures
        only, no bodies. Useful for understanding what a class exposes without
        reading thousands of lines.

        Args:
            file_path:  Absolute path to the source file.
            class_name: Name of the class or struct.
        """
        try:
            import tree_sitter_language_pack as ts_pack
            if not os.path.exists(file_path):
                return "File not found."
            with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                code = fh.read()
            lang = ts_pack.detect_language(file_path)
            if not lang:
                return f"Language not supported for `{os.path.basename(file_path)}`."
            cfg    = ts_pack.ProcessConfig(lang)
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
            sl   = (cls_span.get("start_line") or 0) + 1
            el   = (cls_span.get("end_line")   or 0) + 1
            out  = [f"## `{class_name}` ({kind})  L{sl}–{el}\n"]

            for child in cls_node.get("children") or []:
                child_kind = child.get("kind") or ""
                if child_kind in ("Method", "Function", "Property", "Variable",
                                  "Const", "Enum", "Struct", "Class"):
                    name = child.get("name") or "?"
                    cspan = child.get("span") or {}
                    csl   = (cspan.get("start_line") or 0) + 1
                    out.append(f"  {name}  (L{csl})")

            if len(out) == 1:
                out.append("  (no public members found)")
            return "\n".join(out)
        except Exception as e:
            return f"Error extracting interface: {str(e)}"

    @mcp.tool()
    async def find_symbol_usages(file_path: str, symbol_name: str) -> str:
        """
        Find all usages of a symbol within a single file using tree-sitter AST.

        Complements find_references (which is cross-file) — this focuses on
        intra-file call sites, assignments, and type annotations.

        Args:
            file_path:   Absolute path to the source file.
            symbol_name: Identifier to search for.
        """
        try:
            import re
            if not os.path.exists(file_path):
                return "File not found."
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
            _, _, _, _, proxy = get_memory_modules()
            models_data = await proxy.fetch_lmstudio_models()
            keys = proxy.extract_model_keys(models_data)
            if keys:
                return "\n".join(keys)
            return "No models found."
        except Exception as e:
            return f"Error listing models: {str(e)}"
