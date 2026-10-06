"""Offline strict-provider tests; no LM Studio service or model assets required."""
from dataclasses import replace
import json
import unittest
from unittest import mock

from local_embeddings.base import EmbeddingModelInfo
from local_embeddings.lmstudio import LMStudioConfig
from local_embeddings.strict_lmstudio import StrictLMStudioEncoder


class StrictEncoder(unittest.IsolatedAsyncioTestCase):
    def encoder(self, artifact='a' * 64):
        config = replace(LMStudioConfig.from_env(), base_url='http://127.0.0.1:1234',
                         embed_model='fixture', context_length=20, input_token_margin=1.0,
                         estimated_chars_per_token=3)
        encoder = StrictLMStudioEncoder(artifact, config)
        model = EmbeddingModelInfo('fixture', type='embedding', loaded=True,
                                   raw={'loaded_instances': [{'id': 'instance-1', 'config': {'context_length': 20}}]})
        encoder.provider.list_models = mock.AsyncMock(return_value=[model])
        encoder.provider.embed_texts = mock.AsyncMock(return_value=[[1., 0., 0.]])
        return encoder, model

    async def test_identity_truncation_and_instance_change(self):
        encoder, model = self.encoder()
        try:
            await encoder.connect()
            self.assertEqual(encoder.dimension, 3)
            self.assertTrue(encoder.encoder_id.startswith('lmstudio-sha256:'))
            self.assertFalse(encoder.provider.config.auto_load)
            self.assertEqual(await encoder.embed_texts(['short']), [[1., 0., 0.]])
            calls = encoder.provider.embed_texts.await_count
            with self.assertRaises(ValueError):
                await encoder.embed_texts(['x' * 100])
            self.assertEqual(encoder.provider.embed_texts.await_count, calls)
            model.raw['loaded_instances'][0]['id'] = 'replacement'
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                await encoder.embed_texts(['short'])
        finally:
            await encoder.close()

    async def test_response_dimension_and_norm_failures(self):
        encoder, _ = self.encoder()
        try:
            await encoder.connect()
            for vectors in ([], [[1., 0.]], [[0., 0., 0.]], [[float('nan'), 1., 0.]]):
                encoder.provider.embed_texts.return_value = vectors
                with self.assertRaises(ValueError):
                    await encoder.embed_texts(['short'])
        finally:
            await encoder.close()

    async def test_unloaded_model_refused_without_embedding_or_load(self):
        encoder, model = self.encoder()
        model.loaded = False
        encoder.provider.load_model = mock.AsyncMock()
        try:
            with self.assertRaises(RuntimeError):
                await encoder.connect()
            encoder.provider.embed_texts.assert_not_awaited()
            encoder.provider.load_model.assert_not_awaited()
        finally:
            await encoder.close()

    async def test_identity_stable_across_instances_but_changes_with_artifact(self):
        one, _ = self.encoder()
        two, model = self.encoder()
        model.raw['loaded_instances'][0]['id'] = 'new-instance'
        try:
            await one.connect()
            await two.connect()
            self.assertEqual(one.encoder_id, two.encoder_id)
            three, _ = self.encoder('b' * 64)
            try:
                await three.connect()
                self.assertNotEqual(one.encoder_id, three.encoder_id)
            finally:
                await three.close()
        finally:
            await one.close()
            await two.close()

    async def test_credentials_do_not_change_encoder_identity_or_descriptor(self):
        with mock.patch.dict('os.environ', {'LMSTUDIO_API_KEY': 'first-fixture-secret'}):
            one, _ = self.encoder()
        with mock.patch.dict('os.environ', {'LMSTUDIO_API_KEY': 'second-fixture-secret'}):
            two, _ = self.encoder()
        try:
            await one.connect()
            await two.connect()
            self.assertEqual(one.encoder_id, two.encoder_id)
            rendered = json.dumps(one.descriptor) + one._configuration
            self.assertNotIn('first-fixture-secret', rendered)
            self.assertNotIn('second-fixture-secret', rendered)
        finally:
            await one.close()
            await two.close()


if __name__ == '__main__':
    unittest.main()
