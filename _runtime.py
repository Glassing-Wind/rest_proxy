"""Runtime helpers for deterministic subprocess interpreter selection."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache


def _existing_python(path: str) -> str | None:
    if not path:
        return None
    expanded = os.path.expanduser(path.strip())
    if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
        return expanded
    return None


def _conda_base() -> str | None:
    conda_exe = os.getenv("CONDA_EXE") or shutil.which("conda")
    if not conda_exe:
        return None
    try:
        result = subprocess.run(
            [conda_exe, "info", "--base"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except Exception:
        return None
    base = (result.stdout or "").strip()
    return base or None


def _conda_env_python(env_name: str) -> str | None:
    current_env = os.getenv("CONDA_DEFAULT_ENV", "").strip()
    current_prefix = os.getenv("CONDA_PREFIX", "").strip()
    if current_env == env_name and current_prefix:
        for name in ("python", "python3", "python3.11"):
            candidate = _existing_python(os.path.join(current_prefix, "bin", name))
            if candidate:
                return candidate

    candidates: list[str] = []
    base = _conda_base()
    if base:
        candidates.extend(
            [
                os.path.join(base, "envs", env_name, "bin", "python"),
                os.path.join(base, "envs", env_name, "bin", "python3"),
                os.path.join(base, "envs", env_name, "bin", "python3.11"),
            ]
        )

    candidates.extend(
        [
            f"/opt/homebrew/Caskroom/miniforge/base/envs/{env_name}/bin/python",
            f"/opt/homebrew/Caskroom/miniforge/base/envs/{env_name}/bin/python3",
            f"/opt/homebrew/Caskroom/miniforge/base/envs/{env_name}/bin/python3.11",
            f"/opt/homebrew/Caskroom/miniconda/base/envs/{env_name}/bin/python",
            f"/opt/homebrew/Caskroom/miniconda/base/envs/{env_name}/bin/python3",
            f"/opt/homebrew/Caskroom/miniconda/base/envs/{env_name}/bin/python3.11",
        ]
    )

    for candidate in candidates:
        resolved = _existing_python(candidate)
        if resolved:
            return resolved
    return None


@lru_cache(maxsize=1)
def resolve_python_runtime() -> dict[str, object]:
    """Return the deterministic interpreter command for background jobs."""
    explicit = _existing_python(os.getenv("LM_PROXY_INDEX_PYTHON", "")) or _existing_python(
        os.getenv("LM_PROXY_PYTHON", "")
    )
    if explicit:
        return {
            "cmd": [explicit],
            "python": explicit,
            "source": "env",
            "conda_env": os.getenv("LM_PROXY_CONDA_ENV", "").strip(),
        }

    target_env = os.getenv("LM_PROXY_CONDA_ENV", "lmproxy").strip()
    resolved = _conda_env_python(target_env) if target_env else None
    if resolved:
        return {
            "cmd": [resolved],
            "python": resolved,
            "source": "conda_env_path",
            "conda_env": target_env,
        }

    return {
        "cmd": [sys.executable],
        "python": sys.executable,
        "source": "sys.executable",
        "conda_env": os.getenv("CONDA_DEFAULT_ENV", "").strip(),
    }
