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
    paths = [p.strip() for p in output.splitlines() if p.strip()]
    if fallback_glob:
        paths = [p for p in paths if fnmatch.fnmatch(p, fallback_glob)]

    return paths[: max(0, fallback_max)], {"code": proc.returncode, "count": len(paths)}
