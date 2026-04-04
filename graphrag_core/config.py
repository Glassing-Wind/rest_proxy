"""Centralized environment loading and base-path helpers."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_LOADED = False


def get_repo_root() -> Path:
    """Return the repository root (directory containing this package's parent)."""
    return Path(__file__).resolve().parents[1]


def load_env() -> None:
    """Idempotently load .env from the repo root."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    repo_root = get_repo_root()
    load_dotenv(os.path.join(repo_root, ".env"))
    _ENV_LOADED = True

