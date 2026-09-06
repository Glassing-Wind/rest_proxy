"""Shared config and runtime state paths for lmproxy."""

from __future__ import annotations

import os
from pathlib import Path


_LEGACY_BASE_DIR = Path("~/.gemini/antigravity").expanduser()


def get_config_dir() -> Path:
    """Return the machine-local config directory for lmproxy."""
    override = os.getenv("LM_PROXY_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path("~/.config/lmproxy").expanduser()


def get_legacy_config_dir() -> Path:
    """Return the legacy config directory used by older integrations."""
    return _LEGACY_BASE_DIR


def get_sessions_registry_path() -> Path:
    return get_config_dir() / "sessions.json"


def get_legacy_sessions_registry_path() -> Path:
    return get_legacy_config_dir() / "sessions.json"


def get_shared_env_path() -> Path:
    return get_config_dir() / ".env"


def get_legacy_shared_env_path() -> Path:
    return get_legacy_config_dir() / ".env"


def get_pinned_watches_path() -> Path:
    return get_config_dir() / "pinned_watches.json"


def get_legacy_watched_projects_path() -> Path:
    return get_legacy_config_dir() / "rest_proxy_config" / "watched_projects.json"


def get_active_sessions_path() -> Path:
    return get_config_dir() / "active_sessions.json"


def get_indexed_projects_path() -> Path:
    return get_config_dir() / "indexed_projects.json"


def get_legacy_indexed_projects_path() -> Path:
    return get_legacy_config_dir() / "rest_proxy_config" / "indexed_projects.json"
