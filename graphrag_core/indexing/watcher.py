"""Shared watcher state and persistence for indexing."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

from graphrag_core.app_state import (
    get_active_sessions_path,
    get_indexed_projects_path,
    get_legacy_indexed_projects_path,
    get_pinned_watches_path,
)
from graphrag_core.config import load_env
from graphrag_core.indexing.manifest import build_manifest

load_env()

WATCHED_PATHS: Dict[str, Dict[str, float]] = {}
PINNED_WATCHES: set[str] = set()
WATCH_INTERVAL = 30
WATCHER_ENABLED = os.getenv("LM_PROXY_WATCHER_ENABLED", "0").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
AUTO_WATCH_SESSION_WORKSPACE = os.getenv(
    "LM_PROXY_AUTO_WATCH_SESSION_WORKSPACE", "0"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_WATCHER_TASK: asyncio.Task | None = None
_WATCHER_INDEX_FN = None


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def _get_pinned_watches_path() -> Path:
    return get_pinned_watches_path()


def _get_active_sessions_path() -> Path:
    return get_active_sessions_path()


def _get_indexed_projects_path() -> Path:
    return get_indexed_projects_path()


def _legacy_indexed_projects_path() -> Path:
    return get_legacy_indexed_projects_path()


def _save_pinned_watches() -> None:
    try:
        _write_json(_get_pinned_watches_path(), sorted(PINNED_WATCHES))
    except Exception as e:
        print(f"[lm-proxy:watcher] Failed to save pinned watches: {e}", file=sys.stderr)


def _effective_watch_roots() -> list[str]:
    return sorted(PINNED_WATCHES)


def _sync_runtime_watch_roots() -> None:
    effective = set(_effective_watch_roots())
    for root in list(WATCHED_PATHS.keys()):
        if root not in effective:
            del WATCHED_PATHS[root]
    for root in effective:
        WATCHED_PATHS.setdefault(root, {})


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
    abs_path = os.path.abspath(abs_path)
    return abs_path in _effective_watch_roots()


def add_watch(abs_path: str) -> bool:
    abs_path = os.path.abspath(abs_path)
    if abs_path in PINNED_WATCHES:
        return False
    PINNED_WATCHES.add(abs_path)
    _save_pinned_watches()
    _sync_runtime_watch_roots()
    return True


def maybe_auto_watch(abs_path: str, *, reason: str = "session") -> bool:
    """
    Reserved for future client-specific auto-watch integrations.

    Default shipped behavior is manual-only, so this is a no-op unless
    explicit auto-watch is enabled.
    """
    if not WATCHER_ENABLED or not AUTO_WATCH_SESSION_WORKSPACE:
        return False
    return False


def remove_watch(abs_path: str) -> bool:
    abs_path = os.path.abspath(abs_path)
    if abs_path not in PINNED_WATCHES:
        return False
    PINNED_WATCHES.remove(abs_path)
    _save_pinned_watches()
    _sync_runtime_watch_roots()
    return True


def load_indexed_projects() -> dict[str, dict[str, object]]:
    paths = [_get_indexed_projects_path(), _legacy_indexed_projects_path()]
    for path in paths:
        try:
            if path.exists():
                data = _read_json(path)
                if isinstance(data, dict):
                    return data
        except Exception:
            continue
    return {}


def save_indexed_projects(data: dict[str, dict[str, object]]) -> None:
    try:
        _write_json(_get_indexed_projects_path(), data)
    except Exception as e:
        print(f"[lm-proxy:indexing] Failed to save index map: {e}", file=sys.stderr)


async def load_watched_config() -> None:
    """Load pinned watches and active session leases on startup."""
    if not WATCHER_ENABLED:
        print(
            "[lm-proxy:watcher] Disabled via LM_PROXY_WATCHER_ENABLED=0.",
            file=sys.stderr,
        )
        return
    try:
        pinned_path = _get_pinned_watches_path()
        if pinned_path.exists():
            paths = _read_json(pinned_path)
            if isinstance(paths, list):
                for project_path in paths:
                    if isinstance(project_path, str) and os.path.isdir(project_path):
                        PINNED_WATCHES.add(os.path.abspath(project_path))
        _sync_runtime_watch_roots()
        print(
            "[lm-proxy:watcher] Restored "
            f"{len(PINNED_WATCHES)} pinned watches.",
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
            _sync_runtime_watch_roots()
            for project_path, last_mtimes in list(WATCHED_PATHS.items()):
                changed = False
                current_mtimes = {}
                for entry in build_manifest(project_path):
                    fpath = str(entry.get("abs_path") or "")
                    if not fpath:
                        continue
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
