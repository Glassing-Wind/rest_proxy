"""Compatibility wrapper around the local embedding provider surface.

Primary backend:
- LM Studio native v1 REST API for model lifecycle (`/api/v1/*`)
- LM Studio OpenAI-compatible `/v1/embeddings` for actual embeddings

Existing repo callers keep using `get_embedding_service()` and
`embed_batch_async()`.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from typing import List

from local_embeddings import get_lmstudio_provider

_BATCH_SIZE = int(os.getenv("LM_EMBED_BATCH_SIZE", "64"))
_CONCURRENCY = int(os.getenv("LM_EMBED_CONCURRENCY", "4"))
_FAKE_EMBEDDINGS = os.getenv("LM_PROXY_FAKE_EMBEDDINGS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_FAKE_DIM = int(os.getenv("LM_PROXY_MEMORY_EMBEDDING_DIM", "768"))


def _fake_embedding_for_text(text: str, dim: int) -> List[float]:
    out: List[float] = []
    counter = 0
    seed = text.encode("utf-8", errors="ignore")
    while len(out) < dim:
        digest = hashlib.sha256(seed + counter.to_bytes(4, "big")).digest()
        counter += 1
        for idx in range(0, len(digest), 4):
            if len(out) >= dim:
                break
            chunk = digest[idx : idx + 4]
            value = int.from_bytes(chunk, "big", signed=False)
            normalized = (value / 0xFFFFFFFF) * 2.0 - 1.0
            out.append(normalized)
    return out


class EmbeddingService:
    """Compatibility wrapper used throughout the repo."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def effective_batch_size(self) -> int:
        if _FAKE_EMBEDDINGS:
            return _BATCH_SIZE
        return get_lmstudio_provider().effective_batch_size

    @property
    def _device(self) -> str:
        if _FAKE_EMBEDDINGS:
            return f"fake({_FAKE_DIM})"
        return get_lmstudio_provider().device_label

    async def close(self) -> None:
        if _FAKE_EMBEDDINGS:
            return
        await get_lmstudio_provider().close()

    async def embed_batch_async(
        self,
        texts: List[str],
        batch_size: int | None = None,
    ) -> List[List[float]]:
        if _FAKE_EMBEDDINGS:
            return [_fake_embedding_for_text(text, _FAKE_DIM) for text in texts]
        return await get_lmstudio_provider().embed_texts(texts, batch_size=batch_size)

    def embed_batch(
        self,
        texts: List[str],
        batch_size: int | None = None,
    ) -> List[List[float]]:
        return asyncio.run(self.embed_batch_async(texts, batch_size))


def get_embedding_service() -> EmbeddingService:
    return EmbeddingService()
