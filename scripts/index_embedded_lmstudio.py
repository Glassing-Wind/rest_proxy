#!/usr/bin/env python3
"""Opt-in real-model embedded indexing using an already loaded loopback model.

Requires Ladybug/LanceDB/ts-pack and an explicit local GGUF fingerprint. This is
experimental; the serving process does not attest its resident artifact bytes.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def run(args):
    from dotenv import load_dotenv
    from graphrag_core.indexing.embedded_repository import EmbeddedRepositoryOwner
    from local_embeddings import LMStudioConfig
    from local_embeddings.strict_lmstudio import StrictLMStudioEncoder

    load_dotenv(ROOT / '.env')
    paths = json.loads(args.manifest.read_text())
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise ValueError('Manifest must be a JSON array of relative paths')
    with args.model_artifact.open('rb') as stream:
        fingerprint = hashlib.file_digest(stream, 'sha256').hexdigest()
    encoder = StrictLMStudioEncoder(fingerprint, LMStudioConfig.from_env())
    try:
        await encoder.connect()
        async with EmbeddedRepositoryOwner(str(args.state), encoder.dimension) as owner:
            publication = await owner.index(str(args.root), args.project_id, paths,
                                              embed=encoder.embed_texts, encoder_id=encoder.encoder_id,
                                              encoder_metadata=encoder.descriptor)
        return {'project_id': args.project_id, 'run_id': publication['run_id'],
                'files': len(publication['manifest']['files']),
                'chunks': publication['manifest']['retrieval']['chunks'],
                'encoder_id': encoder.encoder_id, 'dimension': encoder.dimension,
                'artifact_binding_verified': False}
    finally:
        await encoder.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--model-artifact', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(asyncio.run(run(args)), sort_keys=True))
        return 0
    except Exception as exc:
        print(f'Real-model embedded indexing failed: {type(exc).__name__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
