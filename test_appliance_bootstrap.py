"""Schema bootstrap must report unavailable required graph storage; offline mocks."""
import asyncio
import unittest
from unittest import mock

from memory import bootstrap


class BootstrapTests(unittest.TestCase):
    def test_graph_unavailable_returns_false(self):
        with mock.patch.object(bootstrap, '_ENABLE_PERSISTENCE', True), \
             mock.patch.object(bootstrap, '_PG_DSN', ''), \
             mock.patch.object(bootstrap.graph_bootstrap, '_NEO4J_ENABLED', True), \
             mock.patch.object(bootstrap.graph_bootstrap, 'require_driver',
                               mock.AsyncMock(side_effect=RuntimeError('unavailable'))):
            self.assertFalse(asyncio.run(bootstrap.bootstrap_schema()))

    def test_disabled_optional_graph_does_not_fail_bootstrap(self):
        with mock.patch.object(bootstrap, '_ENABLE_PERSISTENCE', True), \
             mock.patch.object(bootstrap, '_PG_DSN', ''), \
             mock.patch.object(bootstrap.graph_bootstrap, '_NEO4J_ENABLED', False), \
             mock.patch.object(bootstrap.graph_bootstrap, 'require_driver', mock.AsyncMock()) as driver:
            self.assertTrue(asyncio.run(bootstrap.bootstrap_schema()))
            driver.assert_not_called()

    def test_available_graph_passes(self):
        with mock.patch.object(bootstrap, '_ENABLE_PERSISTENCE', True), \
             mock.patch.object(bootstrap, '_PG_DSN', ''), \
             mock.patch.object(bootstrap.graph_bootstrap, '_NEO4J_ENABLED', True), \
             mock.patch.object(bootstrap.graph_bootstrap, 'require_driver', mock.AsyncMock()):
            self.assertTrue(asyncio.run(bootstrap.bootstrap_schema()))


if __name__ == '__main__':
    unittest.main()
