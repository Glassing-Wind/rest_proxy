"""embedding_service.py – Embedding backend via LM Studio HTTP API.

Primary: LM Studio OpenAI-compatible embeddings endpoint (async httpx).
  - Model stays warm across jobs (no 13-19s cold-load per index)
  - Truly async HTTP → safe to call from asyncio without threading hacks
  - Supports up to MAX_CONCURRENT_REQUESTS parallel batch calls (default 4)
  - Falls back to raising a clear error if LM Studio is unreachable

Config (via env or .env):
  LM_BASE                          LM Studio base URL (default: http://127.0.0.1:1234)
  LM_PROXY_MEMORY_EMBEDDING_MODEL  Model name loaded in LM Studio
  LM_EMBED_BATCH_SIZE              Chunks per HTTP request (default: 64)
  LM_EMBED_CONCURRENCY             Max parallel requests (default: 4)
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import List

# ── Config ────────────────────────────────────────────────────────────────────
_BASE_URL   = os.getenv("LM_BASE", "http://127.0.0.1:1234").rstrip("/")
_MODEL      = os.getenv(
    "LM_PROXY_MEMORY_EMBEDDING_MODEL",
    "text-embedding-jina-embeddings-v2-base-code",
)
_BATCH_SIZE   = int(os.getenv("LM_EMBED_BATCH_SIZE",   "64"))
_CONCURRENCY  = int(os.getenv("LM_EMBED_CONCURRENCY",  "4"))
_EMBED_URL    = f"{_BASE_URL}/v1/embeddings"
_TIMEOUT      = 120  # seconds per HTTP request


async def _post_batch(client, texts: List[str]) -> List[List[float]]:
    """Send one batch to LM Studio and return float embeddings."""
    resp = await client.post(
        _EMBED_URL,
        json={"model": _MODEL, "input": texts},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    # Sort by index in case LM Studio returns out of order
    items = sorted(data["data"], key=lambda x: x["index"])
    return [item["embedding"] for item in items]


class EmbeddingService:
    """Thin async wrapper around LM Studio's embeddings API.

    Holds a single persistent httpx.AsyncClient for the process lifetime,
    reusing keep-alive TCP connections across all embedding rounds.
    This avoids ~1.5s of TCP handshake overhead per batch compared to
    creating a new client per request.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._client = None
        return cls._instance

    def __init__(self):
        pass  # client is created lazily in _get_client()

    def _get_client(self):
        """Return the shared AsyncClient, creating it on first call."""
        import httpx
        if self._client is None:
            # Size the pool to exactly CONCURRENCY so every concurrent
            # request reuses an existing keep-alive connection.
            limits = httpx.Limits(
                max_keepalive_connections=_CONCURRENCY,
                max_connections=_CONCURRENCY + 2,
                keepalive_expiry=30,
            )
            self._client = httpx.AsyncClient(limits=limits, timeout=_TIMEOUT)
        return self._client

    async def close(self) -> None:
        """Gracefully close the underlying HTTP client (call on shutdown)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def effective_batch_size(self) -> int:
        return _BATCH_SIZE

    @property
    def _device(self) -> str:
        return f"lmstudio({_MODEL})"

    async def embed_batch_async(
        self,
        texts: List[str],
        batch_size: int | None = None,
    ) -> List[List[float]]:
        """Embed *texts* via LM Studio, parallelising up to _CONCURRENCY batches.

        Splits texts into sub-batches of `batch_size`, sends up to
        _CONCURRENCY requests concurrently over the persistent client,
        returns embeddings in original order.
        """
        bs = batch_size if batch_size is not None else _BATCH_SIZE
        # Split into sub-batches
        sub_batches = [texts[i : i + bs] for i in range(0, len(texts), bs)]

        results: List[List[List[float]]] = [None] * len(sub_batches)  # type: ignore
        client = self._get_client()
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def _fetch(idx: int, batch: List[str]) -> None:
            async with sem:
                results[idx] = await _post_batch(client, batch)

        await asyncio.gather(*[_fetch(i, b) for i, b in enumerate(sub_batches)])

        # Flatten sub-batch results back into a single list
        out: List[List[float]] = []
        for chunk in results:
            out.extend(chunk)
        return out

    def embed_batch(
        self,
        texts: List[str],
        batch_size: int | None = None,
    ) -> List[List[float]]:
        """Synchronous wrapper — runs the async version in a new event loop.

        Used by doc_indexer.py and any other sync callers.
        For async callers (index_workspace.py), use embed_batch_async() directly.
        """
        return asyncio.run(self.embed_batch_async(texts, batch_size))


def get_embedding_service() -> EmbeddingService:
    return EmbeddingService()
