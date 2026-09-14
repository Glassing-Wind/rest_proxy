#!/usr/bin/env python3
"""Benchmark LM Studio embedding throughput across request batch sizes."""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from local_embeddings import get_lmstudio_provider

DEFAULT_BATCH_SIZES = [16, 32, 64, 128, 256]
DEFAULT_TEXTS = [
    "def add(a, b):\n    return a + b\n",
    "struct SidebarView: View { var body: some View { Text(\"Sidebar\") } }\n",
    "MATCH (n:File)-[:CONTAINS]->(s:Struct) RETURN n.file_path, count(s)\n",
    "async function embedBatch(texts) { return client.embeddings.create({ model, input: texts }); }\n",
]


def _load_texts(input_file: str | None, repeat: int) -> list[str]:
    if input_file:
        path = Path(input_file)
        raw = path.read_text()
        texts = [chunk.strip() for chunk in raw.split("\n---\n") if chunk.strip()]
    else:
        texts = list(DEFAULT_TEXTS)
    expanded: list[str] = []
    for _ in range(max(repeat, 1)):
        expanded.extend(texts)
    return expanded


async def _run_once(texts: list[str], batch_size: int) -> tuple[float, int]:
    provider = get_lmstudio_provider()
    latencies: list[float] = []
    started = time.perf_counter()
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        batch_started = time.perf_counter()
        await provider.embed_texts(batch, batch_size=batch_size)
        latencies.append(time.perf_counter() - batch_started)
    elapsed = time.perf_counter() - started
    avg_latency = statistics.mean(latencies) if latencies else 0.0
    return elapsed, avg_latency


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-file", help="Optional text file split by '\\n---\\n'")
    parser.add_argument(
        "--batch-sizes",
        default=",".join(str(v) for v in DEFAULT_BATCH_SIZES),
        help="Comma-separated request batch sizes",
    )
    parser.add_argument("--repeat", type=int, default=64, help="Repeat the sample corpus N times")
    args = parser.parse_args()

    texts = _load_texts(args.input_file, args.repeat)
    provider = get_lmstudio_provider()
    health = await provider.health_check()
    if not health.ok:
        raise SystemExit(f"LM Studio unavailable: {health.detail}")

    print(
        f"Benchmarking LM Studio embeddings model={provider.config.embed_model} "
        f"context_length={provider.config.context_length} "
        f"eval_batch_size={provider.config.eval_batch_size}"
    )
    for raw in args.batch_sizes.split(","):
        batch_size = int(raw.strip())
        elapsed, avg_latency = await _run_once(texts, batch_size)
        docs_per_s = len(texts) / elapsed if elapsed > 0 else 0.0
        print(
            f"batch_size={batch_size:<4} docs={len(texts):<5} "
            f"docs_per_s={docs_per_s:>7.2f} texts_per_s={docs_per_s:>7.2f} "
            f"avg_batch_latency_s={avg_latency:>6.3f}"
        )
    await provider.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
