"""Shared watcher state and persistence for indexing."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Dict

from graphrag_core.config import load_env

load_env()

CONFIG_DIR = os.path.expanduser("~/.gemini/antigravity/rest_proxy_config")
WATCHED_CONFIG_PATH = os.path.join(CONFIG_DIR, "watched_projects.json")
INDEXED_CONFIG_PATH = os.path.join(CONFIG_DIR, "indexed_projects.json")
WATCHED_PATHS: Dict[str, Dict[str, float]] = {}  # project_path -> {file_path: mtime}
WATCH_INTERVAL = 30  # seconds between polls
WATCHER_ENABLED = os.getenv("LM_PROXY_WATCHER_ENABLED", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
AUTO_WATCH_SESSION_WORKSPACE = os.getenv(
    "LM_PROXY_AUTO_WATCH_SESSION_WORKSPACE", "1"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_WATCHER_TASK: asyncio.Task | None = None
_WATCHER_INDEX_FN = None


def is_enabled() -> bool:
    return WATCHER_ENABLED


def set_enabled(enabled: bool) -> None:
    global WATCHER_ENABLED
    WATCHER_ENABLED = enabled


def get_task() -> asyncio.Task | None:
    return _WATCHER_TASK


def get_index_fn():
    return _WATCHER_INDEX_FN


def set_index_fn(index_fn) -> None:
    global _WATCHER_INDEX_FN
    _WATCHER_INDEX_FN = index_fn


def is_watched(abs_path: str) -> bool:
    return abs_path in WATCHED_PATHS


def add_watch(abs_path: str) -> bool:
    if abs_path in WATCHED_PATHS:
        return False
    WATCHED_PATHS[abs_path] = {}
    _save_watched_config()
    return True


def maybe_auto_watch(abs_path: str, *, reason: str = "session") -> bool:
    """
    Opportunistically add a workspace root to the watched set.

    This is a no-op unless the watcher loop is enabled and auto-watch is on.
    """
    if not WATCHER_ENABLED or not AUTO_WATCH_SESSION_WORKSPACE:
        return False
    if not abs_path or not os.path.isdir(abs_path):
        return False
    abs_path = os.path.abspath(abs_path)
    if abs_path in WATCHED_PATHS:
        return False
    WATCHED_PATHS[abs_path] = {}
    _save_watched_config()
    print(
        f"[lm-proxy:watcher] Auto-watching project: {abs_path} (reason={reason})",
        file=sys.stderr,
    )
    return True


def remove_watch(abs_path: str) -> bool:
    if abs_path not in WATCHED_PATHS:
        return False
    del WATCHED_PATHS[abs_path]
    _save_watched_config()
    return True


def _save_watched_config() -> None:
    """Save the list of watched project paths to a local JSON config."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(WATCHED_CONFIG_PATH, "w") as f:
            json.dump(list(WATCHED_PATHS.keys()), f)
    except Exception as e:
        print(f"[lm-proxy:watcher] Failed to save config: {e}", file=sys.stderr)


def load_indexed_projects() -> dict[str, dict[str, object]]:
    try:
        if os.path.exists(INDEXED_CONFIG_PATH):
            with open(INDEXED_CONFIG_PATH) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        return {}
    return {}


def save_indexed_projects(data: dict[str, dict[str, object]]) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(INDEXED_CONFIG_PATH, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[lm-proxy:indexing] Failed to save index map: {e}", file=sys.stderr)


async def load_watched_config() -> None:
    """Load the list of watched project paths from the config on startup."""
    if not WATCHER_ENABLED:
        print(
            "[lm-proxy:watcher] Disabled via LM_PROXY_WATCHER_ENABLED=0.",
            file=sys.stderr,
        )
        return
    try:
        if os.path.exists(WATCHED_CONFIG_PATH):
            with open(WATCHED_CONFIG_PATH) as f:
                paths = json.load(f)
            for p in paths:
                if os.path.exists(p):
                    WATCHED_PATHS[os.path.abspath(p)] = {}
            print(
                f"[lm-proxy:watcher] Restored {len(WATCHED_PATHS)} watched projects.",
                file=sys.stderr,
            )
    except Exception as e:
        print(f"[lm-proxy:watcher] Failed to load config: {e}", file=sys.stderr)


async def start_watcher(index_fn) -> asyncio.Task | None:
    """Start the polling watcher loop and return the task."""
    global _WATCHER_TASK
    set_index_fn(index_fn)
    if not WATCHER_ENABLED:
        return None
    _WATCHER_TASK = asyncio.create_task(_poll_watcher(index_fn))
    return _WATCHER_TASK


async def stop_watcher() -> None:
    """Cancel the watcher task if running."""
    global _WATCHER_TASK
    if _WATCHER_TASK:
        _WATCHER_TASK.cancel()
        try:
            await _WATCHER_TASK
        except asyncio.CancelledError:
            pass
        _WATCHER_TASK = None


async def _poll_watcher(index_fn) -> None:
    """Background loop to check for file changes in watched projects."""
    while True:
        try:
            for project_path, last_mtimes in list(WATCHED_PATHS.items()):
                changed = False
                current_mtimes = {}
                for root, _, files in os.walk(project_path):
                    if any(
                        x in root
                        for x in [
                            ".git",
                            "node_modules",
                            "__pycache__",
                            "build",
                            "dist",
                        ]
                    ):
                        continue
                    for f in files:
                        if not f.endswith(
                            (
                                ".py",
                                ".swift",
                                ".js",
                                ".ts",
                                ".jsx",
                                ".tsx",
                                ".md",
                                ".rs",
                                ".go",
                                ".cpp",
                                ".c",
                                ".h",
                                ".java",
                                ".rb",
                                ".php",
                                ".cs",
                                ".json",
                                ".toml",
                                ".yaml",
                                ".yml",
                            )
                        ):
                            continue
                        fpath = os.path.join(root, f)
                        try:
                            mtime = os.path.getmtime(fpath)
                            current_mtimes[fpath] = mtime
                            if fpath not in last_mtimes or mtime > last_mtimes[fpath]:
                                changed = True
                        except (OSError, FileNotFoundError):
                            continue
                if not changed and len(current_mtimes) != len(last_mtimes):
                    changed = True
                WATCHED_PATHS[project_path] = current_mtimes
                if changed:
                    print(
                        f"[lm-proxy:watcher] Change detected in {project_path}. Triggering index...",
                        file=sys.stderr,
                    )
                    try:
                        await index_fn(project_path)
                    except Exception as e:
                        print(
                            f"[lm-proxy:watcher] Indexing failed: {e}", file=sys.stderr
                        )
        except Exception as e:
            print(f"[lm-proxy:watcher] Loop error: {e}", file=sys.stderr)
        await asyncio.sleep(WATCH_INTERVAL)
