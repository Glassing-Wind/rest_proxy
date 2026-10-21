"""Disposable native embedded benchmark; model-free synthetic vectors by default.

Each thread/trial uses a fresh process and new temporary graph/vector state. Cold
means first publication in that process, not cleared OS/disk/parser caches. Optional
LM Studio measurements require an already loaded model and explicit artifact hash.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tempfile
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def fixture_sources(file_count: int) -> dict[str, str]:
    """Build a reproducible public Python corpus with no repository/private inputs."""
    return {f'module_{index:04d}.py': (
        f'"""ARM validation fixture {index}; deterministic public source."""\n'
        f'def armprobeauthenticate_{index}(token):\n'
        '    """Validate authenticate token evidence."""\n'
        f'    return normalize_{index}(token)\n\n'
        f'def normalize_{index}(token):\n'
        '    return str(token).strip()\n\n'
        f'class Authenticator{index}:\n'
        '    def authenticate(self, token):\n'
        f'        return armprobeauthenticate_{index}(token)\n'
    ) for index in range(file_count)}


def capture_sources(root: Path, manifest_path: Path) -> dict[str, str]:
    """Freeze only explicitly listed Python files; never recursively discover sources."""
    root = root.resolve(strict=True)
    with manifest_path.open('rb') as stream:
        raw_manifest = stream.read(16385)
    if len(raw_manifest) > 16384:
        raise ValueError('Source manifest exceeds 16 KiB')
    paths = json.loads(raw_manifest)
    if (not root.is_dir() or not isinstance(paths, list) or not 1 <= len(paths) <= 100
            or any(not isinstance(path, str) for path in paths) or len(set(paths)) != len(paths)):
        raise ValueError('Manifest must list 1–100 distinct relative Python paths')
    sources = {}
    total = 0
    for name in sorted(paths):
        relative = Path(name)
        if (relative.is_absolute() or '..' in relative.parts or name != relative.as_posix()
                or relative.suffix != '.py'):
            raise ValueError('Manifest requires normalized relative Python source paths')
        path = root / relative
        if path.resolve(strict=True) != path or not path.is_file():
            raise ValueError('Source manifest symlinks/non-files are not supported')
        with path.open('rb') as stream:
            raw = stream.read(65537)
        total += len(raw)
        if len(raw) > 65536 or total > 4 * 1024 * 1024 or b'\0' in raw:
            raise ValueError('Explicit sources exceed 64 KiB/file or 4 MiB total, or contain binary data')
        sources[name] = raw.decode('utf-8')
    return sources


async def synthetic_vectors(texts: list[str]) -> list[list[float]]:
    """Deterministic nonzero hash vectors: storage exercise, no semantic quality claim."""
    return [[(byte + 1) / 256 for byte in hashlib.sha256(text.encode()).digest()[:8]] for text in texts]


def latency_summary(values: list[float]) -> dict:
    ordered = sorted(values)
    return {'samples': len(values), 'median_seconds': statistics.median(values),
            'p95_seconds': ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]}


async def run_trial(args) -> dict:
    """Exercise the real owner/parser/engines using only newly created fixture state."""
    started = time.perf_counter()
    import tree_sitter_language_pack as ts_pack
    from graphrag_core.indexing.embedded_repository import EmbeddedRepositoryOwner

    if not ts_pack.has_language('python'):
        raise RuntimeError('A cached Python grammar is required; prefetch it separately before benchmarking')
    sources = (json.loads(args.frozen_corpus.read_text(encoding='utf-8'))
               if args.frozen_corpus else fixture_sources(args.files))
    source_hashes = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in sources.items()}
    encoder = None
    embed = synthetic_vectors
    dimension = 8
    encoder_id = 'synthetic-sha256-eight-v1'
    encoder_descriptor = {'synthetic': True, 'semantic_quality_measured': False, 'model_calls': 0}
    encoder_setup_seconds = 0.0
    if args.encoder == 'lmstudio':
        from local_embeddings.strict_lmstudio import StrictLMStudioEncoder

        encoder_started = time.perf_counter()
        encoder = await StrictLMStudioEncoder(args.artifact_sha256).connect()
        encoder_setup_seconds = time.perf_counter() - encoder_started
        embed, dimension, encoder_id = encoder.embed_texts, encoder.dimension, encoder.encoder_id
        encoder_descriptor = encoder.descriptor
    import_preflight_seconds = time.perf_counter() - started - encoder_setup_seconds
    try:
        with tempfile.TemporaryDirectory(prefix='rest-proxy-arm-benchmark-') as directory:
            root = Path(directory) / 'source'
            root.mkdir()
            for name, source in sources.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding='utf-8')
            state = Path(directory) / 'state'
            opened = time.perf_counter()
            owner = EmbeddedRepositoryOwner(str(state), dimension, graph_threads=args.worker_threads)
            async with owner:
                cold_open_seconds = time.perf_counter() - opened
                if owner.graph.max_num_threads != args.worker_threads:
                    raise AssertionError('Thread override was not applied')
                indexed = time.perf_counter()
                first = await owner.index(str(root), 'armbenchmark', list(sources), embed=embed,
                                          encoder_id=encoder_id, encoder_metadata=encoder_descriptor)
                cold_index_seconds = time.perf_counter() - indexed
                chunks = first['manifest']['retrieval']['chunks']
                manifest_sha = first['manifest']['sha256']

                async def query(publication):
                    query_started = time.perf_counter()
                    hits = await owner.search('armbenchmark', encoder_id=encoder_id, text=args.text_query,
                                              mode='text', limit=5)
                    elapsed = time.perf_counter() - query_started
                    if not hits or any(hit['source_sha256'] != source_hashes.get(hit['file_path'])
                                       or hit['run_id'] != publication['run_id'] for hit in hits):
                        raise AssertionError('Text retrieval failed publication/source integrity')
                    return elapsed

                cold_query_seconds = await query(first)
                warm_query = [await query(first) for _ in range(args.queries)]
                warm_index = []
                last = first
                for _ in range(args.warm_reindexes):
                    indexed = time.perf_counter()
                    last = await owner.index(str(root), 'armbenchmark', list(sources), embed=embed,
                                             encoder_id=encoder_id, encoder_metadata=encoder_descriptor)
                    warm_index.append(time.perf_counter() - indexed)
                    if last['manifest']['retrieval']['chunks'] != chunks or last['run_id'] == first['run_id']:
                        raise AssertionError('Warm replacement changed corpus or failed to publish a fresh run')
                    await query(last)
            reopened = time.perf_counter()
            async with EmbeddedRepositoryOwner(str(state), dimension, graph_threads=args.worker_threads) as owner:
                durable = await owner.overview('armbenchmark')
                if durable['run_id'] != last['run_id']:
                    raise AssertionError('Durable reopen lost the current publication')
                await query(last)
            reopen_and_query_seconds = time.perf_counter() - reopened
            state_bytes = sum(path.stat().st_size for path in state.rglob('*') if path.is_file())
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {'threads': args.worker_threads, 'files': len(sources), 'chunks': chunks,
                'symbols': first['manifest']['symbols'], 'first_manifest_sha256': manifest_sha,
                'encoder': encoder_descriptor, 'import_preflight_seconds': import_preflight_seconds,
                'encoder_setup_seconds': encoder_setup_seconds, 'cold_open_seconds': cold_open_seconds,
                'cold_index_seconds': cold_index_seconds, 'cold_text_query_seconds': cold_query_seconds,
                'warm_index_seconds': warm_index, 'warm_text_query': latency_summary(warm_query),
                'reopen_and_text_query_seconds': reopen_and_query_seconds, 'state_bytes': state_bytes,
                'peak_process_rss_bytes': peak_rss if sys.platform == 'darwin' else peak_rss * 1024,
                'integrity': {'source_hashes': True, 'active_run': True, 'durable_reopen': True}}
    finally:
        if encoder is not None:
            await encoder.close()


def positive_count(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError('Use an integer between 1 and 100')
    return number


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--threads', default='2,4,8')
    parser.add_argument('--files', type=positive_count, default=12)
    parser.add_argument('--trials', type=positive_count, default=1)
    parser.add_argument('--queries', type=positive_count, default=10)
    parser.add_argument('--warm-reindexes', type=positive_count, default=2)
    parser.add_argument('--encoder', choices=('synthetic', 'lmstudio'), default='synthetic')
    parser.add_argument('--artifact-sha256', default='')
    parser.add_argument('--wheel', type=Path)
    parser.add_argument('--source-root', type=Path, help='Root for an explicitly authorized public Python corpus')
    parser.add_argument('--manifest', type=Path, help='JSON array of normalized relative Python paths')
    parser.add_argument('--text-query', default='authenticate', help='Nonempty text query expected in the corpus')
    parser.add_argument('--worker-threads', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--frozen-corpus', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if bool(args.source_root) != bool(args.manifest):
        parser.error('--source-root and --manifest must be supplied together')
    if not args.text_query.strip() or len(args.text_query.encode()) > 1024:
        parser.error('--text-query must be nonempty and at most 1024 bytes')
    if args.encoder == 'lmstudio' and (len(args.artifact_sha256) != 64 or any(
            c not in '0123456789abcdef' for c in args.artifact_sha256)):
        parser.error('LM Studio requires --artifact-sha256 for an already loaded model; no model is loaded by this tool')
    from memory.embedded_ladybug import resolve_ladybug_threads

    try:
        threads = [resolve_ladybug_threads(int(raw)) for raw in args.threads.split(',')]
        if len(threads) > 8 or len(set(threads)) != len(threads):
            raise ValueError('Use at most eight distinct thread settings')
        if args.worker_threads is not None:
            resolve_ladybug_threads(args.worker_threads)
    except ValueError as error:
        parser.error(str(error))
    if args.worker_threads is not None:
        print(json.dumps(asyncio.run(run_trial(args)), sort_keys=True))
        return
    from scripts.runtime_hardware import runtime_hardware, sha256_file

    corpus = capture_sources(args.source_root, args.manifest) if args.source_root else fixture_sources(args.files)
    manifest = [{'path': name, 'sha256': hashlib.sha256(source.encode()).hexdigest(),
                 'bytes': len(source.encode())} for name, source in sorted(corpus.items())]
    result = {'schema_version': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'runtime': runtime_hardware(wheel=args.wheel),
              'source_sha256': {str(path.relative_to(REPO_ROOT)): sha256_file(path) for path in (
                  Path(__file__), REPO_ROOT / 'scripts/runtime_hardware.py',
                  REPO_ROOT / 'memory/embedded_ladybug.py',
                  REPO_ROOT / 'graphrag_core/indexing/embedded_repository.py',
                  REPO_ROOT / 'requirements-embedded.txt')},
              'corpus': {'kind': ('explicit-frozen-python-manifest-v1' if args.source_root
                                 else 'built-in-public-deterministic-python-v1'), 'files': manifest,
                         'text_query': args.text_query,
                         'sha256': hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()},
              'policy': {'existing_state_used': False, 'model_loading': False,
                         'inference_calls': args.encoder != 'synthetic',
                         'cold_definition': 'fresh process and empty state; OS/disk/parser cache not cleared',
                         'comparative_acceleration_claim': False, 'concurrent_machine_activity_isolated': False},
              'status': 'running', 'trials': []}

    def retain_receipt():
        rendered = json.dumps(result, indent=2, sort_keys=True) + '\n'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding='utf-8')
        else:
            print(rendered, end='')

    with tempfile.TemporaryDirectory(prefix='rest-proxy-arm-corpus-') as directory:
        frozen = Path(directory) / 'source.json'
        frozen.write_text(json.dumps(corpus, sort_keys=True), encoding='utf-8')
        for trial in range(args.trials):
            # Rotate order to reduce a fixed first-setting bias; still an observational pilot.
            order = threads[trial % len(threads):] + threads[:trial % len(threads)]
            for count in order:
                command = [sys.executable, str(Path(__file__)), '--worker-threads', str(count),
                           '--files', str(args.files), '--queries', str(args.queries), '--frozen-corpus', str(frozen),
                           '--text-query', args.text_query, '--warm-reindexes', str(args.warm_reindexes),
                           '--encoder', args.encoder]
                if args.artifact_sha256:
                    command += ['--artifact-sha256', args.artifact_sha256]
                started = time.perf_counter()
                try:
                    completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=600)
                except subprocess.TimeoutExpired as error:
                    result.update(status='failed', failure={'threads': count, 'trial': trial + 1,
                                  'type': 'worker-timeout', 'timeout_seconds': error.timeout})
                    retain_receipt()
                    raise SystemExit('Benchmark worker timed out; partial receipt retained') from error
                if completed.returncode:
                    result.update(status='failed', failure={'threads': count, 'trial': trial + 1,
                                  'type': 'worker-exit', 'returncode': completed.returncode,
                                  'stderr_bytes': len(completed.stderr.encode()),
                                  'stderr_sha256': hashlib.sha256(completed.stderr.encode()).hexdigest()})
                    retain_receipt()
                    raise SystemExit('Benchmark worker failed; partial receipt retained without potentially sensitive stderr')
                row = json.loads(completed.stdout)
                row.update(trial=trial + 1, process_wall_seconds=time.perf_counter() - started)
                result['trials'].append(row)
    if len({(row['files'], row['chunks'], row['symbols']) for row in result['trials']}) != 1:
        raise AssertionError('Thread sweeps produced different native facts/chunk counts')
    result['summary'] = {str(count): {
        'cold_index': latency_summary([row['cold_index_seconds'] for row in result['trials'] if row['threads'] == count]),
        'warm_index': latency_summary([value for row in result['trials'] if row['threads'] == count
                                       for value in row['warm_index_seconds']])} for count in threads}
    result['status'] = 'passed'
    retain_receipt()
    if args.output:
        print(json.dumps({'output': str(args.output), 'trials': len(result['trials']), 'summary': result['summary']}))


if __name__ == '__main__':
    main()
