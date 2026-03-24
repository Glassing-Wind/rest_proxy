"""embedding_service.py – Singleton local embedding service.

Device: CPU unconditionally.

MPS (Apple Silicon GPU) is NOT used — torch 2.10.0 + jina-embeddings-v2-base-code
causes a Metal GPU driver kernel panic on macOS.

CPU strategy for M3 Max (12 perf-cores, 4 eff-cores):
- torch on arm64 links to Apple Accelerate (BLAS/BNNS) automatically.
- Pinned to p-cores only via set_num_threads() — e-cores are slow for matmul.
- batch_size=128 saturates Accelerate on this hardware with no memory pressure.
- encode() is serialised with a Lock (single-process design, no concurrency).
"""
import os
import sys
import threading
from typing import List


def _get_perf_cores() -> int:
    """Return the number of performance cores via sysctl (macOS) or fallback."""
    try:
        import subprocess
        out = subprocess.check_output(
            ["sysctl", "-n", "hw.perflevel0.physicalcpu"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return max(1, int(out))
    except Exception:
        return max(1, (os.cpu_count() or 4) // 2)


import torch  # noqa: E402 — torch must be imported before set_num_threads
from sentence_transformers import SentenceTransformer

# Pin torch BLAS threads to performance cores only.
_PERF_CORES = _get_perf_cores()
torch.set_num_threads(_PERF_CORES)
torch.set_num_interop_threads(1)  # Suppress extra inter-op threads

_ENCODE_LOCK = threading.Lock()
_BATCH_SIZE  = 128  # Saturates Accelerate on M3 Max; safe for RAM


class EmbeddingService:
    _instance = None
    _model: SentenceTransformer | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if EmbeddingService._model is not None:
            return
        print(
            f"[lm-proxy:embedding_service] Loading jina-embeddings-v2-base-code "
            f"(device=cpu, torch_threads={_PERF_CORES}, batch_size={_BATCH_SIZE})…",
            file=sys.stderr, flush=True,
        )
        try:
            EmbeddingService._model = SentenceTransformer(
                "jinaai/jina-embeddings-v2-base-code",
                trust_remote_code=True,
                device="cpu",
            )
            print(
                f"[lm-proxy:embedding_service] Model ready "
                f"(cpu, {_PERF_CORES} p-cores, batch={_BATCH_SIZE}).",
                file=sys.stderr, flush=True,
            )
        except Exception as exc:
            print(f"[lm-proxy:embedding_service] ERROR: {exc}", file=sys.stderr, flush=True)
            raise

    @property
    def effective_batch_size(self) -> int:
        return _BATCH_SIZE

    @property
    def _device(self) -> str:
        return "cpu"

    def embed_batch(self, texts: List[str], batch_size: int | None = None) -> List[List[float]]:
        """Return normalized float32 embeddings as Python lists (Neo4j-compatible).

        Serialised — never called from multiple threads simultaneously.
        """
        if EmbeddingService._model is None:
            raise RuntimeError("Embedding model not loaded")
        bs = batch_size if batch_size is not None else _BATCH_SIZE
        with _ENCODE_LOCK:
            embeddings = EmbeddingService._model.encode(
                texts,
                batch_size=bs,
                show_progress_bar=False,
                convert_to_tensor=False,
                normalize_embeddings=True,
            )
        return embeddings.tolist()


def get_embedding_service() -> EmbeddingService:
    return EmbeddingService()
