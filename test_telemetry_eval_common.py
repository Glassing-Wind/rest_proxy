import json
import tempfile
import unittest
from pathlib import Path

from tools.brain.search.telemetry_eval_common import (
    count_signal,
    read_events,
    safe_bool_rate,
    telemetry_payload,
)


class TelemetryEvalCommonTests(unittest.TestCase):
    def test_read_events_skips_blank_malformed_and_non_object_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.ndjson"
            path.write_text(
                '\nnot-json\n[]\n' + json.dumps({"telemetry": {"hits": 2}}) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(read_events(path), [{"telemetry": {"hits": 2}}])

    def test_numeric_and_payload_helpers_fail_closed(self):
        self.assertEqual(safe_bool_rate(3, 0), 0.0)
        self.assertEqual(safe_bool_rate(1, 4), 0.25)
        self.assertEqual(telemetry_payload({"telemetry": []}), {})
        self.assertEqual(count_signal({"hits": "3"}, "hits"), 3)
        self.assertEqual(count_signal({"hits": "bad"}, "hits"), 0)


if __name__ == "__main__":
    unittest.main()
