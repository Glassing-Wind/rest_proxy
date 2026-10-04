"""Telemetry must work under /app and must never break retrieval on path/IO failures."""
from pathlib import Path
import unittest
from unittest import mock

from memory import retrieval_telemetry as telemetry


class TelemetryTests(unittest.TestCase):
    def append(self):
        telemetry._append_telemetry_event({}, path_env='TEST_TELEMETRY_PATH',
            default_filename='probe.ndjson', max_events_env='TEST_TELEMETRY_MAX', default_max=2)

    def test_shallow_container_path(self):
        with mock.patch.object(telemetry, '__file__', '/app/memory/retrieval_telemetry.py'), \
             mock.patch.dict('os.environ', {'TEST_TELEMETRY_PATH': ''}), \
             mock.patch.object(telemetry, '_append_bounded_ndjson_event') as append:
            self.append()
            self.assertEqual(append.call_args.args[0], Path('/app/.runtime/probe.ndjson'))

    def test_path_resolution_failure_is_best_effort(self):
        with mock.patch.object(telemetry, 'Path', side_effect=OSError('unavailable')):
            self.append()

    def test_writer_failure_is_best_effort(self):
        with mock.patch.object(telemetry, '_append_bounded_ndjson_event', side_effect=OSError('read-only')):
            self.append()


if __name__ == '__main__':
    unittest.main()
