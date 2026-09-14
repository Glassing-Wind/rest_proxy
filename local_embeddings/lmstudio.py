"""LM Studio local embedding provider.

Uses LM Studio's native v1 REST API for lifecycle calls and the OpenAI-compatible
`/v1/embeddings` endpoint for actual embedding generation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable, List, Sequence

import httpx

from .base import (
    EmbeddingModelInfo,
    EmbeddingServerUnavailable,
    EmbeddingTimeoutError,
    LocalEmbeddingProvider,
    ModelLoadError,
    ModelNotLoadedError,
    ModelTypeError,
    ProviderHealth,
)

logger = logging.getLogger(__name__)


def _env_flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


@dataclass(slots=True)
class LMStudioConfig:
    base_url: str
    embed_model: str
    context_length: int
    eval_batch_size: int
    embed_timeout_s: float
    auto_load: bool
    max_batch_size: int
    max_batch_tokens: int
    concurrency: int
    input_token_margin: float
    estimated_chars_per_token: int

    @classmethod
    def from_env(cls) -> "LMStudioConfig":
        base_url = os.getenv(
            "LMSTUDIO_BASE_URL",
            os.getenv(
                "LM_PROXY_MEMORY_EMBEDDING_BASE_URL",
                os.getenv("LM_BASE", "http://127.0.0.1:1234"),
            ),
        ).rstrip("/")
        embed_model = os.getenv(
            "LMSTUDIO_EMBED_MODEL",
            os.getenv(
                "LM_PROXY_MEMORY_EMBEDDING_MODEL",
                "text-embedding-jina-embeddings-v2-base-code",
            ),
        ).strip()
        context_length = _int_env("LMSTUDIO_CONTEXT_LENGTH", 2048)
        eval_batch_size = _int_env("LMSTUDIO_EVAL_BATCH_SIZE", 512)
        embed_timeout_s = _float_env("LMSTUDIO_EMBED_TIMEOUT_S", 120.0)
        max_batch_size = _int_env(
            "LMSTUDIO_MAX_BATCH_SIZE",
            _int_env("LM_EMBED_BATCH_SIZE", 64),
        )
        max_batch_tokens = _int_env(
            "LMSTUDIO_MAX_BATCH_TOKENS",
            max(context_length, 1) * max(max_batch_size, 1),
        )
        concurrency = _int_env("LM_EMBED_CONCURRENCY", 4)
        input_token_margin = _float_env("LMSTUDIO_INPUT_TOKEN_MARGIN", 0.9)
        estimated_chars_per_token = _int_env("LMSTUDIO_ESTIMATED_CHARS_PER_TOKEN", 3)
        return cls(
            base_url=base_url,
            embed_model=embed_model,
            context_length=max(context_length, 1),
            eval_batch_size=max(eval_batch_size, 1),
            embed_timeout_s=max(embed_timeout_s, 1.0),
            auto_load=_env_flag("LMSTUDIO_AUTO_LOAD", "true"),
            max_batch_size=max(max_batch_size, 1),
            max_batch_tokens=max(max_batch_tokens, 1),
            concurrency=max(concurrency, 1),
            input_token_margin=min(max(input_token_margin, 0.25), 1.0),
            estimated_chars_per_token=max(estimated_chars_per_token, 1),
        )


class LMStudioEmbeddingProvider(LocalEmbeddingProvider):
    """Thin async LM Studio client for local embeddings."""

    def __init__(self, config: LMStudioConfig | None = None):
        self.config = config or LMStudioConfig.from_env()
        self._client: httpx.AsyncClient | None = None
        self._load_lock = asyncio.Lock()
        self._last_loaded_model_id: str | None = None

    @property
    def effective_batch_size(self) -> int:
        return self.config.max_batch_size

    @property
    def device_label(self) -> str:
        return f"lmstudio({self.config.embed_model})"

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            limits = httpx.Limits(
                max_keepalive_connections=self.config.concurrency,
                max_connections=self.config.concurrency + 2,
                keepalive_expiry=30,
            )
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                timeout=self.config.embed_timeout_s,
                limits=limits,
            )
        return self._client

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        retries: int = 0,
    ) -> Any:
        client = self._client_or_create()
        attempt = 0
        while True:
            try:
                response = await client.request(method, path, json=json_body)
                response.raise_for_status()
                if not response.content:
                    return None
                return response.json()
            except httpx.TimeoutException as exc:
                raise EmbeddingTimeoutError(
                    f"LM Studio request timed out for {method} {path} "
                    f"after {self.config.embed_timeout_s:.1f}s"
                ) from exc
            except httpx.ConnectError as exc:
                if attempt < retries:
                    attempt += 1
                    await asyncio.sleep(0.25 * attempt)
                    continue
                raise EmbeddingServerUnavailable(
                    f"LM Studio server is unavailable at {self.config.base_url}. "
                    "Start the server or check LMSTUDIO_BASE_URL."
                ) from exc
            except httpx.HTTPStatusError as exc:
                body = exc.response.text.strip()
                detail = body[:300] if body else exc.response.reason_phrase
                if exc.response.status_code in {404, 409, 422} and "/v1/embeddings" in path:
                    raise ModelNotLoadedError(
                        f"Embedding model '{self.config.embed_model}' is not loaded or "
                        f"cannot serve embeddings: {detail}"
                    ) from exc
                raise ModelLoadError(
                    f"LM Studio request failed for {method} {path}: "
                    f"{exc.response.status_code} {detail}"
                ) from exc

    @staticmethod
    def _parse_model_entry(raw: dict[str, Any]) -> EmbeddingModelInfo:
        model_id = (
            raw.get("id")
            or raw.get("identifier")
            or raw.get("model")
            or raw.get("modelKey")
            or raw.get("key")
            or ""
        )
        model_type = raw.get("type")
        status = raw.get("status")
        loaded = raw.get("loaded")
        loaded_instances = raw.get("loaded_instances")
        if loaded is None and isinstance(loaded_instances, list):
            loaded = bool(loaded_instances)
            if status is None:
                status = "loaded" if loaded else "unloaded"
        if loaded is None and status is not None:
            loaded = status not in {"unloaded", "available"}
        return EmbeddingModelInfo(
            id=model_id,
            type=model_type,
            status=status,
            loaded=loaded,
            raw=raw,
        )

    async def health_check(self) -> ProviderHealth:
        try:
            models = await self.list_models()
        except Exception as exc:
            return ProviderHealth(
                ok=False,
                base_url=self.config.base_url,
                detail=str(exc),
            )
        return ProviderHealth(
            ok=True,
            base_url=self.config.base_url,
            detail=f"{len(models)} model(s) visible",
        )

    async def list_models(self) -> List[EmbeddingModelInfo]:
        payload = await self._request_json("GET", "/api/v1/models", retries=1)
        if isinstance(payload, dict):
            data = payload.get("data")
            if not isinstance(data, list):
                data = payload.get("models")
            if isinstance(data, list):
                payload = data
        if not isinstance(payload, list):
            raise ModelLoadError("LM Studio returned an unexpected response for GET /api/v1/models")
        return [self._parse_model_entry(item) for item in payload if isinstance(item, dict)]

    async def load_model(self) -> EmbeddingModelInfo:
        models = await self.list_models()
        match = next((m for m in models if m.id == self.config.embed_model), None)
        if match is None:
            raise ModelLoadError(
                f"Embedding model '{self.config.embed_model}' was not found in LM Studio."
            )
        if match.type and match.type != "embedding":
            raise ModelTypeError(
                f"Configured LM Studio model '{self.config.embed_model}' is type "
                f"'{match.type}', not an embedding model."
            )
        if match.loaded is True and match.status not in {"unloaded", "available"}:
            self._last_loaded_model_id = self.config.embed_model
            return match

        payload = {
            "model": self.config.embed_model,
            "context_length": self.config.context_length,
            "eval_batch_size": self.config.eval_batch_size,
            "echo_load_config": True,
        }
        try:
            data = await self._request_json("POST", "/api/v1/models/load", json_body=payload, retries=1)
        except ModelLoadError as exc:
            message = str(exc)
            if "embedding models" not in message or "eval_batch_size" not in message:
                raise
            payload = {
                "model": self.config.embed_model,
                "context_length": self.config.context_length,
                "echo_load_config": True,
            }
            data = await self._request_json("POST", "/api/v1/models/load", json_body=payload, retries=1)
        info = self._parse_model_entry(data if isinstance(data, dict) else {})
        if info.id and info.id != self.config.embed_model:
            info = EmbeddingModelInfo(
                id=self.config.embed_model,
                type=info.type,
                status=info.status,
                loaded=info.loaded,
                raw=data if isinstance(data, dict) else None,
            )
        if info.type and info.type != "embedding":
            raise ModelTypeError(
                f"Configured LM Studio model '{self.config.embed_model}' is type '{info.type}', "
                "not an embedding model."
            )
        self._last_loaded_model_id = self.config.embed_model
        logger.info(
            "lmstudio model loaded model=%s context_length=%s eval_batch_size=%s",
            self.config.embed_model,
            self.config.context_length,
            self.config.eval_batch_size,
        )
        return info

    async def unload_model(self) -> None:
        await self._request_json(
            "POST",
            "/api/v1/models/unload",
            json_body={"model": self.config.embed_model},
            retries=1,
        )
        self._last_loaded_model_id = None

    async def _ensure_model_ready(self) -> None:
        async with self._load_lock:
            if self._last_loaded_model_id == self.config.embed_model:
                return
            models = await self.list_models()
            match = next((m for m in models if m.id == self.config.embed_model), None)
            if match is None:
                raise ModelLoadError(
                    f"Embedding model '{self.config.embed_model}' was not found in LM Studio."
                )
            if match.type and match.type != "embedding":
                raise ModelTypeError(
                    f"Configured LM Studio model '{self.config.embed_model}' is type "
                    f"'{match.type}', not an embedding model."
                )
            if match.loaded is True and match.status not in {"unloaded", "available"}:
                self._last_loaded_model_id = self.config.embed_model
                return
            if not self.config.auto_load:
                raise ModelNotLoadedError(
                    f"Embedding model '{self.config.embed_model}' is visible in LM Studio "
                    "but is not loaded. Enable LMSTUDIO_AUTO_LOAD=true or load it first."
                )
            await self.load_model()

    @staticmethod
    def _estimate_tokens_with_ratio(text: str, chars_per_token: int) -> int:
        # Conservative rough estimate, adequate for batching guardrails.
        return max(1, len(text) // max(chars_per_token, 1))

    def _estimate_tokens(self, text: str) -> int:
        return self._estimate_tokens_with_ratio(text, self.config.estimated_chars_per_token)

    def _max_input_tokens(self) -> int:
        return max(1, int(self.config.context_length * self.config.input_token_margin))

    def _normalize_text_for_context(self, text: str) -> tuple[str, bool]:
        limit_tokens = self._max_input_tokens()
        estimated = self._estimate_tokens(text)
        if estimated <= limit_tokens:
            return text, False

        char_budget = max(1, limit_tokens * self.config.estimated_chars_per_token)
        if len(text) <= char_budget:
            return text, False

        marker = "\n// ... truncated for embedding ...\n"
        if char_budget <= len(marker) + 32:
            return text[:char_budget], True

        payload_budget = char_budget - len(marker)
        head_budget = max(1, int(payload_budget * 0.75))
        tail_budget = max(1, payload_budget - head_budget)
        normalized = text[:head_budget] + marker + text[-tail_budget:]
        return normalized[:char_budget], True

    def _prepare_texts_for_context(self, texts: Sequence[str]) -> tuple[List[str], int]:
        normalized: List[str] = []
        truncated = 0
        for text in texts:
            clipped, changed = self._normalize_text_for_context(text)
            normalized.append(clipped)
            if changed:
                truncated += 1
        return normalized, truncated

    def _iter_batches(self, texts: Sequence[str], batch_size: int) -> Iterable[List[str]]:
        current: List[str] = []
        current_tokens = 0
        for text in texts:
            estimated = self._estimate_tokens(text)
            if estimated > self._max_input_tokens():
                raise ValueError(
                    f"Input text exceeds LM Studio safe context budget={self._max_input_tokens()} "
                    f"(estimated_tokens={estimated})."
                )
            if current and (
                len(current) >= batch_size
                or current_tokens + estimated > self.config.max_batch_tokens
            ):
                yield current
                current = []
                current_tokens = 0
            current.append(text)
            current_tokens += estimated
        if current:
            yield current

    async def _embed_request(self, texts: List[str]) -> List[List[float]]:
        started = time.perf_counter()
        data = await self._request_json(
            "POST",
            "/v1/embeddings",
            json_body={"model": self.config.embed_model, "input": texts},
            retries=0,
        )
        elapsed = time.perf_counter() - started
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ModelLoadError("LM Studio returned an unexpected response for POST /v1/embeddings")
        items = sorted(data["data"], key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") for item in items]
        if any(not isinstance(vec, list) for vec in vectors):
            raise ModelLoadError("LM Studio embeddings response was missing embedding vectors.")
        logger.debug("lmstudio embedding batch batch_size=%s latency_s=%.3f", len(texts), elapsed)
        return vectors  # type: ignore[return-value]

    async def embed_texts(
        self,
        texts: List[str],
        *,
        batch_size: int | None = None,
    ) -> List[List[float]]:
        if not texts:
            return []
        await self._ensure_model_ready()
        requested_batch = max(1, min(batch_size or self.config.max_batch_size, self.config.max_batch_size))
        prepared_texts, truncated = self._prepare_texts_for_context(texts)
        if truncated:
            logger.warning(
                "lmstudio embedding truncated %s/%s input texts to fit safe token budget=%s "
                "(context_length=%s chars_per_token=%s)",
                truncated,
                len(texts),
                self._max_input_tokens(),
                self.config.context_length,
                self.config.estimated_chars_per_token,
            )
        logger.debug("lmstudio embedding request batch_size=%s texts=%s", requested_batch, len(prepared_texts))
        batches = list(self._iter_batches(prepared_texts, requested_batch))
        if len(batches) == 1:
            return await self._embed_request(batches[0])

        results: List[List[List[float]]] = [None] * len(batches)  # type: ignore
        semaphore = asyncio.Semaphore(self.config.concurrency)

        async def _run(idx: int, group: List[str]) -> None:
            async with semaphore:
                results[idx] = await self._embed_request(group)

        await asyncio.gather(*[_run(idx, group) for idx, group in enumerate(batches)])
        flat: List[List[float]] = []
        for group in results:
            flat.extend(group)
        return flat


_LMSTUDIO_PROVIDER: LMStudioEmbeddingProvider | None = None


def get_lmstudio_provider() -> LMStudioEmbeddingProvider:
    global _LMSTUDIO_PROVIDER
    if _LMSTUDIO_PROVIDER is None:
        _LMSTUDIO_PROVIDER = LMStudioEmbeddingProvider()
    return _LMSTUDIO_PROVIDER
