"""Offline NLTK upgrade checks; no corpus downloads or external services required."""
import socket
import unittest
from unittest.mock import patch

from nltk import pathsec
from nltk.tokenize import PunktSentenceTokenizer, wordpunct_tokenize


class NltkSecurityTests(unittest.TestCase):
    def test_shared_address_space_is_rejected_with_default_enforcement(self):
        self.assertTrue(pathsec.ENFORCE)
        for address in ('100.64.0.1', '100.127.255.254'):
            resolved = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 80))]
            with self.subTest(address=address), patch.object(pathsec, '_resolve_hostname', return_value=resolved):
                with self.assertRaises(PermissionError):
                    pathsec.validate_network_url('http://example.invalid/resource')

    def test_text_tokenization_does_not_require_network_or_corpus_downloads(self):
        self.assertEqual(wordpunct_tokenize('Safe text extraction.'), ['Safe', 'text', 'extraction', '.'])
        self.assertEqual(PunktSentenceTokenizer().tokenize('First sentence. Second sentence.'),
                         ['First sentence.', 'Second sentence.'])


if __name__ == '__main__':
    unittest.main()
