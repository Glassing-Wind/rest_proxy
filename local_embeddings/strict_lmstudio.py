"""Opt-in LM Studio adapter for provenance-sensitive embedded indexing.

Uses an already loaded loopback model; no fake fallback or lifecycle mutation.
The caller fingerprints the local model artifact. That fingerprint is not a
cryptographic attestation of bytes resident in the serving process.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
import math
from urllib.parse import urlsplit

from local_embeddings.lmstudio import LMStudioConfig, LMStudioEmbeddingProvider


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


class StrictLMStudioEncoder:
    def __init__(self, artifact_sha256: str, config: LMStudioConfig | None = None):
        if len(artifact_sha256) != 64 or any(c not in '0123456789abcdef' for c in artifact_sha256):
            raise ValueError('An explicit lowercase SHA256 model-artifact fingerprint is required')
        config = config or LMStudioConfig.from_env()
        if urlsplit(config.base_url).hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('Strict LM Studio acceptance supports loopback endpoints only')
        self.provider = LMStudioEmbeddingProvider(replace(config, auto_load=False))
        self._artifact_sha256 = artifact_sha256
        self._configuration = _canonical(asdict(self.provider.config))
        self._closed = False
        self.dimension = None
        self.encoder_id = None
        self.descriptor = None
        self._instance_signature = None

    async def _loaded_model(self):
        if self._closed:
            raise RuntimeError('Encoder is closed')
        if _canonical(asdict(self.provider.config)) != self._configuration:
            raise RuntimeError('Encoder configuration changed; create a new encoder')
        models = await self.provider.list_models()
        model = next((m for m in models if m.id == self.provider.config.embed_model), None)
        if model is None or model.type != 'embedding' or model.loaded is not True:
            raise RuntimeError('Configured embedding model must already be loaded')
        instances = (model.raw or {}).get('loaded_instances') or []
        if len(instances) != 1:
            raise RuntimeError('Exactly one loaded model instance is required for acceptance')
        config = instances[0].get('config') or {}
        context = config.get('context_length')
        if not isinstance(context, int) or context < self.provider.config.context_length:
            raise RuntimeError('Loaded model context is smaller than the configured input budget')
        signature = _canonical(instances)
        if self._instance_signature is not None and signature != self._instance_signature:
            raise RuntimeError('Loaded model instance/configuration changed during this encoder session')
        return model, signature

    async def connect(self):
        if self.encoder_id is not None:
            raise RuntimeError('Encoder is already connected')
        model, self._instance_signature = await self._loaded_model()
        # Non-private probe; validate model response dimension before creating stores.
        vectors = await self.provider.embed_texts(['FIRE repository embedding dimension probe'])
        if len(vectors) != 1 or not vectors[0] or not all(math.isfinite(float(x)) for x in vectors[0]):
            raise RuntimeError('Invalid embedding probe response')
        self.dimension = len(vectors[0])
        self._validate(vectors, 1)
        config = self.provider.config
        self.descriptor = {
            'provider': 'lmstudio', 'model': model.id, 'artifact_sha256': self._artifact_sha256,
            'dimension': self.dimension, 'loaded_config': (model.raw or {})['loaded_instances'][0]['config'],
            'context_length': config.context_length, 'input_token_margin': config.input_token_margin,
            'estimated_chars_per_token': config.estimated_chars_per_token,
            'input_policy': 'reject-if-provider-normalization-changes-text-v1',
            'query_prefix': '', 'document_prefix': '', 'output_normalization': 'cosine-unit-v1',
            'tokenizer_pooling': 'model-artifact-and-serving-runtime-defined',
            'runtime_version': 'not-attested',
            'artifact_binding': 'local-file-fingerprint-not-attested-against-resident-weights',
        }
        self.encoder_id = 'lmstudio-sha256:' + hashlib.sha256(_canonical(self.descriptor).encode()).hexdigest()
        return self

    def _validate(self, vectors, count):
        if len(vectors) != count:
            raise ValueError('Embedding count does not match input count')
        for vector in vectors:
            values = [float(x) for x in vector]
            if len(values) != self.dimension or not all(math.isfinite(x) for x in values):
                raise ValueError('Embedding dimension changed or contains nonfinite values')
            norm = math.hypot(*values)
            if norm == 0 or not math.isfinite(norm):
                raise ValueError('Embedding has an invalid norm')

    async def embed_texts(self, texts):
        if self.encoder_id is None:
            raise RuntimeError('Connect the encoder before embedding')
        prepared, truncated = self.provider._prepare_texts_for_context(texts)
        if truncated or prepared != list(texts):
            raise ValueError('Embedding input would be truncated; change chunking before publication')
        await self._loaded_model()
        vectors = await self.provider.embed_texts(texts)
        self._validate(vectors, len(texts))
        await self._loaded_model()
        return vectors

    async def close(self):
        self._closed = True
        await self.provider.close()
