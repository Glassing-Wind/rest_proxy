"""Offline CLI boundary tests; no listener, capture, credentials or models used."""
import os
from unittest import TestCase, main
from unittest.mock import patch

from scripts.lessons_corpus import main as command


class LessonsCliTests(TestCase):
    def test_serve_binds_loopback_and_validates_the_actual_host_port(self):
        token = 'fixture-capability-' + 'x' * 32
        with patch.dict(os.environ, {'LESSONS_TEST_TOKEN': token}), \
                patch('graphrag_core.lessons_corpus.server.create_app') as factory, \
                patch('uvicorn.run') as run:
            self.assertEqual(command(['serve', '--corpus', '/explicit/corpus', '--port', '8790',
                                      '--token-env', 'LESSONS_TEST_TOKEN']), 0)
        factory.assert_called_once_with('/explicit/corpus', token, allowed_host='127.0.0.1:8790')
        run.assert_called_once_with(factory.return_value, host='127.0.0.1', port=8790,
                                    access_log=False, proxy_headers=False)

    def test_missing_token_refuses_before_opening_a_listener_or_reading_state(self):
        with patch.dict(os.environ, {}, clear=True), patch('uvicorn.run') as run:
            with self.assertRaisesRegex(ValueError, 'capability'):
                command(['serve', '--corpus', '/not-an-existing-corpus'])
        run.assert_not_called()

    def test_privileged_port_refuses_before_app_creation(self):
        with patch('graphrag_core.lessons_corpus.server.create_app') as factory, patch('uvicorn.run') as run:
            with self.assertRaisesRegex(ValueError, 'nonprivileged'):
                command(['serve', '--corpus', '/explicit/corpus', '--port', '80'])
        factory.assert_not_called()
        run.assert_not_called()


if __name__ == '__main__':
    main()
