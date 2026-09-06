"""Provider contract for local embedding backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional


class EmbeddingProviderError(RuntimeError):
    """Base class for local embedding provider failures."""


class EmbeddingServerUnavailable(EmbeddingProviderError):
    """Raised when the local embedding server cannot be reached."""


class EmbeddingTimeoutError(EmbeddingProviderError):
    """Raised when a backend request times out."""


class ModelLoadError(EmbeddingProviderError):
    """Raised when a model lifecycle operation fails."""


class ModelNotLoadedError(EmbeddingProviderError):
    """Raised when an embedding request requires a model that is not loaded."""


class ModelTypeError(EmbeddingProviderError):
    """Raised when the selected model is not an embedding model."""


@dataclass(slots=True)
class ProviderHealth:
    ok: bool
    base_url: str
    detail: str


@dataclass(slots=True)
class EmbeddingModelInfo:
    id: str
    type: Optional[str] = None
    status: Optional[str] = None
    loaded: Optional[bool] = None
    raw: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class EmbeddingBatchResult:
    vectors: List[List[float]]
    batch_size: int
    latency_s: float


class LocalEmbeddingProvider(ABC):
    """Abstract interface for local embedding backends."""

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        raise NotImplementedError

    @abstractmethod
    async def list_models(self) -> List[EmbeddingModelInfo]:
        raise NotImplementedError

    @abstractmethod
    async def load_model(self) -> EmbeddingModelInfo:
        raise NotImplementedError

    @abstractmethod
    async def unload_model(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def embed_texts(
        self,
        texts: List[str],
        *,
        batch_size: int | None = None,
    ) -> List[List[float]]:
        raise NotImplementedError

    @property
    @abstractmethod
    def effective_batch_size(self) -> int:
        raise NotImplementedError

