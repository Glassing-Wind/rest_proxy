"""Offline regressions for the October 2026 dependency security batch.

Core checks need the normal CI dependencies. The optional model check runs only
when Transformers/Sentence Transformers are installed; it downloads no models.
"""
import hashlib
import importlib.util
import unittest

import jwt
from hpack import Decoder, Encoder
from hpack.exceptions import OversizedHeaderListError


class DependencySecurityBatchTests(unittest.TestCase):
    def test_jwt_decode_does_not_mutate_reused_options(self):
        key = hashlib.sha256(b'offline JWT regression fixture').digest()
        options = {'require': ['sub'], 'verify_signature': True}
        token = jwt.encode({'sub': 'fixture'}, key, algorithm='HS256')
        self.assertEqual(jwt.decode(token, key, algorithms=['HS256'], options=options)['sub'], 'fixture')
        self.assertEqual(options, {'require': ['sub'], 'verify_signature': True})
        invalid = jwt.encode({'unrelated': True}, key, algorithm='HS256')
        with self.assertRaises(jwt.MissingRequiredClaimError):
            jwt.decode(invalid, key, algorithms=['HS256'], options=options)

    def test_hpack_rejects_header_list_above_configured_limit(self):
        data = Encoder().encode([('x-fixture', 'x' * 256)])
        with self.assertRaises(OversizedHeaderListError):
            Decoder(max_header_list_size=64).decode(data)

    @unittest.skipUnless(
        importlib.util.find_spec('sentence_transformers') is not None,
        'optional model dependencies not installed',
    )
    def test_local_model_forward_without_remote_code_or_downloads(self):
        import sentence_transformers
        import torch
        from transformers import BertConfig, BertModel

        self.assertTrue(sentence_transformers.__version__)
        model = BertModel(BertConfig(
            vocab_size=32, hidden_size=16, num_hidden_layers=1,
            num_attention_heads=2, intermediate_size=32,
        ))
        with torch.no_grad():
            output = model(input_ids=torch.tensor([[1, 2, 3]]))
        self.assertEqual(tuple(output.last_hidden_state.shape), (1, 3, 16))


if __name__ == '__main__':
    unittest.main()
