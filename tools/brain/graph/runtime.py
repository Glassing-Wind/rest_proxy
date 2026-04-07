"""tools/graph/runtime.py — graph build runtime (locks, queue, metrics)."""

from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from typing import Awaitable, Callable


_GRAPH_LOCK_ENABLED = os.getenv(
    "LM_PROXY_GRAPH_BUILD_LOCK_REDIS", "1"
).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_GRAPH_LOCK_KEY = os.getenv("LM_PROXY_GRAPH_BUILD_LOCK_KEY", "lmproxy:graph_build_lock")
_GRAPH_LOCK_TTL_S = float(os.getenv("LM_PROXY_GRAPH_BUILD_LOCK_TTL", "300"))
_GRAPH_LOCK_WAIT_S = float(os.getenv("LM_PROXY_GRAPH_BUILD_LOCK_WAIT", "30"))
_GRAPH_LOCK_POLL_S = float(os.getenv("LM_PROXY_GRAPH_BUILD_LOCK_POLL", "0.2"))
_GRAPH_LOCK_RENEW_S = float(os.getenv("LM_PROXY_GRAPH_BUILD_LOCK_RENEW", "20"))
_REDIS_URL = os.getenv("LM_PROXY_REDIS_URL", "redis://localhost:6379/0")

_GRAPH_BUILD_QUEUE: asyncio.Queue | None = None
_GRAPH_BUILD_WORKER: asyncio.Task | None = None
_GRAPH_BUILD_LOCK = asyncio.Lock()
_REDIS_CLIENT: object | None = None

_METRICS_LOCK = threading.Lock()
_METRICS_MAX = 200
_METRICS: list[dict[str, object]] = []

_debug_log: Callable[..., None] | None = None
_run_build_with_retry: (
    Callable[[Callable[[str], Awaitable[str]], str, str], Awaitable[str]] | None
) = None
_build_asset_graph: Callable[[str], Awaitable[str]] | None = None


def configure(
    debug_log: Callable[..., None],
    run_build_with_retry: Callable[
        [Callable[[str], Awaitable[str]], str, str], Awaitable[str]
    ],
    build_asset_graph: Callable[[str], Awaitable[str]],
) -> None:
    global _debug_log
    global _run_build_with_retry
    global _build_asset_graph
    _debug_log = debug_log
    _run_build_with_retry = run_build_with_retry
    _build_asset_graph = build_asset_graph


def record_metric(event: str, **fields: object) -> None:
    with _METRICS_LOCK:
        _METRICS.append({"event": event, "ts": time.time(), **fields})
        if len(_METRICS) > _METRICS_MAX:
            del _METRICS[: len(_METRICS) - _METRICS_MAX]


def summarize_batches(event: str, limit: int = 50) -> tuple[int, int, int]:
    with _METRICS_LOCK:
        recent = [m for m in _METRICS if m.get("event") == event][-limit:]
    if not recent:
        return 0, 0, 0
    durations = [int(m.get("elapsed_ms", 0) or 0) for m in recent]
    return len(durations), int(sum(durations) / len(durations)), max(durations)


def get_last_graph_build_metric() -> dict[str, object] | None:
    with _METRICS_LOCK:
        for entry in reversed(_METRICS):
            if entry.get("event") == "graph_build_done":
                return dict(entry)
    return None


def get_recent_metrics(limit: int = 10) -> list[dict[str, object]]:
    limit = max(1, int(limit))
    with _METRICS_LOCK:
        return [dict(entry) for entry in _METRICS[-limit:]]


async def _get_redis() -> object | None:
    global _REDIS_CLIENT
    if not _GRAPH_LOCK_ENABLED:
        return None
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    try:
        import redis.asyncio as aioredis  # type: ignore

        client = aioredis.from_url(_REDIS_URL, decode_responses=True)
        await client.ping()
        _REDIS_CLIENT = client
        return _REDIS_CLIENT
    except Exception as exc:
        if _debug_log:
            _debug_log("graph_lock_redis_unavailable", error=str(exc))
        return None


async def _acquire_graph_lock() -> str | None:
    if not _GRAPH_LOCK_ENABLED:
        return None
    client = await _get_redis()
    if client is None:
        return None
    token = uuid.uuid4().hex
    deadline = time.monotonic() + _GRAPH_LOCK_WAIT_S
    while time.monotonic() < deadline:
        try:
            ok = await client.set(
                _GRAPH_LOCK_KEY,
                token,
                nx=True,
                ex=int(_GRAPH_LOCK_TTL_S),
            )
        except Exception as exc:
            if _debug_log:
                _debug_log("graph_lock_acquire_error", error=str(exc))
            return None
        if ok:
            return token
        await asyncio.sleep(_GRAPH_LOCK_POLL_S)
    return None


async def _release_graph_lock(token: str | None) -> None:
    if not token or not _GRAPH_LOCK_ENABLED:
        return
    client = await _get_redis()
    if client is None:
        return
    try:
        await client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
            1,
            _GRAPH_LOCK_KEY,
            token,
        )
    except Exception as exc:
        if _debug_log:
            _debug_log("graph_lock_release_error", error=str(exc))


async def _renew_graph_lock(token: str) -> bool:
    if not _GRAPH_LOCK_ENABLED:
        return False
    client = await _get_redis()
    if client is None:
        return False
    try:
        ok = await client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end",
            1,
            _GRAPH_LOCK_KEY,
            token,
            str(int(_GRAPH_LOCK_TTL_S)),
        )
        return bool(ok)
    except Exception as exc:
        if _debug_log:
            _debug_log("graph_lock_renew_error", error=str(exc))
        return False


async def _ensure_graph_build_worker() -> None:
    global _GRAPH_BUILD_QUEUE, _GRAPH_BUILD_WORKER
    async with _GRAPH_BUILD_LOCK:
        if _GRAPH_BUILD_QUEUE is None:
            _GRAPH_BUILD_QUEUE = asyncio.Queue()
        if _GRAPH_BUILD_WORKER is None or _GRAPH_BUILD_WORKER.done():
            _GRAPH_BUILD_WORKER = asyncio.create_task(_graph_build_worker())


async def enqueue_graph_build(project_path: str) -> None:
    await _ensure_graph_build_worker()
    assert _GRAPH_BUILD_QUEUE is not None
    await _GRAPH_BUILD_QUEUE.put(project_path)


async def _graph_build_worker() -> None:
    assert _GRAPH_BUILD_QUEUE is not None
    while True:
        item = await _GRAPH_BUILD_QUEUE.get()
        if item is None:
            _GRAPH_BUILD_QUEUE.task_done()
            return
        project_path = item
        lock_token = await _acquire_graph_lock()
        if _GRAPH_LOCK_ENABLED and lock_token is None:
            if _debug_log:
                _debug_log(
                    "graph_build_lock_busy",
                    project_path=project_path,
                    wait_s=_GRAPH_LOCK_WAIT_S,
                )
            record_metric(
                "graph_build_lock_busy",
                project_path=project_path,
                wait_s=_GRAPH_LOCK_WAIT_S,
            )
            await asyncio.sleep(0.5)
            await _GRAPH_BUILD_QUEUE.put(item)
            _GRAPH_BUILD_QUEUE.task_done()
            continue
        renew_task: asyncio.Task | None = None
        if _GRAPH_LOCK_ENABLED and lock_token:

            async def _renew_loop() -> None:
                while True:
                    await asyncio.sleep(_GRAPH_LOCK_RENEW_S)
                    ok = await _renew_graph_lock(lock_token)
                    if not ok:
                        record_metric(
                            "graph_build_lock_renew_failed",
                            project_path=project_path,
                        )
                        return

            renew_task = asyncio.create_task(_renew_loop())
        if _debug_log:
            _debug_log(
                "graph_build_start",
                project_path=project_path,
            )
        record_metric(
            "graph_build_start",
            project_path=project_path,
        )
        start = time.perf_counter()
        try:
            if _run_build_with_retry is None:
                raise RuntimeError("Graph runtime is not configured.")
            if _build_asset_graph is None:
                raise RuntimeError("Asset graph builder not configured.")
            await _run_build_with_retry(_build_asset_graph, "assets", project_path)
        except Exception as exc:
            if _debug_log:
                _debug_log(
                    "graph_build_error", project_path=project_path, error=str(exc)
                )
            record_metric(
                "graph_build_error", project_path=project_path, error=str(exc)
            )
        else:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            if _debug_log:
                _debug_log(
                    "graph_build_done",
                    project_path=project_path,
                    elapsed_ms=elapsed_ms,
                )
            record_metric(
                "graph_build_done",
                project_path=project_path,
                elapsed_ms=elapsed_ms,
            )
        finally:
            if renew_task:
                renew_task.cancel()
                try:
                    await renew_task
                except Exception:
                    pass
            await _release_graph_lock(lock_token)
            _GRAPH_BUILD_QUEUE.task_done()
