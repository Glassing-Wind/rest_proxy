"""Fallback helpers for search_codebase."""

import asyncio
import fnmatch
import os
import re
import shutil
from typing import Tuple, List, Dict


def extract_fallback_tokens(text: str) -> List[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]+", text)
    return [t for t in tokens if len(t) >= 3]


def build_definition_fallback_pattern(symbols: List[str]) -> str:
    escaped = [re.escape(symbol) for symbol in symbols if symbol]
    if not escaped:
        return ""
    joined = "|".join(sorted(set(escaped)))
    return (
        rf"(\bpub\s+fn\s+({joined})\s*\()"
        rf"|(\bfn\s+({joined})\s*\()"
        rf"|(\bdef\s+({joined})\s*\()"
        rf"|(\basync\s+def\s+({joined})\s*\()"
        rf"|(\bexport\s+(?:async\s+)?function\s+({joined})\s*\()"
        rf"|(\bfunction\s+({joined})\s*\()"
        rf"|(\bpub\s+use\b.*\b({joined})\b)"
    )


def build_member_usage_fallback_pattern(exprs: List[str]) -> str:
    patterns: List[str] = []
    for expr in exprs:
        if not expr:
            continue
        parts = [re.escape(part) for part in expr.split(".") if part]
        if len(parts) < 2:
            continue
        patterns.append(r"\b" + r"\s*\.\s*".join(parts) + r"\b")
    return "|".join(sorted(set(patterns)))


def normalize_fallback_paths(paths: List[str]) -> List[str]:
    normalized: List[str] = []
    for path in paths:
        p = (path or "").strip()
        if not p:
            continue
        if p.startswith("./"):
            p = p[2:]
        normalized.append(p)
    return normalized


async def run_definition_fallback_grep(
    project_root: str,
    query: str,
    fallback_glob: str,
    fallback_max: int,
    timeout_s: float = 8.0,
) -> Tuple[List[str], Dict[str, object]]:
    tokens = extract_fallback_tokens(query)
    if not tokens:
        return [], {"error": "no_tokens"}
    pattern = build_definition_fallback_pattern(tokens)
    if not pattern:
        return [], {"error": "no_pattern"}

    rg_path = os.getenv("LM_PROXY_RG_PATH") or shutil.which("rg")
    if not rg_path:
        return [], {"error": "rg_not_found"}

    cmd = [rg_path, "-l", "-i", "-P", pattern]
    if fallback_glob:
        root = fallback_glob
        if any(ch in fallback_glob for ch in "*?["):
            root = fallback_glob.split("*")[0]
            if root.endswith("/"):
                root = root[:-1]
            if not root:
                root = "."
        cmd.append(root)
    else:
        cmd.append(".")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=project_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            return [], {"error": "timeout"}
    except Exception:
        return [], {"error": "spawn_failed"}

    if proc.returncode not in (0, 1):
        err_text = stderr.decode("utf-8", errors="ignore")
        return [], {
            "error": "rg_failed",
            "code": proc.returncode,
            "stderr": err_text[:200],
        }

    output = stdout.decode("utf-8", errors="ignore")
    paths = normalize_fallback_paths([p.strip() for p in output.splitlines() if p.strip()])
    if fallback_glob:
        paths = [p for p in paths if fnmatch.fnmatch(p, fallback_glob)]
    return paths[: max(0, fallback_max)], {"code": proc.returncode, "count": len(paths)}


async def run_member_usage_fallback_grep(
    project_root: str,
    exprs: List[str],
    fallback_glob: str,
    fallback_max: int,
    timeout_s: float = 8.0,
) -> Tuple[List[str], Dict[str, object]]:
    pattern = build_member_usage_fallback_pattern(exprs)
    if not pattern:
        return [], {"error": "no_pattern"}

    rg_path = os.getenv("LM_PROXY_RG_PATH") or shutil.which("rg")
    if not rg_path:
        return [], {"error": "rg_not_found"}

    cmd = [rg_path, "-l", "-i", "-P", pattern]
    if fallback_glob:
        root = fallback_glob
        if any(ch in fallback_glob for ch in "*?["):
            root = fallback_glob.split("*")[0]
            if root.endswith("/"):
                root = root[:-1]
            if not root:
                root = "."
        cmd.append(root)
    else:
        cmd.append(".")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=project_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            return [], {"error": "timeout"}
    except Exception:
        return [], {"error": "spawn_failed"}

    if proc.returncode not in (0, 1):
        err_text = stderr.decode("utf-8", errors="ignore")
        return [], {
            "error": "rg_failed",
            "code": proc.returncode,
            "stderr": err_text[:200],
        }

    output = stdout.decode("utf-8", errors="ignore")
    paths = normalize_fallback_paths([p.strip() for p in output.splitlines() if p.strip()])
    if fallback_glob:
        paths = [p for p in paths if fnmatch.fnmatch(p, fallback_glob)]
    return paths[: max(0, fallback_max)], {"code": proc.returncode, "count": len(paths)}


async def run_fallback_grep(
    project_root: str,
    query: str,
    fallback_glob: str,
    fallback_max: int,
    timeout_s: float = 8.0,
) -> Tuple[List[str], Dict[str, object]]:
    tokens = extract_fallback_tokens(query)
    if not tokens:
        return [], {"error": "no_tokens"}
    pattern = "|".join(sorted(set(tokens)))

    rg_path = os.getenv("LM_PROXY_RG_PATH") or shutil.which("rg")
    if not rg_path:
        return [], {"error": "rg_not_found"}

    cmd = [rg_path, "-l", pattern]
    if fallback_glob:
        root = fallback_glob
        if any(ch in fallback_glob for ch in "*?["):
            root = fallback_glob.split("*")[0]
            if root.endswith("/"):
                root = root[:-1]
            if not root:
                root = "."
        cmd.append(root)
    else:
        cmd.append(".")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=project_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            proc.kill()
            return [], {"error": "timeout"}
    except Exception:
        return [], {"error": "spawn_failed"}

    if proc.returncode not in (0, 1):
        err_text = stderr.decode("utf-8", errors="ignore")
        return [], {
            "error": "rg_failed",
            "code": proc.returncode,
            "stderr": err_text[:200],
        }

    output = stdout.decode("utf-8", errors="ignore")
    paths = normalize_fallback_paths([p.strip() for p in output.splitlines() if p.strip()])
    if fallback_glob:
        paths = [p for p in paths if fnmatch.fnmatch(p, fallback_glob)]

    return paths[: max(0, fallback_max)], {"code": proc.returncode, "count": len(paths)}
